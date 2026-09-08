from __future__ import annotations

from datetime import datetime, timedelta
import json
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core import catalog
from core.knowledge_governance import AuditContext, KnowledgeGovernanceRepository
from models.orm import Account, Base, Dataset, KnowledgeAuditEvent, Tenant, TenantMember
from server.knowledge_audit_api import router
from server.knowledge_auth import issue_knowledge_actor_token


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("audit-api-actor-secret"), actor_max_ttl_s=900
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
    from sqlalchemy.orm import Session

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
            ]
        )
        session.commit()
    return engine


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch):
    engine, settings = _engine(), _settings()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    repo = KnowledgeGovernanceRepository(engine)
    base = datetime(2026, 8, 25, 8, 0, 0)
    repo.append_audit_event(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        audit=AuditContext("editor-a", "req-1", "127.0.0.1"),
        action="qa.create",
        resource_type="qa_knowledge",
        resource_id="qa-1",
        after_snapshot={
            "metadata": {
                "token": "must-not-leak",
                "clientSecret": "also-must-not-leak",
                "topic": "safe",
            }
        },
        occurred_at=base,
    )
    repo.append_audit_event(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        audit=AuditContext("owner-a", "req-2", "127.0.0.1"),
        action="tag.merge",
        resource_type="knowledge_tag",
        resource_id="tag-2",
        before_snapshot={"api_key": "secret-value"},
        after_snapshot={"name": "Approved"},
        occurred_at=base + timedelta(minutes=1),
    )
    repo.append_audit_event(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        audit=AuditContext("editor-a", "req-3", "127.0.0.1"),
        action="qa.update",
        resource_type="qa_knowledge",
        resource_id="qa-1",
        before_snapshot={"answer": "old"},
        after_snapshot={"answer": "new"},
        occurred_at=base + timedelta(minutes=2),
    )
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.include_router(router)
    return TestClient(app, client=("10.0.0.2", 50000)), settings


def _headers(settings: SimpleNamespace, actor: str, tenant: str = "tenant-a") -> dict[str, str]:
    token = issue_knowledge_actor_token(actor, tenant, 300, int(time.time()), settings=settings)
    return {"Authorization": f"Bearer {token}", "X-RAG4C-Tenant": tenant}


def test_audit_requires_audit_permission_and_hides_cross_tenant_dataset(api) -> None:
    client, settings = api
    assert (
        client.get(
            "/api/knowledge-bases/dataset-a/audit-events", headers=_headers(settings, "member-a")
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/knowledge-bases/dataset-a/audit-events", headers=_headers(settings, "editor-a")
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/knowledge-bases/dataset-a/audit-events", headers=_headers(settings, "owner-a")
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/knowledge-bases/dataset-a/audit-events",
            headers=_headers(settings, "owner-b", "tenant-b"),
        ).status_code
        == 403
    )


def test_audit_filters_time_resource_request_and_paginates_deterministically(api) -> None:
    client, settings = api
    headers = _headers(settings, "owner-a")
    filtered = client.get(
        "/api/knowledge-bases/dataset-a/audit-events",
        headers=headers,
        params={
            "actor_id": "editor-a",
            "action": "qa.update",
            "resource_type": "qa_knowledge",
            "resource_id": "qa-1",
            "request_id": "req-3",
            "occurred_from": "2026-08-25T08:01:30Z",
            "occurred_to": "2026-08-25T08:02:30Z",
            "limit": 10,
        },
    )
    assert filtered.status_code == 200
    assert [item["request_id"] for item in filtered.json()["items"]] == ["req-3"]

    first = client.get(
        "/api/knowledge-bases/dataset-a/audit-events?limit=2",
        headers=headers,
    )
    assert first.status_code == 200
    payload = first.json()
    assert [item["request_id"] for item in payload["items"]] == ["req-3", "req-2"]
    assert payload["next_before_sequence"] is not None
    second = client.get(
        "/api/knowledge-bases/dataset-a/audit-events",
        headers=headers,
        params={"limit": 2, "before_sequence": payload["next_before_sequence"]},
    )
    assert [item["request_id"] for item in second.json()["items"]] == ["req-1"]


