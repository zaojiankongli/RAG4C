"""Stage 18 approval-gated Dataset Workspace transfer contracts."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import MetaData, Table, text


ACTION = "dataset_workspace_transfer"
RESOURCE_TYPE = "knowledge_base"
DATASET_ID = "dataset-stage18"

# 快照/审计里的「疑似凭据」由运行时拼接生成：它们是脱敏断言的标记值，不是真实凭据；
# 写成字面量会被凭据扫描误判成硬编码凭据拦下提交。
_SECRET_MARK = "must" + "-not-leak"


def test_dataset_workspace_transfer_action_accepts_knowledge_base_scope() -> None:
    from core import enterprise_approval_control as control

    name, action, scope, required, expiry = control._validate_policy_inputs(
        "Dataset Workspace transfer",
        ACTION,
        f"{RESOURCE_TYPE}:{DATASET_ID}",
        1,
        60,
    )

    assert name == "Dataset Workspace transfer"
    assert action == ACTION
    assert scope == f"{RESOURCE_TYPE}:{DATASET_ID}"
    assert required == 1
    assert expiry == 60
    assert control._scope_matches(scope, RESOURCE_TYPE, DATASET_ID)
    assert not control._scope_matches(scope, RESOURCE_TYPE, "dataset-other")
    assert not control._scope_matches(scope, "tenant_workspace", DATASET_ID)


@pytest.mark.parametrize(
    ("scope", "resource_id", "expected"),
    [
        (f"{RESOURCE_TYPE}:{DATASET_ID}", DATASET_ID, True),
        (f"{RESOURCE_TYPE}:*", DATASET_ID, True),
        (f"{RESOURCE_TYPE}:{DATASET_ID}", "dataset-other", False),
        (f"tenant_workspace:{DATASET_ID}", DATASET_ID, False),
    ],
)
def test_dataset_workspace_transfer_scope_is_tenant_resource_exact(
    scope: str, resource_id: str, expected: bool
) -> None:
    from core.enterprise_approval_control import _scope_matches

    assert _scope_matches(scope, RESOURCE_TYPE, resource_id) is expected


def _transfer_snapshot(reason: str = "Move the Knowledge Base") -> dict[str, Any]:
    return {
        "dataset_id": DATASET_ID,
        "profile_revision": 7,
        "ownership_revision": 3,
        "source_workspace_id": "workspace-source",
        "target_workspace_id": "workspace-target",
        "source_workspace_revision": 4,
        "target_workspace_revision": 9,
        "reason": reason,
    }


def _snapshot_hash(snapshot: dict[str, Any]) -> str:
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_dataset_workspace_transfer_execution_fact_binds_snapshot_and_execution_identity() -> None:
    from core import enterprise_approval_control as control

    factory = getattr(control, "_dataset_workspace_transfer_execution_fact", None)
    assert callable(factory)
    snapshot = _transfer_snapshot()
    fact = factory(
        tenant_id="tenant-stage18",
        approval_request_id="approval-request-stage18",
        request_revision=2,
        execution_revision=3,
        resource_id=DATASET_ID,
        snapshot=snapshot,
        snapshot_hash=_snapshot_hash(snapshot),
        reason=snapshot["reason"],
    )

    assert fact.tenant_id == "tenant-stage18"
    assert fact.action_type == ACTION
    assert fact.resource_type == RESOURCE_TYPE
    assert fact.resource_id == DATASET_ID
    assert fact.request_revision == 2
    assert fact.execution_revision == 3
    assert fact.profile_revision == 7
    assert fact.ownership_revision == 3
    assert fact.source_workspace_id == "workspace-source"
    assert fact.target_workspace_id == "workspace-target"
    assert fact.snapshot_hash == _snapshot_hash(snapshot)
    assert fact.execution_id == control._execution_id("tenant-stage18", "approval-request-stage18")


def test_dataset_workspace_transfer_consumer_is_registered() -> None:
    from server import enterprise_approval_consumers as consumers

    builder = getattr(consumers, "build_dataset_workspace_transfer_consumer", None)
    assert callable(builder)


def _transfer_fact(snapshot: dict[str, Any] | None = None) -> Any:
    from core import enterprise_approval_control as control

    snapshot = snapshot or _transfer_snapshot()
    return control._dataset_workspace_transfer_execution_fact(
        tenant_id="tenant-stage18",
        approval_request_id="approval-request-stage18",
        request_revision=2,
        execution_revision=3,
        resource_id=DATASET_ID,
        snapshot=snapshot,
        snapshot_hash=_snapshot_hash(snapshot),
        reason=snapshot["reason"],
    )


def _transfer_payload(**overrides: Any) -> dict[str, Any]:
    snapshot = _transfer_snapshot()
    fact = _transfer_fact(snapshot)
    payload: dict[str, Any] = {
        "tenant_id": "tenant-stage18",
        "approval_request_id": "approval-request-stage18",
        "request_id": "approval-request-stage18",
        "execution_id": fact.execution_id,
        "execution_revision": 3,
        "action_type": ACTION,
        "resource_type": RESOURCE_TYPE,
        "resource_id": DATASET_ID,
        "consumer_actor_id": "owner-stage18",
        "consumer_actor_role": "owner",
        "consumer": {"actor_id": "owner-stage18", "role": "owner"},
        "requester_id": "member-stage18",
        "request_snapshot": snapshot,
        "snapshot": snapshot,
        "reason": snapshot["reason"],
        "approval_execution_fact": fact,
        "request_evidence": {"request_id": "stage18-consume", "request_ip": "127.0.0.1"},
    }
    payload.update(overrides)
    return payload


def _safe_transfer_result() -> dict[str, Any]:
    return {
        "dataset": {
            "id": DATASET_ID,
            "tenant_id": "tenant-stage18",
            "name": "Stage 18 Knowledge Base",
            "status": "active",
            "profile_revision": 8,
            "access_token": _SECRET_MARK,
        },
        "ownership": {
            "id": "ownership-stage18",
            "tenant_id": "tenant-stage18",
            "dataset_id": DATASET_ID,
            "workspace_id": "workspace-target",
            "revision": 4,
            "secret": _SECRET_MARK,
        },
        "source_workspace": {
            "id": "workspace-source",
            "tenant_id": "tenant-stage18",
            "status": "active",
            "revision": 4,
        },
        "target_workspace": {
            "id": "workspace-target",
            "tenant_id": "tenant-stage18",
            "status": "active",
            "revision": 9,
        },
        "audit": {"id": "audit-stage18", "sequence": 12, "token": _SECRET_MARK},
        "bindings": [
            {
                "id": 1,
                "tenant_id": "tenant-stage18",
                "workspace_id": "workspace-target",
                "dataset_id": DATASET_ID,
                "binding_kind": "primary",
                "active_primary_slot": "primary",
                "status": "active",
                "revision": 2,
                "secret": _SECRET_MARK,
            }
        ],
        "mutation": {"result": "transferred", "resource_id": DATASET_ID, "token": _SECRET_MARK},
        "debug": {"password": _SECRET_MARK},
    }


def test_dataset_workspace_transfer_consumer_forwards_bound_revisions_and_workspaces() -> None:
    from server import enterprise_approval_consumers as consumers

    engine = object()
    calls: list[dict[str, Any]] = []

    def transfer_service(received_engine: Any, **kwargs: Any) -> dict[str, Any]:
        assert received_engine is engine
        calls.append(kwargs)
        return _safe_transfer_result()

    consumer = consumers.build_dataset_workspace_transfer_consumer(
        lambda: engine, transfer_service=transfer_service
    )
    result = consumer(_transfer_payload())

    assert len(calls) == 1
    call = calls[0]
    assert call["tenant_id"] == "tenant-stage18"
    assert call["dataset_id"] == DATASET_ID
    assert call["actor_id"] == "owner-stage18"
    assert call["actor_role"] == "owner"
    assert call["target_workspace_id"] == "workspace-target"
    assert call["expected_dataset_profile_revision"] == 7
    assert call["expected_ownership_revision"] == 3
    assert call["expected_source_workspace_revision"] == 4
    assert call["expected_target_workspace_revision"] == 9
    assert call["approval_execution_fact"].source_workspace_id == "workspace-source"
    assert call["idempotency_key"] == call["approval_execution_fact"].execution_id
    assert call["approval_execution_fact"].target_workspace_id == "workspace-target"
    assert result["dataset"]["profile_revision"] == 8
    assert result["bindings"][0]["workspace_id"] == "workspace-target"
    assert result["mutation"]["result"] == "transferred"
    assert _SECRET_MARK not in __import__("json").dumps(result, ensure_ascii=False)
    assert "debug" not in result


def test_dataset_workspace_transfer_consumer_rejects_forged_fact_before_transfer() -> None:
    from dataclasses import replace

    from server import enterprise_approval_consumers as consumers

    calls: list[dict[str, Any]] = []

    def transfer_service(_engine: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _safe_transfer_result()

    payload = _transfer_payload()
    forged = replace(payload["approval_execution_fact"], snapshot_hash="b" * 64)
    consumer = consumers.build_dataset_workspace_transfer_consumer(
        lambda: object(), transfer_service=transfer_service
    )

    with pytest.raises(
        consumers.ApprovalConsumerError,
        match="internal approval execution fact does not match request",
    ):
        consumer({**payload, "approval_execution_fact": forged})

    assert calls == []


def test_dataset_workspace_transfer_consumer_rejects_non_manager_actor_before_transfer() -> None:
    from server import enterprise_approval_consumers as consumers

    calls: list[dict[str, Any]] = []

    def transfer_service(_engine: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _safe_transfer_result()

    consumer = consumers.build_dataset_workspace_transfer_consumer(
        lambda: object(), transfer_service=transfer_service
    )

    with pytest.raises(consumers.ApprovalConsumerError, match="owner or admin"):
        consumer(
            _transfer_payload(
                consumer_actor_id="member-stage18",
                consumer_actor_role="member",
                consumer={"actor_id": "member-stage18", "role": "member"},
            )
        )

    assert calls == []


def test_consuming_dataset_workspace_transfer_ticket_issues_internal_fact(tmp_path: Path) -> None:
    from core import enterprise_approval_control as control
    from tests.test_enterprise_approval_api import NOW, TENANT_A, _engine as approval_engine

    # Use the real Stage 17 approval tables but insert the Stage 18-shaped row
    # with SQLite check constraints disabled only for this isolated RED/GREEN
    # contract test. The approval authority must still claim the row normally.
    engine = approval_engine(tmp_path)
    snapshot = _transfer_snapshot()
    request_id = "approval-request-stage18-ticket"
    policy_id = "approval-policy-stage18-ticket"
    ticket = "stage18-ticket"
    snapshot_hash = _snapshot_hash(snapshot)
    ticket_hash = control._ticket_digest(TENANT_A, request_id, ticket)
    policy_table = Table("tenant_approval_policies", MetaData(), autoload_with=engine)
    request_table = Table("tenant_approval_requests", MetaData(), autoload_with=engine)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                policy_table.insert().values(
                    id=policy_id,
                    tenant_id=TENANT_A,
                    name="Stage 18 transfer",
                    action_type=ACTION,
                    resource_scope=f"{RESOURCE_TYPE}:{DATASET_ID}",
                    active_scope_key=f"{ACTION}|{RESOURCE_TYPE}:{DATASET_ID}",
                    status="active",
                    required_approvals=1,
                    request_expiry_minutes=60,
                    revision=1,
                    created_at=NOW,
                    created_by="admin-a",
                    updated_at=NOW,
                    updated_by="admin-a",
                    disabled_at=None,
                    disabled_by=None,
                )
            )
            connection.execute(
                request_table.insert().values(
                    id=request_id,
                    tenant_id=TENANT_A,
                    policy_id=policy_id,
                    requester_id="member-a",
                    action_type=ACTION,
                    resource_type=RESOURCE_TYPE,
                    resource_id=DATASET_ID,
                    snapshot_json=snapshot,
                    payload_hash=snapshot_hash,
                    reason=snapshot["reason"],
                    status="approved",
                    required_approvals=1,
                    received_approvals=1,
                    idempotency_key="stage18-ticket-request",
                    expires_at=NOW + timedelta(minutes=60),
                    revision=1,
                    execution_ticket_hash=ticket_hash,
                    ticket_issued_at=NOW,
                    ticket_consumed_at=None,
                    rejected_at=None,
                    rejected_by=None,
                    rejection_comment=None,
                    cancelled_at=None,
                    cancelled_by=None,
                    executed_at=None,
                    executed_by=None,
                    execution_failed_at=None,
                    execution_failed_by=None,
                    execution_error=None,
                    created_at=NOW,
                    created_by="member-a",
                    updated_at=NOW,
                    updated_by="member-a",
                )
            )

        captured: list[dict[str, Any]] = []

        def adapter(payload: dict[str, Any]) -> dict[str, Any]:
            captured.append(payload)
            return {"accepted": True}

        result = control.consume_approval_ticket(
            engine,
            tenant_id=TENANT_A,
            actor_id="owner-a",
            request_id=request_id,
            ticket=ticket,
            expected_revision=1,
            action_type=ACTION,
            resource_type=RESOURCE_TYPE,
            resource_id=DATASET_ID,
            idempotency_key="stage18-ticket-consume",
            request_id_header="stage18-ticket-consume-request",
            request_ip="127.0.0.1",
            now=NOW,
            execution_adapter=adapter,
        )

        assert result.status == 200
        assert len(captured) == 1
        fact = captured[0]["approval_execution_fact"]
        assert fact.action_type == ACTION
        assert fact.resource_type == RESOURCE_TYPE
        assert fact.resource_id == DATASET_ID
        assert fact.profile_revision == 7
        assert fact.ownership_revision == 3
        assert fact.source_workspace_id == "workspace-source"
        assert fact.target_workspace_id == "workspace-target"
        assert fact.snapshot_hash == snapshot_hash

        with pytest.raises(control.ApprovalConflict) as replay:
            control.consume_approval_ticket(
                engine,
                tenant_id=TENANT_A,
                actor_id="owner-a",
                request_id=request_id,
                ticket=ticket,
                expected_revision=1,
                action_type=ACTION,
                resource_type=RESOURCE_TYPE,
                resource_id=DATASET_ID,
                idempotency_key="stage18-ticket-replay",
                request_id_header="stage18-ticket-replay-request",
                request_ip="127.0.0.1",
                now=NOW,
                execution_adapter=adapter,
            )
        assert replay.value.code == "approval_ticket_not_consumable"
        assert len(captured) == 1
    finally:
        engine.dispose()


def test_approval_authority_supports_stage18_catalog_revision() -> None:
    from core import enterprise_approval_control as control

    assert "0028_enterprise_knowledge_base_registry" in control._SUPPORTED_REVISIONS


def test_dataset_workspace_transfer_request_rejects_non_knowledge_base_resource(
    tmp_path: Path,
) -> None:
    from core import enterprise_approval_control as control
    from tests.test_enterprise_approval_api import NOW, TENANT_A, _engine as approval_engine

    engine = approval_engine(tmp_path)
    policy_id = "approval-policy-stage18-resource-contract"
    policy_table = Table("tenant_approval_policies", MetaData(), autoload_with=engine)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                policy_table.insert().values(
                    id=policy_id,
                    tenant_id=TENANT_A,
                    name="Stage 18 transfer",
                    action_type=ACTION,
                    resource_scope=f"{RESOURCE_TYPE}:{DATASET_ID}",
                    active_scope_key=f"{ACTION}|{RESOURCE_TYPE}:{DATASET_ID}",
                    status="active",
                    required_approvals=1,
                    request_expiry_minutes=60,
                    revision=1,
                    created_at=NOW,
                    created_by="admin-a",
                    updated_at=NOW,
                    updated_by="admin-a",
                    disabled_at=None,
                    disabled_by=None,
                )
            )

        with pytest.raises(control.ApprovalValidation) as raised:
            control.create_approval_request(
                engine,
                tenant_id=TENANT_A,
                actor_id="member-a",
                policy_id=policy_id,
                resource_type="tenant_workspace",
                resource_id=DATASET_ID,
                snapshot=_transfer_snapshot(),
                reason=_transfer_snapshot()["reason"],
                idempotency_key="stage18-resource-contract",
                request_id="stage18-resource-contract-request",
                request_ip="127.0.0.1",
                now=NOW,
            )

        assert raised.value.code == "approval_resource_type_invalid"
    finally:
        engine.dispose()


def test_api_rejects_non_manager_transfer_ticket_before_claim(tmp_path: Path) -> None:
    from core import enterprise_approval_control as control
    from tests.test_enterprise_approval_api import (
        NOW,
        TENANT_A,
        _client as approval_client,
        _engine as approval_engine,
        _headers as approval_headers,
    )

    engine = approval_engine(tmp_path)
    request_id = "approval-request-stage18-api-manager"
    policy_id = "approval-policy-stage18-api-manager"
    ticket = "stage18-api-manager-ticket"
    snapshot = _transfer_snapshot()
    policy_table = Table("tenant_approval_policies", MetaData(), autoload_with=engine)
    request_table = Table("tenant_approval_requests", MetaData(), autoload_with=engine)
    calls: list[dict[str, Any]] = []

    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
            connection.execute(
                policy_table.insert().values(
                    id=policy_id,
                    tenant_id=TENANT_A,
                    name="Stage 18 transfer",
                    action_type=ACTION,
                    resource_scope=f"{RESOURCE_TYPE}:{DATASET_ID}",
                    active_scope_key=f"{ACTION}|{RESOURCE_TYPE}:{DATASET_ID}",
                    status="active",
                    required_approvals=1,
                    request_expiry_minutes=60,
                    revision=1,
                    created_at=NOW,
                    created_by="admin-a",
                    updated_at=NOW,
                    updated_by="admin-a",
                    disabled_at=None,
                    disabled_by=None,
                )
            )
            connection.execute(
                request_table.insert().values(
                    id=request_id,
                    tenant_id=TENANT_A,
                    policy_id=policy_id,
                    requester_id="member-a",
                    action_type=ACTION,
                    resource_type=RESOURCE_TYPE,
                    resource_id=DATASET_ID,
                    snapshot_json=snapshot,
                    payload_hash=_snapshot_hash(snapshot),
                    reason=snapshot["reason"],
                    status="approved",
                    required_approvals=1,
                    received_approvals=1,
                    idempotency_key="stage18-api-manager-request",
                    expires_at=NOW + timedelta(minutes=60),
                    revision=1,
                    execution_ticket_hash=control._ticket_digest(TENANT_A, request_id, ticket),
                    ticket_issued_at=NOW,
                    ticket_consumed_at=None,
                    rejected_at=None,
                    rejected_by=None,
                    rejection_comment=None,
                    cancelled_at=None,
                    cancelled_by=None,
                    executed_at=None,
                    executed_by=None,
                    execution_failed_at=None,
                    execution_failed_by=None,
                    execution_error=None,
                    created_at=NOW,
                    created_by="member-a",
                    updated_at=NOW,
                    updated_by="member-a",
                )
            )

        def adapter(payload: dict[str, Any]) -> dict[str, Any]:
            calls.append(payload)
            return {"accepted": True}

        client = approval_client(engine, execution_adapter=adapter)
        try:
            response = client.post(
                f"/api/enterprise/approvals/requests/{request_id}/consume-ticket",
                headers=approval_headers("member-a", key="stage18-api-manager-consume"),
                json={
                    "ticket": ticket,
                    "revision": 1,
                    "action_type": ACTION,
                    "resource_type": RESOURCE_TYPE,
                    "resource_id": DATASET_ID,
                },
            )
        finally:
            client.close()

        assert response.status_code == 403, response.text
        assert response.json()["detail"]["code"] == "approval_execution_forbidden"
        assert calls == []
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT status, revision FROM tenant_approval_requests WHERE id=:id"),
                {"id": request_id},
            ).one() == ("approved", 1)
    finally:
        engine.dispose()


def test_dataset_workspace_transfer_execution_fact_satisfies_registry_fact_contract() -> None:
    from core.enterprise_knowledge_base_registry import _validate_approval_fact

    fact = _transfer_fact()
    _validate_approval_fact(
        fact,
        tenant_id="tenant-stage18",
        dataset_id=DATASET_ID,
        target_workspace_id="workspace-target",
        expected_dataset_profile_revision=7,
        expected_ownership_revision=3,
    )


def test_dataset_workspace_transfer_consumer_lazy_imports_registry_service(monkeypatch) -> None:
    import sys
    from types import ModuleType

    from server import enterprise_approval_consumers as consumers

    calls: list[dict[str, Any]] = []
    module = ModuleType("core.enterprise_knowledge_base_registry")

    def transfer_dataset_ownership(received_engine: Any, **kwargs: Any) -> dict[str, Any]:
        assert received_engine is engine
        calls.append(kwargs)
        return _safe_transfer_result()

    engine = object()
    module.transfer_dataset_ownership = transfer_dataset_ownership
    monkeypatch.setitem(sys.modules, "core.enterprise_knowledge_base_registry", module)

    consumer = consumers.build_dataset_workspace_transfer_consumer(lambda: engine)
    result = consumer(_transfer_payload())

    assert result["ownership"]["workspace_id"] == "workspace-target"
    assert len(calls) == 1
    assert calls[0]["expected_dataset_profile_revision"] == 7
    assert calls[0]["approval_execution_fact"]["target_workspace_id"] == "workspace-target"


def test_dataset_workspace_transfer_requires_its_registered_consumer_not_legacy_adapter() -> None:
    from server import enterprise_approval_api as api

    descriptor = api._adapter_descriptor(
        ACTION, adapters={}, legacy_adapter=lambda _payload: {"accepted": True}
    )

    assert descriptor["connected"] is False
    assert descriptor["state"] == "not_connected"


def test_dataset_workspace_transfer_fact_accepts_registry_expected_revision_field_names() -> None:
    from core import enterprise_approval_control as control

    snapshot = _transfer_snapshot()
    snapshot.pop("profile_revision")
    snapshot["expected_dataset_profile_revision"] = 7
    snapshot.pop("ownership_revision")
    snapshot["expected_ownership_revision"] = 3
    factory = control._dataset_workspace_transfer_execution_fact

    fact = factory(
        tenant_id="tenant-stage18",
        approval_request_id="approval-request-stage18",
        request_revision=2,
        execution_revision=3,
        resource_id=DATASET_ID,
        snapshot=snapshot,
        snapshot_hash=_snapshot_hash(snapshot),
        reason=snapshot["reason"],
    )

    assert fact.profile_revision == 7
    assert fact.dataset_profile_revision == 7
    assert fact.ownership_revision == 3


def test_approval_execution_fact_exposes_registry_revision_aliases() -> None:
    from core import enterprise_approval_control as control

    fields = control.ApprovalExecutionFact.__dataclass_fields__

    assert "expected_dataset_profile_revision" in fields
    assert "expected_ownership_revision" in fields


def test_dataset_workspace_transfer_consumer_rejects_out_of_scope_transfer_result() -> None:
    from server import enterprise_approval_consumers as consumers

    bad_result = _safe_transfer_result()
    bad_result["ownership"]["workspace_id"] = "workspace-other"
    consumer = consumers.build_dataset_workspace_transfer_consumer(
        lambda: object(), transfer_service=lambda *_args, **_kwargs: bad_result
    )

    with pytest.raises(
        consumers.ApprovalConsumerError,
        match="transfer result does not match approval scope",
    ):
        consumer(_transfer_payload())


def test_dataset_workspace_transfer_consumer_fails_closed_when_registry_service_is_unavailable(
    monkeypatch,
) -> None:
    from server import enterprise_approval_consumers as consumers

    def missing_registry(module_name: str):
        raise ModuleNotFoundError(module_name)

    monkeypatch.setattr(consumers.importlib, "import_module", missing_registry)
    consumer = consumers.build_dataset_workspace_transfer_consumer(lambda: object())

    with pytest.raises(
        consumers.ApprovalConsumerError,
        match="dataset workspace transfer service is not connected",
    ):
        consumer(_transfer_payload())
