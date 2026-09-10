from __future__ import annotations

import importlib
import time
from datetime import datetime
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from models.orm import Account, Base, Dataset, Tenant, TenantMember
from server.knowledge_auth import issue_knowledge_actor_token


ENTERPRISE_CAPABILITY_KEYS = {
    "organization_units",
    "user_groups",
    "dataset_acl",
    "member_mutations",
    "sso",
    "invitations",
    "tenant_audit",
}


def enterprise_api() -> ModuleType:
    return importlib.import_module("server.enterprise_admin_api")


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
        knowledge_security=KnowledgeSecuritySettings(
            actor_signing_secret=SecretStr("enterprise-admin-actor-secret"),
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
    joined = datetime(2026, 8, 20, 8, 0, 0)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(
                    id="tenant-a",
                    name="RAG4C Enterprise",
                    plan="enterprise",
                    status="active",
                    quota_documents=50_000,
                    quota_chunks=5_000_000,
                    doc_count=98,
                    chunk_count=12_420,
                ),
                Tenant(id="tenant-b", name="Other Tenant", status="active"),
                Account(
                    id="owner-a",
                    name="Owner A",
                    email="owner-a@example.test",
                    created_at=joined,
                ),
                Account(
                    id="admin-a",
                    name="Admin A",
                    email="admin-a@example.test",
                    created_at=joined,
                ),
                Account(
                    id="editor-a",
                    name="Editor A",
                    email="editor-a@example.test",
                    created_at=joined,
                ),
                Account(
                    id="member-a",
                    name="Member A",
                    email="member-a@example.test",
                    created_at=joined,
                ),
                Account(
                    id="owner-b",
                    name="Owner B",
                    email="owner-b@example.test",
                    created_at=joined,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                TenantMember(
                    account_id="owner-a",
                    tenant_id="tenant-a",
                    role="owner",
                    created_at=joined,
                ),
                TenantMember(
                    account_id="admin-a",
                    tenant_id="tenant-a",
                    role="admin",
                    created_at=joined,
                ),
                TenantMember(
                    account_id="editor-a",
                    tenant_id="tenant-a",
                    role="editor",
                    created_at=joined,
                ),
                TenantMember(
                    account_id="member-a",
                    tenant_id="tenant-a",
                    role="member",
                    created_at=joined,
                ),
                TenantMember(
                    account_id="owner-b",
                    tenant_id="tenant-b",
                    role="owner",
                    created_at=joined,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                Dataset(
                    id="dataset-a",
                    tenant_id="tenant-a",
                    name="制度知识库",
                    status="active",
                    owner_id="owner-a",
                    visibility="private",
                    profile_revision=7,
                ),
                Dataset(
                    id="dataset-archived",
                    tenant_id="tenant-a",
                    name="历史制度",
                    status="archived",
                    owner_id="admin-a",
                    visibility="tenant",
                    profile_revision=3,
                ),
                Dataset(
                    id="dataset-disabled",
                    tenant_id="tenant-a",
                    name="停用知识库",
                    status="disabled",
                    owner_id="owner-a",
                    visibility="private",
                ),
                Dataset(
                    id="dataset-b",
                    tenant_id="tenant-b",
                    name="Other Dataset",
                    status="active",
                    owner_id="owner-b",
                    visibility="tenant",
                ),
            ]
        )
        session.commit()
    return engine


def _headers(
    settings: SimpleNamespace,
    actor_id: str,
    tenant_id: str = "tenant-a",
) -> dict[str, str]:
    token = issue_knowledge_actor_token(
        actor_id,
        tenant_id,
        300,
        int(time.time()),
        settings=settings,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant_id,
        "X-Request-ID": "req-enterprise-admin",
    }


def _client(*, route_engine_provider=None) -> tuple[TestClient, Any, SimpleNamespace]:
    api = enterprise_api()
    engine = _engine()
    settings = _settings()
    read_provider = route_engine_provider or (lambda: engine)
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.include_router(
        api.build_enterprise_admin_router(
            read_engine_provider=read_provider,
            mutation_engine_provider=lambda: engine,
        )
    )
    return TestClient(app, client=("10.0.0.2", 50000)), engine, settings


def test_context_returns_real_tenant_actor_counts_permissions_and_capabilities() -> None:
    client, _, settings = _client()

    response = client.get(
        "/api/enterprise/context",
        headers=_headers(settings, "owner-a"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "tenant": {
            "id": "tenant-a",
            "name": "RAG4C Enterprise",
            "plan": "enterprise",
            "status": "active",
            "quota_documents": 50_000,
            "quota_chunks": 5_000_000,
            "doc_count": 98,
            "chunk_count": 12_420,
        },
        "actor": {
            "id": "owner-a",
            "name": "Owner A",
            "email": "owner-a@example.test",
            "role": "owner",
        },
        "role_permissions": {
            "owner": [
                "knowledge.audit",
                "knowledge.delete",
                "knowledge.manage",
                "knowledge.read",
                "knowledge.write",
            ],
            "admin": [
                "knowledge.audit",
                "knowledge.delete",
                "knowledge.manage",
                "knowledge.read",
                "knowledge.write",
            ],
            "editor": ["knowledge.delete", "knowledge.read", "knowledge.write"],
            "member": ["knowledge.read"],
        },
        "effective_permissions": [
            "knowledge.audit",
            "knowledge.delete",
            "knowledge.manage",
            "knowledge.read",
            "knowledge.write",
        ],
        "member_count": 4,
        "dataset_count": 3,
        "capabilities": {
            "member_directory": {"label": "成员目录", "state": "ready", "reason": None},
            "fixed_role_permissions": {"label": "固定角色权限", "state": "ready", "reason": None},
            "knowledge_bases": {"label": "知识库目录", "state": "ready", "reason": None},
            "knowledge_audit": {"label": "知识库审计", "state": "ready", "reason": None},
            "organization_units": {
                "label": "组织架构",
                "state": "limited",
                "reason": "企业组织目录已支持权威只读，组织变更尚未开放",
            },
            "user_groups": {
                "label": "用户组",
                "state": "limited",
                "reason": "企业用户组已支持权威只读，组成员变更尚未开放",
            },
            "dataset_acl": {
                "label": "知识库 ACL",
                "state": "limited",
                "reason": "知识库授权关系已支持权威只读，授权变更尚未开放",
            },
            "member_mutations": {
                "label": "成员变更",
                "state": "ready",
                "reason": None,
            },
            "sso": {
                "label": "企业 SSO",
                "state": "unavailable",
                "reason": "尚未接入企业 SSO",
            },
            "invitations": {
                "label": "成员邀请",
                "state": "limited",
                "reason": "成员邀请目录已支持权威只读，邀请签发尚未开放",
            },
            "tenant_audit": {
                "label": "企业管理日志",
                "state": "ready",
                "reason": None,
            },
            "enterprise_notification_center": {
                "label": "企业通知中心",
                "state": "unavailable",
                "reason": "0032 企业通知中心数据库升级尚未就绪：catalog is not at a known pre-0032, 0032 or 0033 revision",
            },
            "enterprise_content_recovery": {
                "label": "企业内容恢复",
                "state": "unavailable",
                "reason": "0033 企业内容恢复数据库升级尚未就绪：catalog is not at a known pre-0033, 0033 or 0034 revision",
            },
            "enterprise_task_operations": {
                "label": "企业任务运营",
                "state": "unavailable",
                "reason": "0034 企业任务运营数据库升级尚未就绪：catalog is not at a known pre-0034, 0034 or 0035 revision",
            },
            "enterprise_automation_workflows": {
                "label": "企业自动化",
                "state": "unavailable",
                "reason": "0035 企业自动化数据库升级尚未就绪：catalog revision is missing",
            },
            "enterprise_knowledge_serving_reliability": {
                "label": "知识服务可靠性",
                "state": "unavailable",
                "reason": "0036 知识服务可靠性数据库升级尚未就绪：catalog is not at a known pre-0036 or 0036 revision",
            },
        },
    }


def test_context_requires_only_read_permission_and_returns_member_permissions() -> None:
    client, _, settings = _client()

    response = client.get(
        "/api/enterprise/context",
        headers=_headers(settings, "member-a"),
    )

    assert response.status_code == 200
    assert response.json()["actor"]["role"] == "member"
    assert response.json()["effective_permissions"] == ["knowledge.read"]
    assert response.json()["capabilities"]["member_directory"]["label"] == "成员目录"
    assert response.json()["capabilities"]["organization_units"]["label"] == "组织架构"


def test_members_requires_audit_permission() -> None:
    client, _, settings = _client()

    response = client.get(
        "/api/enterprise/members",
        headers=_headers(settings, "member-a"),
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "knowledge_permission_forbidden"


def test_members_returns_real_accounts_without_invented_status() -> None:
    client, _, settings = _client()

    response = client.get(
        "/api/enterprise/members?limit=2",
        headers=_headers(settings, "owner-a"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["next_before_id"] is not None
    assert [item["account_id"] for item in payload["items"]] == [
        "member-a",
        "editor-a",
    ]
    assert payload["items"][0] == {
        "membership_id": 4,
        "account_id": "member-a",
        "name": "Member A",
        "email": "member-a@example.test",
        "role": "member",
        "joined_at": "2026-08-20T08:00:00",
    }
    assert all("status" not in item for item in payload["items"])


def test_members_supports_keyset_q_and_role_filters_with_tenant_isolation() -> None:
    client, _, settings = _client()

    first = client.get(
        "/api/enterprise/members?limit=2",
        headers=_headers(settings, "admin-a"),
    )
    cursor = first.json()["next_before_id"]
    second = client.get(
        f"/api/enterprise/members?limit=2&before_id={cursor}",
        headers=_headers(settings, "admin-a"),
    )
    filtered = client.get(
        "/api/enterprise/members?q=EDITOR&role=editor&limit=50",
        headers=_headers(settings, "owner-a"),
    )

    assert [item["account_id"] for item in second.json()["items"]] == [
        "admin-a",
        "owner-a",
    ]
    assert second.json()["next_before_id"] is None
    assert filtered.json()["count"] == 1
    assert [item["account_id"] for item in filtered.json()["items"]] == ["editor-a"]
    assert all(item["account_id"] != "owner-b" for item in second.json()["items"])


def test_members_rejects_unknown_role_and_invalid_keyset_limits() -> None:
    client, _, settings = _client()
    headers = _headers(settings, "owner-a")

    assert (
        client.get(
            "/api/enterprise/members?role=superadmin",
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/enterprise/members?before_id=0",
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/enterprise/members?limit=201",
            headers=headers,
        ).status_code
        == 422
    )


def test_access_summary_reports_real_profile_and_honest_tenant_role_enforcement() -> None:
    client, _, settings = _client()

    response = client.get(
        "/api/knowledge-bases/dataset-a/access-summary",
        headers=_headers(settings, "editor-a"),
    )

    assert response.status_code == 200
    assert response.json() == {
        "dataset_id": "dataset-a",
        "owner_id": "owner-a",
        "visibility": "private",
        "status": "active",
        "profile_revision": 7,
        "enforcement_mode": "tenant_role_fallback",
        "actor_role": "editor",
        "dataset_role": None,
        "bypass_reason": None,
        "role_permissions": {
            "owner": [
                "knowledge.audit",
                "knowledge.delete",
                "knowledge.manage",
                "knowledge.read",
                "knowledge.write",
            ],
            "admin": [
                "knowledge.audit",
                "knowledge.delete",
                "knowledge.manage",
                "knowledge.read",
                "knowledge.write",
            ],
            "editor": ["knowledge.delete", "knowledge.read", "knowledge.write"],
            "member": ["knowledge.read"],
        },
        "effective_permissions": [
            "knowledge.delete",
            "knowledge.read",
            "knowledge.write",
        ],
        "matched_grants": [],
        "workspace_authorization": {
            "state": "workspace_authorization_not_available",
            "mode": None,
            "permission_model_version": None,
            "workspace_ids": [],
            "workspace_roles": [],
            "contributing_workspaces": [],
            "candidate_permissions": [],
            "would_grant_permissions": [],
            "granted_permissions": [],
            "policy_revisions": [],
            "warnings": ["Workspace 授权迁移尚未完成"],
        },
        "dataset_acl_supported": True,
        "group_grants_supported": True,
        "organization_inheritance_supported": False,
        "warnings": [
            "当前知识库按持久化租户角色模式执行",
            "Workspace 授权迁移尚未完成",
            "private 可见性由服务端授权策略执行，不应由前端字段自行推断",
            "组织单元授权当前只匹配直接归属，不执行父级继承",
        ],
    }


def test_access_summary_allows_archived_dataset_but_rejects_disabled_and_cross_tenant() -> None:
    client, _, settings = _client()
    headers = _headers(settings, "member-a")

    archived = client.get(
        "/api/knowledge-bases/dataset-archived/access-summary",
        headers=headers,
    )
    disabled = client.get(
        "/api/knowledge-bases/dataset-disabled/access-summary",
        headers=headers,
    )
    cross_tenant = client.get(
        "/api/knowledge-bases/dataset-b/access-summary",
        headers=headers,
    )

    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert disabled.status_code == 403
    assert disabled.json()["detail"]["code"] == "knowledge_dataset_inactive"
    assert cross_tenant.status_code == 403
    assert cross_tenant.json()["detail"]["code"] == "knowledge_dataset_scope_forbidden"


@pytest.mark.parametrize(
    ("path", "actor"),
    [
        ("/api/enterprise/context", "owner-a"),
        ("/api/enterprise/members", "owner-a"),
        ("/api/knowledge-bases/dataset-a/access-summary", "owner-a"),
    ],
)
def test_directory_query_failures_are_503_and_do_not_leak_exception_details(
    path: str,
    actor: str,
) -> None:
    def broken_provider():
        raise RuntimeError("mysql://root:secret@db.internal/rag4c")

    client, _, settings = _client(route_engine_provider=broken_provider)

    response = client.get(path, headers=_headers(settings, actor))

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "enterprise_directory_unavailable",
            "message": "企业目录服务暂不可用",
        }
    }
    assert "secret" not in response.text
    assert "db.internal" not in response.text


def test_router_builder_requires_explicit_engine_provider() -> None:
    api = enterprise_api()

    with pytest.raises(TypeError, match="read_engine_provider"):
        api.build_enterprise_admin_router(mutation_engine_provider=lambda: object())
    with pytest.raises(TypeError, match="mutation_engine_provider"):
        api.build_enterprise_admin_router(read_engine_provider=lambda: object())


def test_capability_contract_reports_ready_limited_and_unavailable_states_honestly() -> None:
    client, _, settings = _client()

    response = client.get(
        "/api/enterprise/context",
        headers=_headers(settings, "owner-a"),
    )

    capabilities = response.json()["capabilities"]
    assert ENTERPRISE_CAPABILITY_KEYS <= set(capabilities)
    assert capabilities["sso"]["state"] == "unavailable"
    assert capabilities["member_mutations"]["state"] == "ready"
    assert capabilities["tenant_audit"]["state"] == "ready"
    for key in ("organization_units", "user_groups", "dataset_acl", "invitations"):
        assert capabilities[key]["state"] == "limited"
        assert "只读" in str(capabilities[key]["reason"])


def test_main_application_mounts_enterprise_admin_routes() -> None:
    from server.app import app

    paths = app.openapi()["paths"]
    assert "/api/enterprise/context" in paths
    assert "/api/enterprise/members" in paths
    assert "/api/knowledge-bases/{dataset_id}/access-summary" in paths


def test_capabilities_mark_knowledge_audit_unavailable_when_table_is_missing() -> None:
    client, engine, settings = _client()
    Base.metadata.tables["knowledge_audit_events"].drop(engine)

    response = client.get(
        "/api/enterprise/context",
        headers=_headers(settings, "owner-a"),
    )

    assert response.status_code == 200
    capability = response.json()["capabilities"]["knowledge_audit"]
    assert capability["state"] == "unavailable"
    assert "数据库" in capability["reason"]


def test_member_role_constraint_rejects_noncanonical_values_and_filter_remains_exact() -> None:
    client, engine, settings = _client()
    from sqlalchemy import text

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE tenant_members SET role=' ADMIN ' WHERE account_id='admin-a'")
            )

    response = client.get(
        "/api/enterprise/members?role=admin",
        headers=_headers(settings, "owner-a"),
    )

    assert response.status_code == 200
    assert [item["account_id"] for item in response.json()["items"]] == ["admin-a"]


def test_main_app_uses_the_same_read_only_engine_for_auth_and_enterprise_queries() -> None:
    from server import app as server_app
    from server import enterprise_readiness_api

    expected = enterprise_readiness_api.get_read_only_catalog_engine()
    assert server_app.app.state.knowledge_auth_engine is expected


def test_enterprise_capabilities_report_dataset_access_as_limited_on_legacy_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import catalog_schema, enterprise_directory

    class Inspector:
        def get_table_names(self):
            return ["tenants", "accounts", "tenant_members", "datasets"]

        def get_columns(self, table_name: str):
            if table_name == "datasets":
                return [{"name": name} for name in ("id", "tenant_id", "status")]
            return []

    # enterprise_capabilities 两处都用 inspect：主探测走 enterprise_directory.inspect，
    # 各 schema capability checker（catalog_schema.inspect_*）走 catalog_schema.inspect。
    # 只 patch 一处会导致另一处用真 sqlalchemy.inspect(object()) 抛 NoInspectionAvailable。
    monkeypatch.setattr(enterprise_directory, "inspect", lambda _engine: Inspector())
    monkeypatch.setattr(catalog_schema, "inspect", lambda _engine: Inspector())

    capabilities = enterprise_directory.enterprise_capabilities(object())

    assert capabilities["knowledge_bases"]["state"] == "limited"
    assert "数据库升级" in capabilities["knowledge_bases"]["reason"]
    assert capabilities["knowledge_audit"]["state"] == "unavailable"


def test_access_summary_fails_before_query_when_profile_columns_are_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import enterprise_directory

    class Inspector:
        def get_table_names(self):
            return ["datasets"]

        def get_columns(self, table_name: str):
            assert table_name == "datasets"
            return [{"name": name} for name in ("id", "tenant_id", "status")]

    monkeypatch.setattr(enterprise_directory, "inspect", lambda _engine: Inspector())

    with pytest.raises(enterprise_directory.EnterpriseCapabilityUnavailable):
        enterprise_directory.get_dataset_access_summary(
            object(),
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            actor_role="admin",
        )


def _explicit_provider_client(
    engine: Any,
    settings: SimpleNamespace,
    *,
    read_engine_provider,
    mutation_engine_provider,
) -> TestClient:
    api = enterprise_api()
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = settings
    app.include_router(
        api.build_enterprise_admin_router(
            read_engine_provider=read_engine_provider,
            mutation_engine_provider=mutation_engine_provider,
        )
    )
    return TestClient(app, client=("10.0.0.2", 50000))


def test_enterprise_get_routes_never_call_writable_engine_provider() -> None:
    engine = _engine()
    settings = _settings()
    read_calls = 0
    mutation_calls = 0

    def read_provider():
        nonlocal read_calls
        read_calls += 1
        return engine

    def mutation_provider():
        nonlocal mutation_calls
        mutation_calls += 1
        raise AssertionError("GET routes must not initialize the writable engine")

    client = _explicit_provider_client(
        engine,
        settings,
        read_engine_provider=read_provider,
        mutation_engine_provider=mutation_provider,
    )

    responses = [
        client.get("/api/enterprise/context", headers=_headers(settings, "owner-a")),
        client.get("/api/enterprise/members", headers=_headers(settings, "owner-a")),
        client.get("/api/enterprise/audit-events", headers=_headers(settings, "owner-a")),
        client.get(
            "/api/knowledge-bases/dataset-a/access-summary",
            headers=_headers(settings, "owner-a"),
        ),
    ]

    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    assert read_calls == 4
    assert mutation_calls == 0


def test_enterprise_member_mutations_lazily_call_only_writable_engine_provider() -> None:
    engine = _engine()
    settings = _settings()
    read_calls = 0
    mutation_calls = 0

    def read_provider():
        nonlocal read_calls
        read_calls += 1
        return engine

    def mutation_provider():
        nonlocal mutation_calls
        mutation_calls += 1
        return engine

    client = _explicit_provider_client(
        engine,
        settings,
        read_engine_provider=read_provider,
        mutation_engine_provider=mutation_provider,
    )

    role_change = client.patch(
        "/api/enterprise/members/editor-a/role",
        headers=_headers(settings, "owner-a"),
        json={"expected_revision": 1, "role": "member", "reason": "职责调整"},
    )
    suspend = client.post(
        "/api/enterprise/members/editor-a/suspend",
        headers=_headers(settings, "owner-a"),
        json={"expected_revision": 2, "reason": "暂时停用"},
    )
    resume = client.post(
        "/api/enterprise/members/editor-a/resume",
        headers=_headers(settings, "owner-a"),
        json={"expected_revision": 3, "reason": "恢复使用"},
    )

    assert [response.status_code for response in (role_change, suspend, resume)] == [200, 200, 200]
    assert read_calls == 0
    assert mutation_calls == 3


def test_writable_schema_verification_failure_maps_to_migration_required_without_writing() -> None:
    from core.catalog_schema import CatalogSchemaError
    from sqlalchemy import text

    engine = _engine()
    settings = _settings()
    mutation_calls = 0

    def mutation_provider():
        nonlocal mutation_calls
        mutation_calls += 1
        raise CatalogSchemaError("catalog schema is behind application head")

    client = _explicit_provider_client(
        engine,
        settings,
        read_engine_provider=lambda: engine,
        mutation_engine_provider=mutation_provider,
    )

    response = client.patch(
        "/api/enterprise/members/editor-a/role",
        headers=_headers(settings, "owner-a"),
        json={"expected_revision": 1, "role": "member", "reason": "迁移未完成"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "enterprise_membership_migration_required"
    assert mutation_calls == 1
    with engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT role FROM tenant_members WHERE account_id = 'editor-a'")
            ).scalar_one()
            == "editor"
        )
        assert (
            connection.execute(text("SELECT COUNT(*) FROM tenant_audit_events")).scalar_one() == 0
        )


def test_main_app_keeps_writable_catalog_engine_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import catalog
    from server import app as server_app

    engine = _engine()
    settings = _settings()
    monkeypatch.setattr(server_app.app.state, "knowledge_auth_engine", engine)
    monkeypatch.setattr(server_app.app.state, "knowledge_auth_settings", settings, raising=False)

    get_engine_calls = 0

    def writable_engine_provider():
        nonlocal get_engine_calls
        get_engine_calls += 1
        return engine

    monkeypatch.setattr(catalog, "get_engine", writable_engine_provider)
    assert get_engine_calls == 0

    client = TestClient(server_app.app, client=("10.0.0.2", 50000))
    response = client.patch(
        "/api/enterprise/members/editor-a/role",
        headers=_headers(settings, "owner-a"),
        json={"expected_revision": 1, "role": "member", "reason": "职责调整"},
    )

    assert response.status_code == 200, response.text
    assert get_engine_calls == 1


def test_enterprise_access_graph_capabilities_become_limited_when_0017_tables_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import catalog_schema, enterprise_directory

    class Inspector:
        def get_table_names(self):
            return [
                "datasets",
                "knowledge_audit_events",
                "tenant_members",
                "tenant_audit_events",
                "tenant_organization_units",
                "tenant_groups",
                "tenant_group_members",
                "dataset_access_grants",
                "tenant_invitations",
            ]

        def get_columns(self, table_name: str):
            if table_name == "datasets":
                return [
                    {"name": name}
                    for name in (
                        "id",
                        "tenant_id",
                        "owner_id",
                        "visibility",
                        "profile_revision",
                        "status",
                    )
                ]
            return []

    monkeypatch.setattr(enterprise_directory, "inspect", lambda _engine: Inspector())
    monkeypatch.setattr(catalog_schema, "inspect", lambda _engine: Inspector())

    capabilities = enterprise_directory.enterprise_capabilities(object())

    for key in ("organization_units", "user_groups", "dataset_acl", "invitations"):
        assert capabilities[key]["state"] == "limited"
        assert "只读" in str(capabilities[key]["reason"])
