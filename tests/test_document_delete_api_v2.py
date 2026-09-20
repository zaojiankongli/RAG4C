from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core import catalog
from models.orm import (
    Account,
    Base,
    Dataset,
    Document,
    DocumentDeleteBatch,
    DocumentDeleteOperation,
    KnowledgeAuditEvent,
    Tenant,
    TenantMember,
)
from server import documents
from server.knowledge_auth import issue_knowledge_actor_token


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("delete-api-actor-secret"),
            actor_max_ttl_s=900,
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )


def _engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="Tenant A", status="active"),
                Tenant(id="tenant-b", name="Tenant B", status="active"),
                Dataset(id="dataset-a", tenant_id="tenant-a", name="Dataset A", status="active"),
                Dataset(id="dataset-b", tenant_id="tenant-b", name="Dataset B", status="active"),
                Account(id="owner-a", name="Owner", email="owner-a@example.test"),
                Account(id="editor-a", name="Editor", email="editor-a@example.test"),
                Account(id="member-a", name="Member", email="member-a@example.test"),
                Account(id="owner-b", name="Owner B", email="owner-b@example.test"),
                TenantMember(account_id="owner-a", tenant_id="tenant-a", role="owner"),
                TenantMember(account_id="editor-a", tenant_id="tenant-a", role="editor"),
                TenantMember(account_id="member-a", tenant_id="tenant-a", role="member"),
                TenantMember(account_id="owner-b", tenant_id="tenant-b", role="owner"),
                Document(
                    id="doc-a",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    name="Guide A",
                    chunk_count=0,
                    mutation_generation=0,
                    lifecycle_state="active",
                    retrieval_enabled=True,
                ),
                Document(
                    id="doc-b",
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    name="Guide B",
                    chunk_count=0,
                    mutation_generation=3,
                    lifecycle_state="active",
                    retrieval_enabled=True,
                ),
                Document(
                    id="doc-secret",
                    tenant_id="tenant-b",
                    dataset_id="dataset-b",
                    name="Secret",
                    chunk_count=0,
                    mutation_generation=0,
                    lifecycle_state="active",
                    retrieval_enabled=True,
                ),
            ]
        )
        session.commit()
    return engine


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch):
    engine = _engine()
    settings = _settings()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.include_router(documents.router)
    return TestClient(app, client=("10.0.0.2", 50000)), engine, settings


def _headers(
    settings: SimpleNamespace,
    actor: str,
    *,
    tenant: str = "tenant-a",
    idempotency_key: str | None = None,
    request_id: str = "req-delete-api",
) -> dict[str, str]:
    token = issue_knowledge_actor_token(actor, tenant, 300, int(time.time()), settings=settings)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant,
        "X-Request-ID": request_id,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def test_single_delete_requires_delete_permission_and_returns_202_without_external_pipeline(
    api, monkeypatch
):
    client, engine, settings = api
    monkeypatch.setattr(
        documents,
        "_get_ingest_pipeline",
        lambda: pytest.fail("durable delete API must not build or call the ingest pipeline"),
    )
    url = "/api/knowledge-bases/dataset-a/documents/doc-a/delete"
    payload = {"expected_generation": 0, "reason": "obsolete guide"}

    forbidden = client.post(
        url,
        headers=_headers(settings, "member-a", idempotency_key="delete-member"),
        json=payload,
    )
    assert forbidden.status_code == 403

    response = client.post(
        url,
        headers=_headers(
            settings,
            "editor-a",
            idempotency_key="delete-doc-a",
            request_id="req-delete-doc-a",
        ),
        json=payload,
    )
    assert response.status_code == 202
    body = response.json()
    assert body["document_id"] == "doc-a"
    assert body["operation_id"].startswith("doc-delete-")
    assert body["status"] == "queued"
    assert body["delete_generation"] == 1
    assert body["retrieval_enabled"] is False
    assert body["projection_pending"] is True

    with Session(engine) as session:
        document = session.get(Document, "doc-a")
        assert document is not None
        assert document.mutation_generation == 1
        assert document.lifecycle_state == "delete_requested"
        assert document.retrieval_enabled is False
        event = session.scalar(
            select(KnowledgeAuditEvent).where(
                KnowledgeAuditEvent.action == "document.delete_requested"
            )
        )
        assert event is not None
        assert event.actor_id == "editor-a"
        assert event.request_id == "req-delete-doc-a"


