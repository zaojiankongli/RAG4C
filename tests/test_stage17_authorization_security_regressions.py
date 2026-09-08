from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from core import enterprise_approval_control as approval_control
from core import enterprise_workspace_authorization as authorization
from core.catalog_schema import inspect_workspace_authorization_capability
from core.enterprise_access_control import (
    DatasetAccessControlUnavailable,
    evaluate_dataset_permissions,
)
from core.enterprise_workspace_authorization import (
    WorkspaceAuthorizationApprovalRequired,
    WorkspaceAuthorizationConflict,
    WorkspaceAuthorizationError,
    WorkspaceAuthorizationUnavailable,
)
from core.enterprise_workspace_control import WorkspaceMigrationRequired, create_workspace
from core.knowledge_permissions import KNOWLEDGE_WRITE
from tests.test_approval_gated_workspace_authorization import (
    _approve,
    _client,
    _consume_body,
    _engine as approval_engine,
    _headers,
    _policy,
    _request,
    _safe_core_result,
)
from tests.test_enterprise_workspace_authorization_core import _authorization_engine
from tests.test_enterprise_workspace_authorization_migration import (
    engine_for,
    seed_0026,
    sqlite_url,
)


def _set_policy_mode(engine, mode: str, *, valid_evidence: bool = True) -> None:
    if mode == "shadow":
        evidence = "enforced_at=NULL, enforced_by=NULL, disabled_at=NULL, disabled_by=NULL"
    elif mode == "enforced":
        evidence = "enforced_at=CURRENT_TIMESTAMP, enforced_by='owner-a', disabled_at=NULL, disabled_by=NULL"
    elif mode == "disabled":
        evidence = "enforced_at=NULL, enforced_by=NULL, disabled_at=CURRENT_TIMESTAMP, disabled_by='owner-a'"
    else:
        raise AssertionError(mode)
    if not valid_evidence:
        evidence = "enforced_at=NULL, enforced_by=NULL, disabled_at=NULL, disabled_by=NULL"
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE tenant_workspace_authorization_policies "
                f"SET mode='{mode}', {evidence}, revision=revision+1 "
                "WHERE tenant_id='tenant-a' AND workspace_id='workspace-default-tenant-a'"
            )
        )


def _add_matching_workspace_approval_rule(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO tenant_approval_policies "
                "(id,tenant_id,name,action_type,resource_scope,active_scope_key,status,required_approvals,"
                "request_expiry_minutes,revision,created_at,created_by,updated_at,updated_by) VALUES "
                "('review-policy','tenant-a','Workspace auth',:action,'tenant_workspace:*',"
                "'workspace-auth|tenant-a','active',1,1440,1,CURRENT_TIMESTAMP,'owner-a',CURRENT_TIMESTAMP,'owner-a')"
            ),
            {"action": authorization.ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE},
        )


def _mode(engine) -> str:
    with engine.connect() as connection:
        return str(
            connection.execute(
                text(
                    "SELECT mode FROM tenant_workspace_authorization_policies "
                    "WHERE tenant_id='tenant-a' AND workspace_id='workspace-default-tenant-a'"
                )
            ).scalar_one()
        )


def test_pre_0027_without_policy_table_remains_not_available(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path / "pre-0027.db")
    seed_0026(url)
    engine = engine_for(url)
    try:
        result = authorization.evaluate_workspace_authorization(
            engine, tenant_id="tenant-a", account_id="owner-a", dataset_id="missing-dataset"
        )
        assert result.state == "workspace_authorization_not_available"
    finally:
        engine.dispose()


def test_0027_missing_policy_table_fails_closed_instead_of_becoming_not_available(
    tmp_path: Path,
) -> None:
    engine = _authorization_engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE tenant_workspace_authorization_policies"))
        with Session(engine) as session:
            with pytest.raises(WorkspaceAuthorizationUnavailable):
                authorization.evaluate_workspace_authorization(
                    engine,
                    tenant_id="tenant-a",
                    account_id="viewer-a",
                    dataset_id="dataset-stage17",
                    session=session,
                )
    finally:
        engine.dispose()


