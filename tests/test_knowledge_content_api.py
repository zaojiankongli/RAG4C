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
    KnowledgeAuditEvent,
    Tenant,
    TenantMember,
)
from server.knowledge_auth import issue_knowledge_actor_token
from server.knowledge_content_api import router


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("content-api-actor-secret"),
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
                Document(id="doc-a", tenant_id="tenant-a", dataset_id="dataset-a", name="Guide"),
                Document(id="doc-b", tenant_id="tenant-b", dataset_id="dataset-b", name="Secret"),
            ]
        )
        session.commit()
    return engine


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, object, SimpleNamespace]:
    engine = _engine()
    settings = _settings()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.include_router(router)
    return TestClient(app, client=("10.0.0.2", 50000)), engine, settings


def _headers(
    settings: SimpleNamespace, actor: str, tenant: str = "tenant-a", *, request_id: str = "req-api"
) -> dict[str, str]:
    token = issue_knowledge_actor_token(actor, tenant, 300, int(time.time()), settings=settings)
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant,
        "X-Request-ID": request_id,
    }


def test_versions_use_read_write_permissions_and_hide_cross_tenant_resources(api) -> None:
    client, _, settings = api
    payload = {
        "expected_current_revision": 0,
        "source_identity": "upload:guide.pdf",
        "source_hash": "a" * 64,
        "parser_policy_snapshot": {"engine": "mineru"},
        "parser_metadata": {"pages": 4},
        "source_content_ref": "vault://documents/doc-a/v1",
        "change_reason": "initial import",
    }

    assert (
        client.post(
            "/api/knowledge-bases/dataset-a/documents/doc-a/versions",
            headers=_headers(settings, "member-a"),
            json=payload,
        ).status_code
        == 403
    )

    created = client.post(
        "/api/knowledge-bases/dataset-a/documents/doc-a/versions",
        headers=_headers(settings, "editor-a", request_id="req-version-create"),
        json=payload,
    )
    assert created.status_code == 201
    assert created.json()["revision"] == 1
    assert created.json()["source_content_ref"] == "vault://documents/doc-a/v1"
    assert "source_content" not in created.json()

    conflict = client.post(
        "/api/knowledge-bases/dataset-a/documents/doc-a/versions",
        headers=_headers(settings, "editor-a"),
        json=payload,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "knowledge_content_conflict"

    listed = client.get(
        "/api/knowledge-bases/dataset-a/documents/doc-a/versions",
        headers=_headers(settings, "member-a"),
    )
    assert listed.status_code == 200
    assert [item["revision"] for item in listed.json()["items"]] == [1]

    hidden = client.get(
        "/api/knowledge-bases/dataset-a/documents/doc-b/versions",
        headers=_headers(settings, "owner-a"),
    )
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "knowledge_resource_not_found"

    cross_tenant = client.get(
        "/api/knowledge-bases/dataset-a/documents/doc-a/versions",
        headers=_headers(settings, "owner-b", "tenant-b"),
    )
    assert cross_tenant.status_code == 403


def test_qa_revision_review_gate_alternatives_and_manage_restore(api) -> None:
    client, _, settings = api
    created = client.post(
        "/api/knowledge-bases/dataset-a/qa",
        headers=_headers(settings, "editor-a", request_id="req-qa-create"),
        json={
            "question": "How is access approved?",
            "answer": "By the knowledge owner.",
            "origin": "manual",
            "source_document_id": "doc-a",
            "metadata": {"topic": "security"},
        },
    )
    assert created.status_code == 201
    qa = created.json()
    assert (qa["revision"], qa["review_status"], qa["retrieval_enabled"]) == (1, "pending", False)

    member_list = client.get(
        "/api/knowledge-bases/dataset-a/qa?review_status=pending",
        headers=_headers(settings, "member-a"),
    )
    assert member_list.status_code == 200
    assert [item["id"] for item in member_list.json()["items"]] == [qa["id"]]

    reviewed = client.post(
        f"/api/knowledge-bases/dataset-a/qa/{qa['id']}/review",
        headers=_headers(settings, "editor-a", request_id="req-review"),
        json={"expected_revision": 1, "decision": "approved"},
    )
    assert reviewed.status_code == 200
    assert (
        reviewed.json()["revision"],
        reviewed.json()["review_status"],
        reviewed.json()["retrieval_enabled"],
    ) == (2, "approved", True)

    conflict = client.patch(
        f"/api/knowledge-bases/dataset-a/qa/{qa['id']}",
        headers=_headers(settings, "editor-a"),
        json={"expected_revision": 1, "answer": "stale"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "knowledge_content_conflict"

    updated = client.patch(
        f"/api/knowledge-bases/dataset-a/qa/{qa['id']}",
        headers=_headers(settings, "editor-a"),
        json={"expected_revision": 2, "answer": "Only an owner can approve access."},
    )
    assert updated.status_code == 200
    assert (
        updated.json()["revision"],
        updated.json()["review_status"],
        updated.json()["retrieval_enabled"],
    ) == (3, "pending", False)

    alternative = client.post(
        f"/api/knowledge-bases/dataset-a/qa/{qa['id']}/alternatives",
        headers=_headers(settings, "editor-a"),
        json={"expected_revision": 3, "question": "Who approves access?"},
    )
    assert alternative.status_code == 201
    assert alternative.json()["qa_revision"] == 4

    for invalid_revision in ("2.0", "+4", "04", "true", "", " 4 "):
        rejected = client.delete(
            f"/api/knowledge-bases/dataset-a/qa/{qa['id']}/alternatives/{alternative.json()['id']}",
            headers=_headers(settings, "editor-a"),
            params={"expected_revision": invalid_revision},
        )
        assert rejected.status_code == 422

    deleted = client.delete(
        f"/api/knowledge-bases/dataset-a/qa/{qa['id']}/alternatives/{alternative.json()['id']}?expected_revision=4",
        headers=_headers(settings, "editor-a"),
    )
    assert deleted.status_code == 204

    expired = client.post(
        f"/api/knowledge-bases/dataset-a/qa/{qa['id']}/expire",
        headers=_headers(settings, "owner-a"),
        json={"expected_revision": 5},
    )
    assert expired.status_code == 200
    assert expired.json()["lifecycle_state"] == "expired"

    editor_restore = client.post(
        f"/api/knowledge-bases/dataset-a/qa/{qa['id']}/restore",
        headers=_headers(settings, "editor-a"),
        json={"expected_revision": 6},
    )
    assert editor_restore.status_code == 403
    owner_restore = client.post(
        f"/api/knowledge-bases/dataset-a/qa/{qa['id']}/restore",
        headers=_headers(settings, "owner-a"),
        json={"expected_revision": 6},
    )
    assert owner_restore.status_code == 200
    assert owner_restore.json()["lifecycle_state"] == "active"


def test_content_mutations_use_authenticated_actor_for_atomic_audit(api) -> None:
    client, engine, settings = api
    response = client.post(
        "/api/knowledge-bases/dataset-a/qa",
        headers=_headers(settings, "editor-a", request_id="req-authenticated-actor"),
        json={"question": "Audited?", "answer": "Yes.", "origin": "automatic"},
    )
    assert response.status_code == 201
    with Session(engine) as session:
        event = session.scalar(
            select(KnowledgeAuditEvent).where(
                KnowledgeAuditEvent.request_id == "req-authenticated-actor"
            )
        )
        assert event is not None
        assert event.actor_id == "editor-a"
        assert event.action == "qa.create"


def test_content_models_reject_extra_fields_and_invalid_enums(api) -> None:
    client, _, settings = api
    invalid = client.post(
        "/api/knowledge-bases/dataset-a/qa",
        headers=_headers(settings, "editor-a"),
        json={"question": "Q", "answer": "A", "origin": "trusted", "role": "owner"},
    )
    assert invalid.status_code == 422


def test_version_omitted_lifecycle_fields_preserve_document_values(api) -> None:
    from datetime import datetime

    client, engine, settings = api
    effective = datetime(2026, 8, 1, 8, 0, 0)
    expires = datetime(2026, 9, 1, 8, 0, 0)
    purge = datetime(2026, 10, 1, 8, 0, 0)
    with Session(engine) as session:
        document = session.get(Document, "doc-a")
        assert document is not None
        document.effective_from = effective
        document.expires_at = expires
        document.purge_after = purge
        document.retrieval_enabled = True
        session.commit()

    response = client.post(
        "/api/knowledge-bases/dataset-a/documents/doc-a/versions",
        headers=_headers(settings, "editor-a"),
        json={
            "expected_current_revision": 0,
            "source_identity": "upload:guide.pdf",
            "source_hash": "b" * 64,
        },
    )
    assert response.status_code == 201
    with Session(engine) as session:
        document = session.get(Document, "doc-a")
        assert document is not None
        assert document.effective_from == effective
        assert document.expires_at == expires
        assert document.purge_after == purge
        assert document.retrieval_enabled is True


def test_qa_patch_rejects_null_for_required_material_text(api) -> None:
    client, _, settings = api
    created = client.post(
        "/api/knowledge-bases/dataset-a/qa",
        headers=_headers(settings, "editor-a"),
        json={"question": "Question", "answer": "Answer"},
    ).json()

    for field in ("question", "answer", "source_uri"):
        response = client.patch(
            f"/api/knowledge-bases/dataset-a/qa/{created['id']}",
            headers=_headers(settings, "editor-a"),
            json={"expected_revision": 1, field: None},
        )
        assert response.status_code == 422


def test_revision_fields_are_strict_in_json_bodies(api) -> None:
    client, _, settings = api
    created = client.post(
        "/api/knowledge-bases/dataset-a/qa",
        headers=_headers(settings, "editor-a"),
        json={"question": "Strict revision?", "answer": "Yes."},
    ).json()
    response = client.patch(
        f"/api/knowledge-bases/dataset-a/qa/{created['id']}",
        headers=_headers(settings, "editor-a"),
        json={"expected_revision": "1", "answer": "Must reject coercion."},
    )
    assert response.status_code == 422


def test_version_retrieval_enabled_rejects_explicit_null(api) -> None:
    client, _, settings = api
    response = client.post(
        "/api/knowledge-bases/dataset-a/documents/doc-a/versions",
        headers=_headers(settings, "editor-a"),
        json={
            "expected_current_revision": 0,
            "source_identity": "upload:guide.pdf",
            "source_hash": "c" * 64,
            "retrieval_enabled": None,
        },
    )
    assert response.status_code == 422


@pytest.fixture()
def bridge_api(monkeypatch: pytest.MonkeyPatch):
    engine = _engine()
    settings = _settings()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    from server.app import app as bridge_app

    bridge_app.state.knowledge_auth_engine = engine
    bridge_app.state.knowledge_auth_settings = settings
    return (
        TestClient(
            bridge_app,
            client=("10.0.0.2", 50000),
            raise_server_exceptions=False,
        ),
        settings,
    )


def test_bridge_preserves_structured_knowledge_errors(bridge_api) -> None:
    client, settings = bridge_api
    missing = client.get(
        "/api/knowledge-bases/dataset-a/documents/missing/versions",
        headers=_headers(settings, "member-a"),
    )
    assert missing.status_code == 404
    assert missing.json() == {
        "error": {"code": "knowledge_resource_not_found", "message": "资源不存在"}
    }

    payload = {
        "expected_current_revision": 0,
        "source_identity": "upload:guide.pdf",
        "source_hash": "d" * 64,
    }
    assert (
        client.post(
            "/api/knowledge-bases/dataset-a/documents/doc-a/versions",
            headers=_headers(settings, "editor-a"),
            json=payload,
        ).status_code
        == 201
    )
    conflict = client.post(
        "/api/knowledge-bases/dataset-a/documents/doc-a/versions",
        headers=_headers(settings, "editor-a"),
        json=payload,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "knowledge_content_conflict"

    forbidden = client.post(
        "/api/knowledge-bases/dataset-a/qa",
        headers=_headers(settings, "member-a"),
        json={"question": "Forbidden", "answer": "Forbidden"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "knowledge_permission_forbidden"

    invalid = client.get(
        "/api/knowledge-bases/dataset-a/audit-events",
        headers=_headers(settings, "owner-a"),
        params={
            "occurred_from": "2026-08-25T09:00:00Z",
            "occurred_to": "2026-08-25T08:00:00Z",
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "knowledge_audit_invalid"


def test_content_audit_snapshot_is_sanitized_before_database_insert(api) -> None:
    client, engine, settings = api
    response = client.post(
        "/api/knowledge-bases/dataset-a/qa",
        headers=_headers(settings, "editor-a", request_id="req-content-secret"),
        json={
            "question": "Where are credentials stored?",
            "answer": "In the secret manager.",
            "metadata": {
                "accessKey": "must-not-persist",
                "dsn": "postgresql://user:pass@db.example/rag4c",
                "nested": [{"session_id": "must-not-persist-either"}],
            },
        },
    )
    assert response.status_code == 201
    with Session(engine) as session:
        event = session.scalar(
            select(KnowledgeAuditEvent).where(
                KnowledgeAuditEvent.request_id == "req-content-secret"
            )
        )
        assert event is not None
        assert event.after_snapshot is not None
        assert event.after_snapshot["metadata"] == {
            "accessKey": "[REDACTED]",
            "dsn": "postgresql://db.example/rag4c",
            "nested": [{"session_id": "[REDACTED]"}],
        }