def test_audit_snapshots_preserve_structure_but_redact_secret_values(api) -> None:
    client, settings = api
    response = client.get(
        "/api/knowledge-bases/dataset-a/audit-events?limit=10",
        headers=_headers(settings, "owner-a"),
    )
    assert response.status_code == 200
    by_request = {item["request_id"]: item for item in response.json()["items"]}
    assert by_request["req-1"]["after_snapshot"] == {
        "metadata": {
            "token": "[REDACTED]",
            "clientSecret": "[REDACTED]",
            "topic": "safe",
        }
    }
    assert by_request["req-2"]["before_snapshot"] == {"api_key": "[REDACTED]"}


def test_openapi_exposes_only_the_approved_knowledge_routes(api) -> None:
    client, _ = api
    from server.knowledge_content_api import router as content_router
    from server.knowledge_governance_api import router as governance_router

    app = FastAPI()
    app.include_router(content_router)
    app.include_router(governance_router)
    app.include_router(router)
    paths = app.openapi()["paths"]
    approved = {
        "/api/knowledge-bases/{dataset_id}/documents/{document_id}/versions",
        "/api/knowledge-bases/{dataset_id}/qa",
        "/api/knowledge-bases/{dataset_id}/qa/{qa_id}",
        "/api/knowledge-bases/{dataset_id}/qa/{qa_id}/review",
        "/api/knowledge-bases/{dataset_id}/qa/{qa_id}/expire",
        "/api/knowledge-bases/{dataset_id}/qa/{qa_id}/restore",
        "/api/knowledge-bases/{dataset_id}/qa/{qa_id}/alternatives",
        "/api/knowledge-bases/{dataset_id}/qa/{qa_id}/alternatives/{alternative_id}",
        "/api/knowledge-bases/{dataset_id}/folders",
        "/api/knowledge-bases/{dataset_id}/folders/{folder_id}",
        "/api/knowledge-bases/{dataset_id}/folders/{folder_id}/move",
        "/api/knowledge-bases/{dataset_id}/folders/{folder_id}/archive",
        "/api/knowledge-bases/{dataset_id}/tags",
        "/api/knowledge-bases/{dataset_id}/tags/{tag_id}",
        "/api/knowledge-bases/{dataset_id}/tags/{target_tag_id}/merge",
        "/api/knowledge-bases/{dataset_id}/documents/{document_id}/tags/{tag_id}",
        "/api/knowledge-bases/{dataset_id}/audit-events",
    }
    assert set(paths) == approved
    allowed_methods = {
        "/api/knowledge-bases/{dataset_id}/documents/{document_id}/versions": {"get", "post"},
        "/api/knowledge-bases/{dataset_id}/qa": {"get", "post"},
        "/api/knowledge-bases/{dataset_id}/qa/{qa_id}": {"patch"},
        "/api/knowledge-bases/{dataset_id}/qa/{qa_id}/review": {"post"},
        "/api/knowledge-bases/{dataset_id}/qa/{qa_id}/expire": {"post"},
        "/api/knowledge-bases/{dataset_id}/qa/{qa_id}/restore": {"post"},
        "/api/knowledge-bases/{dataset_id}/qa/{qa_id}/alternatives": {"post"},
        "/api/knowledge-bases/{dataset_id}/qa/{qa_id}/alternatives/{alternative_id}": {"delete"},
        "/api/knowledge-bases/{dataset_id}/folders": {"get", "post"},
        "/api/knowledge-bases/{dataset_id}/folders/{folder_id}": {"patch"},
        "/api/knowledge-bases/{dataset_id}/folders/{folder_id}/move": {"post"},
        "/api/knowledge-bases/{dataset_id}/folders/{folder_id}/archive": {"post"},
        "/api/knowledge-bases/{dataset_id}/tags": {"get", "post"},
        "/api/knowledge-bases/{dataset_id}/tags/{tag_id}": {"patch"},
        "/api/knowledge-bases/{dataset_id}/tags/{target_tag_id}/merge": {"post"},
        "/api/knowledge-bases/{dataset_id}/documents/{document_id}/tags/{tag_id}": {
            "post",
            "delete",
        },
        "/api/knowledge-bases/{dataset_id}/audit-events": {"get"},
    }
    for path, methods in allowed_methods.items():
        assert set(paths[path]) == methods
    schema_text = str(app.openapi()).casefold()
    assert "actor_signing_secret" not in schema_text
    assert "ops_bearer_token" not in schema_text


def test_bridge_app_includes_all_knowledge_api_routers() -> None:
    from server.app import app as bridge_app

    paths = bridge_app.openapi()["paths"]
    required = {
        "/api/knowledge-bases/{dataset_id}/documents/{document_id}/versions",
        "/api/knowledge-bases/{dataset_id}/qa",
        "/api/knowledge-bases/{dataset_id}/folders",
        "/api/knowledge-bases/{dataset_id}/tags",
        "/api/knowledge-bases/{dataset_id}/audit-events",
    }
    assert required <= set(paths)


