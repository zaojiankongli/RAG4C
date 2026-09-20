from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import importlib
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session

from config.settings import KnowledgeSecuritySettings, RunHistorySettings, TenantSettings
from core.catalog_schema import _alembic_config
from tests.head_catalog import align_era_columns
from models.orm import Account, Base, Tenant, TenantGroup, TenantGroupMember, TenantMember
from server.knowledge_auth import issue_knowledge_actor_token

REVISION = "0025_enterprise_approval_control"
TENANT_A = "tenant-approval-a"
TENANT_B = "tenant-approval-b"
NOW = datetime(2026, 8, 27, 12, 0, 0)
SETTINGS = SimpleNamespace(
    run_history=RunHistorySettings(ops_bearer_token=SecretStr("ops-secret")),
    knowledge_security=KnowledgeSecuritySettings(
        actor_signing_secret=SecretStr("stage13-approval-secret"), actor_max_ttl_s=900
    ),
    tenant=TenantSettings(enforced=True, default_tenant=TENANT_A),
)


class _Missing:
    def __getattr__(self, name: str) -> Any:
        pytest.fail(f"approval implementation is missing: {name}")


def _control() -> Any:
    try:
        return importlib.import_module("core.enterprise_approval_control")
    except ModuleNotFoundError:
        return _Missing()


def _api() -> Any:
    try:
        return importlib.import_module("server.enterprise_approval_api")
    except ModuleNotFoundError:
        return _Missing()


def _sqlite(engine: Any) -> None:
    @event.listens_for(engine, "connect")
    def configure(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def _upgrade_catalog(url: str, revision: str) -> None:
    command.upgrade(_alembic_config(url), revision)


def _seed_scope(engine: Any) -> None:
    joined = NOW - timedelta(days=30)
    align_era_columns(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, name="Approval A", status="active"),
                Tenant(id=TENANT_B, name="Approval B", status="active"),
                Account(
                    id="owner-a", name="Owner A", email="owner-a@example.test", created_at=joined
                ),
                Account(
                    id="admin-a", name="Admin A", email="admin-a@example.test", created_at=joined
                ),
                Account(
                    id="reviewer-a",
                    name="Reviewer A",
                    email="reviewer-a@example.test",
                    created_at=joined,
                ),
                Account(
                    id="member-a", name="Member A", email="member-a@example.test", created_at=joined
                ),
                Account(
                    id="owner-b", name="Owner B", email="owner-b@example.test", created_at=joined
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                TenantMember(
                    account_id="owner-a",
                    tenant_id=TENANT_A,
                    role="owner",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    account_id="admin-a",
                    tenant_id=TENANT_A,
                    role="admin",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    account_id="reviewer-a",
                    tenant_id=TENANT_A,
                    role="editor",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    account_id="member-a",
                    tenant_id=TENANT_A,
                    role="member",
                    status="active",
                    revision=1,
                ),
                TenantMember(
                    account_id="owner-b",
                    tenant_id=TENANT_B,
                    role="owner",
                    status="active",
                    revision=1,
                ),
            ]
        )
        session.flush()
        session.add(
            TenantGroup(
                id="approval-group-a",
                tenant_id=TENANT_A,
                name="Approval Group A",
                normalized_name="approval group a",
                description="Stage 13 test group",
                status="active",
                revision=1,
                created_at=joined,
                updated_at=joined,
            )
        )
        session.flush()
        session.add(
            TenantGroupMember(
                tenant_id=TENANT_A,
                group_id="approval-group-a",
                account_id="reviewer-a",
                status="active",
                created_at=joined,
                created_by="admin-a",
            )
        )
        session.commit()


def _engine(tmp_path: Path, *, revision: str = REVISION) -> Any:
    db_path = tmp_path / "approval.db"
    url = f"sqlite:///{db_path.as_posix()}"
    _upgrade_catalog(url, revision)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    _sqlite(engine)
    _seed_scope(engine)
    return engine


def _table_exists(engine: Any, table: str) -> bool:
    return table in set(inspect(engine).get_table_names())


def _headers(actor: str, *, tenant: str = TENANT_A, key: str = "stage13-key") -> dict[str, str]:
    token = issue_knowledge_actor_token(actor, tenant, 300, int(time.time()), settings=SETTINGS)
    return {
        "Authorization": f"Bearer {token}",
        "X-RAG4C-Tenant": tenant,
        "X-RAG4C-Actor": actor,
        "X-Request-ID": f"stage13-{actor}-{time.time_ns()}",
        "Idempotency-Key": key,
    }


def _client(
    engine: Any,
    *,
    now: datetime = NOW,
    execution_adapter: Any | None = None,
) -> TestClient:
    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = SETTINGS
    router_kwargs: dict[str, Any] = {
        "read_engine_provider": lambda: engine,
        "mutation_engine_provider": lambda: engine,
        "now_provider": lambda: now,
    }
    if execution_adapter is not None:
        router_kwargs["execution_adapter"] = execution_adapter
    app.include_router(_api().build_enterprise_approval_router(**router_kwargs))
    return TestClient(app)


