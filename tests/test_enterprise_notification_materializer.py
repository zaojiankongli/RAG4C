from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import importlib
import json
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from core.enterprise_release_quality_alerts import create_or_update_quality_alert_from_observation
from models.orm import (
    Account,
    Dataset,
    DatasetAccessGrant,
    DatasetReleaseQualityAlert,
    DatasetReleaseQualityObservation,
    TenantApprovalPolicy,
    TenantApprovalPolicyApprover,
    TenantApprovalRequest,
    TenantMember,
    TenantWorkspaceAuthorizationPolicy,
)
from test_enterprise_release_quality_alerts import (
    _add_observation,
    _seed_quality_authority,
)
from test_enterprise_knowledge_base_release_snapshot import _release_engine


NOW = datetime(2026, 8, 29, 12, 0, 0)
MATERIALIZER_MODULE = "core.enterprise_notification_materializer"
SENSITIVE_MARKERS = (
    "select private customer records",
    "rag4c-approval-ticket-secret",
    "bearer stage22-secret",
    "sk_live_stage22_materializer",
    "https://webhook.example/secret",
    "private reviewer note",
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _materializer() -> Any:
    """Load the deliberately-missing Stage 22 Task 3 implementation."""
    try:
        return importlib.import_module(MATERIALIZER_MODULE)
    except ModuleNotFoundError as exc:
        pytest.fail(
            "Stage 22 RED contract: core.enterprise_notification_materializer "
            f"is required but is not implemented yet: {exc}"
        )


def _api(name: str) -> Callable[..., Any]:
    module = _materializer()
    operation = getattr(module, name, None)
    if not callable(operation):
        pytest.fail(
            f"Stage 22 RED contract: core.enterprise_notification_materializer.{name} is required"
        )
    return operation


def _body(result: Any) -> Mapping[str, Any]:
    value = getattr(result, "body", result)
    if not isinstance(value, Mapping):
        pytest.fail("materializer result must expose a mapping body")
    return value


def _notification_models() -> dict[str, Any]:
    module = importlib.import_module("models.orm")
    names = (
        "TenantNotification",
        "TenantNotificationRecipient",
        "TenantNotificationReceipt",
        "TenantNotificationEvent",
        "TenantNotificationSubscription",
    )
    missing = [name for name in names if not hasattr(module, name)]
    if missing:
        pytest.fail(
            "Stage 22 RED contract: expected 0032 ORM models are missing: " + ", ".join(missing)
        )
    return {name: getattr(module, name) for name in names}


def _count(engine: Any, model: Any) -> int:
    with Session(engine) as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _bundle_counts(engine: Any) -> dict[str, int]:
    models = _notification_models()
    return {
        "notifications": _count(engine, models["TenantNotification"]),
        "recipients": _count(engine, models["TenantNotificationRecipient"]),
        "receipts": _count(engine, models["TenantNotificationReceipt"]),
        "events": _count(engine, models["TenantNotificationEvent"]),
    }


def _rows(engine: Any, model: Any) -> list[Any]:
    with Session(engine) as session:
        return list(session.scalars(select(model).order_by(model.id)))


def _quality_source(engine: Any, alert_id: str) -> tuple[int, str]:
    with Session(engine) as session:
        alert = session.scalar(
            select(DatasetReleaseQualityAlert).where(
                DatasetReleaseQualityAlert.tenant_id == "tenant-a",
                DatasetReleaseQualityAlert.dataset_id == "dataset-a",
                DatasetReleaseQualityAlert.id == alert_id,
            )
        )
        assert alert is not None
        return int(alert.revision), str(alert.source_observation_digest)


def _approval_source(engine: Any, request_id: str) -> tuple[int, str]:
    with Session(engine) as session:
        request = session.scalar(
            select(TenantApprovalRequest).where(
                TenantApprovalRequest.tenant_id == "tenant-a",
                TenantApprovalRequest.id == request_id,
            )
        )
        assert request is not None
        return int(request.revision), str(request.payload_hash)


def _quality_call(
    operation: Callable[..., Any],
    engine: Any,
    *,
    alert_id: str,
    tenant_id: str = "tenant-a",
    dataset_id: str = "dataset-a",
    source_revision: int,
    source_digest: str,
    now: datetime = NOW,
) -> Any:
    """Contract: revision means Alert revision; digest means source observation digest."""
    return operation(
        engine,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        alert_id=alert_id,
        expected_source_revision=source_revision,
        expected_source_digest=source_digest,
        now=now,
    )


def _approval_call(
    operation: Callable[..., Any],
    engine: Any,
    *,
    request_id: str,
    tenant_id: str = "tenant-a",
    source_revision: int,
    source_digest: str,
    now: datetime = NOW,
) -> Any:
    """Contract: revision means Approval request revision; digest means payload hash."""
    return operation(
        engine,
        tenant_id=tenant_id,
        approval_request_id=request_id,
        expected_source_revision=source_revision,
        expected_source_digest=source_digest,
        now=now,
    )


def _add_member(
    session: Session,
    *,
    account_id: str,
    role: str = "member",
    status: str = "active",
) -> None:
    session.add(Account(id=account_id, name=account_id, email=f"{account_id}@notification.test"))
    session.add(
        TenantMember(
            account_id=account_id,
            tenant_id="tenant-a",
            role=role,
            status=status,
            revision=1,
            created_at=NOW,
            updated_at=NOW,
            updated_by="owner-a",
        )
    )


def _add_subscription(
    session: Session,
    subscription_model: Any,
    *,
    account_id: str,
    category: str,
    preference: str = "subscribed",
    minimum_severity: str = "info",
    muted_until: datetime | None = None,
) -> None:
    session.add(
        subscription_model(
            id=f"subscription-{account_id}-{category}",
            tenant_id="tenant-a",
            account_id=account_id,
            category=category,
            status="active",
            preference=preference,
            active_subscription_key=f"{account_id}:{category}",
            revision=1,
            minimum_severity=minimum_severity,
            muted_until=muted_until,
            created_at=NOW,
            created_by="owner-a",
            updated_at=NOW,
            updated_by="owner-a",
        )
    )


def _create_quality_alert(
    engine: Any,
    *,
    alert_type: str,
    observation_id: str = "observation-a",
) -> str:
    with Session(engine, expire_on_commit=False) as session:
        observation = session.scalar(
            select(DatasetReleaseQualityObservation).where(
                DatasetReleaseQualityObservation.tenant_id == "tenant-a",
                DatasetReleaseQualityObservation.dataset_id == "dataset-a",
                DatasetReleaseQualityObservation.id == observation_id,
            )
        )
        assert observation is not None
        alert = create_or_update_quality_alert_from_observation(
            session,
            observation,
            alert_type=alert_type,
            now=NOW,
        )
        assert alert is not None
        alert_id = alert.id
        session.commit()
        return alert_id


def _seed_quality_engine(
    tmp_path: Path,
    *,
    severity: str = "critical",
    alert_type: str = "quality_gate_blocked",
    subscriptions: tuple[Mapping[str, Any], ...] = (),
) -> tuple[Any, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine, _ = _release_engine(tmp_path)
    _seed_quality_authority(engine)

    models = _notification_models()
    observation_id = "observation-a"
    if severity != "critical":
        _add_observation(
            engine,
            "observation-warning-a",
            observed_at=NOW,
            gate_state="blocked",
            gate_reason="certification_expiring",
            severity=severity,
        )
        observation_id = "observation-warning-a"

    with Session(engine) as session:
        _add_member(session, account_id="dataset-owner-a", role="member")
        _add_member(session, account_id="admin-a", role="admin")
        session.flush()
        session.add(
            TenantWorkspaceAuthorizationPolicy(
                id="workspace-auth-policy-a",
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                mode="shadow",
                permission_model_version=1,
                revision=1,
                created_at=NOW,
                created_by="owner-a",
                updated_at=NOW,
                updated_by="owner-a",
            )
        )
        session.flush()
        dataset = session.get(Dataset, "dataset-a")
        assert dataset is not None
        dataset.owner_id = "dataset-owner-a"
        dataset.acl_mode = "dataset_acl"
        dataset.acl_revision = 2
        dataset.acl_enabled_at = NOW
        dataset.acl_enabled_by = "owner-a"

        for index, specification in enumerate(subscriptions):
            account_id = str(specification["account_id"])
            role = str(specification.get("role", "member"))
            _add_member(session, account_id=account_id, role=role)
            session.flush()
            if bool(specification.get("grant", True)):
                session.add(
                    DatasetAccessGrant(
                        id=f"grant-notification-{index}-{account_id}",
                        tenant_id="tenant-a",
                        dataset_id="dataset-a",
                        subject_type="account",
                        subject_id=account_id,
                        role="viewer",
                        status="active",
                        revision=1,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
            _add_subscription(
                session,
                models["TenantNotificationSubscription"],
                account_id=account_id,
                category="quality",
                preference=str(specification.get("preference", "subscribed")),
                minimum_severity=str(specification.get("minimum_severity", "info")),
                muted_until=(
                    NOW + timedelta(hours=1)
                    if str(specification.get("preference", "subscribed")) == "muted"
                    else None
                ),
            )
        session.commit()

    return engine, _create_quality_alert(
        engine,
        alert_type=alert_type,
        observation_id=observation_id,
    )


def _add_approval_source(engine: Any, *, unsafe: bool = False) -> tuple[str, str]:
    models = _notification_models()
    request_id = "approval-request-a"
    if unsafe:
        snapshot: dict[str, Any] = {
            "dataset_id": "dataset-a",
            "release_id": "release-a",
            "query": "SELECT private customer records",
            "execution_ticket": "rag4c-approval-ticket-secret",
            "authorization": "Bearer stage22-secret",
            "api_key": "sk_live_stage22_materializer",
            "webhook_url": "https://webhook.example/secret",
            "note": "private reviewer note",
        }
    else:
        snapshot = {
            "dataset_id": "dataset-a",
            "release_id": "release-a",
            "channel_id": "channel-production",
            "resource_revision": 1,
        }
    payload_hash = _digest(json.dumps(snapshot, sort_keys=True, separators=(",", ":")))

    with Session(engine) as session:
        _add_member(session, account_id="approver-a", role="editor")
        _add_member(session, account_id="approval-subscriber-a", role="member")
        session.flush()
        session.add(
            TenantApprovalPolicy(
                id="approval-policy-a",
                tenant_id="tenant-a",
                name="Release approval policy",
                action_type="knowledge_base_release_publish",
                resource_scope="dataset:dataset-a",
                active_scope_key="knowledge_base_release_publish:dataset-a",
                status="active",
                required_approvals=1,
                request_expiry_minutes=1440,
                revision=1,
                created_at=NOW,
                created_by="owner-a",
                updated_at=NOW,
                updated_by="owner-a",
            )
        )
        session.flush()
        session.add(
            TenantApprovalPolicyApprover(
                id="approval-approver-a",
                tenant_id="tenant-a",
                policy_id="approval-policy-a",
                approver_kind="account",
                approver_ref="approver-a",
                account_id="approver-a",
                group_id=None,
                status="active",
                revision=1,
                created_at=NOW,
                created_by="owner-a",
                updated_at=NOW,
                updated_by="owner-a",
            )
        )
        session.add(
            TenantApprovalRequest(
                id=request_id,
                tenant_id="tenant-a",
                policy_id="approval-policy-a",
                requester_id="owner-a",
                action_type="knowledge_base_release_publish",
                resource_type="dataset_release",
                resource_id="release-a",
                snapshot_json=snapshot,
                payload_hash=payload_hash,
                reason="publish the governed release",
                status="pending",
                required_approvals=1,
                received_approvals=0,
                idempotency_key="approval-source-request-key",
                expires_at=NOW + timedelta(hours=1),
                revision=1,
                created_at=NOW,
                created_by="owner-a",
                updated_at=NOW,
                updated_by="owner-a",
            )
        )
        _add_subscription(
            session,
            models["TenantNotificationSubscription"],
            account_id="approval-subscriber-a",
            category="approval",
            preference="subscribed",
            minimum_severity="info",
        )
        session.commit()
    return request_id, payload_hash


def _seed_approval_engine(tmp_path: Path, *, unsafe: bool = False) -> tuple[Any, str, str]:
    engine, _ = _release_engine(tmp_path)
    request_id, payload_hash = _add_approval_source(engine, unsafe=unsafe)
    return engine, request_id, payload_hash


def _assert_no_sensitive_values(value: Any) -> None:
    rendered = repr(value).casefold()
    for marker in SENSITIVE_MARKERS:
        assert marker.casefold() not in rendered


def _assert_empty_bundle(engine: Any) -> None:
    assert _bundle_counts(engine) == {
        "notifications": 0,
        "recipients": 0,
        "receipts": 0,
        "events": 0,
    }


def test_quality_alert_replay_is_deterministic_and_freezes_acl_recipient_reasons(
    tmp_path: Path,
) -> None:
    operation = _api("materialize_quality_alert_notifications")
    engine, alert_id = _seed_quality_engine(
        tmp_path,
        subscriptions=(
            {"account_id": "subscriber-a", "minimum_severity": "warning", "grant": True},
            {"account_id": "no-read-a", "minimum_severity": "warning", "grant": False},
        ),
    )
    try:
        source_revision, source_digest = _quality_source(engine, alert_id)
        first = _body(
            _quality_call(
                operation,
                engine,
                alert_id=alert_id,
                source_revision=source_revision,
                source_digest=source_digest,
            )
        )
        replay = _body(
            _quality_call(
                operation,
                engine,
                alert_id=alert_id,
                source_revision=source_revision,
                source_digest=source_digest,
            )
        )

        assert first["notification"]["source_kind"] == "quality_alert"
        assert first["notification"]["source_id"] == alert_id
        assert first["notification"]["source_revision"] == source_revision
        assert first["notification"]["source_digest"] == source_digest
        assert first["notification"]["target_route_code"] == "knowledge_quality_operations"
        assert first["notification"]["target_route_params_json"] == {
            "tenant_id": "tenant-a",
            "dataset_id": "dataset-a",
            "release_id": "release-a",
            "channel_id": "channel-production",
            "alert_id": alert_id,
        }
        assert (
            first["notification"]["notification_key"] == replay["notification"]["notification_key"]
        )
        assert first["notification"]["id"] == replay["notification"]["id"]

        models = _notification_models()
        notifications = _rows(engine, models["TenantNotification"])
        recipients = _rows(engine, models["TenantNotificationRecipient"])
        receipts = _rows(engine, models["TenantNotificationReceipt"])
        events = _rows(engine, models["TenantNotificationEvent"])
        assert len(notifications) == 1
        assert len(recipients) == 4
        assert len(receipts) == 4
        assert len(events) == 4
        assert {row.account_id: row.recipient_reason for row in recipients} == {
            "dataset-owner-a": "dataset_owner",
            "owner-a": "tenant_owner",
            "admin-a": "tenant_admin",
            "subscriber-a": "explicit_subscription",
        }
        assert all(row.status == "unread" for row in receipts)
        assert all(row.event_type == "materialized" and row.sequence == 1 for row in events)
        assert "no-read-a" not in {row.account_id for row in recipients}
        assert _bundle_counts(engine) == {
            "notifications": 1,
            "recipients": 4,
            "receipts": 4,
            "events": 4,
        }
        _assert_no_sensitive_values(first)
    finally:
        engine.dispose()


def test_pending_approval_replay_requires_current_eligible_approver_and_is_tenant_scoped(
    tmp_path: Path,
) -> None:
    operation = _api("materialize_pending_approval_notifications")
    engine, request_id, source_digest = _seed_approval_engine(tmp_path)
    try:
        source_revision, source_digest = _approval_source(engine, request_id)
        first = _body(
            _approval_call(
                operation,
                engine,
                request_id=request_id,
                source_revision=source_revision,
                source_digest=source_digest,
            )
        )
        replay = _body(
            _approval_call(
                operation,
                engine,
                request_id=request_id,
                source_revision=source_revision,
                source_digest=source_digest,
            )
        )

        assert first["notification"]["source_kind"] == "approval_pending_for_me"
        assert first["notification"]["source_id"] == request_id
        assert first["notification"]["source_dataset_id"] is None
        assert first["notification"]["category"] == "approval"
        assert first["notification"]["action_required"] is True
        assert first["notification"]["mandatory"] is True
        assert first["notification"]["target_route_code"] == "enterprise_approval"
        assert first["notification"]["target_route_params_json"] == {
            "tenant_id": "tenant-a",
            "approval_request_id": request_id,
        }
        assert (
            first["notification"]["notification_key"] == replay["notification"]["notification_key"]
        )
        assert first["notification"]["id"] == replay["notification"]["id"]

        models = _notification_models()
        recipients = _rows(engine, models["TenantNotificationRecipient"])
        assert len(recipients) == 1
        assert recipients[0].account_id == "approver-a"
        assert recipients[0].recipient_reason == "eligible_approver"
        assert len(_rows(engine, models["TenantNotification"])) == 1
        assert len(_rows(engine, models["TenantNotificationReceipt"])) == 1
        assert len(_rows(engine, models["TenantNotificationEvent"])) == 1
        _assert_no_sensitive_values(first)
    finally:
        engine.dispose()


def test_subscription_mute_and_minimum_severity_filter_optional_warning_but_mandatory_critical_overrides(
    tmp_path: Path,
) -> None:
    operation = _api("materialize_quality_alert_notifications")
    optional_engine, optional_alert_id = _seed_quality_engine(
        tmp_path / "optional",
        severity="warning",
        alert_type="certification_expiring",
        subscriptions=(
            {
                "account_id": "muted-warning-a",
                "preference": "muted",
                "minimum_severity": "info",
                "grant": True,
            },
            {
                "account_id": "critical-threshold-a",
                "preference": "subscribed",
                "minimum_severity": "critical",
                "grant": True,
            },
            {
                "account_id": "warning-threshold-a",
                "preference": "subscribed",
                "minimum_severity": "warning",
                "grant": True,
            },
        ),
    )
    critical_dir = tmp_path / "critical"
    critical_dir.mkdir()
    critical_engine, critical_alert_id = _seed_quality_engine(
        critical_dir,
        severity="critical",
        alert_type="quality_gate_blocked",
        subscriptions=(
            {
                "account_id": "muted-critical-a",
                "preference": "muted",
                "minimum_severity": "critical",
                "grant": True,
            },
        ),
    )
    try:
        optional_revision, optional_digest = _quality_source(optional_engine, optional_alert_id)
        optional = _body(
            _quality_call(
                operation,
                optional_engine,
                alert_id=optional_alert_id,
                source_revision=optional_revision,
                source_digest=optional_digest,
            )
        )
        optional_recipients = _rows(
            optional_engine,
            _notification_models()["TenantNotificationRecipient"],
        )
        optional_ids = {row.account_id for row in optional_recipients}
        assert optional["notification"]["mandatory"] is False
        assert "warning-threshold-a" in optional_ids
        assert "muted-warning-a" not in optional_ids
        assert "critical-threshold-a" not in optional_ids

        critical_revision, critical_digest = _quality_source(critical_engine, critical_alert_id)
        critical = _body(
            _quality_call(
                operation,
                critical_engine,
                alert_id=critical_alert_id,
                source_revision=critical_revision,
                source_digest=critical_digest,
            )
        )
        critical_recipients = _rows(
            critical_engine,
            _notification_models()["TenantNotificationRecipient"],
        )
        assert critical["notification"]["mandatory"] is True
        assert "muted-critical-a" in {row.account_id for row in critical_recipients}
    finally:
        optional_engine.dispose()
        critical_engine.dispose()


def test_materializer_writes_notification_bundle_atomically_when_event_insert_fails(
    tmp_path: Path,
) -> None:
    operation = _api("materialize_quality_alert_notifications")
    engine, alert_id = _seed_quality_engine(tmp_path)
    source_revision, source_digest = _quality_source(engine, alert_id)

    def fail_event_insert(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        normalized = statement.casefold()
        if normalized.lstrip().startswith("insert") and "tenant_notification_events" in normalized:
            raise RuntimeError("forced notification event insert failure")

    event.listen(engine, "before_cursor_execute", fail_event_insert)
    try:
        with pytest.raises(Exception):
            _quality_call(
                operation,
                engine,
                alert_id=alert_id,
                source_revision=source_revision,
                source_digest=source_digest,
            )
    finally:
        event.remove(engine, "before_cursor_execute", fail_event_insert)

    try:
        _assert_empty_bundle(engine)
    finally:
        engine.dispose()


@pytest.mark.parametrize("stale_field", ("revision", "digest"))
def test_quality_source_stale_revision_or_digest_fails_closed_without_partial_rows(
    tmp_path: Path,
    stale_field: str,
) -> None:
    operation = _api("materialize_quality_alert_notifications")
    engine, alert_id = _seed_quality_engine(tmp_path)
    try:
        source_revision, source_digest = _quality_source(engine, alert_id)
        if stale_field == "revision":
            source_revision = source_revision + 1
        else:
            source_digest = "f" * 64
        with pytest.raises(Exception):
            _quality_call(
                operation,
                engine,
                alert_id=alert_id,
                source_revision=source_revision,
                source_digest=source_digest,
            )
        _assert_empty_bundle(engine)
    finally:
        engine.dispose()


@pytest.mark.parametrize("stale_field", ("revision", "digest"))
def test_approval_source_stale_revision_or_digest_fails_closed_without_partial_rows(
    tmp_path: Path,
    stale_field: str,
) -> None:
    operation = _api("materialize_pending_approval_notifications")
    engine, request_id, _ = _seed_approval_engine(tmp_path)
    try:
        source_revision, source_digest = _approval_source(engine, request_id)
        if stale_field == "revision":
            source_revision = source_revision + 1
        else:
            source_digest = "f" * 64
        with pytest.raises(Exception):
            _approval_call(
                operation,
                engine,
                request_id=request_id,
                source_revision=source_revision,
                source_digest=source_digest,
            )
        _assert_empty_bundle(engine)
    finally:
        engine.dispose()


def test_approval_source_revalidates_current_approver_eligibility_before_materialization(
    tmp_path: Path,
) -> None:
    operation = _api("materialize_pending_approval_notifications")
    engine, request_id, _ = _seed_approval_engine(tmp_path)
    try:
        with Session(engine) as session:
            approver = session.scalar(
                select(TenantApprovalPolicyApprover).where(
                    TenantApprovalPolicyApprover.tenant_id == "tenant-a",
                    TenantApprovalPolicyApprover.id == "approval-approver-a",
                )
            )
            assert approver is not None
            approver.status = "disabled"
            approver.revision = 2
            session.commit()

        source_revision, source_digest = _approval_source(engine, request_id)
        with pytest.raises(Exception):
            _approval_call(
                operation,
                engine,
                request_id=request_id,
                source_revision=source_revision,
                source_digest=source_digest,
            )
        _assert_empty_bundle(engine)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("tenant_id", "dataset_id"),
    (("tenant-b", "dataset-a"), ("tenant-a", "dataset-other")),
)
def test_quality_materializer_rejects_cross_tenant_or_cross_dataset_source_scope(
    tmp_path: Path,
    tenant_id: str,
    dataset_id: str,
) -> None:
    operation = _api("materialize_quality_alert_notifications")
    engine, alert_id = _seed_quality_engine(tmp_path)
    try:
        source_revision, source_digest = _quality_source(engine, alert_id)
        with pytest.raises(Exception):
            _quality_call(
                operation,
                engine,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                alert_id=alert_id,
                source_revision=source_revision,
                source_digest=source_digest,
            )
        _assert_empty_bundle(engine)
    finally:
        engine.dispose()


def test_unsafe_approval_source_payload_fails_closed_and_is_not_persisted_or_reflected(
    tmp_path: Path,
) -> None:
    operation = _api("materialize_pending_approval_notifications")
    engine, request_id, _ = _seed_approval_engine(tmp_path, unsafe=True)
    try:
        source_revision, source_digest = _approval_source(engine, request_id)
        with pytest.raises(Exception) as exc_info:
            _approval_call(
                operation,
                engine,
                request_id=request_id,
                source_revision=source_revision,
                source_digest=source_digest,
            )
        _assert_no_sensitive_values(str(exc_info.value))
        _assert_empty_bundle(engine)
    finally:
        engine.dispose()


def test_reconcile_notification_sources_replays_both_allow_listed_sources_without_duplicates(
    tmp_path: Path,
) -> None:
    operation = _api("reconcile_notification_sources")
    engine, alert_id = _seed_quality_engine(tmp_path)
    request_id, _ = _add_approval_source(engine)
    try:
        first = _body(operation(engine, tenant_id="tenant-a", now=NOW))
        replay = _body(operation(engine, tenant_id="tenant-a", now=NOW + timedelta(minutes=1)))
        models = _notification_models()
        notifications = _rows(engine, models["TenantNotification"])
        assert {row.source_kind for row in notifications} == {
            "quality_alert",
            "approval_pending_for_me",
        }
        assert {row.source_id for row in notifications} == {alert_id, request_id}
        assert len(notifications) == 2
        assert _bundle_counts(engine)["notifications"] == 2
        assert first["notification_count"] == replay["notification_count"] == 2
        _assert_no_sensitive_values(first)
        _assert_no_sensitive_values(replay)
    finally:
        engine.dispose()


@pytest.mark.parametrize("source_kind", ("quality_alert", "approval_pending_for_me"))
def test_concurrent_materialization_has_one_canonical_notification_bundle(
    tmp_path: Path,
    source_kind: str,
) -> None:
    if source_kind == "quality_alert":
        operation = _api("materialize_quality_alert_notifications")
        engine, source_id = _seed_quality_engine(tmp_path)
        source_revision, source_digest = _quality_source(engine, source_id)

        def invoke() -> Any:
            return _body(
                _quality_call(
                    operation,
                    engine,
                    alert_id=source_id,
                    source_revision=source_revision,
                    source_digest=source_digest,
                )
            )

    else:
        operation = _api("materialize_pending_approval_notifications")
        engine, source_id, source_digest = _seed_approval_engine(tmp_path)
        source_revision, source_digest = _approval_source(engine, source_id)

        def invoke() -> Any:
            return _body(
                _approval_call(
                    operation,
                    engine,
                    request_id=source_id,
                    source_revision=source_revision,
                    source_digest=source_digest,
                )
            )

    barrier = Barrier(2)

    def worker() -> Any:
        barrier.wait(timeout=10)
        return invoke()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker) for _ in range(2)]
            outcomes = [future.result(timeout=30) for future in futures]
        assert (
            outcomes[0]["notification"]["notification_key"]
            == outcomes[1]["notification"]["notification_key"]
        )
        assert outcomes[0]["notification"]["id"] == outcomes[1]["notification"]["id"]
        assert _bundle_counts(engine) == {
            "notifications": 1,
            "recipients": 1 if source_kind == "approval_pending_for_me" else 3,
            "receipts": 1 if source_kind == "approval_pending_for_me" else 3,
            "events": 1 if source_kind == "approval_pending_for_me" else 3,
        }
    finally:
        engine.dispose()


def test_registered_notification_materializer_dispatches_through_real_quality_flow(
    tmp_path: Path,
) -> None:
    module = _materializer()
    from core.notification_materializers import (
        NotificationMaterializationRequest,
        NotificationMaterializerPolicy,
        register_notification_materializer,
        unregister_notification_materializer,
    )

    engine, alert_id = _seed_quality_engine(tmp_path)
    revision, digest = _quality_source(engine, alert_id)
    discovered_tenants: list[str] = []
    materialized_kinds: list[str] = []

    def materialize(request: NotificationMaterializationRequest) -> dict[str, Any]:
        materialized_kinds.append(request.source_kind)
        return module.materialize_quality_alert_notifications(
            request.engine,
            tenant_id=request.tenant_id,
            dataset_id=request.source_scope["dataset_id"],
            alert_id=request.source_id,
            expected_source_revision=request.expected_source_revision,
            expected_source_digest=request.expected_source_digest,
            now=request.now,
        )

    def discover(context: Any) -> tuple[NotificationMaterializationRequest, ...]:
        discovered_tenants.append(context.tenant_id)
        return (
            NotificationMaterializationRequest(
                source_kind="quality_alert_alias",
                engine=context.engine,
                tenant_id=context.tenant_id,
                source_id=alert_id,
                expected_source_revision=revision,
                expected_source_digest=digest,
                now=context.now,
                source_scope={"dataset_id": "dataset-a"},
            ),
        )

    register_notification_materializer(
        "quality_alert_alias",
        NotificationMaterializerPolicy(
            order=99,
            materialize=materialize,
            discover=discover,
        ),
    )
    try:
        request = NotificationMaterializationRequest(
            source_kind="quality_alert_alias",
            engine=engine,
            tenant_id="tenant-a",
            source_id=alert_id,
            expected_source_revision=revision,
            expected_source_digest=digest,
            now=NOW,
            source_scope={"dataset_id": "dataset-a"},
        )
        result = module.materialize_notification_source(request)
        body = _body(result)
        assert body["notification"]["source_kind"] == "quality_alert"
        assert body["receipt_count"] > 0
        assert materialized_kinds == ["quality_alert_alias"]

        reconciled = module.reconcile_notification_sources(
            engine, tenant_id="tenant-a", now=NOW
        )
        assert discovered_tenants == ["tenant-a"]
        assert materialized_kinds == ["quality_alert_alias", "quality_alert_alias"]
        assert reconciled["notification_count"] == 1
    finally:
        unregister_notification_materializer("quality_alert_alias")
        engine.dispose()


def test_materializer_reconciliation_has_no_source_kind_query_or_dispatch_branches() -> None:
    import inspect

    source = inspect.getsource(_materializer().reconcile_notification_sources)

    assert "DatasetReleaseQualityAlert" not in source
    assert "TenantApprovalRequest" not in source
    assert "materialize_quality_alert_notifications" not in source
    assert "materialize_pending_approval_notifications" not in source
    assert "notification_materializer_snapshot" in source


def test_notification_materializer_registration_rejects_duplicate_and_bad_callbacks() -> None:
    from core.notification_materializers import (
        NotificationMaterializerPolicy,
        register_notification_materializer,
        unregister_notification_materializer,
    )

    policy = NotificationMaterializerPolicy(
        order=90,
        materialize=lambda _request: {},
        discover=lambda _context: (),
    )
    register_notification_materializer("custom_notice", policy)
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_notification_materializer("custom_notice", policy)
        with pytest.raises(TypeError, match="accept one positional request"):
            register_notification_materializer(
                "invalid_notice",
                NotificationMaterializerPolicy(
                    order=91,
                    materialize=lambda: {},
                    discover=lambda _context: (),
                ),
            )
        with pytest.raises(TypeError, match="accept one positional discovery context"):
            register_notification_materializer(
                "invalid_notice",
                NotificationMaterializerPolicy(
                    order=91,
                    materialize=lambda _request: {},
                    discover=lambda: (),
                ),
            )
    finally:
        unregister_notification_materializer("custom_notice")



def test_notification_materializer_builtin_names_are_reserved_before_and_after_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.notification_materializers import (
        InvalidNotificationMaterializer,
        NotificationMaterializerPolicy,
        register_notification_materializer,
        unregister_notification_materializer,
    )
    from core.providers import ProviderRegistry
    import core.notification_materializers as materializers

    policy = NotificationMaterializerPolicy(
        order=1,
        materialize=lambda _request: {},
        discover=lambda _context: (),
    )
    with pytest.raises(InvalidNotificationMaterializer, match="built-in.*immutable"):
        register_notification_materializer("quality_alert", policy, replace=True)
    with pytest.raises(InvalidNotificationMaterializer, match="built-in.*cannot be unregistered"):
        unregister_notification_materializer("quality_alert")

    # Simulate the pre-install state: built-in keys stay reserved before their
    # schema-backed policies are registered during enterprise-module import.
    monkeypatch.setattr(
        materializers,
        "_NOTIFICATION_MATERIALIZERS",
        ProviderRegistry("notification materializer test"),
    )
    monkeypatch.setattr(materializers, "_BUILTIN_POLICIES", {})
    with pytest.raises(InvalidNotificationMaterializer, match="built-in.*immutable"):
        register_notification_materializer("quality_alert", policy)
    assert "NOTIFICATION_MATERIALIZERS" not in materializers.__all__
    assert "install_builtin_notification_materializers" not in materializers.__all__
    assert not hasattr(materializers, "install_builtin_notification_materializers")


def test_notification_materializer_direct_dispatch_fails_closed_if_builtin_is_replaced() -> None:
    module = _materializer()
    from core.notification_materializers import (
        InvalidNotificationMaterializer,
        NotificationMaterializationRequest,
        NotificationMaterializerPolicy,
        _NOTIFICATION_MATERIALIZERS,
    )

    original_factory = _NOTIFICATION_MATERIALIZERS.get_factory("quality_alert")
    replacement = NotificationMaterializerPolicy(
        order=10,
        materialize=lambda _request: {"notification": {}, "receipt_count": 0},
        discover=lambda _context: (),
    )
    _NOTIFICATION_MATERIALIZERS.register(
        "quality_alert", lambda _context: replacement, replace=True
    )
    try:
        request = NotificationMaterializationRequest(
            source_kind="quality_alert",
            engine=object(),
            tenant_id="tenant-a",
            source_id="alert-a",
            expected_source_revision=1,
            expected_source_digest="digest",
            now=NOW,
            source_scope={"dataset_id": "dataset-a"},
        )
        with pytest.raises(InvalidNotificationMaterializer, match="built-in.*replaced"):
            module.materialize_notification_source(request)
    finally:
        _NOTIFICATION_MATERIALIZERS.register(
            "quality_alert", original_factory, replace=True
        )


def test_notification_materialization_request_copies_and_freezes_scope() -> None:
    from core.notification_materializers import NotificationMaterializationRequest

    source_scope = {"dataset_id": "dataset-a"}
    request = NotificationMaterializationRequest(
        source_kind="quality_alert",
        engine=object(),
        tenant_id="tenant-a",
        source_id="alert-a",
        expected_source_revision=1,
        expected_source_digest="digest",
        now=NOW,
        source_scope=source_scope,
    )
    source_scope["dataset_id"] = "dataset-b"
    assert request.source_scope["dataset_id"] == "dataset-a"
    with pytest.raises(TypeError):
        request.source_scope["dataset_id"] = "dataset-c"  # type: ignore[index]
