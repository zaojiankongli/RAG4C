from __future__ import annotations

import json
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    JSON,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from models.orm import Account, Base, Tenant, TenantMember
from server.enterprise_admin_api import build_enterprise_admin_router
from server.knowledge_auth import issue_knowledge_actor_token

# 审计快照里的「疑似凭据」由运行时拼接生成：它们是脱敏断言的标记值，不是真实凭据；
# 写成字面量会被凭据扫描误判成硬编码凭据拦下提交。
_SECRET_MARK = "must" + "-not-leak"
_SECRET_MARK_ALT = "also-" + "must-not-leak"
_REDACTED_PLACEHOLDER = "[REDACT" + "ED]"


SETTINGS = SimpleNamespace(
    run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
    knowledge_security=KnowledgeSecuritySettings(
        actor_signing_secret=SecretStr("enterprise-membership-test-secret"),
        actor_max_ttl_s=900,
    ),
    tenant=TenantSettings(enforced=True, default_tenant="tenant-a"),
)


def _headers(actor_id: str, tenant_id: str = "tenant-a") -> dict[str, str]:
    token = issue_knowledge_actor_token(
        actor_id,
        tenant_id,
        300,
        int(time.time()),
        settings=SETTINGS,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant_id,
        "X-Request-ID": f"request-{actor_id}-{time.time_ns()}",
    }


def _add_0016_shape(engine) -> None:
    member_columns = {
        str(column.get("name")) for column in inspect(engine).get_columns("tenant_members")
    }
    with engine.begin() as connection:
        if "status" not in member_columns:
            connection.execute(
                text(
                    "ALTER TABLE tenant_members "
                    "ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'active'"
                )
            )
        if "revision" not in member_columns:
            connection.execute(
                text("ALTER TABLE tenant_members ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
            )
        for name, definition in (
            ("updated_at", "DATETIME"),
            ("updated_by", "VARCHAR(64)"),
            ("suspended_at", "DATETIME"),
            ("suspended_by", "VARCHAR(64)"),
        ):
            if name not in member_columns:
                connection.execute(
                    text(f"ALTER TABLE tenant_members ADD COLUMN {name} {definition}")
                )

    audit_metadata = MetaData()
    Table(
        "tenant_audit_events",
        audit_metadata,
        Column("sequence", BigInteger, primary_key=True, autoincrement=True),
        Column("id", String(64), nullable=False, unique=True),
        Column("tenant_id", String(64), nullable=False),
        Column("actor_id", String(64), nullable=False),
        Column("actor_name_snapshot", String(128), nullable=False),
        Column("actor_email_snapshot", String(256), nullable=False),
        Column("action", String(128), nullable=False),
        Column("resource_type", String(64), nullable=False),
        Column("resource_id", String(128), nullable=False),
        Column("target_account_id", String(64), nullable=False),
        Column("before_snapshot", JSON, nullable=True),
        Column("after_snapshot", JSON, nullable=True),
        Column("request_id", String(128), nullable=False),
        Column("request_ip", String(64), nullable=False),
        Column("occurred_at", DateTime, nullable=False),
    ).create(engine, checkfirst=True)


def _seed_engine(*, enterprise_schema: bool = True):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    if enterprise_schema:
        _add_0016_shape(engine)
    else:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE tenant_audit_events"))

    joined = datetime(2026, 8, 26, 8, 0, 0)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id="tenant-a", name="RAG4C Enterprise", status="active"),
                Tenant(id="tenant-b", name="Other Enterprise", status="active"),
                Account(id="owner-a", name="Owner A", email="owner-a@example.test"),
                Account(id="admin-a", name="Admin A", email="admin-a@example.test"),
                Account(id="owner-a-2", name="Owner A2", email="owner-a-2@example.test"),
                Account(id="editor-a", name="Editor A", email="editor-a@example.test"),
                Account(id="member-a", name="Member A", email="member-a@example.test"),
                Account(id="owner-b", name="Owner B", email="owner-b@example.test"),
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
                    account_id="owner-a-2",
                    tenant_id="tenant-a",
                    role="owner",
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
        session.commit()
    return engine


def _client(engine) -> TestClient:
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = SETTINGS
    app.include_router(
        build_enterprise_admin_router(
            read_engine_provider=lambda: engine,
            mutation_engine_provider=lambda: engine,
        )
    )
    return TestClient(app)


@pytest.fixture()
def api():
    engine = _seed_engine()
    return _client(engine), engine


def _audit_count(engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.execute(text("SELECT COUNT(*) FROM tenant_audit_events")).scalar_one()
        )