def _policy(
    client: TestClient,
    *,
    key: str = "policy-create",
    threshold: int = 2,
    approvers: list[dict[str, str]] | None = None,
    action_type: str = "catalog_upgrade",
    resource_scope: str = "catalog:*",
) -> Any:
    return client.post(
        "/api/enterprise/approvals/policies",
        headers=_headers("admin-a", key=key),
        json={
            "name": "Catalog changes",
            "action_type": action_type,
            "resource_scope": resource_scope,
            "required_approvals": threshold,
            "request_expiry_minutes": 60,
            "approvers": approvers
            or [{"kind": "account", "ref": "owner-a"}, {"kind": "account", "ref": "admin-a"}],
            "reason": "Stage 13",
        },
    )


def _request(
    client: TestClient,
    policy_id: str,
    *,
    key: str = "request-create",
    actor: str = "member-a",
    snapshot: dict[str, Any] | None = None,
) -> Any:
    return client.post(
        "/api/enterprise/approvals/requests",
        headers=_headers(actor, key=key),
        json={
            "policy_id": policy_id,
            "resource_type": "catalog",
            "resource_id": "catalog-main",
            "snapshot": snapshot or {"revision": 24, "api_key": "sk_live_secret", "safe": "value"},
            "reason": "申请目录升级",
        },
    )


def _rows(engine: Any, table: str) -> list[dict[str, Any]]:
    with engine.connect() as c:
        return [dict(row) for row in c.execute(text(f"SELECT * FROM {table}")).mappings()]


def test_missing_0025_fails_closed_before_any_write(tmp_path: Path) -> None:
    engine = _engine(tmp_path, revision="0024_oidc_sso_runtime")
    client = _client(engine)
    try:
        response = _policy(client)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "approval_migration_required"
        assert not _table_exists(engine, "tenant_approval_policies")
        assert _rows(engine, "tenant_control_mutation_requests") == []
    finally:
        client.close()
        engine.dispose()


def test_policy_request_redaction_idempotency_and_tenant_read_model(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        first = _policy(client)
        assert first.status_code == 201, first.text
        replay = _policy(client)
        assert replay.status_code == 201 and replay.json() == first.json()
        conflict = _policy(client, threshold=1)
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "approval_idempotency_conflict"
        request = _request(client, first.json()["policy"]["id"])
        assert request.status_code == 201, request.text
        body = json.dumps(request.json(), ensure_ascii=False)
        assert "sk_live_secret" not in body
        assert request.json()["request"]["snapshot"]["api_key"] == "[REDACTED]"
        assert _request(client, first.json()["policy"]["id"]).json() == request.json()
        listed = client.get(
            "/api/enterprise/approvals/requests?mine=true&limit=20",
            headers=_headers("member-a", key="request-list"),
        )
        assert (
            listed.status_code == 200
            and listed.json()["items"][0]["id"] == request.json()["request"]["id"]
        )
        cross = client.get(
            "/api/enterprise/approvals/policies",
            headers=_headers("owner-b", tenant=TENANT_B, key="cross-read"),
        )
        assert cross.status_code == 200 and cross.json()["items"] == []
    finally:
        client.close()
        engine.dispose()


def test_approve_reject_threshold_no_self_and_decision_uniqueness(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        policy = _policy(
            client,
            key="decision-policy",
            threshold=2,
            approvers=[
                {"kind": "account", "ref": "member-a"},
                {"kind": "account", "ref": "admin-a"},
                {"kind": "account", "ref": "owner-a"},
            ],
        ).json()["policy"]
        request = _request(client, policy["id"], key="decision-request").json()["request"]
        self_decision = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/approve",
            headers=_headers("member-a", key="self"),
            json={"revision": 1, "comment": "self"},
        )
        assert self_decision.status_code == 403
        assert self_decision.json()["detail"]["code"] == "approval_self_decision_forbidden"
        approved_once = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/approve",
            headers=_headers("admin-a", key="approve-one"),
            json={"revision": 1, "comment": "通过"},
        )
        assert (
            approved_once.status_code == 200
            and approved_once.json()["request"]["received_approvals"] == 1
        )
        rejected = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/reject",
            headers=_headers("owner-a", key="reject"),
            json={"revision": 2, "comment": "风险存在"},
        )
        assert rejected.status_code == 200 and rejected.json()["request"]["status"] == "rejected"
        terminal = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/approve",
            headers=_headers("owner-a", key="after-reject"),
            json={"revision": 3, "comment": "late"},
        )
        assert (
            terminal.status_code == 409
            and terminal.json()["detail"]["code"] == "approval_request_terminal"
        )
    finally:
        client.close()
        engine.dispose()


