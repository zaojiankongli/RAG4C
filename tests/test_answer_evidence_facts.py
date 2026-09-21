from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core import catalog
from core.answer_evidence_facts import (
    AnswerEvidenceRepository,
    digest_sha256,
    safe_preview,
)
from models.orm import Account, Base, Tenant, TenantMember
from server.answer_evidence_api import router
from server.knowledge_auth import issue_knowledge_actor_token


def test_safe_preview_redacts_sensitive_shapes() -> None:
    preview = safe_preview("联系 user@example.com 用 sk-abcd1234 重置 123456789012")
    assert "user@example.com" not in preview
    assert "sk-abcd1234" not in preview
    assert "123456789012" not in preview
    assert "[email]" in preview
    assert "[redacted]" in preview
    assert "[num]" in preview
    assert digest_sha256("a") == digest_sha256("a")
    assert digest_sha256("a") != digest_sha256("b")


def _engine_with_tenant():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="A", status="active"),
                Account(id="owner-a", name="O", email="o@a.test"),
                TenantMember(account_id="owner-a", tenant_id="tenant-a", role="owner"),
            ]
        )
        session.commit()
    return engine


def test_record_and_query_answer_fact_evidence_refs() -> None:
    engine = _engine_with_tenant()
    repo = AnswerEvidenceRepository(engine)
    fact_id = repo.record_answer_fact(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id="run-1",
        question="How is access approved?",
        answer="Via approval workflow with evidence.",
        route="rag",
        outcome="answered",
        citations=[
            {"chunk_id": "chunk-1", "status": "ok"},
            {"chunk_id": "chunk-2", "status": "unsupported"},
        ],
        evidence=[{"chunk_id": "chunk-1", "doc_id": "doc-1", "text": "RAW SECRET BODY"}],
    )
    assert fact_id.startswith("af-")
    detail = repo.get_answer_fact("tenant-a", fact_id)
    assert detail is not None
    assert detail["query_digest"]
    assert detail["safe_query_preview"]
    assert "approval" in detail["safe_query_preview"].lower() or "How" in detail["safe_query_preview"]
    dumped = str(detail)
    assert "RAW SECRET BODY" not in dumped
    assert "Via approval workflow" not in dumped  # raw answer not stored
    assert detail["citation_count"] == 2
    assert detail["evidence_count"] == 2
    assert detail["evidence_refs"][0]["chunk_id"] == "chunk-1"
    assert detail["evidence_refs"][0]["document_id"] == "doc-1"
    assert detail["evidence_refs"][0]["source_kind"] == "document"
    assert "evidence_digest" in detail["evidence_refs"][0]
    assert detail["evidence_refs"][1]["citation_status"] == "unsupported"
    assert detail.get("qa_evidence_count") == 0

    by_run = repo.get_by_run("tenant-a", "run-1", dataset_id="dataset-a")
    assert by_run is not None
    assert by_run["id"] == fact_id
    # dataset scope enforcement: wrong dataset must miss
    assert repo.get_by_run("tenant-a", "run-1", dataset_id="other-ds") is None
    assert repo.get_answer_fact("tenant-a", fact_id, dataset_id="other-ds") is None
    assert repo.get_answer_fact("tenant-a", fact_id, dataset_id="dataset-a") is not None
    listed = repo.list_answer_facts("tenant-a", dataset_id="dataset-a")
    assert listed["count"] == 1

    # empty answer becomes abstained
    abstain_id = repo.record_answer_fact(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        question="no answer",
        answer="",
        outcome="answered",
    )
    abstain = repo.get_answer_fact("tenant-a", abstain_id)
    assert abstain["outcome_code"] == "abstained"


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch):
    engine = _engine_with_tenant()
    settings = SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("ae-secret"), actor_max_ttl_s=900
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.include_router(router)
    client = TestClient(app, client=("10.0.0.2", 50000))
    token = issue_knowledge_actor_token(
        "owner-a", "tenant-a", 300, int(time.time()), settings=settings
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": "tenant-a",
        "X-Request-ID": "req-ae",
    }
    repo = AnswerEvidenceRepository(engine)
    fid = repo.record_answer_fact(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id="run-api",
        question="q",
        answer="a",
        citations=[{"chunk_id": "c1", "status": "ok"}],
    )
    return client, headers, fid


def test_answer_evidence_api_read_paths(api) -> None:
    client, headers, fid = api
    listed = client.get(
        "/api/knowledge-bases/dataset-a/answer-facts",
        headers=headers,
    )
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    detail = client.get(
        f"/api/knowledge-bases/dataset-a/answer-facts/{fid}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["id"] == fid
    by_run = client.get(
        "/api/knowledge-bases/dataset-a/answer-facts/by-run/run-api",
        headers=headers,
    )
    assert by_run.status_code == 200
    assert by_run.json()["run_id"] == "run-api"
    missing = client.get(
        "/api/knowledge-bases/dataset-a/answer-facts/by-run/nope",
        headers=headers,
    )
    assert missing.status_code == 404
    wrong_ds = client.get(
        "/api/knowledge-bases/dataset-other/answer-facts/by-run/run-api",
        headers=headers,
    )
    assert wrong_ds.status_code == 404
    wrong_detail = client.get(
        f"/api/knowledge-bases/dataset-other/answer-facts/{fid}",
        headers=headers,
    )
    assert wrong_detail.status_code == 404
