from __future__ import annotations

import importlib
from pathlib import Path
import time
from types import ModuleType, SimpleNamespace
from typing import Any

from alembic import command
from alembic.config import Config

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
)
from models.orm import Account, Base, Dataset, Tenant, TenantMember
from server.knowledge_auth import issue_knowledge_actor_token


EXPECTED_ROUTES = {
    ("GET", "/api/enterprise/workspaces"),
    ("GET", "/api/enterprise/workspaces/{workspace_id}"),
    ("POST", "/api/enterprise/workspaces"),
    ("PATCH", "/api/enterprise/workspaces/{workspace_id}"),
    ("POST", "/api/enterprise/workspaces/{workspace_id}/archive"),
    ("GET", "/api/enterprise/workspaces/{workspace_id}/members"),
    ("POST", "/api/enterprise/workspaces/{workspace_id}/members"),
    ("PATCH", "/api/enterprise/workspaces/{workspace_id}/members/{account_id}"),
    ("POST", "/api/enterprise/workspaces/{workspace_id}/members/{account_id}/remove"),
    ("GET", "/api/enterprise/workspaces/{workspace_id}/datasets"),
    ("POST", "/api/enterprise/workspaces/{workspace_id}/datasets"),
    ("POST", "/api/enterprise/workspaces/{workspace_id}/datasets/{dataset_id}/remove"),
}


def workspace_api() -> ModuleType:
    return importlib.import_module("server.enterprise_workspace_api")


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("workspace-api-actor-secret"),
            actor_max_ttl_s=900,
        ),
        tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
    )


def _auth_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[Tenant.__table__, Account.__table__, TenantMember.__table__],
    )
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="Tenant A", status="active"),
                Tenant(id="tenant-b", name="Tenant B", status="active"),
                Account(id="owner-a", name="Owner A", email="owner-a@example.test"),
                Account(id="member-a", name="Member A", email="member-a@example.test"),
                Account(id="owner-b", name="Owner B", email="owner-b@example.test"),
            ]
        )
        session.flush()
        session.add_all(
            [
                TenantMember(
                    account_id="owner-a",
                    tenant_id="tenant-a",
                    role="owner",
                    status="active",
                ),
                TenantMember(
                    account_id="member-a",
                    tenant_id="tenant-a",
                    role="member",
                    status="active",
                ),
                TenantMember(
                    account_id="owner-b",
                    tenant_id="tenant-b",
                    role="owner",
                    status="active",
                ),
            ]
        )
        session.commit()
    return engine


def _headers(
    settings: SimpleNamespace,
    actor_id: str = "owner-a",
    tenant_id: str = "tenant-a",
    *,
    idempotency_key: str | None = None,
) -> dict[str, str]:
    token = issue_knowledge_actor_token(
        actor_id,
        tenant_id,
        300,
        int(time.time()),
        settings=settings,
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant_id,
        "X-RAG4C-Actor": actor_id,
        "X-Request-ID": "req-workspace-api",
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


class RecordingWorkspaceService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []
        self.failure: Exception | None = None

    def _record(self, name: str, engine: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, engine, kwargs))
        if self.failure is not None:
            raise self.failure
        if name.startswith("list_"):
            return {"items": [], "next_cursor": None}
        resource = {
            "list_workspaces": "workspace",
            "get_workspace": "workspace",
            "create_workspace": "workspace",
            "update_workspace": "workspace",
            "archive_workspace": "workspace",
            "list_workspace_members": "member",
            "add_workspace_member": "member",
            "update_workspace_member": "member",
            "remove_workspace_member": "member",
            "list_workspace_datasets": "binding",
            "bind_workspace_dataset": "binding",
            "remove_workspace_dataset": "binding",
        }[name]
        return {resource: {"id": kwargs.get("workspace_id", "workspace-new")}}

    def __getattr__(self, name: str):
        if name not in {
            "list_workspaces",
            "get_workspace",
            "create_workspace",
            "update_workspace",
            "archive_workspace",
            "list_workspace_members",
            "add_workspace_member",
            "update_workspace_member",
            "remove_workspace_member",
            "list_workspace_datasets",
            "bind_workspace_dataset",
            "remove_workspace_dataset",
        }:
            raise AttributeError(name)

        def operation(engine: Any, **kwargs: Any) -> dict[str, Any]:
            return self._record(name, engine, kwargs)

        return operation