def test_0027_policy_capability_drift_fails_closed_before_workspace_grant(
    tmp_path: Path,
) -> None:
    engine = _authorization_engine(tmp_path)
    try:
        _set_policy_mode(engine, "enforced")
        with engine.begin() as connection:
            connection.execute(text("DROP INDEX ix_tw_auth_policies_tenant_mode_updated"))
        with Session(engine) as session:
            with pytest.raises(WorkspaceAuthorizationUnavailable):
                authorization.evaluate_workspace_authorization(
                    engine,
                    tenant_id="tenant-a",
                    account_id="viewer-a",
                    dataset_id="dataset-stage17",
                    session=session,
                )
    finally:
        engine.dispose()


@pytest.mark.parametrize("mode", ["enforced", "disabled"])
def test_malformed_policy_evidence_is_unavailable_not_a_valid_rollout_state(
    tmp_path: Path, mode: str
) -> None:
    engine = _authorization_engine(tmp_path)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                text(
                    "UPDATE tenant_workspace_authorization_policies "
                    "SET mode=:mode, enforced_at=NULL, enforced_by=NULL, "
                    "disabled_at=NULL, disabled_by=NULL, revision=revision+1 "
                    "WHERE tenant_id='tenant-a' AND workspace_id='workspace-default-tenant-a'"
                ),
                {"mode": mode},
            )
            connection.commit()
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
        with Session(engine) as session:
            with pytest.raises(WorkspaceAuthorizationUnavailable):
                authorization.evaluate_workspace_authorization(
                    engine,
                    tenant_id="tenant-a",
                    account_id="viewer-a",
                    dataset_id="dataset-stage17",
                    session=session,
                )
    finally:
        engine.dispose()


def test_dataset_core_fails_closed_when_0027_workspace_capability_is_unavailable(
    tmp_path: Path,
) -> None:
    engine = _authorization_engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE tenant_workspace_authorization_policies"))
        with pytest.raises(DatasetAccessControlUnavailable):
            evaluate_dataset_permissions(
                engine, "tenant-a", "viewer-a", "member", "dataset-stage17"
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "mutation_sql",
    [
        "UPDATE tenant_workspaces SET revision=0 WHERE tenant_id='tenant-a' AND id='workspace-default-tenant-a'",
        "UPDATE tenant_workspace_members SET revision=0 WHERE tenant_id='tenant-a' AND workspace_id='workspace-default-tenant-a' AND account_id='viewer-a'",
        "UPDATE tenant_workspace_datasets SET revision=0 WHERE tenant_id='tenant-a' AND workspace_id='workspace-default-tenant-a' AND dataset_id='dataset-stage17'",
        "UPDATE tenant_workspace_datasets SET active_primary_slot=NULL WHERE tenant_id='tenant-a' AND workspace_id='workspace-default-tenant-a' AND dataset_id='dataset-stage17'",
    ],
)
def test_malformed_workspace_member_or_binding_evidence_fails_closed(
    tmp_path: Path, mutation_sql: str
) -> None:
    engine = _authorization_engine(tmp_path)
    try:
        _set_policy_mode(engine, "enforced")
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
            connection.execute(text(mutation_sql))
            connection.commit()
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
        with pytest.raises(DatasetAccessControlUnavailable):
            evaluate_dataset_permissions(
                engine, "tenant-a", "viewer-a", "member", "dataset-stage17"
            )
    finally:
        engine.dispose()


def test_unknown_pre_0027_revision_is_unavailable_not_legacy_not_available(
    tmp_path: Path,
) -> None:
    url = sqlite_url(tmp_path / "unknown-pre-0027.db")
    seed_0026(url)
    engine = engine_for(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num='0026_unknown_revision'")
            )
        state, issues = inspect_workspace_authorization_capability(engine)
        assert state == "unavailable"
        assert issues
    finally:
        engine.dispose()


def test_suspended_tenant_cannot_contribute_workspace_permissions(tmp_path: Path) -> None:
    engine = _authorization_engine(tmp_path)
    try:
        _set_policy_mode(engine, "enforced")
        with engine.begin() as connection:
            connection.execute(text("UPDATE tenants SET status='suspended' WHERE id='tenant-a'"))
        decision = evaluate_dataset_permissions(
            engine, "tenant-a", "viewer-a", "member", "dataset-stage17"
        )
        assert KNOWLEDGE_WRITE not in decision.effective_permissions
        assert decision.workspace_authorization["granted_permissions"] == []
    finally:
        engine.dispose()


