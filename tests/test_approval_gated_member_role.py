from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any

from alembic import command
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core.catalog_schema import _alembic_config
from tests.head_catalog import align_era_columns
from models.orm import Account, Tenant, TenantMember
from server.knowledge_auth import issue_knowledge_actor_token

REVISION = "0025_enterprise_approval_control"
TENANT_ID = "tenant-stage15-a"
NOW = datetime(2026, 8, 27, 15, 0, 0)
SETTINGS = SimpleNamespace(
    run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
    knowledge_security=KnowledgeSecuritySettings(
        actor_signing_secret=SecretStr("stage15-member-role-secret"),
        actor_max_ttl_s=900,
    ),
    tenant=TenantSettings(enforced=True, default_tenant=TENANT_ID),
)


def _configure_sqlite(engine: Any) -> None:
    @event.listens_for(engine, "connect")
    def configure(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def _seed(engine: Any) -> None:
    joined = NOW - timedelta(days=30)
    align_era_columns(engine)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT_ID, name="Stage 15 Tenant", status="active"))
        session.add_all(
            [
                Account(
                    id="owner-stage15",
                    name="Stage 15 Owner",
                    email="owner-stage15@example.test",
                    created_at=joined,
                ),
                Account(
                    id="admin-stage15",
                    name="Stage 15 Admin",
                    email="admin-stage15@example.test",
                    created_at=joined,
                ),
                Account(
                    id="member-stage15",
                    name="Stage 15 Member",
                    email="member-stage15@example.test",
                    created_at=joined,
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                TenantMember(
                    account_id="owner-stage15",
                    tenant_id=TENANT_ID,
                    role="owner",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    account_id="admin-stage15",
                    tenant_id=TENANT_ID,
                    role="admin",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    account_id="member-stage15",
                    tenant_id=TENANT_ID,
                    role="member",
                    status="active",
                    revision=1,
                ),
            ]
        )
        session.commit()


def _engine(tmp_path: Path) -> Any:
    url = f"sqlite:///{(tmp_path / 'stage15-member-role.db').as_posix()}"
    command.upgrade(_alembic_config(url), REVISION)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    _configure_sqlite(engine)
    _seed(engine)
    return engine


def _headers(actor_id: str, *, key: str) -> dict[str, str]:
    token = issue_knowledge_actor_token(
        actor_id,
        TENANT_ID,
        300,
        int(time.time()),
        settings=SETTINGS,
    )
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": TENANT_ID,
        "X-RAG4C-Actor": actor_id,
        "X-Request-ID": f"stage15-{actor_id}-{time.time_ns()}",
        "Idempotency-Key": key,
    }


def _client(
    engine: Any,
    *,
    execution_adapters: dict[str, Any] | None = None,
) -> TestClient:
    from server import enterprise_admin_api, enterprise_approval_api

    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = SETTINGS
    app.include_router(
        enterprise_admin_api.build_enterprise_admin_router(
            read_engine_provider=lambda: engine,
            mutation_engine_provider=lambda: engine,
        )
    )
    app.include_router(
        enterprise_approval_api.build_enterprise_approval_router(
            read_engine_provider=lambda: engine,
            mutation_engine_provider=lambda: engine,
            now_provider=lambda: NOW,
            execution_adapters=execution_adapters,
        )
    )
    return TestClient(app)