def _real_0026_engine(tmp_path):
    from core.catalog_schema import upgrade_catalog

    tenant_id = "tenant-" + "a" * 57
    assert len(tenant_id) == 64
    database_path = tmp_path / "workspace-api-0026.db"
    url = f"sqlite:///{database_path.as_posix()}"
    upgrade_catalog(url, "0025_enterprise_approval_control")

    seed_engine = create_engine(url)
    with Session(seed_engine) as session:
        session.add_all(
            [
                Tenant(id=tenant_id, name="Long Tenant", plan="enterprise", status="active"),
                Account(id="owner-long", name="Owner Long", email="owner-long@example.test"),
                Account(id="viewer-long", name="Viewer Long", email="viewer-long@example.test"),
            ]
        )
        session.flush()
        session.add_all(
            [
                TenantMember(
                    account_id="owner-long",
                    tenant_id=tenant_id,
                    role="owner",
                    status="active",
                ),
                TenantMember(
                    account_id="viewer-long",
                    tenant_id=tenant_id,
                    role="member",
                    status="active",
                ),
            ]
        )
        session.add_all(
            [
                Dataset(
                    id="dataset-primary",
                    tenant_id=tenant_id,
                    name="Primary Dataset",
                    description="",
                    status="active",
                ),
                Dataset(
                    id="dataset-shared",
                    tenant_id=tenant_id,
                    name="Shared Dataset",
                    description="",
                    status="active",
                ),
            ]
        )
        session.commit()
    seed_engine.dispose()

    config = Config(str((Path(__file__).resolve().parents[1] / "alembic.ini").resolve()))
    config.set_main_option(
        "script_location",
        str((Path(__file__).resolve().parents[1] / "catalog_migrations").resolve()),
    )
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "0026_enterprise_workspace_control")

    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine, tenant_id


def _real_client(engine: Any, tenant_id: str) -> tuple[TestClient, SimpleNamespace]:
    api = workspace_api()
    settings = _settings()
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.include_router(
        api.build_enterprise_workspace_router(
            read_engine_provider=lambda: engine,
            mutation_engine_provider=lambda: engine,
            service=None,
        )
    )
    return TestClient(app), settings


def _client(service: Any | None) -> tuple[TestClient, Any, Any, SimpleNamespace]:
    api = workspace_api()
    auth_engine = _auth_engine()
    read_engine = object()
    mutation_engine = object()
    settings = _settings()
    app = FastAPI()
    app.state.knowledge_auth_engine = auth_engine
    app.state.knowledge_auth_settings = settings
    app.include_router(
        api.build_enterprise_workspace_router(
            read_engine_provider=lambda: read_engine,
            mutation_engine_provider=lambda: mutation_engine,
            service=service,
        )
    )
    return TestClient(app), read_engine, mutation_engine, settings


def _route_pairs(app: FastAPI) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()

    def collect(routes: Any) -> None:
        for route in routes:
            path = getattr(route, "path", None)
            for method in getattr(route, "methods", set()):
                if path is not None and method in {"GET", "POST", "PATCH"}:
                    pairs.add((method, path))
            nested = getattr(route, "routes", None)
            if nested is None:
                original_router = getattr(route, "original_router", None)
                nested = getattr(original_router, "routes", None)
            if nested is not None:
                collect(nested)

    collect(app.routes)
    return pairs


def test_router_registers_complete_stage16_workspace_contract() -> None:
    api = workspace_api()
    app = FastAPI()
    app.include_router(
        api.build_enterprise_workspace_router(
            read_engine_provider=lambda: object(),
            mutation_engine_provider=lambda: object(),
            service=RecordingWorkspaceService(),
        )
    )

    assert EXPECTED_ROUTES <= _route_pairs(app)