def test_threshold_approval_issues_ticket_digest_only_and_audit_failure_rolls_back(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        policy = _policy(client, key="ticket-policy").json()["policy"]
        request = _request(client, policy["id"], key="ticket-request").json()["request"]
        client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/approve",
            headers=_headers("admin-a", key="ticket-one"),
            json={"revision": 1, "comment": "一审"},
        )
        final = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/approve",
            headers=_headers("owner-a", key="ticket-two"),
            json={"revision": 2, "comment": "二审"},
        )
        assert final.status_code == 200 and final.json()["request"]["status"] == "approved"
        ticket = final.json()["execution"]["ticket"]
        stored = next(
            row for row in _rows(engine, "tenant_approval_requests") if row["id"] == request["id"]
        )
        assert stored["execution_ticket_hash"] and ticket not in json.dumps(
            _rows(engine, "tenant_approval_requests"), ensure_ascii=False
        )
        replay = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/approve",
            headers=_headers("owner-a", key="ticket-two"),
            json={"revision": 2, "comment": "二审"},
        )
        assert replay.status_code == 200 and "ticket" not in replay.json()["execution"]

        control = _control()
        original = control._audit
        control._audit = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("audit down"))
        failed = _policy(client, key="audit-failure", action_type="membership_bootstrap")
        assert failed.status_code == 503
        assert not any(
            row["id"] == failed.json().get("policy", {}).get("id")
            for row in _rows(engine, "tenant_approval_policies")
        )
        control._audit = original
    finally:
        client.close()
        engine.dispose()