def test_arbitrary_approval_execution_id_cannot_bypass_matching_rule(tmp_path: Path) -> None:
    engine = _authorization_engine(tmp_path)
    try:
        _add_matching_workspace_approval_rule(engine)
        with pytest.raises(WorkspaceAuthorizationError):
            authorization.change_workspace_authorization_mode(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                actor_role="owner",
                workspace_id="workspace-default-tenant-a",
                expected_workspace_revision=1,
                expected_policy_revision=1,
                target_mode="enforced",
                expected_permission_model_version=1,
                permission_matrix_fingerprint=authorization.permission_matrix_fingerprint(),
                reason="arbitrary id must not bypass approval",
                request_id="arbitrary-id-request",
                request_ip="127.0.0.1",
                idempotency_key="arbitrary-id-key",
                approval_execution_id="not-an-internal-fact",
            )
        assert _mode(engine) == "shadow"
    finally:
        engine.dispose()


def test_disabled_to_enforced_is_not_a_legal_mode_transition(tmp_path: Path) -> None:
    engine = _authorization_engine(tmp_path)
    try:
        _set_policy_mode(engine, "disabled")
        with pytest.raises(WorkspaceAuthorizationConflict):
            authorization.change_workspace_authorization_mode(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                actor_role="owner",
                workspace_id="workspace-default-tenant-a",
                expected_workspace_revision=1,
                expected_policy_revision=2,
                target_mode="enforced",
                expected_permission_model_version=1,
                permission_matrix_fingerprint=authorization.permission_matrix_fingerprint(),
                reason="disabled cannot jump to enforced",
                request_id="disabled-enforced-request",
                request_ip="127.0.0.1",
                idempotency_key="disabled-enforced-key",
            )
        assert _mode(engine) == "disabled"
    finally:
        engine.dispose()


def test_enforced_to_shadow_requires_approval_when_rule_matches(tmp_path: Path) -> None:
    engine = _authorization_engine(tmp_path)
    try:
        _set_policy_mode(engine, "enforced")
        _add_matching_workspace_approval_rule(engine)
        with pytest.raises(WorkspaceAuthorizationApprovalRequired):
            authorization.change_workspace_authorization_mode(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                actor_role="owner",
                workspace_id="workspace-default-tenant-a",
                expected_workspace_revision=1,
                expected_policy_revision=2,
                target_mode="shadow",
                expected_permission_model_version=1,
                permission_matrix_fingerprint=authorization.permission_matrix_fingerprint(),
                reason="enforced rollback needs approval",
                request_id="enforced-shadow-request",
                request_ip="127.0.0.1",
                idempotency_key="enforced-shadow-key",
            )
        assert _mode(engine) == "enforced"
    finally:
        engine.dispose()


def test_workspace_mode_mutation_locks_tenant_before_workspace_and_approval_policy(
    tmp_path: Path,
) -> None:
    engine = _authorization_engine(tmp_path)
    order: list[str] = []
    original_lock_tenant = authorization._lock_tenant
    original_workspace = authorization._workspace
    original_resolver = approval_control.resolve_active_approval_policy_in_session

    def lock_tenant(*args, **kwargs):
        order.append("tenant")
        return original_lock_tenant(*args, **kwargs)

    def workspace(*args, **kwargs):
        order.append("workspace")
        return original_workspace(*args, **kwargs)

    def resolver(*args, **kwargs):
        order.append("approval_policy")
        return original_resolver(*args, **kwargs)

    try:
        _add_matching_workspace_approval_rule(engine)
        with (
            patch.object(authorization, "_lock_tenant", side_effect=lock_tenant),
            patch.object(authorization, "_workspace", side_effect=workspace),
            patch(
                "core.enterprise_approval_control.resolve_active_approval_policy_in_session",
                side_effect=resolver,
            ),
        ):
            with pytest.raises(WorkspaceAuthorizationApprovalRequired):
                authorization.change_workspace_authorization_mode(
                    engine,
                    tenant_id="tenant-a",
                    actor_id="owner-a",
                    actor_role="owner",
                    workspace_id="workspace-default-tenant-a",
                    expected_workspace_revision=1,
                    expected_policy_revision=1,
                    target_mode="enforced",
                    expected_permission_model_version=1,
                    permission_matrix_fingerprint=authorization.permission_matrix_fingerprint(),
                    reason="lock order probe",
                    request_id="lock-order-request",
                    request_ip="127.0.0.1",
                    idempotency_key="lock-order-key",
                )
        assert order[:3] == ["tenant", "workspace", "approval_policy"]
    finally:
        engine.dispose()