def test_list_and_detail_are_tenant_actor_bound_keyset_reads() -> None:
    service = RecordingWorkspaceService()
    client, read_engine, _mutation_engine, settings = _client(service)
    headers = _headers(settings)

    listed = client.get(
        "/api/enterprise/workspaces",
        params={
            "status": "active",
            "environment": "production",
            "cursor": "cursor-1",
            "limit": 25,
        },
        headers=headers,
    )
    detail = client.get("/api/enterprise/workspaces/workspace-a", headers=headers)
    members = client.get(
        "/api/enterprise/workspaces/workspace-a/members",
        params={"status": "active", "role": "admin", "cursor": "cursor-2", "limit": 20},
        headers=headers,
    )
    datasets = client.get(
        "/api/enterprise/workspaces/workspace-a/datasets",
        params={
            "status": "active",
            "binding_kind": "primary",
            "cursor": "cursor-3",
            "limit": 15,
        },
        headers=headers,
    )

    assert [listed.status_code, detail.status_code, members.status_code, datasets.status_code] == [
        200,
        200,
        200,
        200,
    ]
    for response in (listed, detail, members, datasets):
        assert response.json()["workspace_authorization_not_enforced"] is True

    assert service.calls == [
        (
            "list_workspaces",
            read_engine,
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "status": "active",
                "environment": "production",
                "cursor": "cursor-1",
                "limit": 25,
            },
        ),
        (
            "get_workspace",
            read_engine,
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "workspace_id": "workspace-a",
            },
        ),
        (
            "list_workspace_members",
            read_engine,
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "workspace_id": "workspace-a",
                "status": "active",
                "role": "admin",
                "cursor": "cursor-2",
                "limit": 20,
            },
        ),
        (
            "list_workspace_datasets",
            read_engine,
            {
                "tenant_id": "tenant-a",
                "actor_id": "owner-a",
                "workspace_id": "workspace-a",
                "status": "active",
                "binding_kind": "primary",
                "cursor": "cursor-3",
                "limit": 15,
            },
        ),
    ]


def test_workspace_lifecycle_mutations_forward_revision_idempotency_and_audit_evidence() -> None:
    service = RecordingWorkspaceService()
    client, _read_engine, mutation_engine, settings = _client(service)

    created = client.post(
        "/api/enterprise/workspaces",
        json={
            "code": "prod-search",
            "name": "生产检索",
            "description": "Production knowledge operations",
            "environment": "production",
            "reason": "创建生产知识空间",
        },
        headers=_headers(settings, idempotency_key="workspace-create-1"),
    )
    updated = client.patch(
        "/api/enterprise/workspaces/workspace-a",
        json={
            "revision": 3,
            "name": "生产检索中心",
            "description": "Updated",
            "environment": "production",
            "reason": "更新空间信息",
        },
        headers=_headers(settings, idempotency_key="workspace-update-1"),
    )
    archived = client.post(
        "/api/enterprise/workspaces/workspace-a/archive",
        json={"revision": 4, "reason": "空间退役"},
        headers=_headers(settings, idempotency_key="workspace-archive-1"),
    )

    assert [created.status_code, updated.status_code, archived.status_code] == [201, 200, 200]
    assert all(
        response.json()["workspace_authorization_not_enforced"] is True
        for response in (created, updated, archived)
    )
    common = {
        "tenant_id": "tenant-a",
        "actor_id": "owner-a",
        "request_id": "req-workspace-api",
        "request_ip": "testclient",
    }
    assert service.calls == [
        (
            "create_workspace",
            mutation_engine,
            common
            | {
                "code": "prod-search",
                "name": "生产检索",
                "description": "Production knowledge operations",
                "environment": "production",
                "reason": "创建生产知识空间",
                "idempotency_key": "workspace-create-1",
            },
        ),
        (
            "update_workspace",
            mutation_engine,
            common
            | {
                "workspace_id": "workspace-a",
                "expected_revision": 3,
                "name": "生产检索中心",
                "description": "Updated",
                "environment": "production",
                "reason": "更新空间信息",
                "idempotency_key": "workspace-update-1",
            },
        ),
        (
            "archive_workspace",
            mutation_engine,
            common
            | {
                "workspace_id": "workspace-a",
                "expected_revision": 4,
                "reason": "空间退役",
                "idempotency_key": "workspace-archive-1",
            },
        ),
    ]