def test_active_member_role_policy_blocks_direct_route_with_sanitized_evidence(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        policy = client.post(
            "/api/enterprise/approvals/policies",
            headers=_headers("admin-stage15", key="stage15-policy-create"),
            json={
                "name": "成员角色变更双人审批",
                "action_type": "member_role_change",
                "resource_scope": "tenant_member:member-stage15",
                "required_approvals": 1,
                "request_expiry_minutes": 60,
                "approvers": [{"kind": "account", "ref": "owner-stage15"}],
                "reason": "保护高风险成员权限变更",
            },
        )
        assert policy.status_code == 201, policy.text

        response = client.patch(
            "/api/enterprise/members/member-stage15/role",
            headers=_headers("admin-stage15", key="stage15-direct-role"),
            json={
                "expected_revision": 1,
                "role": "admin",
                "reason": "需要提升成员权限",
            },
        )

        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "member_role_approval_required"
        assert detail["policy_id"] == policy.json()["policy"]["id"]
        assert detail["policy_name"] == "成员角色变更双人审批"
        assert detail["policy_revision"] == 1
        assert detail["required_approvals"] == 1
        assert detail["request_expiry_minutes"] == 60
        assert "ticket" not in detail
        assert "secret" not in str(detail).casefold()

        with Session(engine) as session:
            member = (
                session.query(TenantMember)
                .filter_by(
                    tenant_id=TENANT_ID,
                    account_id="member-stage15",
                )
                .one()
            )
            assert member.role == "member"
            assert member.revision == 1
    finally:
        client.close()
        engine.dispose()


def _create_member_role_policy(
    client: TestClient,
    *,
    scope: str | None,
    key: str,
    threshold: int = 1,
) -> dict[str, Any]:
    response = client.post(
        "/api/enterprise/approvals/policies",
        headers=_headers("admin-stage15", key=key),
        json={
            "name": "成员角色变更审批",
            "action_type": "member_role_change",
            "resource_scope": scope,
            "required_approvals": threshold,
            "request_expiry_minutes": 60,
            "approvers": [{"kind": "account", "ref": "owner-stage15"}],
            "reason": "保护成员角色变更",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["policy"]


def _rows(engine: Any, table: str) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]


def _member(engine: Any, account_id: str = "member-stage15") -> dict[str, Any]:
    with Session(engine) as session:
        row = (
            session.query(TenantMember)
            .filter_by(
                tenant_id=TENANT_ID,
                account_id=account_id,
            )
            .one()
        )
        return {
            "role": row.role,
            "status": row.status,
            "revision": row.revision,
        }


def test_no_active_member_role_policy_preserves_direct_mutation(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        response = client.patch(
            "/api/enterprise/members/member-stage15/role",
            headers=_headers("admin-stage15", key="stage15-no-policy-role"),
            json={"expected_revision": 1, "role": "editor", "reason": "岗位调整"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["membership"]["role"] == "editor"
        assert response.json()["membership"]["revision"] == 2
        assert _member(engine) == {"role": "editor", "status": "active", "revision": 2}
    finally:
        client.close()
        engine.dispose()


@pytest.mark.parametrize(
    "scope",
    [
        "tenant_member:member-stage15",
        "member-stage15",
        "tenant_member:*",
        "tenant_member/*",
        "tenant_member:wildcard:global",
        "tenant_member:global",
        "tenant_member:wildcard",
        "tenant_member/wildcard/global",
        "global",
        "wildcard",
        "*",
    ],
)
def test_matching_member_role_scope_blocks_direct_mutation(
    tmp_path: Path,
    scope: str,
) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        policy = _create_member_role_policy(client, scope=scope, key=f"stage15-scope-{scope}")
        response = client.patch(
            "/api/enterprise/members/member-stage15/role",
            headers=_headers("admin-stage15", key=f"stage15-direct-{scope}"),
            json={"expected_revision": 1, "role": "editor", "reason": "岗位调整"},
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "member_role_approval_required"
        assert response.json()["detail"]["policy_id"] == policy["id"]
        assert _member(engine) == {"role": "member", "status": "active", "revision": 1}
    finally:
        client.close()
        engine.dispose()


def _create_member_role_request(
    client: TestClient,
    policy: dict[str, Any],
    *,
    key: str,
    requested_role: str = "admin",
    expected_revision: int = 1,
    actor_id: str = "admin-stage15",
    target_account_id: str = "member-stage15",
    current_role: str = "member",
    current_status: str = "active",
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/api/enterprise/approvals/requests",
        headers=_headers(actor_id, key=key),
        json={
            "policy_id": policy["id"],
            "resource_type": "tenant_member",
            "resource_id": target_account_id,
            "snapshot": snapshot
            or {
                "target_account_id": target_account_id,
                "expected_member_revision": expected_revision,
                "current_role": current_role,
                "requested_role": requested_role,
                "current_status": current_status,
            },
            "reason": "提交成员角色变更审批",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["request"]


def test_approval_request_keeps_exact_member_role_snapshot_and_live_role_unchanged(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        policy = _create_member_role_policy(
            client,
            scope="tenant_member:member-stage15",
            key="stage15-request-policy",
        )
        request = _create_member_role_request(client, policy, key="stage15-role-request")
        assert request["action_type"] == "member_role_change"
        assert request["resource_type"] == "tenant_member"
        assert request["resource_id"] == "member-stage15"
        assert request["snapshot"] == {
            "target_account_id": "member-stage15",
            "expected_member_revision": 1,
            "current_role": "member",
            "requested_role": "admin",
            "current_status": "active",
        }
        assert request["status"] == "pending"
        assert _member(engine) == {"role": "member", "status": "active", "revision": 1}
    finally:
        client.close()
        engine.dispose()


def test_approved_member_role_ticket_executes_with_approval_and_member_audits(
    tmp_path: Path,
) -> None:
    from server import enterprise_approval_consumers

    engine = _engine(tmp_path)
    adapters = enterprise_approval_consumers.build_enterprise_approval_execution_adapters(
        lambda: engine
    )
    client = _client(engine, execution_adapters=adapters)
    try:
        policy = _create_member_role_policy(
            client,
            scope="tenant_member:member-stage15",
            key="stage15-execute-policy",
        )
        request = _create_member_role_request(client, policy, key="stage15-execute-request")
        approved = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/approve",
            headers=_headers("owner-stage15", key="stage15-execute-approve"),
            json={"revision": 1, "comment": "批准角色变更"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["request"]["status"] == "approved"
        ticket = approved.json()["execution"]["ticket"]

        consumed = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("owner-stage15", key="stage15-execute-consume"),
            json={
                "ticket": ticket,
                "revision": 2,
                "action_type": "member_role_change",
                "resource_type": "tenant_member",
                "resource_id": "member-stage15",
            },
        )
        assert consumed.status_code == 200, consumed.text
        assert consumed.json()["request"]["status"] == "executed"
        assert _member(engine) == {"role": "admin", "status": "active", "revision": 2}

        audit_actions = [row["action"] for row in _rows(engine, "tenant_audit_events")]
        assert "approval.request.approved" in audit_actions
        assert "approval.ticket.consumed" in audit_actions
        assert "member.role.update" in audit_actions
    finally:
        client.close()
        engine.dispose()


def _approve_member_role_ticket(
    client: TestClient,
    *,
    policy_scope: str = "tenant_member:member-stage15",
    policy_key: str,
    request_key: str,
    approve_key: str,
    requested_role: str = "admin",
    expected_member_revision: int = 1,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    policy = _create_member_role_policy(client, scope=policy_scope, key=policy_key)
    request = _create_member_role_request(
        client,
        policy,
        key=request_key,
        requested_role=requested_role,
        expected_revision=expected_member_revision,
    )
    approved = client.post(
        f"/api/enterprise/approvals/requests/{request['id']}/approve",
        headers=_headers("owner-stage15", key=approve_key),
        json={"revision": 1, "comment": "批准角色变更"},
    )
    assert approved.status_code == 200, approved.text
    return policy, request, approved.json()["execution"]["ticket"]


def test_non_manager_cannot_claim_member_role_ticket_and_ticket_remains_approved(
    tmp_path: Path,
) -> None:
    from server import enterprise_approval_consumers

    engine = _engine(tmp_path)
    adapters = enterprise_approval_consumers.build_enterprise_approval_execution_adapters(
        lambda: engine
    )
    client = _client(engine, execution_adapters=adapters)
    try:
        _policy, request, ticket = _approve_member_role_ticket(
            client,
            policy_key="stage15-nonmanager-policy",
            request_key="stage15-nonmanager-request",
            approve_key="stage15-nonmanager-approve",
        )
        denied = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("member-stage15", key="stage15-nonmanager-consume"),
            json={
                "ticket": ticket,
                "revision": 2,
                "action_type": "member_role_change",
                "resource_type": "tenant_member",
                "resource_id": "member-stage15",
            },
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"]["code"] == "approval_execution_forbidden"
        assert _member(engine) == {"role": "member", "status": "active", "revision": 1}
        with engine.connect() as connection:
            row = dict(
                connection.execute(
                    __import__("sqlalchemy").text(
                        "SELECT status, revision, ticket_consumed_at "
                        "FROM tenant_approval_requests WHERE id=:id"
                    ),
                    {"id": request["id"]},
                )
                .mappings()
                .one()
            )
        assert row == {"status": "approved", "revision": 2, "ticket_consumed_at": None}
    finally:
        client.close()
        engine.dispose()


def test_stale_member_revision_finalizes_approval_as_execution_failed_without_role_change(
    tmp_path: Path,
) -> None:
    from server import enterprise_approval_consumers

    engine = _engine(tmp_path)
    adapters = enterprise_approval_consumers.build_enterprise_approval_execution_adapters(
        lambda: engine
    )
    client = _client(engine, execution_adapters=adapters)
    try:
        _policy, request, ticket = _approve_member_role_ticket(
            client,
            policy_key="stage15-stale-revision-policy",
            request_key="stage15-stale-revision-request",
            approve_key="stage15-stale-revision-approve",
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE tenant_members SET role='editor', revision=2 "
                    "WHERE tenant_id=:tenant_id AND account_id=:account_id"
                ),
                {"tenant_id": TENANT_ID, "account_id": "member-stage15"},
            )

        failed = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("owner-stage15", key="stage15-stale-revision-consume"),
            json={
                "ticket": ticket,
                "revision": 2,
                "action_type": "member_role_change",
                "resource_type": "tenant_member",
                "resource_id": "member-stage15",
            },
        )
        assert failed.status_code == 502, failed.text
        assert failed.json()["request"]["status"] == "execution_failed"
        assert failed.json()["execution"]["state"] == "execution_failed"
        assert "revision" in failed.json()["request"]["execution_error"]
        assert _member(engine) == {"role": "editor", "status": "active", "revision": 2}
        assert not any(
            row["action"] == "member.role.update" for row in _rows(engine, "tenant_audit_events")
        )
        assert any(
            row["action"] == "approval.ticket.execution_failed"
            for row in _rows(engine, "tenant_audit_events")
        )
    finally:
        client.close()
        engine.dispose()


def test_last_active_owner_protection_survives_approved_member_role_execution(
    tmp_path: Path,
) -> None:
    from server import enterprise_approval_consumers

    engine = _engine(tmp_path)
    adapters = enterprise_approval_consumers.build_enterprise_approval_execution_adapters(
        lambda: engine
    )
    client = _client(engine, execution_adapters=adapters)
    try:
        policy = _create_member_role_policy(
            client,
            scope="tenant_member:owner-stage15",
            key="stage15-last-owner-policy",
        )
        request = _create_member_role_request(
            client,
            policy,
            key="stage15-last-owner-request",
            target_account_id="owner-stage15",
            current_role="owner",
        )
        approved = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/approve",
            headers=_headers("owner-stage15", key="stage15-last-owner-approve"),
            json={"revision": 1, "comment": "审批角色变更"},
        )
        assert approved.status_code == 200, approved.text

        failed = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("owner-stage15", key="stage15-last-owner-consume"),
            json={
                "ticket": approved.json()["execution"]["ticket"],
                "revision": 2,
                "action_type": "member_role_change",
                "resource_type": "tenant_member",
                "resource_id": "owner-stage15",
            },
        )
        assert failed.status_code == 502, failed.text
        assert failed.json()["request"]["status"] == "execution_failed"
        assert failed.json()["request"]["execution_error"]
        assert _member(engine, "owner-stage15") == {
            "role": "owner",
            "status": "active",
            "revision": 1,
        }
    finally:
        client.close()
        engine.dispose()


def test_transactional_member_role_gate_rechecks_when_route_precheck_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        policy = _create_member_role_policy(
            client,
            scope="tenant_member:member-stage15",
            key="stage15-stale-route-policy",
        )
        monkeypatch.setattr(
            "server.enterprise_admin_api.resolve_active_approval_policy",
            lambda *_args, **_kwargs: None,
        )
        blocked = client.patch(
            "/api/enterprise/members/member-stage15/role",
            headers=_headers("admin-stage15", key="stage15-stale-route-role"),
            json={"expected_revision": 1, "role": "admin", "reason": "事务内复核策略"},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "member_role_approval_required"
        assert blocked.json()["detail"]["policy_id"] == policy["id"]
        assert _member(engine) == {"role": "member", "status": "active", "revision": 1}
    finally:
        client.close()
        engine.dispose()


def test_unsupported_member_role_execution_fails_closed_before_ticket_claim(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        _policy, request, ticket = _approve_member_role_ticket(
            client,
            policy_key="stage15-notconnected-policy",
            request_key="stage15-notconnected-request",
            approve_key="stage15-notconnected-approve",
        )
        response = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("owner-stage15", key="stage15-notconnected-consume"),
            json={
                "ticket": ticket,
                "revision": 2,
                "action_type": "member_role_change",
                "resource_type": "tenant_member",
                "resource_id": "member-stage15",
            },
        )
        assert response.status_code == 503, response.text
        assert response.json()["detail"]["code"] == "execution_adapter_not_connected"
        with engine.connect() as connection:
            row = dict(
                connection.execute(
                    text(
                        "SELECT status, revision, ticket_consumed_at "
                        "FROM tenant_approval_requests WHERE id=:id"
                    ),
                    {"id": request["id"]},
                )
                .mappings()
                .one()
            )
        assert row == {"status": "approved", "revision": 2, "ticket_consumed_at": None}
        assert _member(engine) == {"role": "member", "status": "active", "revision": 1}
    finally:
        client.close()
        engine.dispose()


def test_member_role_ticket_replay_is_exact_and_does_not_repeat_member_mutation(
    tmp_path: Path,
) -> None:
    from server import enterprise_approval_consumers

    engine = _engine(tmp_path)
    calls: list[dict[str, Any]] = []
    base_consumer = enterprise_approval_consumers.build_member_role_change_consumer(lambda: engine)

    def counting_consumer(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(payload))
        return base_consumer(payload)

    client = _client(
        engine,
        execution_adapters={"member_role_change": counting_consumer},
    )
    try:
        _policy, request, ticket = _approve_member_role_ticket(
            client,
            policy_key="stage15-replay-policy",
            request_key="stage15-replay-request",
            approve_key="stage15-replay-approve",
        )
        body = {
            "ticket": ticket,
            "revision": 2,
            "action_type": "member_role_change",
            "resource_type": "tenant_member",
            "resource_id": "member-stage15",
        }
        first = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("owner-stage15", key="stage15-replay-consume"),
            json=body,
        )
        replay = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("owner-stage15", key="stage15-replay-consume"),
            json=body,
        )
        assert first.status_code == 200, first.text
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()
        assert len(calls) == 1
        assert _member(engine) == {"role": "admin", "status": "active", "revision": 2}
        assert (
            sum(
                row["action"] == "member.role.update"
                for row in _rows(engine, "tenant_audit_events")
            )
            == 1
        )
    finally:
        client.close()
        engine.dispose()


@pytest.mark.parametrize(
    "invalid_snapshot",
    [
        {
            "target_account_id": "other-account",
            "expected_member_revision": 1,
            "current_role": "member",
            "requested_role": "admin",
            "current_status": "active",
        },
        {
            "target_account_id": "member-stage15",
            "expected_member_revision": 0,
            "current_role": "member",
            "requested_role": "admin",
            "current_status": "active",
        },
        {
            "target_account_id": "member-stage15",
            "expected_member_revision": 1,
            "current_role": "superuser",
            "requested_role": "admin",
            "current_status": "active",
        },
        {
            "target_account_id": "member-stage15",
            "expected_member_revision": 1,
            "current_role": "member",
            "requested_role": "member",
            "current_status": "active",
        },
        {
            "target_account_id": "member-stage15",
            "expected_member_revision": 1,
            "current_role": "member",
            "requested_role": "admin",
            "current_status": "suspended",
        },
    ],
)
def test_member_role_consumer_rejects_invalid_snapshot_and_preserves_member(
    tmp_path: Path, invalid_snapshot: dict[str, Any]
) -> None:
    from server import enterprise_approval_consumers

    engine = _engine(tmp_path)
    adapters = enterprise_approval_consumers.build_enterprise_approval_execution_adapters(
        lambda: engine
    )
    client = _client(engine, execution_adapters=adapters)
    try:
        policy = _create_member_role_policy(
            client,
            scope="tenant_member:member-stage15",
            key="stage15-invalid-snapshot-policy",
        )
        request = _create_member_role_request(
            client,
            policy,
            key="stage15-invalid-snapshot-request",
            snapshot=invalid_snapshot,
        )
        approved = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/approve",
            headers=_headers("owner-stage15", key="stage15-invalid-snapshot-approve"),
            json={"revision": 1, "comment": "批准"},
        )
        assert approved.status_code == 200, approved.text
        failed = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("owner-stage15", key="stage15-invalid-snapshot-consume"),
            json={
                "ticket": approved.json()["execution"]["ticket"],
                "revision": 2,
                "action_type": "member_role_change",
                "resource_type": "tenant_member",
                "resource_id": "member-stage15",
            },
        )
        assert failed.status_code == 502, failed.text
        assert failed.json()["request"]["status"] == "execution_failed"
        assert _member(engine) == {"role": "member", "status": "active", "revision": 1}
    finally:
        client.close()
        engine.dispose()


def test_production_app_registers_member_role_change_consumer_registry() -> None:
    from server import enterprise_approval_consumers

    source = Path("server/app.py").read_text(encoding="utf-8")
    assert "enterprise_approval_consumers" in source
    assert "build_enterprise_approval_execution_adapters" in source
    registry = enterprise_approval_consumers.build_enterprise_approval_execution_adapters(
        lambda: None
    )
    assert "member_role_change" in registry