def test_audit_api_uses_sequence_keyset_when_timestamps_are_non_monotonic(api) -> None:
    client, settings = api
    repository = KnowledgeGovernanceRepository(client.app.state.knowledge_auth_engine)
    first = repository.append_audit_event(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        audit=AuditContext("editor-a", "seq-1"),
        action="sequence.api",
        resource_type="dataset",
        resource_id="dataset-a",
        occurred_at=datetime(2026, 8, 25, 10, 0, 0),
    )
    second = repository.append_audit_event(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        audit=AuditContext("editor-a", "seq-2"),
        action="sequence.api",
        resource_type="dataset",
        resource_id="dataset-a",
        occurred_at=datetime(2026, 8, 25, 8, 0, 0),
    )
    third = repository.append_audit_event(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        audit=AuditContext("editor-a", "seq-3"),
        action="sequence.api",
        resource_type="dataset",
        resource_id="dataset-a",
        occurred_at=datetime(2026, 8, 25, 9, 0, 0),
    )
    headers = _headers(settings, "owner-a")
    page_one = client.get(
        "/api/knowledge-bases/dataset-a/audit-events",
        headers=headers,
        params={"action": "sequence.api", "limit": 2},
    ).json()
    page_two = client.get(
        "/api/knowledge-bases/dataset-a/audit-events",
        headers=headers,
        params={
            "action": "sequence.api",
            "limit": 2,
            "before_sequence": page_one["next_before_sequence"],
        },
    ).json()
    assert [item["id"] for item in page_one["items"]] == [third.id, second.id]
    assert [item["id"] for item in page_two["items"]] == [first.id]


def test_audit_api_sanitizes_legacy_unsanitized_rows_on_output(api) -> None:
    client, settings = api
    engine = client.app.state.knowledge_auth_engine
    with Session(engine) as session:
        event = KnowledgeAuditEvent(
            id="audit-legacy-unsafe",
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            actor_id="editor-a",
            action="legacy.unsafe",
            resource_type="dataset",
            resource_id="dataset-a",
            before_snapshot={},
            request_id="legacy-unsafe",
            request_ip="127.0.0.1",
            occurred_at=datetime(2026, 8, 25, 12, 0, 0),
        )
        session.add(event)
        session.commit()
        session.execute(
            text(
                "UPDATE knowledge_audit_events SET before_snapshot = :snapshot WHERE id = :event_id"
            ),
            {
                "snapshot": json.dumps(
                    {
                        "sessionId": "leak",
                        "dsn": "mysql://user:pass@db.example/rag4c?charset=utf8mb4&pwd=db-pass#password=fragment-pass",
                        "oauth": "https://example.test/callback?state=safe&refresh_token=refresh&api_key=api#tab=profile&secret=fragment",
                        "plain_fragment": "https://example.test/path#dashboard",
                        "aws": "https://bucket.s3.amazonaws.com/object?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=credential&X-Amz-Date=20260825T120000Z&X-Amz-Expires=900&X-Amz-SignedHeaders=host&X-Amz-Signature=signature&X-Amz-Security-Token=session",
                        "items": [{"accessKey": "leak-too"}],
                    }
                ),
                "event_id": event.id,
            },
        )
        session.commit()
    response = client.get(
        "/api/knowledge-bases/dataset-a/audit-events",
        headers=_headers(settings, "owner-a"),
        params={"request_id": "legacy-unsafe"},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["before_snapshot"] == {
        "sessionId": "[REDACTED]",
        "dsn": "mysql://db.example/rag4c?charset=utf8mb4&pwd=%5BREDACTED%5D#password=%5BREDACTED%5D",
        "oauth": "https://example.test/callback?state=safe&refresh_token=%5BREDACTED%5D&api_key=%5BREDACTED%5D#tab=profile&secret=%5BREDACTED%5D",
        "plain_fragment": "https://example.test/path",
        "aws": "https://bucket.s3.amazonaws.com/object?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=%5BREDACTED%5D&X-Amz-Date=20260825T120000Z&X-Amz-Expires=900&X-Amz-SignedHeaders=host&X-Amz-Signature=%5BREDACTED%5D&X-Amz-Security-Token=%5BREDACTED%5D",
        "items": [{"accessKey": "[REDACTED]"}],
    }
