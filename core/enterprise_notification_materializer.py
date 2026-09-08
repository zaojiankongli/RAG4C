"""Replay-safe Stage 22 Notification Center source materialization.

Allow-listed Stage 21 sources are revalidated and projected into one immutable
Notification, Recipient, unread Receipt, and materialized Event transaction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.enterprise_access_control import (
    DatasetAccessControlUnavailable,
    evaluate_dataset_permissions,
)
from core.enterprise_acl_idempotency import engine_serialization_lock
from core.enterprise_notification_center import (
    NotificationAuthorityError,
    canonical_assignment_digest,
    canonical_notification_digest,
    canonical_notification_event,
    project_notification_payload,
)
from core.knowledge_permissions import KNOWLEDGE_READ
from models.orm import (
    Dataset,
    DatasetReleaseQualityAlert,
    DatasetReleaseQualityObservation,
    TenantApprovalPolicy,
    TenantApprovalPolicyApprover,
    TenantApprovalRequest,
    TenantGroup,
    TenantGroupMember,
    TenantMember,
    TenantNotification,
    TenantNotificationEvent,
    TenantNotificationReceipt,
    TenantNotificationRecipient,
    TenantNotificationSubscription,
)

SYSTEM_ACTOR = "system:notification-materializer"
_ACTIVE_ALERT_STATUSES = frozenset({"open", "acknowledged", "suppressed"})
_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class NotificationMaterializationError(RuntimeError):
    """Base class for fail-closed materializer failures."""


class NotificationMaterializationInvalid(NotificationMaterializationError, ValueError):
    """The caller or persisted source cannot be safely materialized."""


class NotificationMaterializationConflict(NotificationMaterializationError):
    """Expected source authority no longer matches persisted currentness."""


class NotificationMaterializationForbidden(NotificationMaterializationError):
    """No currently authorized recipient can be proven."""


class NotificationMaterializationUnavailable(NotificationMaterializationError):
    """Required source or Notification authority is unavailable."""


def _component(value: Any, field: str, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise NotificationMaterializationInvalid(f"{field} is invalid")
    value = value.strip()
    if not value or len(value) > maximum or _ID_RE.fullmatch(value) is None:
        raise NotificationMaterializationInvalid(f"{field} is invalid")
    return value


def _revision(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise NotificationMaterializationInvalid("expected source revision is invalid")
    return value


def _digest(value: Any) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value.strip().casefold()) is None:
        raise NotificationMaterializationInvalid("expected source digest is invalid")
    return value.strip().casefold()


def _now(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise NotificationMaterializationInvalid("now must be a datetime")
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _utc_naive(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise NotificationMaterializationUnavailable(f"{field} is unavailable")
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _projected_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _utc_naive(value, "occurred_at")
    if not isinstance(value, str):
        raise NotificationMaterializationInvalid("projected occurred_at is invalid")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return _utc_naive(datetime.fromisoformat(raw), "occurred_at")
    except ValueError as exc:
        raise NotificationMaterializationInvalid("projected occurred_at is invalid") from exc


def _stable_id(prefix: str, digest: str) -> str:
    return prefix + digest[: 64 - len(prefix)]


def _json_hash(value: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NotificationMaterializationInvalid("approval source is unsafe") from exc
    return hashlib.sha256(encoded).hexdigest()


def _safe_payload(**values: Any) -> dict[str, Any]:
    try:
        return project_notification_payload(**values)
    except NotificationAuthorityError as exc:
        raise NotificationMaterializationInvalid("notification source is unsafe") from exc


def _notification_projection(row: TenantNotification) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "source_kind": row.source_kind,
        "source_id": row.source_id,
        "source_revision": int(row.source_revision),
        "source_dataset_id": row.source_dataset_id,
        "category": row.category,
        "severity": row.severity,
        "action_required": bool(row.action_required),
        "mandatory": bool(row.mandatory),
        "notification_key": row.notification_key,
        "source_digest": row.source_digest,
        "title_code": row.title_code,
        "summary_code": row.summary_code,
        "safe_facts_json": dict(row.safe_facts_json or {}),
        "target_route_code": row.target_route_code,
        "target_route_params_json": dict(row.target_route_params_json or {}),
        "occurred_at": _utc_naive(row.occurred_at, "notification.occurred_at").isoformat(
            timespec="microseconds"
        )
        + "Z",
        "created_by": row.created_by,
    }


def _recipient_projection(row: TenantNotificationRecipient) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "notification_id": row.notification_id,
        "account_id": row.account_id,
        "recipient_reason": row.recipient_reason,
        "mandatory": bool(row.mandatory),
        "assignment_digest": row.assignment_digest,
    }


def _notification_matches(row: TenantNotification, payload: Mapping[str, Any]) -> bool:
    fields = (
        "tenant_id",
        "source_kind",
        "source_id",
        "source_revision",
        "source_dataset_id",
        "category",
        "severity",
        "action_required",
        "mandatory",
        "notification_key",
        "source_digest",
        "title_code",
        "summary_code",
        "target_route_code",
    )
    for field in fields:
        stored = getattr(row, field)
        expected = payload[field]
        if field in {"source_revision"}:
            stored = int(stored)
        if field in {"action_required", "mandatory"}:
            stored = bool(stored)
        if stored != expected:
            return False
    return (
        dict(row.safe_facts_json or {}) == dict(payload["safe_facts_json"])
        and dict(row.target_route_params_json or {}) == dict(payload["target_route_params_json"])
        and _utc_naive(row.occurred_at, "notification.occurred_at")
        == _projected_time(payload["occurred_at"])
    )


def _active_members(session: Session, tenant_id: str) -> dict[str, TenantMember]:
    rows = session.scalars(
        select(TenantMember)
        .where(TenantMember.tenant_id == tenant_id, TenantMember.status == "active")
        .order_by(TenantMember.account_id)
        .with_for_update()
    )
    return {row.account_id: row for row in rows}


def _allows_read(
    engine: Any,
    session: Session,
    tenant_id: str,
    dataset_id: str,
    member: TenantMember,
) -> bool:
    try:
        decision = evaluate_dataset_permissions(
            engine,
            tenant_id,
            member.account_id,
            member.role,
            dataset_id,
            session=session,
            lock_for_update=True,
        )
    except DatasetAccessControlUnavailable as exc:
        raise NotificationMaterializationUnavailable(
            "dataset access authority is unavailable"
        ) from exc
    return decision.allows(KNOWLEDGE_READ)


def _subscription_allows(
    row: TenantNotificationSubscription,
    severity: str,
    mandatory: bool,
    now: datetime,
) -> bool:
    if row.status != "active" or row.minimum_severity not in _SEVERITY_RANK:
        return False
    if _SEVERITY_RANK[severity] < _SEVERITY_RANK[row.minimum_severity]:
        return False
    if mandatory or row.preference == "subscribed":
        return True
    if row.preference != "muted":
        raise NotificationMaterializationUnavailable("notification subscription is invalid")
    return row.muted_until is not None and _utc_naive(row.muted_until, "muted_until") <= now


def _quality_recipients(
    engine: Any,
    session: Session,
    tenant_id: str,
    dataset: Dataset,
    severity: str,
    mandatory: bool,
    now: datetime,
) -> list[tuple[str, str]]:
    members = _active_members(session, tenant_id)
    resolved: dict[str, str] = {}
    owner_id = str(dataset.owner_id or "").strip()
    if (
        owner_id
        and owner_id in members
        and _allows_read(engine, session, tenant_id, dataset.id, members[owner_id])
    ):
        resolved[owner_id] = "dataset_owner"
    for account_id, member in members.items():
        if account_id in resolved or member.role not in {"owner", "admin"}:
            continue
        if _allows_read(engine, session, tenant_id, dataset.id, member):
            resolved[account_id] = "tenant_owner" if member.role == "owner" else "tenant_admin"
    subscriptions = session.scalars(
        select(TenantNotificationSubscription)
        .where(
            TenantNotificationSubscription.tenant_id == tenant_id,
            TenantNotificationSubscription.category == "quality",
            TenantNotificationSubscription.status == "active",
        )
        .order_by(TenantNotificationSubscription.account_id)
        .with_for_update()
    )
    for subscription in subscriptions:
        account_id = subscription.account_id
        member = members.get(account_id)
        if (
            account_id in resolved
            or member is None
            or not _subscription_allows(subscription, severity, mandatory, now)
        ):
            continue
        if _allows_read(engine, session, tenant_id, dataset.id, member):
            resolved[account_id] = "explicit_subscription"
    return sorted(resolved.items())


def _approval_recipients(
    session: Session,
    tenant_id: str,
    request: TenantApprovalRequest,
) -> list[tuple[str, str]]:
    policy = session.scalar(
        select(TenantApprovalPolicy)
        .where(
            TenantApprovalPolicy.tenant_id == tenant_id,
            TenantApprovalPolicy.id == request.policy_id,
        )
        .with_for_update()
    )
    if policy is None or policy.status != "active":
        raise NotificationMaterializationUnavailable("approval policy is unavailable")
    members = _active_members(session, tenant_id)
    approvers = session.scalars(
        select(TenantApprovalPolicyApprover)
        .where(
            TenantApprovalPolicyApprover.tenant_id == tenant_id,
            TenantApprovalPolicyApprover.policy_id == request.policy_id,
            TenantApprovalPolicyApprover.status == "active",
        )
        .order_by(TenantApprovalPolicyApprover.id)
        .with_for_update()
    )
    resolved: set[str] = set()
    role_refs: set[str] = set()
    group_refs: set[str] = set()
    for row in approvers:
        if row.approver_kind == "account":
            account_id = str(row.account_id or row.approver_ref)
            if account_id in members:
                resolved.add(account_id)
        elif row.approver_kind == "role":
            role_refs.add(str(row.approver_ref).casefold())
        elif row.approver_kind == "group":
            group_refs.add(str(row.group_id or row.approver_ref))
        else:
            raise NotificationMaterializationUnavailable("approval approver authority is invalid")
    resolved.update(
        account_id for account_id, member in members.items() if member.role.casefold() in role_refs
    )
    if group_refs:
        rows = session.execute(
            select(TenantGroupMember.account_id)
            .join(
                TenantGroup,
                (TenantGroup.tenant_id == TenantGroupMember.tenant_id)
                & (TenantGroup.id == TenantGroupMember.group_id),
            )
            .where(
                TenantGroupMember.tenant_id == tenant_id,
                TenantGroupMember.group_id.in_(group_refs),
                TenantGroupMember.status == "active",
                TenantGroup.status == "active",
            )
            .with_for_update()
        )
        resolved.update(str(row[0]) for row in rows if str(row[0]) in members)
    resolved.discard(str(request.requester_id))
    return [(account_id, "eligible_approver") for account_id in sorted(resolved)]


def _persist_bundle(
    session: Session,
    payload: Mapping[str, Any],
    recipients: Iterable[tuple[str, str]],
    now: datetime,
) -> dict[str, Any]:
    tenant_id = str(payload["tenant_id"])
    notification_key = str(payload["notification_key"])
    notification_id = _stable_id("notification-", notification_key)
    notification = session.scalar(
        select(TenantNotification)
        .where(
            TenantNotification.tenant_id == tenant_id,
            TenantNotification.notification_key == notification_key,
        )
        .with_for_update()
    )
    created_notification = notification is None
    if notification is None:
        notification = TenantNotification(
            id=notification_id,
            tenant_id=tenant_id,
            source_kind=str(payload["source_kind"]),
            source_id=str(payload["source_id"]),
            source_revision=int(payload["source_revision"]),
            source_dataset_id=payload["source_dataset_id"],
            category=str(payload["category"]),
            severity=str(payload["severity"]),
            action_required=bool(payload["action_required"]),
            mandatory=bool(payload["mandatory"]),
            notification_key=notification_key,
            source_digest=str(payload["source_digest"]),
            title_code=str(payload["title_code"]),
            summary_code=str(payload["summary_code"]),
            safe_facts_json=dict(payload["safe_facts_json"]),
            target_route_code=str(payload["target_route_code"]),
            target_route_params_json=dict(payload["target_route_params_json"]),
            occurred_at=_projected_time(payload["occurred_at"]),
            created_at=now,
            created_by=SYSTEM_ACTOR,
        )
        session.add(notification)
        session.flush()
    elif notification.id != notification_id or not _notification_matches(notification, payload):
        raise NotificationMaterializationConflict("notification identity conflicts with source")

    created_recipients = 0
    for account_id, reason in sorted(set(recipients)):
        assignment_digest = canonical_assignment_digest(
            tenant_id=tenant_id,
            notification_id=notification.id,
            account_id=account_id,
            recipient_reason=reason,
            mandatory=bool(payload["mandatory"]),
        )
        recipient = session.scalar(
            select(TenantNotificationRecipient)
            .where(
                TenantNotificationRecipient.tenant_id == tenant_id,
                TenantNotificationRecipient.notification_id == notification.id,
                TenantNotificationRecipient.account_id == account_id,
            )
            .with_for_update()
        )
        if recipient is not None:
            stored = canonical_assignment_digest(
                tenant_id=tenant_id,
                notification_id=notification.id,
                account_id=account_id,
                recipient_reason=recipient.recipient_reason,
                mandatory=bool(recipient.mandatory),
            )
            if not hmac.compare_digest(stored, recipient.assignment_digest):
                raise NotificationMaterializationConflict("recipient assignment is invalid")
            continue
        recipient = TenantNotificationRecipient(
            id=_stable_id("notification-recipient-", assignment_digest),
            tenant_id=tenant_id,
            notification_id=notification.id,
            account_id=account_id,
            recipient_reason=reason,
            mandatory=bool(payload["mandatory"]),
            assignment_digest=assignment_digest,
            assigned_at=now,
        )
        session.add(recipient)
        session.flush()
        receipt_digest = hashlib.sha256(
            f"{tenant_id}\0{notification.id}\0{account_id}".encode()
        ).hexdigest()
        session.add(
            TenantNotificationReceipt(
                id=_stable_id("notification-receipt-", receipt_digest),
                tenant_id=tenant_id,
                notification_id=notification.id,
                account_id=account_id,
                status="unread",
                revision=1,
                read_at=None,
                archived_at=None,
                updated_at=now,
            )
        )
        # The database append guard requires the Receipt authority to exist before
        # the immutable first Event is inserted. Keep both writes in this transaction.
        session.flush()
        request_id = f"materialize-{notification_key[:32]}-{account_id}"
        event = canonical_notification_event(
            tenant_id=tenant_id,
            notification_id=notification.id,
            account_id=account_id,
            sequence=1,
            event_type="materialized",
            previous_event_digest=None,
            actor_id=SYSTEM_ACTOR,
            request_id=request_id,
            safe_snapshot={
                "status": "unread",
                "revision": 1,
                "source_kind": payload["source_kind"],
                "route_code": payload["target_route_code"],
                "recipient_reason": reason,
                "mandatory": bool(payload["mandatory"]),
            },
            occurred_at=now,
        )
        session.add(
            TenantNotificationEvent(
                id=_stable_id("notification-event-", event["event_digest"]),
                tenant_id=tenant_id,
                notification_id=notification.id,
                account_id=account_id,
                sequence=1,
                event_type="materialized",
                previous_event_digest=None,
                event_digest=event["event_digest"],
                actor_id=SYSTEM_ACTOR,
                request_id=request_id,
                safe_snapshot_json=dict(event["safe_snapshot"]),
                occurred_at=now,
            )
        )
        created_recipients += 1
    session.flush()
    all_recipients = list(
        session.scalars(
            select(TenantNotificationRecipient)
            .where(
                TenantNotificationRecipient.tenant_id == tenant_id,
                TenantNotificationRecipient.notification_id == notification.id,
            )
            .order_by(TenantNotificationRecipient.account_id)
        )
    )
    receipt_count = int(
        session.scalar(
            select(func.count())
            .select_from(TenantNotificationReceipt)
            .where(
                TenantNotificationReceipt.tenant_id == tenant_id,
                TenantNotificationReceipt.notification_id == notification.id,
            )
        )
        or 0
    )
    event_count = int(
        session.scalar(
            select(func.count())
            .select_from(TenantNotificationEvent)
            .where(
                TenantNotificationEvent.tenant_id == tenant_id,
                TenantNotificationEvent.notification_id == notification.id,
                TenantNotificationEvent.event_type == "materialized",
            )
        )
        or 0
    )
    if receipt_count != len(all_recipients) or event_count != len(all_recipients):
        raise NotificationMaterializationUnavailable("notification bundle is incomplete")
    return {
        "notification": _notification_projection(notification),
        "recipients": [_recipient_projection(row) for row in all_recipients],
        "receipt_count": receipt_count,
        "event_count": event_count,
        "replayed": not created_notification and created_recipients == 0,
    }


def materialize_quality_alert_notifications(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    alert_id: str,
    expected_source_revision: int,
    expected_source_digest: str,
    now: datetime,
) -> dict[str, Any]:
    tenant = _component(tenant_id, "tenant_id", 64)
    dataset_id = _component(dataset_id, "dataset_id", 64)
    alert_id = _component(alert_id, "alert_id", 64)
    expected_revision = _revision(expected_source_revision)
    expected_digest = _digest(expected_source_digest)
    timestamp = _now(now)
    with engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            alert = session.scalar(
                select(DatasetReleaseQualityAlert)
                .where(
                    DatasetReleaseQualityAlert.tenant_id == tenant,
                    DatasetReleaseQualityAlert.dataset_id == dataset_id,
                    DatasetReleaseQualityAlert.id == alert_id,
                )
                .with_for_update()
            )
            if alert is None:
                raise NotificationMaterializationForbidden("quality source scope is unavailable")
            if alert.status not in _ACTIVE_ALERT_STATUSES:
                raise NotificationMaterializationUnavailable("quality alert is not active")
            if int(alert.revision) != expected_revision or not hmac.compare_digest(
                str(alert.source_observation_digest), expected_digest
            ):
                raise NotificationMaterializationConflict("quality source is stale")
            observation = session.scalar(
                select(DatasetReleaseQualityObservation)
                .where(
                    DatasetReleaseQualityObservation.tenant_id == tenant,
                    DatasetReleaseQualityObservation.dataset_id == dataset_id,
                    DatasetReleaseQualityObservation.id == alert.source_observation_id,
                )
                .with_for_update()
            )
            if observation is None or not hmac.compare_digest(
                str(observation.observation_digest), expected_digest
            ):
                raise NotificationMaterializationConflict("quality source digest is stale")
            if (
                observation.release_id != alert.release_id
                or observation.channel_id != alert.channel_id
                or observation.release_role != alert.release_role
            ):
                raise NotificationMaterializationConflict(
                    "quality source authority is inconsistent"
                )
            dataset = session.scalar(
                select(Dataset)
                .where(Dataset.tenant_id == tenant, Dataset.id == dataset_id)
                .with_for_update()
            )
            if dataset is None or dataset.status != "active":
                raise NotificationMaterializationUnavailable("dataset authority is unavailable")
            severity = str(alert.severity)
            if severity not in {"warning", "critical"}:
                raise NotificationMaterializationUnavailable("quality alert severity is invalid")
            mandatory = severity == "critical"
            recipients = _quality_recipients(
                engine, session, tenant, dataset, severity, mandatory, timestamp
            )
            if not recipients:
                raise NotificationMaterializationForbidden("quality notification has no recipient")
            occurred_at = _utc_naive(alert.last_observed_at, "alert.last_observed_at")
            payload = _safe_payload(
                source={
                    "source_kind": "quality_alert",
                    "tenant_id": tenant,
                    "source_id": alert.id,
                    "source_revision": int(alert.revision),
                    "source_dataset_id": dataset_id,
                    "source_digest": str(alert.source_observation_digest),
                    "severity": severity,
                    "event_semantic": "opened",
                    "occurred_at": occurred_at,
                    "safe_facts": {
                        "release_id": alert.release_id,
                        "channel_id": alert.channel_id,
                        "release_role": alert.release_role,
                        "alert_type": alert.alert_type,
                        "occurrence_count": int(alert.occurrence_count),
                        "status": alert.status,
                    },
                },
                route={
                    "target_route_code": "knowledge_quality_operations",
                    "target_route_params_json": {
                        "tenant_id": tenant,
                        "dataset_id": dataset_id,
                        "release_id": alert.release_id,
                        "channel_id": alert.channel_id,
                        "alert_id": alert.id,
                    },
                },
                action_required=mandatory,
                mandatory=mandatory,
                title_code="notification.quality_alert.title",
                summary_code="notification.quality_alert.summary",
                occurred_at=occurred_at,
            )
            result = _persist_bundle(session, payload, recipients, timestamp)
            session.commit()
            return result


def materialize_pending_approval_notifications(
    engine: Any,
    *,
    tenant_id: str,
    approval_request_id: str,
    expected_source_revision: int,
    expected_source_digest: str,
    now: datetime,
) -> dict[str, Any]:
    tenant = _component(tenant_id, "tenant_id", 64)
    request_id = _component(approval_request_id, "approval_request_id", 64)
    expected_revision = _revision(expected_source_revision)
    expected_digest = _digest(expected_source_digest)
    timestamp = _now(now)
    with engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            request = session.scalar(
                select(TenantApprovalRequest)
                .where(
                    TenantApprovalRequest.tenant_id == tenant,
                    TenantApprovalRequest.id == request_id,
                )
                .with_for_update()
            )
            if request is None:
                raise NotificationMaterializationForbidden("approval source scope is unavailable")
            if (
                request.status != "pending"
                or _utc_naive(request.expires_at, "approval.expires_at") <= timestamp
            ):
                raise NotificationMaterializationUnavailable("approval request is not pending")
            if int(request.revision) != expected_revision or not hmac.compare_digest(
                str(request.payload_hash), expected_digest
            ):
                raise NotificationMaterializationConflict("approval source is stale")
            snapshot = request.snapshot_json
            if not isinstance(snapshot, Mapping):
                raise NotificationMaterializationInvalid("approval source is unsafe")
            if not hmac.compare_digest(_json_hash(snapshot), str(request.payload_hash)):
                raise NotificationMaterializationConflict("approval source digest is stale")
            try:
                canonical_notification_digest("approval-source-snapshot", snapshot)
            except NotificationAuthorityError as exc:
                raise NotificationMaterializationInvalid("approval source is unsafe") from exc
            recipients = _approval_recipients(session, tenant, request)
            if not recipients:
                raise NotificationMaterializationForbidden("approval notification has no recipient")
            occurred_at = _utc_naive(request.created_at, "approval.created_at")
            safe_facts: dict[str, Any] = {
                "action_type": request.action_type,
                "policy_id": request.policy_id,
                "resource_type": request.resource_type,
                "required_approvals": int(request.required_approvals),
                "received_approvals": int(request.received_approvals),
                "expires_at": _utc_naive(request.expires_at, "approval.expires_at"),
            }
            if request.resource_id is not None:
                safe_facts["resource_id"] = request.resource_id
            payload = _safe_payload(
                source={
                    "source_kind": "approval_pending_for_me",
                    "tenant_id": tenant,
                    "source_id": request.id,
                    "source_revision": int(request.revision),
                    "source_digest": str(request.payload_hash),
                    "severity": "warning",
                    "event_semantic": "pending",
                    "occurred_at": occurred_at,
                    "safe_facts": safe_facts,
                },
                route={
                    "target_route_code": "enterprise_approval",
                    "target_route_params_json": {
                        "tenant_id": tenant,
                        "approval_request_id": request.id,
                    },
                },
                action_required=True,
                mandatory=True,
                title_code="notification.approval_pending.title",
                summary_code="notification.approval_pending.summary",
                occurred_at=occurred_at,
            )
            result = _persist_bundle(session, payload, recipients, timestamp)
            session.commit()
            return result


def reconcile_notification_sources(
    engine: Any,
    *,
    tenant_id: str,
    now: datetime,
) -> dict[str, Any]:
    tenant = _component(tenant_id, "tenant_id", 64)
    timestamp = _now(now)
    with Session(engine) as session:
        alerts = list(
            session.execute(
                select(
                    DatasetReleaseQualityAlert.dataset_id,
                    DatasetReleaseQualityAlert.id,
                    DatasetReleaseQualityAlert.revision,
                    DatasetReleaseQualityAlert.source_observation_digest,
                )
                .where(
                    DatasetReleaseQualityAlert.tenant_id == tenant,
                    DatasetReleaseQualityAlert.status.in_(_ACTIVE_ALERT_STATUSES),
                )
                .order_by(DatasetReleaseQualityAlert.dataset_id, DatasetReleaseQualityAlert.id)
            )
        )
        approvals = list(
            session.execute(
                select(
                    TenantApprovalRequest.id,
                    TenantApprovalRequest.revision,
                    TenantApprovalRequest.payload_hash,
                )
                .where(
                    TenantApprovalRequest.tenant_id == tenant,
                    TenantApprovalRequest.status == "pending",
                    TenantApprovalRequest.expires_at > timestamp,
                )
                .order_by(TenantApprovalRequest.id)
            )
        )
    notifications: dict[str, dict[str, Any]] = {}
    for dataset_id, alert_id, revision, source_digest in alerts:
        result = materialize_quality_alert_notifications(
            engine,
            tenant_id=tenant,
            dataset_id=str(dataset_id),
            alert_id=str(alert_id),
            expected_source_revision=int(revision),
            expected_source_digest=str(source_digest),
            now=timestamp,
        )
        notification = result["notification"]
        notifications[notification["notification_key"]] = notification
    for request_id, revision, source_digest in approvals:
        result = materialize_pending_approval_notifications(
            engine,
            tenant_id=tenant,
            approval_request_id=str(request_id),
            expected_source_revision=int(revision),
            expected_source_digest=str(source_digest),
            now=timestamp,
        )
        notification = result["notification"]
        notifications[notification["notification_key"]] = notification
    ordered = [notifications[key] for key in sorted(notifications)]
    return {
        "tenant_id": tenant,
        "notification_count": len(ordered),
        "notifications": ordered,
        "as_of": timestamp.isoformat(timespec="microseconds") + "Z",
    }


__all__ = [
    "NotificationMaterializationConflict",
    "NotificationMaterializationError",
    "NotificationMaterializationForbidden",
    "NotificationMaterializationInvalid",
    "NotificationMaterializationUnavailable",
    "materialize_pending_approval_notifications",
    "materialize_quality_alert_notifications",
    "reconcile_notification_sources",
]
