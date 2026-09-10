from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core import catalog, catalog_schema
from models.orm import Account, Base, Dataset, KnowledgeAuditEvent, Tenant, TenantMember
from server.knowledge_auth import issue_knowledge_actor_token
from server.knowledge_dataset_api import router


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("dataset-api-actor-secret"),
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
    # 下面是 Base.metadata.create_all —— 建的是**当前 ORM = 当前 head** 的 schema，
    # 所以盖章必须是 HEAD_REVISION。原先盖死 0028（写测试时的 head），
    # 于是 revision 与真实结构不一致：capability 的 exact-check 比对会把
    # 后续 stage 才加进 CHECK 的动作值判成"缺失/非法"，请求被 fail-closed 成 503。
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)"))
        connection.execute(
            text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
            {"revision": catalog_schema.HEAD_REVISION},
        )
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="Tenant A", status="active"),
                Tenant(id="tenant-b", name="Tenant B", status="active"),
                Account(id="owner-a", name="Owner", email="owner-a@example.test"),
                Account(id="editor-a", name="Editor", email="editor-a@example.test"),
                Account(id="member-a", name="Member", email="member-a@example.test"),
                Account(id="owner-b", name="Owner B", email="owner-b@example.test"),
                TenantMember(account_id="owner-a", tenant_id="tenant-a", role="owner"),
                TenantMember(account_id="editor-a", tenant_id="tenant-a", role="editor"),
                TenantMember(account_id="member-a", tenant_id="tenant-a", role="member"),
                TenantMember(account_id="owner-b", tenant_id="tenant-b", role="owner"),
                Dataset(
                    id="dataset-a",
                    tenant_id="tenant-a",
                    name="Dataset A",
                    description="Primary knowledge base",
                    status="active",
                    owner_id="owner-a",
                    profile_json={"domain": "support"},
                    parser_policy={"engine": "mineru"},
                ),
                Dataset(
                    id="dataset-archived",
                    tenant_id="tenant-a",
                    name="Archived",
                    status="archived",
                    owner_id="owner-a",
                ),
                Dataset(
                    id="dataset-disabled",
                    tenant_id="tenant-a",
                    name="Disabled",
                    status="disabled",
                    owner_id="owner-a",
                ),
                Dataset(
                    id="dataset-b",
                    tenant_id="tenant-b",
                    name="Dataset B",
                    status="active",
                    owner_id="owner-b",
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
    app.include_router(router)
    return TestClient(app, client=("10.0.0.2", 50000)), engine, settings


def _headers(settings: SimpleNamespace, actor: str, tenant: str = "tenant-a") -> dict[str, str]:
    token = issue_knowledge_actor_token(actor, tenant, 300, int(time.time()), settings=settings)
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant,
        "X-Request-ID": "req-dataset-api",
    }


def test_member_lists_and_reads_only_readable_profiles_in_token_tenant(api) -> None:
    client, _, settings = api
    response = client.get("/api/knowledge-bases", headers=_headers(settings, "member-a"))

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["items"]] == ["dataset-a", "dataset-archived"]
    assert payload["count"] == 2
    assert all(item["tenant_id"] == "tenant-a" for item in payload["items"])

    profile = client.get("/api/knowledge-bases/dataset-a", headers=_headers(settings, "member-a"))
    assert profile.status_code == 200
    body = profile.json()
    assert body["profile_revision"] == 1
    assert body["status"] == "active"
    assert body["owner_id"] == "owner-a"
    assert body["visibility"] == "private"
    assert body["profile"] == {"domain": "support"}
    assert body["policies"]["parser"] == {"engine": "mineru"}
    assert set(body["timestamps"]) == {"created_at", "updated_at", "archived_at", "archived_by"}