def test_single_delete_requires_idempotency_and_strict_generation(api):
    client, _, settings = api
    url = "/api/knowledge-bases/dataset-a/documents/doc-a/delete"
    headers = _headers(settings, "editor-a")

    missing_key = client.post(url, headers=headers, json={"expected_generation": 0})
    assert missing_key.status_code == 422

    for invalid in ("0", 0.0, False):
        response = client.post(
            url,
            headers=_headers(settings, "editor-a", idempotency_key=f"strict-{invalid!r}"),
            json={"expected_generation": invalid},
        )
        assert response.status_code == 422, invalid


def test_idempotent_replay_and_generation_conflict_use_structured_409(api):
    client, _, settings = api
    url = "/api/knowledge-bases/dataset-a/documents/doc-a/delete"
    headers = _headers(settings, "editor-a", idempotency_key="delete-replay")
    payload = {"expected_generation": 0, "reason": "cleanup"}

    first = client.post(url, headers=headers, json=payload)
    replay = client.post(url, headers=headers, json=payload)
    assert first.status_code == replay.status_code == 202
    assert first.json()["operation_id"] == replay.json()["operation_id"]

    conflict = client.post(url, headers=headers, json={**payload, "reason": "different"})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "document_delete_idempotency_conflict"

    stale = client.post(
        "/api/knowledge-bases/dataset-a/documents/doc-b/delete",
        headers=_headers(settings, "editor-a", idempotency_key="delete-stale"),
        json={"expected_generation": 2, "reason": "stale"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "document_generation_conflict"


@pytest.mark.parametrize("operation_status", ["queued", "projecting", "finalizing", "completed", "failed"])
def test_network_lost_response_replay_returns_same_operation_in_any_current_status(
    api, operation_status: str
) -> None:
    client, engine, settings = api
    url = "/api/knowledge-bases/dataset-a/documents/doc-a/delete"
    headers = _headers(settings, "editor-a", idempotency_key="network-lost-replay")
    payload = {"expected_generation": 0, "reason": "network response was lost"}

    first = client.post(url, headers=headers, json=payload)
    assert first.status_code == 202
    operation_id = first.json()["operation_id"]

    with Session(engine) as session:
        operation = session.get(DocumentDeleteOperation, operation_id)
        assert operation is not None
        operation.status = operation_status
        session.commit()

    replay = client.post(url, headers=headers, json=payload)
    assert replay.status_code == 202
    assert replay.json()["operation_id"] == operation_id
    assert replay.json()["status"] == operation_status


def test_catalog_document_projections_include_durable_delete_authority(api) -> None:
    client, _, settings = api
    before = catalog.get_document("doc-a")
    assert before is not None
    assert before["mutation_generation"] == 0
    assert before["lifecycle_state"] == "active"
    assert before["retrieval_enabled"] is True
    assert before["active_delete_operation_id"] is None

    accepted = client.post(
        "/api/knowledge-bases/dataset-a/documents/doc-a/delete",
        headers=_headers(settings, "editor-a", idempotency_key="catalog-projection"),
        json={"expected_generation": 0, "reason": "projection"},
    )
    assert accepted.status_code == 202

    detail = catalog.get_document("doc-a")
    listed = next(item for item in catalog.list_documents("dataset-a") if item["id"] == "doc-a")
    for projection in (detail, listed):
        assert projection is not None
        assert projection["mutation_generation"] == 1
        assert projection["lifecycle_state"] == "delete_requested"
        assert projection["retrieval_enabled"] is False
        assert projection["active_delete_operation_id"] == accepted.json()["operation_id"]


def test_batch_delete_returns_persisted_mixed_itemized_results(api):
    client, engine, settings = api
    response = client.post(
        "/api/knowledge-bases/dataset-a/documents/batch-delete",
        headers=_headers(settings, "editor-a", idempotency_key="batch-mixed"),
        json={
            "reason": "project cleanup",
            "items": [
                {"document_id": "doc-a", "expected_generation": 0},
                {"document_id": "missing", "expected_generation": 0},
                {"document_id": "doc-b", "expected_generation": 2},
            ],
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    assert body["requested"] == 3
    assert body["accepted"] == 1
    assert body["rejected"] == 2
    assert [item["document_id"] for item in body["items"]] == ["doc-a", "missing", "doc-b"]
    assert [item["status"] for item in body["items"]] == ["queued", "rejected", "rejected"]
    assert body["items"][1]["code"] == "not_found"
    assert body["items"][2]["code"] == "document_generation_conflict"

    with Session(engine) as session:
        batch = session.get(DocumentDeleteBatch, body["batch_operation_id"])
        assert batch is not None
        assert batch.accepted_count == 1
        assert batch.rejected_count == 2


def test_read_permission_can_poll_operation_and_batch_status(api):
    client, _, settings = api
    created = client.post(
        "/api/knowledge-bases/dataset-a/documents/doc-a/delete",
        headers=_headers(settings, "editor-a", idempotency_key="poll-delete"),
        json={"expected_generation": 0, "reason": "poll me"},
    ).json()

    operation = client.get(
        f"/api/knowledge-bases/dataset-a/document-delete-operations/{created['operation_id']}",
        headers=_headers(settings, "member-a"),
    )
    assert operation.status_code == 200
    assert operation.json()["operation_id"] == created["operation_id"]
    assert operation.json()["status"] == "queued"

    batch = client.get(
        f"/api/knowledge-bases/dataset-a/document-delete-batches/{created['batch_operation_id']}",
        headers=_headers(settings, "member-a"),
    )
    assert batch.status_code == 200
    assert batch.json()["batch_operation_id"] == created["batch_operation_id"]
    assert batch.json()["items"][0]["operation_id"] == created["operation_id"]

    cross_tenant = client.get(
        f"/api/knowledge-bases/dataset-b/document-delete-operations/{created['operation_id']}",
        headers=_headers(settings, "owner-b", tenant="tenant-b"),
    )
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["detail"]["code"] == "knowledge_resource_not_found"


def test_legacy_delete_routes_are_gone_and_never_call_synchronous_deletion(api, monkeypatch):
    client, _, _ = api
    monkeypatch.setattr(
        documents,
        "_delete_registered_document",
        lambda _doc_id: pytest.fail("legacy synchronous deletion must not run"),
    )

    single = client.delete("/api/documents/doc-a")
    batch = client.post("/api/documents/batch-delete")
    assert single.status_code == 410
    assert batch.status_code == 410
    assert single.json()["detail"]["code"] == "document_delete_endpoint_gone"
    assert batch.json()["detail"]["code"] == "document_delete_endpoint_gone"


@pytest.fixture()
def bridge_api(monkeypatch: pytest.MonkeyPatch):
    engine = _engine()
    settings = _settings()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    from server.app import app as bridge_app

    # 裸赋值会把内存 sqlite 引擎永久留在进程级 app.state 上，污染后续用例
    # （test_enterprise_admin_api 的引擎同一性断言即因此变红）；monkeypatch 会在 teardown 还原。
    monkeypatch.setattr(bridge_app.state, "knowledge_auth_engine", engine)
    monkeypatch.setattr(bridge_app.state, "knowledge_auth_settings", settings, raising=False)
    return TestClient(
        bridge_app,
        client=("10.0.0.2", 50000),
        raise_server_exceptions=False,
    ), settings


def test_real_server_app_exposes_canonical_delete_and_preserves_errors(bridge_api):
    client, settings = bridge_api
    created = client.post(
        "/api/knowledge-bases/dataset-a/documents/doc-a/delete",
        headers=_headers(settings, "editor-a", idempotency_key="bridge-delete"),
        json={"expected_generation": 0, "reason": "bridge"},
    )
    assert created.status_code == 202

    missing = client.get(
        "/api/knowledge-bases/dataset-a/document-delete-operations/missing",
        headers=_headers(settings, "member-a"),
    )
    assert missing.status_code == 404
    assert missing.json() == {
        "error": {"code": "knowledge_resource_not_found", "message": "资源不存在"}
    }

    invalid = client.post(
        "/api/knowledge-bases/dataset-a/documents/doc-b/delete",
        headers=_headers(settings, "editor-a", idempotency_key="bridge-invalid"),
        json={"expected_generation": "3"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