def test_member_and_dataset_mutations_forward_exact_scope() -> None:
    service = RecordingWorkspaceService()
    client, _read_engine, mutation_engine, settings = _client(service)

    requests = [
        client.post(
            "/api/enterprise/workspaces/workspace-a/members",
            json={"account_id": "member-a", "role": "editor", "reason": "加入空间"},
            headers=_headers(settings, idempotency_key="member-add-1"),
        ),
        client.patch(
            "/api/enterprise/workspaces/workspace-a/members/member-a",
            json={"revision": 2, "role": "viewer", "reason": "调整权限"},
            headers=_headers(settings, idempotency_key="member-update-1"),
        ),
        client.post(
            "/api/enterprise/workspaces/workspace-a/members/member-a/remove",
            json={"revision": 3, "reason": "移出空间"},
            headers=_headers(settings, idempotency_key="member-remove-1"),
        ),
        client.post(
            "/api/enterprise/workspaces/workspace-a/datasets",
            json={"dataset_id": "dataset-a", "binding_kind": "primary", "reason": "主知识库"},
            headers=_headers(settings, idempotency_key="dataset-bind-1"),
        ),
        client.post(
            "/api/enterprise/workspaces/workspace-a/datasets/dataset-a/remove",
            json={"revision": 5, "reason": "解除绑定"},
            headers=_headers(settings, idempotency_key="dataset-remove-1"),
        ),
    ]

    assert [response.status_code for response in requests] == [201, 200, 200, 201, 200]
    assert all(
        response.json()["workspace_authorization_not_enforced"] is True for response in requests
    )
    common = {
        "tenant_id": "tenant-a",
        "actor_id": "owner-a",
        "request_id": "req-workspace-api",
        "request_ip": "testclient",
    }
    assert service.calls == [
        (
            "add_workspace_member",
            mutation_engine,
            common
            | {
                "workspace_id": "workspace-a",
                "account_id": "member-a",
                "role": "editor",
                "reason": "加入空间",
                "idempotency_key": "member-add-1",
            },
        ),
        (
            "update_workspace_member",
            mutation_engine,
            common
            | {
                "workspace_id": "workspace-a",
                "account_id": "member-a",
                "expected_revision": 2,
                "role": "viewer",
                "reason": "调整权限",
                "idempotency_key": "member-update-1",
            },
        ),
        (
            "remove_workspace_member",
            mutation_engine,
            common
            | {
                "workspace_id": "workspace-a",
                "account_id": "member-a",
                "expected_revision": 3,
                "reason": "移出空间",
                "idempotency_key": "member-remove-1",
            },
        ),
        (
            "bind_workspace_dataset",
            mutation_engine,
            common
            | {
                "workspace_id": "workspace-a",
                "dataset_id": "dataset-a",
                "binding_kind": "primary",
                "reason": "主知识库",
                "idempotency_key": "dataset-bind-1",
            },
        ),
        (
            "remove_workspace_dataset",
            mutation_engine,
            common
            | {
                "workspace_id": "workspace-a",
                "dataset_id": "dataset-a",
                "expected_revision": 5,
                "reason": "解除绑定",
                "idempotency_key": "dataset-remove-1",
            },
        ),
    ]