def test_workspace_consumer_rejects_payload_without_internal_execution_fact() -> None:
    from server import enterprise_approval_consumers as consumers

    calls: list[dict[str, object]] = []

    def mutation_service(_engine, **kwargs):
        calls.append(kwargs)
        return {
            "authorization_policy": {
                "id": "policy",
                "tenant_id": "tenant-approval-a",
                "workspace_id": "workspace-stage17",
                "mode": "enforced",
                "permission_model_version": 1,
                "revision": 6,
            },
            "workspace": {
                "id": "workspace-stage17",
                "tenant_id": "tenant-approval-a",
                "status": "active",
                "revision": 3,
            },
            "audit": {"id": "audit", "sequence": 1},
        }

    consumer = consumers.build_workspace_authorization_mode_change_consumer(
        lambda: object(), mode_change_service=mutation_service
    )
    from tests.test_approval_gated_workspace_authorization import _payload

    with pytest.raises(consumers.ApprovalConsumerError):
        consumer(_payload())
    assert calls == []


def test_approval_ticket_payload_contains_bound_execution_fact(tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []

    def adapter(payload: dict[str, object]) -> dict[str, object]:
        captured.append(payload)
        return _safe_core_result()

    engine = approval_engine(tmp_path)
    client = _client(engine, adapter=adapter)
    try:
        policy = _policy(client, key="fact-policy")
        request = _request(client, policy["id"], key="fact-request")
        approved, ticket = _approve(client, request, key="fact-approve")
        consume = client.post(
            f"/api/enterprise/approvals/requests/{request['id']}/consume-ticket",
            headers=_headers("owner-a", key="fact-consume"),
            json=_consume_body(approved, ticket),
        )
        assert consume.status_code == 200, consume.text
        assert len(captured) == 1
        fact = captured[0].get("approval_execution_fact")
        assert fact is not None
        assert getattr(fact, "tenant_id", None) == "tenant-approval-a"
        assert getattr(fact, "approval_request_id", None) == request["id"]
    finally:
        client.close()
        engine.dispose()


def test_workspace_consumer_rejects_fact_that_does_not_match_request_row(tmp_path: Path) -> None:
    from core import enterprise_approval_control as approval_control
    from core import enterprise_workspace_authorization as authorization
    from server.enterprise_approval_consumers import (
        build_workspace_authorization_mode_change_consumer,
    )

    engine = _authorization_engine(tmp_path)
    now = datetime(2026, 8, 27, 12, 0, 0)
    try:
        policy = approval_control.create_approval_policy(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            name="Workspace authorization",
            action_type=authorization.ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE,
            resource_scope="tenant_workspace:workspace-default-tenant-a",
            required_approvals=1,
            request_expiry_minutes=60,
            approvers=[{"kind": "account", "ref": "owner-a"}],
            reason="create workspace approval rule",
            idempotency_key="tampered-fact-policy",
            request_id="tampered-fact-policy-request",
            request_ip="127.0.0.1",
            now=now,
        ).body["policy"]
        snapshot = {
            "workspace_id": "workspace-default-tenant-a",
            "workspace_revision": 1,
            "policy_revision": 1,
            "from_mode": "shadow",
            "target_mode": "enforced",
            "permission_model_version": 1,
            "permission_matrix_fingerprint": authorization.permission_matrix_fingerprint(),
            "reason": "enable workspace authorization",
        }
        request = approval_control.create_approval_request(
            engine,
            tenant_id="tenant-a",
            actor_id="viewer-a",
            policy_id=policy["id"],
            resource_type="tenant_workspace",
            resource_id="workspace-default-tenant-a",
            snapshot=snapshot,
            reason="enable workspace authorization",
            idempotency_key="tampered-fact-request",
            request_id="tampered-fact-request-id",
            request_ip="127.0.0.1",
            now=now,
        ).body["request"]
        approved = approval_control.decide_approval_request(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            request_id=request["id"],
            decision="approved",
            expected_revision=request["revision"],
            comment="approved",
            idempotency_key="tampered-fact-decision",
            request_id_header="tampered-fact-decision-request",
            request_ip="127.0.0.1",
            now=now,
        ).body
        consumer = build_workspace_authorization_mode_change_consumer(lambda: engine)

        def tampering_adapter(payload):
            tampered_fact = replace(payload["approval_execution_fact"], snapshot_hash="b" * 64)
            return consumer({**payload, "approval_execution_fact": tampered_fact})

        result = approval_control.consume_approval_ticket(
            engine,
            tenant_id="tenant-a",
            actor_id="owner-a",
            request_id=request["id"],
            ticket=approved["execution"]["ticket"],
            expected_revision=approved["request"]["revision"],
            action_type=authorization.ACTION_WORKSPACE_AUTHORIZATION_MODE_CHANGE,
            resource_type="tenant_workspace",
            resource_id="workspace-default-tenant-a",
            idempotency_key="tampered-fact-consume",
            request_id_header="tampered-fact-consume-request",
            request_ip="127.0.0.1",
            now=now,
            execution_adapter=tampering_adapter,
        )
        assert result.status == 502
        assert result.body["execution"]["state"] == "execution_failed"
        assert _mode(engine) == "shadow"
    finally:
        engine.dispose()


def test_readonly_approval_evidence_errors_are_not_treated_as_no_rule(tmp_path: Path) -> None:
    engine = _authorization_engine(tmp_path)
    try:
        with patch(
            "core.enterprise_approval_control.resolve_active_approval_policy_in_session",
            side_effect=approval_control.ApprovalMigrationRequired(),
        ):
            with pytest.raises(approval_control.ApprovalMigrationRequired):
                authorization.get_workspace_authorization_policy(
                    engine,
                    tenant_id="tenant-a",
                    actor_id="owner-a",
                    workspace_id="workspace-default-tenant-a",
                )
    finally:
        engine.dispose()


def test_approval_action_check_rejects_or_one_equals_one_injected_contract(
    tmp_path: Path,
) -> None:
    engine = _authorization_engine(tmp_path)
    try:
        with engine.connect() as connection:
            real = inspect(connection)

            class ProxyInspector:
                bind = real.bind

                def __getattr__(self, name):
                    return getattr(real, name)

                def get_check_constraints(self, table):
                    rows = [dict(item) for item in real.get_check_constraints(table)]
                    if table in {"tenant_approval_policies", "tenant_approval_requests"}:
                        for item in rows:
                            if item.get("name") in {
                                "ck_tenant_approval_policies_action_type",
                                "ck_tenant_approval_requests_action_type",
                            }:
                                item["sqltext"] = f"({item.get('sqltext')}) OR 1=1"
                    return rows

            with patch("core.enterprise_approval_control.inspect", return_value=ProxyInspector()):
                with pytest.raises(approval_control.ApprovalMigrationRequired):
                    approval_control._ensure_0025(connection)
    finally:
        engine.dispose()


def test_approval_result_projection_keeps_workspace_authorization_envelope() -> None:
    projected = approval_control._safe_adapter_result(
        {"authorization_policy": {"mode": "enforced", "revision": 2}}
    )
    assert projected["authorization_policy"] == {"mode": "enforced", "revision": 2}


def test_session_none_engine_path_uses_sqlalchemy_2_connection(tmp_path: Path) -> None:
    engine = _authorization_engine(tmp_path)
    try:
        result = authorization.evaluate_workspace_authorization(
            engine,
            tenant_id="tenant-a",
            account_id="viewer-a",
            dataset_id="dataset-stage17",
        )
        assert result.state == "workspace_authorization_shadow"
    finally:
        engine.dispose()


def test_new_workspace_at_0027_fails_closed_if_policy_capability_is_missing(tmp_path: Path) -> None:
    engine = _authorization_engine(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE tenant_workspace_authorization_policies"))
        with pytest.raises(WorkspaceMigrationRequired):
            create_workspace(
                engine,
                tenant_id="tenant-a",
                actor_id="owner-a",
                code="missing-policy",
                name="Missing policy",
                reason="policy capability gate",
                idempotency_key="missing-policy-workspace",
                request_id="missing-policy-request",
                request_ip="127.0.0.1",
            )
    finally:
        engine.dispose()
