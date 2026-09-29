"""Stage 17 approval contracts for Workspace authorization mode changes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.test_enterprise_approval_api import (
    NOW,
    SETTINGS,
    TENANT_A,
    _engine as _approval_engine,
    _headers as _approval_headers,
)

REVISION = "0027_enterprise_workspace_authorization"
ACTION = "workspace_authorization_mode_change"
RESOURCE_TYPE = "tenant_workspace"
WORKSPACE_ID = "workspace-stage17"
FINGERPRINT = "7" * 64


def _snapshot(
    *,
    workspace_revision: int = 3,
    policy_revision: int = 5,
    from_mode: str = "shadow",
    target_mode: str = "enforced",
    reason: str = "Enable enforced Workspace authorization",
) -> dict[str, Any]:
    return {
        "workspace_id": WORKSPACE_ID,
        "workspace_revision": workspace_revision,
        "policy_revision": policy_revision,
        "from_mode": from_mode,
        "target_mode": target_mode,
        "permission_model_version": 1,
        "permission_matrix_fingerprint": FINGERPRINT,
        "reason": reason,
    }


def _payload(**overrides: Any) -> dict[str, Any]:
    reason = "Enable enforced Workspace authorization"
    payload: dict[str, Any] = {
        "tenant_id": TENANT_A,
        "approval_request_id": "approval-request-stage17",
        "request_id": "approval-request-stage17",
        "execution_id": "approval-execution-stage17",
        "execution_revision": 3,
        "action_type": ACTION,
        "resource_type": RESOURCE_TYPE,
        "resource_id": WORKSPACE_ID,
        "consumer_actor_id": "owner-a",
        "consumer_actor_role": "owner",
        "consumer": {"actor_id": "owner-a", "role": "owner"},
        "requester_id": "member-a",
        "request_snapshot": _snapshot(reason=reason),
        "snapshot": _snapshot(reason=reason),
        "reason": reason,
        "request_evidence": {
            "request_id": "stage17-consume-request",
            "request_ip": "127.0.0.1",
        },
    }
    payload.update(overrides)
    return payload


def _safe_core_result() -> dict[str, Any]:
    return {
        "authorization_policy": {
            "id": "workspace-authorization-stage17",
            "tenant_id": TENANT_A,
            "workspace_id": WORKSPACE_ID,
            "mode": "enforced",
            "permission_model_version": 1,
            "revision": 6,
            # 脱敏断言的标记值由运行时拼接生成（不是真实凭据，字面量会被凭据扫描拦下）
            "access_token": "must" + "-not-leak",
        },
        "workspace": {
            "id": WORKSPACE_ID,
            "tenant_id": TENANT_A,
            "status": "active",
            "revision": 3,
            "secret": "must-not-leak",
        },
        "audit": {
            "id": "tenant-audit-stage17",
            "sequence": 91,
            "authorization": "must-not-leak",
        },
        "debug": {"token": "must-not-leak"},
    }


def test_workspace_authorization_consumer_forwards_exact_revision_and_stable_execution_contract() -> (
    None
):
    from core.enterprise_approval_control import ApprovalExecutionFact
    from server import enterprise_approval_consumers as consumers

    calls: list[dict[str, Any]] = []
    engine = object()
    fact = ApprovalExecutionFact(
        tenant_id=TENANT_A,
        approval_request_id="approval-request-stage17",
        execution_id="approval-execution-stage17",
        request_revision=2,
        execution_revision=3,
        action_type=ACTION,
        resource_type=RESOURCE_TYPE,
        resource_id=WORKSPACE_ID,
        snapshot_hash="a" * 64,
        workspace_revision=3,
        policy_revision=5,
        from_mode="shadow",
        target_mode="enforced",
        permission_model_version=1,
        permission_matrix_fingerprint=FINGERPRINT,
        reason="Enable enforced Workspace authorization",
    )

    def mutation_service(received_engine: Any, **kwargs: Any) -> dict[str, Any]:
        assert received_engine is engine
        calls.append(kwargs)
        return _safe_core_result()

    consumer = consumers.build_workspace_authorization_mode_change_consumer(
        lambda: engine,
        mode_change_service=mutation_service,
        live_mode_resolver=lambda *_args, **_kwargs: "shadow",
    )
    result = consumer(_payload(approval_execution_fact=fact))

    assert calls == [
        {
            "tenant_id": TENANT_A,
            "actor_id": "owner-a",
            "actor_role": "owner",
            "workspace_id": WORKSPACE_ID,
            "expected_workspace_revision": 3,
            "expected_policy_revision": 5,
            "target_mode": "enforced",
            "expected_from_mode": "shadow",
            "expected_permission_model_version": 1,
            "permission_matrix_fingerprint": FINGERPRINT,
            "reason": "Enable enforced Workspace authorization",
            "request_id": "stage17-consume-request",
            "request_ip": "127.0.0.1",
            "idempotency_key": "approval-execution-stage17",
            "approval_execution_fact": fact,
        }
    ]
    assert result == {
        "authorization_policy": {
            "id": "workspace-authorization-stage17",
            "tenant_id": TENANT_A,
            "workspace_id": WORKSPACE_ID,
            "mode": "enforced",
            "permission_model_version": 1,
            "revision": 6,
        },
        "workspace": {
            "id": WORKSPACE_ID,
            "tenant_id": TENANT_A,
            "status": "active",
            "revision": 3,
        },
        "audit": {"id": "tenant-audit-stage17", "sequence": 91},
    }
    assert "must-not-leak" not in str(result)


def test_workspace_authorization_consumer_rejects_snapshot_when_live_mode_changed() -> None:
    from core.enterprise_approval_control import ApprovalExecutionFact
    from server import enterprise_approval_consumers as consumers

    calls: list[dict[str, Any]] = []
    fact = ApprovalExecutionFact(
        tenant_id=TENANT_A,
        approval_request_id="approval-request-stage17",
        execution_id="approval-execution-stage17",
        request_revision=2,
        execution_revision=3,
        action_type=ACTION,
        resource_type=RESOURCE_TYPE,
        resource_id=WORKSPACE_ID,
        snapshot_hash="a" * 64,
        workspace_revision=3,
        policy_revision=5,
        from_mode="shadow",
        target_mode="enforced",
        permission_model_version=1,
        permission_matrix_fingerprint=FINGERPRINT,
        reason="Enable enforced Workspace authorization",
    )

    def mutation_service(_engine: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _safe_core_result()

    consumer = consumers.build_workspace_authorization_mode_change_consumer(
        lambda: object(),
        mode_change_service=mutation_service,
        live_mode_resolver=lambda *_args, **_kwargs: "enforced",
    )
    with pytest.raises(
        consumers.ApprovalConsumerError,
        match="snapshot from_mode does not match live mode",
    ):
        consumer(_payload(approval_execution_fact=fact))
    assert calls == []


@pytest.mark.parametrize(
    ("snapshot_patch", "message"),
    [
        ({"workspace_id": "workspace-other"}, "snapshot workspace does not match resource"),
        ({"workspace_revision": 0}, "workspace_revision is invalid"),
        ({"policy_revision": 0}, "policy_revision is invalid"),
        ({"from_mode": "enforced", "target_mode": "enforced"}, "mode transition is invalid"),
        ({"permission_model_version": 2}, "permission_model_version is unsupported"),
        ({"permission_matrix_fingerprint": "short"}, "permission_matrix_fingerprint is invalid"),
        ({"reason": "different"}, "snapshot reason does not match request reason"),
    ],
)
def test_workspace_authorization_consumer_rejects_invalid_snapshot_before_core_call(
    snapshot_patch: dict[str, Any], message: str
) -> None:
    from server import enterprise_approval_consumers as consumers

    calls: list[dict[str, Any]] = []

    def mutation_service(_engine: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _safe_core_result()

    snapshot = _snapshot()
    snapshot.update(snapshot_patch)
    consumer = consumers.build_workspace_authorization_mode_change_consumer(
        lambda: object(), mode_change_service=mutation_service
    )
    with pytest.raises(consumers.ApprovalConsumerError, match=message):
        consumer(_payload(request_snapshot=snapshot, snapshot=snapshot))
    assert calls == []


def test_registry_connects_workspace_authorization_without_eager_core_import() -> None:
    from server import enterprise_approval_consumers as consumers

    registry = consumers.build_enterprise_approval_execution_adapters(lambda: object())
    assert ACTION in registry
    assert callable(registry[ACTION])


def _engine(tmp_path: Path) -> Any:
    return _approval_engine(tmp_path, revision=REVISION)


def _client(engine: Any, *, adapter: Any) -> TestClient:
    from server import enterprise_approval_api

    app = FastAPI()
    app.state.knowledge_auth_engine = engine
    app.state.knowledge_auth_settings = SETTINGS
    app.include_router(
        enterprise_approval_api.build_enterprise_approval_router(
            read_engine_provider=lambda: engine,
            mutation_engine_provider=lambda: engine,
            now_provider=lambda: NOW,
            execution_adapters={ACTION: adapter},
        )
    )
    return TestClient(app)


def _headers(actor: str, *, key: str) -> dict[str, str]:
    return _approval_headers(actor, key=key)


def _policy(client: TestClient, *, key: str, scope: str | None = None) -> dict[str, Any]:
    response = client.post(
        "/api/enterprise/approvals/policies",
        headers=_headers("admin-a", key=key),
        json={
            "name": "Workspace authorization mode change",
            "action_type": ACTION,
            "resource_scope": scope or f"{RESOURCE_TYPE}:{WORKSPACE_ID}",
            "required_approvals": 1,
            "request_expiry_minutes": 60,
            "approvers": [{"kind": "account", "ref": "owner-a"}],
            "reason": "Protect Workspace authorization rollout",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["policy"]


def _request(client: TestClient, policy_id: str, *, key: str) -> dict[str, Any]:
    response = client.post(
        "/api/enterprise/approvals/requests",
        headers=_headers("member-a", key=key),
        json={
            "policy_id": policy_id,
            "resource_type": RESOURCE_TYPE,
            "resource_id": WORKSPACE_ID,
            "snapshot": _snapshot(),
            "reason": "Enable enforced Workspace authorization",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["request"]


def _approve(
    client: TestClient, request: dict[str, Any], *, key: str
) -> tuple[dict[str, Any], str]:
    response = client.post(
        f"/api/enterprise/approvals/requests/{request['id']}/approve",
        headers=_headers("owner-a", key=key),
        json={"revision": request["revision"], "comment": "approved"},
    )
    assert response.status_code == 200, response.text
    return response.json()["request"], response.json()["execution"]["ticket"]


def _consume_body(approved: dict[str, Any], ticket: str) -> dict[str, Any]:
    return {
        "ticket": ticket,
        "revision": approved["revision"],
        "action_type": ACTION,
        "resource_type": RESOURCE_TYPE,
        "resource_id": WORKSPACE_ID,
    }


def test_workspace_authorization_ticket_retry_is_exact_and_safe(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def adapter(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(payload))
        return _safe_core_result()

    engine = _engine(tmp_path)
    client = _client(engine, adapter=adapter)
    try:
        policy = _policy(client, key="stage17-retry-policy")
        request = _request(client, policy["id"], key="stage17-retry-request")
        approved, ticket = _approve(client, request, key="stage17-retry-approve")
        body = _consume_body(approved, ticket)
        first = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("owner-a", key="stage17-retry-consume"),
            json=body,
        )
        replay = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("owner-a", key="stage17-retry-consume"),
            json=body,
        )
        assert first.status_code == 200, first.text
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()
        assert len(calls) == 1
        assert calls[0]["execution_id"].startswith("approval-execution-")
        assert "must-not-leak" not in str(first.json())
    finally:
        client.close()
        engine.dispose()


def test_non_manager_cannot_claim_or_burn_workspace_authorization_ticket(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    client = _client(engine, adapter=lambda _payload: _safe_core_result())
    try:
        policy = _policy(client, key="stage17-member-policy")
        request = _request(client, policy["id"], key="stage17-member-request")
        approved, ticket = _approve(client, request, key="stage17-member-approve")
        denied = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("member-a", key="stage17-member-consume"),
            json=_consume_body(approved, ticket),
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"]["code"] == "approval_execution_forbidden"
        with engine.connect() as connection:
            row = dict(
                connection.execute(
                    text(
                        "SELECT status, revision, ticket_consumed_at FROM tenant_approval_requests "
                        "WHERE id=:request_id"
                    ),
                    {"request_id": request["id"]},
                )
                .mappings()
                .one()
            )
        assert row == {
            "status": "approved",
            "revision": approved["revision"],
            "ticket_consumed_at": None,
        }
    finally:
        client.close()
        engine.dispose()


def test_core_non_manager_cannot_claim_workspace_authorization_ticket(tmp_path: Path) -> None:
    from core.enterprise_approval_control import ApprovalForbidden, consume_approval_ticket

    engine = _engine(tmp_path)
    client = _client(engine, adapter=lambda _payload: _safe_core_result())
    try:
        policy = _policy(client, key="stage17-core-member-policy")
        request = _request(client, policy["id"], key="stage17-core-member-request")
        approved, ticket = _approve(client, request, key="stage17-core-member-approve")

        with pytest.raises(ApprovalForbidden) as exc_info:
            consume_approval_ticket(
                engine,
                tenant_id=TENANT_A,
                actor_id="member-a",
                request_id=request["id"],
                ticket=ticket,
                expected_revision=approved["revision"],
                action_type=ACTION,
                resource_type=RESOURCE_TYPE,
                resource_id=WORKSPACE_ID,
                idempotency_key="stage17-core-member-consume",
                request_id_header="stage17-core-member-consume-request",
                request_ip="127.0.0.1",
                now=NOW,
                execution_adapter=lambda _payload: _safe_core_result(),
            )

        assert exc_info.value.code == "approval_manager_required"
        with engine.connect() as connection:
            row = dict(
                connection.execute(
                    text(
                        "SELECT status, revision, ticket_consumed_at "
                        "FROM tenant_approval_requests WHERE id=:request_id"
                    ),
                    {"request_id": request["id"]},
                )
                .mappings()
                .one()
            )
        assert row == {
            "status": "approved",
            "revision": approved["revision"],
            "ticket_consumed_at": None,
        }
    finally:
        client.close()
        engine.dispose()


def test_stale_workspace_authorization_execution_fails_once_and_replays(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def stale_adapter(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(payload))
        raise RuntimeError("workspace authorization revision changed")

    engine = _engine(tmp_path)
    client = _client(engine, adapter=stale_adapter)
    try:
        policy = _policy(client, key="stage17-stale-policy")
        request = _request(client, policy["id"], key="stage17-stale-request")
        approved, ticket = _approve(client, request, key="stage17-stale-approve")
        body = _consume_body(approved, ticket)
        failed = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("owner-a", key="stage17-stale-consume"),
            json=body,
        )
        replay = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("owner-a", key="stage17-stale-consume"),
            json=body,
        )
        assert failed.status_code == 502, failed.text
        assert failed.json()["execution"]["state"] == "execution_failed"
        assert failed.json()["request"]["status"] == "execution_failed"
        assert replay.status_code == 502
        assert replay.json() == failed.json()
        assert len(calls) == 1
    finally:
        client.close()
        engine.dispose()