def test_contract_is_strict_bounded_and_requires_idempotency() -> None:
    service = RecordingWorkspaceService()
    client, _read_engine, _mutation_engine, settings = _client(service)
    headers = _headers(settings)

    assert (
        client.get("/api/enterprise/workspaces", params={"limit": 201}, headers=headers).status_code
        == 422
    )
    assert (
        client.get(
            "/api/enterprise/workspaces",
            params={"status": "deleted"},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/enterprise/workspaces",
            json={
                "code": "ops",
                "name": "Ops",
                "environment": "production",
                "reason": "Create",
            },
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/enterprise/workspaces",
            json={
                "code": "ops",
                "name": "Ops",
                "environment": "production",
                "reason": "Create",
                "unexpected": True,
            },
            headers=_headers(settings, idempotency_key="strict-1"),
        ).status_code
        == 422
    )
    assert service.calls == []


def test_workspace_description_matches_the_512_character_database_contract() -> None:
    service = RecordingWorkspaceService()
    client, _read_engine, _mutation_engine, settings = _client(service)
    accepted = "a" * 512
    rejected = "b" * 513

    create_ok = client.post(
        "/api/enterprise/workspaces",
        json={
            "code": "description-create",
            "name": "Description Create",
            "description": accepted,
            "environment": "testing",
            "reason": "verify create description boundary",
        },
        headers=_headers(settings, idempotency_key="description-create-ok"),
    )
    create_too_long = client.post(
        "/api/enterprise/workspaces",
        json={
            "code": "description-rejected",
            "name": "Description Rejected",
            "description": rejected,
            "environment": "testing",
            "reason": "verify create description rejection",
        },
        headers=_headers(settings, idempotency_key="description-create-rejected"),
    )
    update_ok = client.patch(
        "/api/enterprise/workspaces/workspace-a",
        json={
            "revision": 1,
            "description": accepted,
            "reason": "verify update description boundary",
        },
        headers=_headers(settings, idempotency_key="description-update-ok"),
    )
    update_too_long = client.patch(
        "/api/enterprise/workspaces/workspace-a",
        json={
            "revision": 1,
            "description": rejected,
            "reason": "verify update description rejection",
        },
        headers=_headers(settings, idempotency_key="description-update-rejected"),
    )

    assert create_ok.status_code == 201
    assert update_ok.status_code == 200
    assert create_too_long.status_code == 422
    assert update_too_long.status_code == 422
    assert [call[0] for call in service.calls] == ["create_workspace", "update_workspace"]
    assert service.calls[0][2]["description"] == accepted
    assert service.calls[1][2]["description"] == accepted


def test_signed_actor_and_tenant_scope_are_enforced_before_service_calls() -> None:
    service = RecordingWorkspaceService()
    client, _read_engine, _mutation_engine, settings = _client(service)

    no_token = client.get(
        "/api/enterprise/workspaces",
        headers={"X-RAG4C-Tenant": "tenant-a", "X-RAG4C-Actor": "owner-a"},
    )
    mismatched = client.get(
        "/api/enterprise/workspaces",
        headers=_headers(settings, actor_id="owner-a", tenant_id="tenant-a")
        | {"X-RAG4C-Tenant": "tenant-b"},
    )

    assert no_token.status_code == 401
    assert mismatched.status_code == 401
    assert service.calls == []


class WorkspaceDomainError(RuntimeError):
    def __init__(self, *, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class EnterpriseWorkspaceMigrationRequired(RuntimeError):
    pass


@pytest.mark.parametrize(
    ("failure", "status_code", "code"),
    [
        (
            WorkspaceDomainError(
                status=409,
                code="workspace_revision_conflict",
                message="workspace changed",
            ),
            409,
            "workspace_revision_conflict",
        ),
        (TenantMutationIdempotencyConflict(), 409, "workspace_idempotency_conflict"),
        (TenantMutationIdempotencyInProgress(), 409, "workspace_idempotency_in_progress"),
        (TenantMutationIdempotencyValidationError(), 422, "workspace_request_invalid"),
        (EnterpriseWorkspaceMigrationRequired(), 503, "enterprise_workspace_migration_required"),
        (RuntimeError("database password leaked"), 503, "enterprise_workspace_unavailable"),
    ],
)
def test_errors_are_mapped_to_stable_safe_contracts(
    failure: Exception,
    status_code: int,
    code: str,
) -> None:
    service = RecordingWorkspaceService()
    service.failure = failure
    client, _read_engine, _mutation_engine, settings = _client(service)

    response = client.get("/api/enterprise/workspaces", headers=_headers(settings))

    assert response.status_code == status_code
    assert response.json()["detail"]["code"] == code
    assert "database password" not in response.text


def test_missing_workspace_core_fails_closed_without_import_crash() -> None:
    client, _read_engine, _mutation_engine, settings = _client(None)

    response = client.get("/api/enterprise/workspaces", headers=_headers(settings))

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "enterprise_workspace_unavailable",
        "message": "企业 Workspace 服务暂不可用",
    }