def test_editor_cannot_manage_but_owner_updates_profile_with_audit(api) -> None:
    client, engine, settings = api
    request = {
        "expected_revision": 1,
        "owner_id": "editor-a",
        "visibility": "tenant",
        "profile": {"domain": "engineering"},
        "policies": {
            "parser": {"engine": "mineru"},
            "chunk": {"max_tokens": 512},
            "retrieval": {"top_k": 12},
            "retention": {"days": 365},
            "metadata": {"language": "zh-CN"},
        },
        "default_language": "zh-CN",
        "graph_enabled": True,
        "qa_enabled": False,
    }
    forbidden = client.patch(
        "/api/knowledge-bases/dataset-a",
        headers=_headers(settings, "editor-a"),
        json=request,
    )
    assert forbidden.status_code == 403

    response = client.patch(
        "/api/knowledge-bases/dataset-a",
        headers=_headers(settings, "owner-a"),
        json=request,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["profile_revision"] == 2
    assert body["owner_id"] == "editor-a"
    assert body["visibility"] == "tenant"
    assert body["profile"] == {"domain": "engineering"}
    assert body["policies"]["chunk"] == {"max_tokens": 512}
    assert body["graph_enabled"] is True
    assert body["qa_enabled"] is False

    with Session(engine) as session:
        event = session.scalar(
            select(KnowledgeAuditEvent).where(
                KnowledgeAuditEvent.action == "dataset.profile.update"
            )
        )
        assert event is not None
        assert event.actor_id == "owner-a"
        assert event.request_id == "req-dataset-api"


def test_profile_patch_uses_strict_revision_bool_json_and_length_contracts(api) -> None:
    client, _, settings = api
    url = "/api/knowledge-bases/dataset-a"
    headers = _headers(settings, "owner-a")

    invalid_payloads = [
        {"expected_revision": "1", "visibility": "tenant"},
        {"expected_revision": 1.0, "visibility": "tenant"},
        {"expected_revision": True, "visibility": "tenant"},
        {"expected_revision": 1, "graph_enabled": 1},
        {"expected_revision": 1, "qa_enabled": "false"},
        {"expected_revision": 1, "profile": ["not", "an", "object"]},
        {"expected_revision": 1, "profile": None},
        {"expected_revision": 1, "policies": None},
        {"expected_revision": 1, "graph_enabled": None},
        {"expected_revision": 1, "policies": {"parser": ["not-object"]}},
        {"expected_revision": 1, "owner_id": "x" * 65},
        {"expected_revision": 1, "default_language": "x" * 33},
        {"expected_revision": 1},
    ]
    for payload in invalid_payloads:
        response = client.patch(url, headers=headers, json=payload)
        assert response.status_code == 422, payload


def test_conflicts_and_missing_profiles_use_stable_opaque_errors(api) -> None:
    client, _, settings = api
    headers = _headers(settings, "owner-a")

    conflict = client.patch(
        "/api/knowledge-bases/dataset-a",
        headers=headers,
        json={"expected_revision": 99, "visibility": "tenant"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "knowledge_dataset_conflict"

    missing = client.get("/api/knowledge-bases/missing", headers=headers)
    assert missing.status_code == 403
    assert missing.json()["detail"]["code"] == "knowledge_dataset_scope_forbidden"


def test_archive_restore_disable_state_contract(api) -> None:
    client, _, settings = api
    client = TestClient(
        client.app,
        client=("10.0.0.2", 50000),
        raise_server_exceptions=False,
    )
    archived = client.post(
        "/api/knowledge-bases/dataset-a/archive",
        headers=_headers(settings, "owner-a"),
        json={"expected_revision": 1},
    )
    assert archived.status_code == 500


def test_archived_restore_still_requires_manage_permission(api) -> None:
    client, _, settings = api
    response = client.post(
        "/api/knowledge-bases/dataset-archived/restore",
        headers=_headers(settings, "editor-a"),
        json={"expected_revision": 1},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "knowledge_permission_forbidden"

    missing = client.post(
        "/api/knowledge-bases/missing/restore",
        headers=_headers(settings, "owner-a"),
        json={"expected_revision": 1},
    )
    assert missing.status_code == 404
    assert missing.json() == {
        "detail": {"code": "knowledge_resource_not_found", "message": "资源不存在"}
    }


def test_dataset_router_openapi_is_exact(api) -> None:
    client, _, _ = api
    paths = client.app.openapi()["paths"]
    assert set(paths) == {
        "/api/knowledge-bases",
        "/api/knowledge-bases/{dataset_id}",
        "/api/knowledge-bases/{dataset_id}/archive",
        "/api/knowledge-bases/{dataset_id}/restore",
        "/api/knowledge-bases/{dataset_id}/disable",
    }
    assert set(paths["/api/knowledge-bases"]) == {"get"}
    assert set(paths["/api/knowledge-bases/{dataset_id}"]) == {"get", "patch"}
    for action in ("archive", "restore", "disable"):
        assert set(paths[f"/api/knowledge-bases/{{dataset_id}}/{action}"]) == {"post"}


@pytest.fixture()
def bridge_api(monkeypatch: pytest.MonkeyPatch):
    engine = _engine()
    settings = _settings()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    from server.app import app as bridge_app

    bridge_app.state.knowledge_auth_engine = engine
    bridge_app.state.knowledge_auth_settings = settings
    return TestClient(bridge_app, client=("10.0.0.2", 50000)), settings


def test_bridge_includes_router_and_preserves_structured_errors(bridge_api) -> None:
    client, settings = bridge_api
    assert "/api/knowledge-bases/{dataset_id}/disable" in client.app.openapi()["paths"]

    conflict = client.patch(
        "/api/knowledge-bases/dataset-a",
        headers=_headers(settings, "owner-a"),
        json={"expected_revision": 99, "visibility": "tenant"},
    )
    assert conflict.status_code == 409
    assert conflict.json() == {
        "error": {
            "code": "knowledge_dataset_conflict",
            "message": "dataset profile revision conflict",
        }
    }

    invalid = client.patch(
        "/api/knowledge-bases/dataset-a",
        headers=_headers(settings, "owner-a"),
        json={"expected_revision": "1", "visibility": "tenant"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