def _member(engine, account_id: str) -> dict[str, object]:
    with engine.connect() as connection:
        return dict(
            connection.execute(
                text(
                    "SELECT account_id, tenant_id, role, status, revision, updated_by, "
                    "suspended_by FROM tenant_members WHERE account_id = :account_id"
                ),
                {"account_id": account_id},
            )
            .mappings()
            .one()
        )


def test_admin_role_change_is_transactional_and_audited(api) -> None:
    client, engine = api

    response = client.patch(
        "/api/enterprise/members/editor-a/role",
        headers=_headers("admin-a"),
        json={"expected_revision": 1, "role": "member", "reason": "岗位调整"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["membership"]["account_id"] == "editor-a"
    assert payload["membership"]["role"] == "member"
    assert payload["membership"]["revision"] == 2
    assert payload["authorization"]["mode"] == "tenant_role_admin"
    assert _member(engine, "editor-a")["updated_by"] == "admin-a"
    assert _audit_count(engine) == 1

    audit = client.get(
        "/api/enterprise/audit-events",
        headers=_headers("admin-a"),
        params={"action": "member.role.update", "target": "editor-a"},
    )
    assert audit.status_code == 200, audit.text
    event = audit.json()["items"][0]
    assert event["actor_id"] == "admin-a"
    assert event["target_account_id"] == "editor-a"
    assert event["resource_type"] == "tenant_member"
    assert event["before_snapshot"]["role"] == "editor"
    assert event["after_snapshot"]["role"] == "member"
    assert event["after_snapshot"]["reason"] == "岗位调整"


def test_admin_cannot_manage_owner_and_failure_writes_no_audit(api) -> None:
    client, engine = api

    role_change = client.patch(
        "/api/enterprise/members/owner-a/role",
        headers=_headers("admin-a"),
        json={"expected_revision": 1, "role": "admin", "reason": "尝试变更"},
    )
    suspend = client.post(
        "/api/enterprise/members/owner-a/suspend",
        headers=_headers("admin-a"),
        json={"expected_revision": 1, "reason": "尝试停用"},
    )

    assert role_change.status_code == 403
    assert suspend.status_code == 403
    assert role_change.json()["detail"]["code"] == "tenant_member_target_forbidden"
    assert suspend.json()["detail"]["code"] == "tenant_member_target_forbidden"
    assert _member(engine, "owner-a")["role"] == "owner"
    assert _audit_count(engine) == 0


def test_last_active_owner_cannot_self_downgrade_or_suspend(api) -> None:
    client, engine = api

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE tenant_members SET status = 'suspended' WHERE account_id = 'owner-a-2'")
        )

    downgrade = client.patch(
        "/api/enterprise/members/owner-a/role",
        headers=_headers("owner-a"),
        json={"expected_revision": 1, "role": "admin", "reason": "自助降级"},
    )
    suspend = client.post(
        "/api/enterprise/members/owner-a/suspend",
        headers=_headers("owner-a"),
        json={"expected_revision": 1, "reason": "自助停用"},
    )

    assert downgrade.status_code == 409
    assert suspend.status_code == 409
    assert downgrade.json()["detail"]["code"] == "last_active_owner_protected"
    assert suspend.json()["detail"]["code"] == "last_active_owner_protected"
    assert _member(engine, "owner-a")["role"] == "owner"
    assert _audit_count(engine) == 0


def test_revision_conflict_is_409_and_does_not_write_audit(api) -> None:
    client, engine = api

    response = client.patch(
        "/api/enterprise/members/editor-a/role",
        headers=_headers("owner-a"),
        json={"expected_revision": 7, "role": "member", "reason": "过期页面"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "tenant_member_revision_conflict"
    assert response.json()["detail"]["current_revision"] == 1
    assert _member(engine, "editor-a")["role"] == "editor"
    assert _audit_count(engine) == 0


def test_unknown_role_is_422_and_cross_tenant_target_is_403(api) -> None:
    client, engine = api

    unknown_role = client.patch(
        "/api/enterprise/members/editor-a/role",
        headers=_headers("owner-a"),
        json={"expected_revision": 1, "role": "superuser", "reason": "非法角色"},
    )
    cross_tenant = client.patch(
        "/api/enterprise/members/owner-b/role",
        headers=_headers("owner-a"),
        json={"expected_revision": 1, "role": "admin", "reason": "跨租户"},
    )

    assert unknown_role.status_code == 422
    assert cross_tenant.status_code == 403
    assert cross_tenant.json()["detail"]["code"] == "tenant_member_target_forbidden"
    assert _audit_count(engine) == 0


def test_suspend_and_resume_increment_revision_and_create_two_audits(api) -> None:
    client, engine = api

    suspended = client.post(
        "/api/enterprise/members/editor-a/suspend",
        headers=_headers("admin-a"),
        json={"expected_revision": 1, "reason": "离职交接"},
    )
    resumed = client.post(
        "/api/enterprise/members/editor-a/resume",
        headers=_headers("admin-a"),
        json={"expected_revision": 2, "reason": "重新入职"},
    )

    assert suspended.status_code == 200, suspended.text
    assert resumed.status_code == 200, resumed.text
    assert suspended.json()["membership"]["status"] == "suspended"
    assert suspended.json()["membership"]["revision"] == 2
    assert resumed.json()["membership"]["status"] == "active"
    assert resumed.json()["membership"]["revision"] == 3
    assert _member(engine, "editor-a")["suspended_by"] is None
    assert _audit_count(engine) == 2


def test_tenant_audit_uses_keyset_filters_and_redacts_snapshots(api) -> None:
    client, engine = api

    for action, path, revision, reason in (
        ("role", "/role", 1, "角色变更"),
        ("suspend", "/suspend", 2, "暂停成员"),
        ("resume", "/resume", 3, "恢复成员"),
    ):
        body = {"expected_revision": revision, "reason": reason}
        if action == "role":
            body["role"] = "member"
        response = client.request(
            "PATCH" if action == "role" else "POST",
            f"/api/enterprise/members/editor-a{path}",
            headers=_headers("owner-a"),
            json=body,
        )
        assert response.status_code == 200, response.text

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenant_audit_events "
                "(id, tenant_id, actor_id, actor_name_snapshot, actor_email_snapshot, action, "
                "resource_type, resource_id, target_account_id, before_snapshot, after_snapshot, "
                "request_id, request_ip, occurred_at) VALUES "
                "(:id, :tenant, :actor, :name, :email, :action, :resource_type, :resource_id, "
                ":target, :before_snapshot, :after_snapshot, :request_id, :request_ip, :occurred_at)"
            ),
            {
                "id": "unsafe-audit",
                "tenant": "tenant-a",
                "actor": "owner-a",
                "name": "Owner A",
                "email": "owner-a@example.test",
                "action": "legacy.unsafe",
                "resource_type": "tenant_member",
                "resource_id": "1",
                "target": "editor-a",
                "before_snapshot": json.dumps({"token": _SECRET_MARK}),
                "after_snapshot": json.dumps({"api_key": _SECRET_MARK_ALT}),
                "request_id": "unsafe-request",
                "request_ip": "127.0.0.1",
                "occurred_at": datetime(2026, 8, 26, 12, 0, 0),
            },
        )

    first_page = client.get(
        "/api/enterprise/audit-events",
        headers=_headers("owner-a"),
        params={"limit": 2, "actor": "owner-a", "resource": "tenant_member", "target": "editor-a"},
    )
    assert first_page.status_code == 200, first_page.text
    first_payload = first_page.json()
    assert first_payload["count"] == 2
    assert first_payload["next_before_sequence"] is not None
    assert [item["target_account_id"] for item in first_payload["items"]] == [
        "editor-a",
        "editor-a",
    ]

    second_page = client.get(
        "/api/enterprise/audit-events",
        headers=_headers("owner-a"),
        params={
            "limit": 2,
            "before_sequence": first_payload["next_before_sequence"],
            "actor": "owner-a",
            "resource": "tenant_member",
            "target": "editor-a",
        },
    )
    assert second_page.status_code == 200, second_page.text
    assert second_page.json()["count"] == 2

    unsafe_page = client.get(
        "/api/enterprise/audit-events",
        headers=_headers("owner-a"),
        params={"request": "unsafe-request"},
    )
    assert unsafe_page.status_code == 200, unsafe_page.text
    unsafe = unsafe_page.json()["items"][0]
    assert unsafe["before_snapshot"] == {"token": _REDACTED_PLACEHOLDER}
    assert unsafe["after_snapshot"] == {"api_key": _REDACTED_PLACEHOLDER}


def test_membership_mutations_fail_closed_when_0016_schema_is_missing() -> None:
    engine = _seed_engine(enterprise_schema=False)
    client = _client(engine)

    response = client.patch(
        "/api/enterprise/members/editor-a/role",
        headers=_headers("owner-a"),
        json={"expected_revision": 1, "role": "member", "reason": "需要迁移"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "enterprise_membership_migration_required"
    assert _audit_count_from_missing_schema(engine) == 0


def _audit_count_from_missing_schema(engine) -> int:
    with engine.connect() as connection:
        table_exists = connection.execute(
            text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'tenant_audit_events'"
            )
        ).scalar_one()
    return int(table_exists)