def test_requester_only_cancel_revision_terminal_replay_and_audit_rollback(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    control = _control()
    original_audit = control._audit
    try:
        policy = _policy(client, key="cancel-policy").json()["policy"]
        pending = _request(client, policy["id"], key="cancel-request").json()["request"]

        non_requester = client.post(
            f"/api/enterprise/approvals/requests/{pending['id']}/cancel",
            headers=_headers("admin-a", key="cancel-non-requester"),
            json={"revision": 1, "reason": "非申请人不能撤回"},
        )
        assert non_requester.status_code == 403
        assert non_requester.json()["detail"]["code"] == "approval_requester_required"

        stale = client.post(
            f"/api/enterprise/approvals/requests/{pending['id']}/cancel",
            headers=_headers("member-a", key="cancel-stale"),
            json={"revision": 9, "reason": "过期 revision"},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "approval_revision_conflict"

        cancelled = client.post(
            f"/api/enterprise/approvals/requests/{pending['id']}/cancel",
            headers=_headers("member-a", key="cancel-success"),
            json={"revision": 1, "reason": "申请人主动撤回"},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["request"]["status"] == "cancelled"
        assert cancelled.json()["request"]["revision"] == 2
        assert cancelled.json()["request"]["cancelled_by"] == "member-a"
        assert cancelled.json()["request"]["cancelled_at"]

        replay = client.post(
            f"/api/enterprise/approvals/requests/{pending['id']}/cancel",
            headers=_headers("member-a", key="cancel-success"),
            json={"revision": 1, "reason": "申请人主动撤回"},
        )
        assert replay.status_code == 200
        assert replay.json() == cancelled.json()

        mismatched_replay = client.post(
            f"/api/enterprise/approvals/requests/{pending['id']}/cancel",
            headers=_headers("member-a", key="cancel-success"),
            json={"revision": 1, "reason": "不同原因"},
        )
        assert mismatched_replay.status_code == 409
        assert mismatched_replay.json()["detail"]["code"] == "approval_idempotency_conflict"

        terminal = client.post(
            f"/api/enterprise/approvals/requests/{pending['id']}/cancel",
            headers=_headers("member-a", key="cancel-terminal"),
            json={"revision": 2, "reason": "不能重复取消"},
        )
        assert terminal.status_code == 409
        assert terminal.json()["detail"]["code"] == "approval_request_terminal"

        approved = _request(client, policy["id"], key="cancel-approved-request").json()["request"]
        client.post(
            f"/api/enterprise/approvals/requests/{approved['id']}/approve",
            headers=_headers("admin-a", key="cancel-approved-one"),
            json={"revision": 1, "comment": "一审"},
        )
        client.post(
            f"/api/enterprise/approvals/requests/{approved['id']}/approve",
            headers=_headers("owner-a", key="cancel-approved-two"),
            json={"revision": 2, "comment": "二审"},
        )
        approved_cancel = client.post(
            f"/api/enterprise/approvals/requests/{approved['id']}/cancel",
            headers=_headers("member-a", key="cancel-approved"),
            json={"revision": 3, "reason": "approved 不可取消"},
        )
        assert approved_cancel.status_code == 409
        assert approved_cancel.json()["detail"]["code"] == "approval_request_terminal"

        rejected = _request(client, policy["id"], key="cancel-rejected-request").json()["request"]
        client.post(
            f"/api/enterprise/approvals/requests/{rejected['id']}/reject",
            headers=_headers("admin-a", key="cancel-reject-decision"),
            json={"revision": 1, "comment": "拒绝"},
        )
        rejected_cancel = client.post(
            f"/api/enterprise/approvals/requests/{rejected['id']}/cancel",
            headers=_headers("member-a", key="cancel-rejected"),
            json={"revision": 2, "reason": "rejected 不可取消"},
        )
        assert rejected_cancel.status_code == 409
        assert rejected_cancel.json()["detail"]["code"] == "approval_request_terminal"

        rollback_request = _request(client, policy["id"], key="cancel-rollback-request").json()[
            "request"
        ]
        ledger_before = len(_rows(engine, "tenant_control_mutation_requests"))
        control._audit = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("cancel audit unavailable")
        )
        rollback = client.post(
            f"/api/enterprise/approvals/requests/{rollback_request['id']}/cancel",
            headers=_headers("member-a", key="cancel-audit-failure"),
            json={"revision": 1, "reason": "触发审计回滚"},
        )
        assert rollback.status_code == 503
        stored = next(
            row
            for row in _rows(engine, "tenant_approval_requests")
            if row["id"] == rollback_request["id"]
        )
        assert stored["status"] == "pending"
        assert stored["revision"] == 1
        assert stored["cancelled_at"] is None
        assert stored["cancelled_by"] is None
        assert len(_rows(engine, "tenant_control_mutation_requests")) == ledger_before
    finally:
        control._audit = original_audit
        client.close()
        engine.dispose()


def test_consume_ticket_scope_revision_tenant_and_second_consumption(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    adapter_calls: list[dict[str, Any]] = []
    client = _client(engine, execution_adapter=lambda payload: adapter_calls.append(dict(payload)))
    try:
        policy = _policy(client, key="consume-policy").json()["policy"]
        request = _request(client, policy["id"], key="consume-request").json()["request"]
        client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/approve",
            headers=_headers("admin-a", key="consume-approve-one"),
            json={"revision": 1, "comment": "一审"},
        )
        approved = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/approve",
            headers=_headers("owner-a", key="consume-approve-two"),
            json={"revision": 2, "comment": "二审"},
        ).json()
        ticket = approved["execution"]["ticket"]
        valid_body = {
            "ticket": ticket,
            "revision": 3,
            "action_type": "catalog_upgrade",
            "resource_type": "catalog",
            "resource_id": "catalog-main",
        }

        wrong_tenant = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("owner-b", tenant=TENANT_B, key="consume-wrong-tenant"),
            json=valid_body,
        )
        assert wrong_tenant.status_code == 404
        assert wrong_tenant.json()["detail"]["code"] == "approval_resource_not_found"

        for key, changed_field, changed_value in (
            ("consume-wrong-action", "action_type", "dataset_acl_disable"),
            ("consume-wrong-resource-type", "resource_type", "dataset"),
            ("consume-wrong-resource-id", "resource_id", "catalog-other"),
        ):
            body = {**valid_body, changed_field: changed_value}
            response = client.post(
                f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
                headers=_headers("admin-a", key=key),
                json=body,
            )
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "approval_ticket_scope_mismatch"

        wrong_revision = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("admin-a", key="consume-wrong-revision"),
            json={**valid_body, "revision": 99},
        )
        assert wrong_revision.status_code == 409
        assert wrong_revision.json()["detail"]["code"] == "approval_revision_conflict"

        wrong_ticket = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("admin-a", key="consume-wrong-ticket"),
            json={**valid_body, "ticket": "rag4c-approval-ticket-invalid"},
        )
        assert wrong_ticket.status_code == 409
        assert wrong_ticket.json()["detail"]["code"] == "approval_ticket_invalid"

        consumed = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("admin-a", key="consume-success"),
            json=valid_body,
        )
        assert consumed.status_code == 200, consumed.text
        assert consumed.json()["request"]["status"] == "executed"
        assert consumed.json()["request"]["revision"] == 5
        assert len(adapter_calls) == 1

        replay = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("admin-a", key="consume-success"),
            json=valid_body,
        )
        assert replay.status_code == 200
        assert replay.json() == consumed.json()
        assert len(adapter_calls) == 1

        second_key = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("admin-a", key="consume-second-key"),
            json={**valid_body, "revision": 4},
        )
        assert second_key.status_code == 409
        assert second_key.json()["detail"]["code"] == "approval_ticket_not_consumable"
        assert len(adapter_calls) == 1
    finally:
        client.close()
        engine.dispose()


def _set_request_created_at(engine: Any, request_id: str, created_at: datetime) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE tenant_approval_requests SET created_at=:created_at, updated_at=:created_at "
                "WHERE tenant_id=:tenant_id AND id=:request_id"
            ),
            {"created_at": created_at, "tenant_id": TENANT_A, "request_id": request_id},
        )