def test_workspace_path_accepts_deterministic_default_id_for_a_64_character_tenant() -> None:
    service = RecordingWorkspaceService()
    client, _read_engine, _mutation_engine, settings = _client(service)
    tenant_id = "tenant-" + "a" * 57
    workspace_id = f"workspace-default-{tenant_id}"

    response = client.get(
        f"/api/enterprise/workspaces/{workspace_id}",
        headers=_headers(settings),
    )

    assert len(workspace_id) == 82
    assert response.status_code == 200
    assert service.calls[-1][2]["workspace_id"] == workspace_id


def test_real_0026_lazy_core_http_contract_is_not_hidden_by_fake_service(tmp_path) -> None:
    engine, tenant_id = _real_0026_engine(tmp_path)
    client, settings = _real_client(engine, tenant_id)
    headers = _headers(settings, actor_id="owner-long", tenant_id=tenant_id)
    default_workspace_id = f"workspace-default-{tenant_id}"
    try:
        listed = client.get(
            "/api/enterprise/workspaces",
            params={"status": "active", "environment": "production", "limit": 10},
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["workspace_authorization_not_enforced"] is True
        assert default_workspace_id in {item["id"] for item in listed.json()["items"]}

        detail = client.get(
            f"/api/enterprise/workspaces/{default_workspace_id}",
            headers=headers,
        )
        assert detail.status_code == 200, detail.text

        created = client.post(
            "/api/enterprise/workspaces",
            json={
                "code": "review",
                "name": "Review Workspace",
                "description": "Real HTTP/Core contract",
                "environment": "testing",
                "reason": "verify real API integration",
            },
            headers=headers | {"Idempotency-Key": "real-workspace-create"},
        )
        assert created.status_code == 201, created.text
        workspace_id = created.json()["workspace"]["id"]

        member = client.post(
            f"/api/enterprise/workspaces/{workspace_id}/members",
            json={
                "account_id": "viewer-long",
                "role": "viewer",
                "reason": "verify member mutation reason",
            },
            headers=headers | {"Idempotency-Key": "real-workspace-member"},
        )
        assert member.status_code == 201, member.text
        members = client.get(
            f"/api/enterprise/workspaces/{workspace_id}/members",
            params={"status": "active", "role": "viewer", "limit": 10},
            headers=headers,
        )
        assert members.status_code == 200, members.text
        assert [item["account_id"] for item in members.json()["items"]] == ["viewer-long"]

        binding = client.post(
            f"/api/enterprise/workspaces/{workspace_id}/datasets",
            json={
                "dataset_id": "dataset-shared",
                "binding_kind": "shared",
                "reason": "verify dataset mutation reason",
            },
            headers=headers | {"Idempotency-Key": "real-workspace-dataset"},
        )
        assert binding.status_code == 201, binding.text
        datasets = client.get(
            f"/api/enterprise/workspaces/{workspace_id}/datasets",
            params={"status": "active", "binding_kind": "shared", "limit": 10},
            headers=headers,
        )
        assert datasets.status_code == 200, datasets.text
        assert [item["dataset_id"] for item in datasets.json()["items"]] == ["dataset-shared"]
    finally:
        engine.dispose()


def test_main_application_mounts_workspace_routes() -> None:
    app_module = importlib.import_module("server.app")

    assert EXPECTED_ROUTES <= _route_pairs(app_module.app)