def _approve_ticket(
    client: TestClient,
    policy_id: str,
    *,
    request_key: str,
    first_actor: str = "admin-a",
    second_actor: str = "owner-a",
) -> tuple[dict[str, Any], str]:
    request = _request(client, policy_id, key=request_key).json()["request"]
    first = client.post(
        f"/api/enterprise/approvals/requests/{request['id']}/approve",
        headers=_headers(first_actor, key=f"{request_key}-approve-one"),
        json={"revision": 1, "comment": "一审"},
    )
    assert first.status_code == 200, first.text
    final = client.post(
        f"/api/enterprise/approvals/requests/{request['id']}/approve",
        headers=_headers(second_actor, key=f"{request_key}-approve-two"),
        json={"revision": 2, "comment": "二审"},
    )
    assert final.status_code == 200, final.text
    approved = final.json()["request"]
    ticket = final.json()["execution"]["ticket"]
    assert approved["status"] == "approved"
    assert approved["revision"] == 3
    return approved, ticket


def test_real_0025_migration_orm_contract_supports_full_lifecycle(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    adapter_calls: list[dict[str, Any]] = []
    client = _client(engine, execution_adapter=lambda payload: adapter_calls.append(dict(payload)))
    try:
        inspector = inspect(engine)
        for table_name in (
            "tenant_approval_policies",
            "tenant_approval_policy_approvers",
            "tenant_approval_requests",
            "tenant_approval_decisions",
        ):
            assert set(Base.metadata.tables[table_name].columns.keys()) <= {
                column["name"] for column in inspector.get_columns(table_name)
            }

        policy_response = _policy(
            client,
            key="real-contract-policy",
            threshold=2,
            approvers=[
                {"kind": "account", "ref": "owner-a"},
                {"kind": "group", "ref": "approval-group-a"},
            ],
        )
        assert policy_response.status_code == 201, policy_response.text
        policy = policy_response.json()["policy"]
        policy_row = next(
            row for row in _rows(engine, "tenant_approval_policies") if row["id"] == policy["id"]
        )
        assert policy_row["active_scope_key"]
        approver_rows = [
            row
            for row in _rows(engine, "tenant_approval_policy_approvers")
            if row["policy_id"] == policy["id"]
        ]
        assert {row["account_id"] for row in approver_rows} == {"owner-a", None}
        assert {row["group_id"] for row in approver_rows} == {"approval-group-a", None}

        approved, ticket = _approve_ticket(
            client,
            policy["id"],
            request_key="real-contract-request",
            first_actor="owner-a",
            second_actor="reviewer-a",
        )
        decision_rows = [
            row
            for row in _rows(engine, "tenant_approval_decisions")
            if row["request_id"] == approved["id"]
        ]
        assert len(decision_rows) == 2
        assert all(row["created_at"] is not None and row["created_by"] for row in decision_rows)

        consumed = client.post(
            f"/api/enterprise/approvals/requests/{approved['id']}/consume-ticket",
            headers=_headers("admin-a", key="real-contract-consume"),
            json={
                "ticket": ticket,
                "revision": 3,
                "action_type": "catalog_upgrade",
                "resource_type": "catalog",
                "resource_id": "catalog-main",
            },
        )
        assert consumed.status_code == 200, consumed.text
        assert consumed.json()["request"]["status"] == "executed"
        assert consumed.json()["request"]["revision"] == 5
        stored = next(
            row for row in _rows(engine, "tenant_approval_requests") if row["id"] == approved["id"]
        )
        assert stored["executed_by"] == "admin-a"
        assert stored["created_by"] == "member-a"
        assert stored["updated_by"] == "admin-a"
        assert len(adapter_calls) == 1
    finally:
        client.close()
        engine.dispose()


def test_stamped_0025_without_authoritative_index_fails_closed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX ix_tenant_approval_requests_tenant_status_expiry"))
        response = client.get(
            "/api/enterprise/approvals/requests",
            headers=_headers("admin-a", key="missing-authoritative-index"),
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "approval_migration_required"
    finally:
        client.close()
        engine.dispose()


def test_fake_0025_revision_fails_closed_even_with_real_tables(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num='0025_fake_revision'"))
        response = _policy(client, key="fake-revision")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "approval_migration_required"
        assert _rows(engine, "tenant_approval_policies") == []
    finally:
        client.close()
        engine.dispose()


def test_expired_request_is_persisted_audited_and_filtered(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        policy = _policy(client, key="expiry-policy", threshold=1).json()["policy"]
        request = _request(client, policy["id"], key="expiry-request").json()["request"]
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE tenant_approval_requests SET expires_at=:expires_at "
                    "WHERE tenant_id=:tenant_id AND id=:request_id"
                ),
                {
                    "expires_at": NOW - timedelta(seconds=1),
                    "tenant_id": TENANT_A,
                    "request_id": request["id"],
                },
            )

        expired = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/cancel",
            headers=_headers("member-a", key="expiry-cancel"),
            json={"revision": 1, "reason": "过期申请"},
        )
        assert expired.status_code == 409
        assert expired.json()["detail"]["code"] == "approval_request_expired"
        stored = next(
            row for row in _rows(engine, "tenant_approval_requests") if row["id"] == request["id"]
        )
        assert stored["status"] == "expired"
        assert stored["revision"] == 2
        assert stored["updated_by"] == "member-a"
        audit_rows = [
            row
            for row in _rows(engine, "tenant_audit_events")
            if row["action"] == "approval.request.expired" and row["resource_id"] == request["id"]
        ]
        assert len(audit_rows) == 1

        pending = client.get(
            "/api/enterprise/approvals/requests?status=pending",
            headers=_headers("admin-a", key="expiry-pending-list"),
        )
        assert pending.status_code == 200
        assert pending.json()["items"] == []
        listed_expired = client.get(
            "/api/enterprise/approvals/requests?status=expired",
            headers=_headers("admin-a", key="expiry-expired-list"),
        )
        assert listed_expired.status_code == 200
        assert [item["id"] for item in listed_expired.json()["items"]] == [request["id"]]
    finally:
        client.close()
        engine.dispose()


def test_active_scope_and_approver_identity_are_maintained_across_policy_lifecycle(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        policy = _policy(
            client,
            key="scope-policy",
            threshold=1,
            approvers=[{"kind": "account", "ref": "owner-a"}],
        ).json()["policy"]
        before = next(
            row for row in _rows(engine, "tenant_approval_policies") if row["id"] == policy["id"]
        )
        assert before["active_scope_key"]
        original_scope_key = before["active_scope_key"]

        updated = client.patch(
            f"/api/enterprise/approvals/policies/{policy['id']}",
            headers=_headers("admin-a", key="scope-policy-update"),
            json={
                "revision": 1,
                "resource_scope": "catalog:secondary:*",
                "reason": "调整资源范围",
            },
        )
        assert updated.status_code == 200, updated.text
        after_update = next(
            row for row in _rows(engine, "tenant_approval_policies") if row["id"] == policy["id"]
        )
        assert after_update["revision"] == 2
        assert after_update["active_scope_key"] != original_scope_key

        duplicate = _policy(
            client,
            key="scope-policy-duplicate",
            threshold=1,
            approvers=[{"kind": "account", "ref": "owner-a"}],
            resource_scope="catalog:secondary:*",
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"]["code"] == "approval_policy_active_conflict"

        disabled = client.post(
            f"/api/enterprise/approvals/policies/{policy['id']}/disable",
            headers=_headers("admin-a", key="scope-policy-disable"),
            json={"revision": 2, "reason": "规则下线"},
        )
        assert disabled.status_code == 200, disabled.text
        after_disable = next(
            row for row in _rows(engine, "tenant_approval_policies") if row["id"] == policy["id"]
        )
        assert after_disable["status"] == "disabled"
        assert after_disable["active_scope_key"] is None
        assert after_disable["revision"] == 3
        approver = next(
            row
            for row in _rows(engine, "tenant_approval_policy_approvers")
            if row["policy_id"] == policy["id"]
        )
        assert approver["account_id"] == "owner-a"
        assert approver["group_id"] is None
        assert approver["status"] == "disabled"
        assert approver["revision"] == 3
    finally:
        client.close()
        engine.dispose()


def test_snapshot_redaction_covers_database_urls_jwt_and_invitation_links(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        policy = _policy(client, key="redaction-policy", threshold=1).json()["policy"]
        snapshot = {
            "db_url": "mysql+pymysql://reader:db-secret@db.internal/knowledge?password=db-secret",
            "connection_string": "postgresql://writer:connection-secret@db.internal/knowledge",
            "url": "https://app.example/invitations/accept/opaque-invitation-secret",
            "value": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.jwt-signature",
            "nested": {"safe": "value"},
        }
        request = _request(
            client,
            policy["id"],
            key="redaction-request",
            snapshot=snapshot,
        )
        assert request.status_code == 201, request.text
        projected = request.json()["request"]["snapshot"]
        assert projected["db_url"] == "[REDACTED]"
        assert projected["connection_string"] == "[REDACTED]"
        assert projected["url"] == "[REDACTED]"
        assert projected["value"] == "[REDACTED]"
        assert projected["nested"]["safe"] == "value"
        all_rows = json.dumps(
            _rows(engine, "tenant_approval_requests") + _rows(engine, "tenant_audit_events"),
            ensure_ascii=False,
        )
        for secret in (
            "db-secret",
            "connection-secret",
            "opaque-invitation-secret",
            "jwt-signature",
        ):
            assert secret not in all_rows
    finally:
        client.close()
        engine.dispose()


def test_pending_for_me_pagination_filters_before_limit(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine)
    try:
        not_for_me = _policy(
            client,
            key="pagination-not-me-policy",
            threshold=1,
            approvers=[{"kind": "account", "ref": "owner-a"}],
            action_type="catalog_upgrade",
        ).json()["policy"]
        for_me = _policy(
            client,
            key="pagination-for-me-policy",
            threshold=1,
            approvers=[{"kind": "account", "ref": "admin-a"}],
            action_type="membership_bootstrap",
        ).json()["policy"]
        not_for_me_ids: list[str] = []
        for index in range(2):
            item = _request(
                client,
                not_for_me["id"],
                key=f"pagination-not-me-request-{index}",
            ).json()["request"]
            not_for_me_ids.append(item["id"])
            _set_request_created_at(engine, item["id"], NOW - timedelta(minutes=index))
        for_me_ids: list[str] = []
        for index in range(3):
            item = _request(
                client,
                for_me["id"],
                key=f"pagination-for-me-request-{index}",
            ).json()["request"]
            for_me_ids.append(item["id"])
            _set_request_created_at(engine, item["id"], NOW - timedelta(minutes=30 + index))

        first = client.get(
            "/api/enterprise/approvals/requests?pending_for_me=true&limit=2",
            headers=_headers("admin-a", key="pagination-first"),
        )
        assert first.status_code == 200, first.text
        first_items = first.json()["items"]
        assert len(first_items) == 2
        assert {item["id"] for item in first_items} <= set(for_me_ids)
        assert not {item["id"] for item in first_items} & set(not_for_me_ids)
        assert first.json()["next_cursor"]

        second = client.get(
            "/api/enterprise/approvals/requests"
            f"?pending_for_me=true&limit=2&cursor={first.json()['next_cursor']}",
            headers=_headers("admin-a", key="pagination-second"),
        )
        assert second.status_code == 200, second.text
        second_items = second.json()["items"]
        assert len(second_items) == 1
        assert {item["id"] for item in second_items} <= set(for_me_ids)
        assert {item["id"] for item in first_items + second_items} == set(for_me_ids)
    finally:
        client.close()
        engine.dispose()


def test_consume_ticket_claims_before_external_adapter_and_passes_stable_identity(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    adapter_calls: list[dict[str, Any]] = []
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE adapter_probe (execution_id VARCHAR(128) PRIMARY KEY)")
        )

    def adapter(payload: dict[str, Any]) -> None:
        adapter_calls.append(dict(payload))
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA busy_timeout=50")
            connection.execute(
                text("INSERT INTO adapter_probe(execution_id) VALUES (:execution_id)"),
                {"execution_id": payload["execution_id"]},
            )
            connection.commit()

    client = _client(engine, execution_adapter=adapter)
    try:
        policy = _policy(client, key="adapter-boundary-policy").json()["policy"]
        approved, ticket = _approve_ticket(
            client, policy["id"], request_key="adapter-boundary-request"
        )
        response = client.post(
            f"/api/enterprise/approvals/requests/{approved['id']}/consume-ticket",
            headers=_headers("admin-a", key="adapter-boundary-consume"),
            json={
                "ticket": ticket,
                "revision": 3,
                "action_type": "catalog_upgrade",
                "resource_type": "catalog",
                "resource_id": "catalog-main",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["request"]["status"] == "executed"
        assert len(adapter_calls) == 1
        assert adapter_calls[0]["request_id"] == approved["id"]
        assert adapter_calls[0]["execution_id"].startswith("approval-execution-")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM adapter_probe")).scalar_one() == 1
    finally:
        client.close()
        engine.dispose()


def test_adapter_exception_persists_execution_failed_without_repeating_adapter(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    adapter_calls: list[dict[str, Any]] = []

    def adapter(payload: dict[str, Any]) -> None:
        adapter_calls.append(dict(payload))
        raise RuntimeError("downstream unavailable")

    client = _client(engine, execution_adapter=adapter)
    try:
        policy = _policy(client, key="adapter-failure-policy").json()["policy"]
        approved, ticket = _approve_ticket(
            client, policy["id"], request_key="adapter-failure-request"
        )
        body = {
            "ticket": ticket,
            "revision": 3,
            "action_type": "catalog_upgrade",
            "resource_type": "catalog",
            "resource_id": "catalog-main",
        }
        response = client.post(
            f"/api/enterprise/approvals/requests/{approved['id']}/consume-ticket",
            headers=_headers("admin-a", key="adapter-failure-consume"),
            json=body,
        )
        assert response.status_code == 502, response.text
        assert response.json()["request"]["status"] == "execution_failed"
        stored = next(
            row for row in _rows(engine, "tenant_approval_requests") if row["id"] == approved["id"]
        )
        assert stored["status"] == "execution_failed"
        assert stored["execution_failed_by"] == "admin-a"
        assert stored["execution_failed_at"] is not None
        assert stored["execution_error"]
        assert stored["ticket_consumed_at"] is not None
        assert len(adapter_calls) == 1

        replay = client.post(
            f"/api/enterprise/approvals/requests/{approved['id']}/consume-ticket",
            headers=_headers("admin-a", key="adapter-failure-consume"),
            json=body,
        )
        assert replay.status_code == 502
        assert replay.json() == response.json()
        assert len(adapter_calls) == 1

        second_key = client.post(
            f"/api/enterprise/approvals/requests/{approved['id']}/consume-ticket",
            headers=_headers("admin-a", key="adapter-failure-second-key"),
            json={**body, "revision": 5},
        )
        assert second_key.status_code == 409
        assert second_key.json()["detail"]["code"] == "approval_ticket_not_consumable"
        assert len(adapter_calls) == 1
    finally:
        client.close()
        engine.dispose()


def test_final_audit_failure_keeps_executing_and_never_repeats_adapter(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    adapter_calls: list[dict[str, Any]] = []

    def adapter(payload: dict[str, Any]) -> None:
        adapter_calls.append(dict(payload))

    control = _control()
    original_audit = control._audit

    def fail_final_audit(*args: Any, **kwargs: Any) -> None:
        if kwargs.get("action") == "approval.ticket.consumed":
            raise RuntimeError("final audit unavailable")
        original_audit(*args, **kwargs)

    control._audit = fail_final_audit
    client = _client(engine, execution_adapter=adapter)
    try:
        policy = _policy(client, key="audit-final-policy").json()["policy"]
        approved, ticket = _approve_ticket(client, policy["id"], request_key="audit-final-request")
        body = {
            "ticket": ticket,
            "revision": 3,
            "action_type": "catalog_upgrade",
            "resource_type": "catalog",
            "resource_id": "catalog-main",
        }
        response = client.post(
            f"/api/enterprise/approvals/requests/{approved['id']}/consume-ticket",
            headers=_headers("admin-a", key="audit-final-consume"),
            json=body,
        )
        assert response.status_code == 503, response.text
        stored = next(
            row for row in _rows(engine, "tenant_approval_requests") if row["id"] == approved["id"]
        )
        assert stored["status"] == "executing"
        assert stored["revision"] == 4
        assert stored["executed_at"] is None
        assert stored["executed_by"] is None
        assert len(adapter_calls) == 1

        replay = client.post(
            f"/api/enterprise/approvals/requests/{approved['id']}/consume-ticket",
            headers=_headers("admin-a", key="audit-final-consume"),
            json=body,
        )
        assert replay.status_code == 409
        assert replay.json()["detail"]["code"] == "approval_idempotency_in_progress"
        assert len(adapter_calls) == 1

        second_key = client.post(
            f"/api/enterprise/approvals/requests/{approved['id']}/consume-ticket",
            headers=_headers("admin-a", key="audit-final-second-key"),
            json={**body, "revision": 4},
        )
        assert second_key.status_code == 409
        assert second_key.json()["detail"]["code"] == "approval_ticket_not_consumable"
        assert len(adapter_calls) == 1
    finally:
        control._audit = original_audit
        client.close()
        engine.dispose()


__all__ = [name for name in globals() if name.startswith("test_")]


def test_known_successor_0026_keeps_approval_authority_available(tmp_path: Path) -> None:
    engine = _engine(tmp_path, revision="0026_enterprise_workspace_control")
    client = _client(engine)
    try:
        response = _policy(client, key="approval-known-successor-0026")
        assert response.status_code == 201, response.text
        assert response.json()["policy"]["action_type"] == "catalog_upgrade"
    finally:
        client.close()
        engine.dispose()


def test_real_0027_schema_supports_workspace_authorization_approval_requests(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path, revision="0027_enterprise_workspace_authorization")
    client = _client(engine)
    try:
        policy_response = _policy(
            client,
            key="approval-real-0027-policy",
            threshold=1,
            action_type="workspace_authorization_mode_change",
            resource_scope="tenant_workspace:workspace-real-0027",
        )
        assert policy_response.status_code == 201, policy_response.text
        policy = policy_response.json()["policy"]
        request = client.post(
            "/api/enterprise/approvals/requests",
            headers=_headers("member-a", key="approval-real-0027-request"),
            json={
                "policy_id": policy["id"],
                "resource_type": "tenant_workspace",
                "resource_id": "workspace-real-0027",
                "snapshot": {
                    "workspace_id": "workspace-real-0027",
                    "workspace_revision": 1,
                    "policy_revision": 1,
                    "from_mode": "shadow",
                    "target_mode": "enforced",
                    "permission_model_version": 1,
                    "permission_matrix_fingerprint": "a" * 64,
                    "reason": "Enable enforced Workspace authorization",
                },
                "reason": "Enable enforced Workspace authorization",
            },
        )
        assert request.status_code == 201, request.text
        assert request.json()["request"]["action_type"] == ("workspace_authorization_mode_change")
        assert request.json()["request"]["resource_type"] == "tenant_workspace"
    finally:
        client.close()
        engine.dispose()
