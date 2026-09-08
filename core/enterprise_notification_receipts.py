"""Tenant-safe Notification receipt reads and lifecycle mutations for Stage 22."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
import re
import uuid
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from core.catalog_schema import inspect_enterprise_notification_center_capability
from core.enterprise_access_control import (
    DatasetAccessControlUnavailable,
    evaluate_dataset_permissions,
)
from core.enterprise_acl_idempotency import engine_serialization_lock, idempotency_key_lock
from core.enterprise_notification_center import canonical_notification_event
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
    complete_tenant_mutation,
    reserve_tenant_mutation,
    tenant_idempotency_key_digest,
    tenant_request_hash,
)
from core.knowledge_governance import sanitize_audit_snapshot
from core.knowledge_permissions import KNOWLEDGE_READ
from core.enterprise_knowledge_base_releases import ServiceResult
from models.orm import (
    Account,
    Dataset,
    DatasetReleaseQualityAlert,
    Tenant,
    TenantApprovalPolicyApprover,
    TenantApprovalRequest,
    TenantAuditEvent,
    TenantGroup,
    TenantGroupMember,
    TenantMember,
    TenantNotification,
    TenantNotificationEvent,
    TenantNotificationReceipt,
    TenantNotificationRecipient,
)

UTC = timezone.utc
_CURSOR_KIND = "notification-inbox-v1"
_ALLOWED_STATUSES = frozenset({"unread", "read", "archived", "all"})
_ALLOWED_CATEGORIES = frozenset({"quality", "approval"})
_ALLOWED_SEVERITIES = frozenset({"info", "warning", "critical"})
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SECRET_RE = re.compile(
    r"(?i)(?:password\\s*[:=]|passwd\\s*[:=]|secret\\s*[:=]|credential\\s*[:=]|"
    r"authorization\\s*[:=]|bearer\\s+|access[_ -]?token\\s*[:=]|"
    r"refresh[_ -]?token\\s*[:=]|api[_ -]?key\\s*[:=]|client[_ -]?secret\\s*[:=]|"
    r"idempotency[_ -]?key\\s*[:=]|sk_(?:live|test)[-_]\\S+|"
    r"(?:https?|ftp|file|mailto|javascript|data):\\S+|"
    r"(?:mysql|mariadb|postgres(?:ql)?|redis|sqlite):\\/\\/\\S+|"
    r"[A-Za-z0-9_-]{16,}\\.[A-Za-z0-9_-]{16,}\\.[A-Za-z0-9_-]{16,})"
)
_FORBIDDEN_KEY_RE = re.compile(
    r"(?i)(?:query|body|content|note|comment|ticket|email|token|credential|"
    r"password|secret|authorization|webhook|url|uri|href|api[_ -]?key)"
)


class NotificationReceiptError(RuntimeError):
    code = "notification_receipt_error"
    status = 500

    def __init__(self, message: str = "Notification receipt operation failed") -> None:
        super().__init__(message)
        self.message = message


class NotificationReceiptInvalid(NotificationReceiptError, ValueError):
    code = "notification_receipt_invalid"
    status = 422


class NotificationReceiptForbidden(NotificationReceiptError):
    code = "notification_receipt_forbidden"
    status = 403


class NotificationReceiptNotFound(NotificationReceiptError):
    code = "notification_receipt_not_found"
    status = 404


class NotificationReceiptUnavailable(NotificationReceiptError):
    code = "notification_receipt_unavailable"
    status = 503


class NotificationReceiptConflict(NotificationReceiptError):
    code = "notification_receipt_conflict"
    status = 409


class NotificationReceiptRevisionConflict(NotificationReceiptConflict):
    code = "notification_receipt_revision_conflict"


ReceiptError = NotificationReceiptError
ReceiptInvalid = NotificationReceiptInvalid
ReceiptForbidden = NotificationReceiptForbidden
ReceiptNotFound = NotificationReceiptNotFound
ReceiptUnavailable = NotificationReceiptUnavailable
ReceiptConflict = NotificationReceiptConflict
ReceiptRevisionConflict = NotificationReceiptRevisionConflict


def _text(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise NotificationReceiptInvalid(f"{field} must be a string")
    result = value.strip()
    if not result and not allow_empty:
        raise NotificationReceiptInvalid(f"{field} must not be empty")
    if len(result) > maximum or any(ord(character) < 32 for character in result):
        raise NotificationReceiptInvalid(f"{field} is invalid")
    return result


def _identifier(value: Any, field: str, maximum: int = 128) -> str:
    result = _text(value, field, maximum)
    if _ID_RE.fullmatch(result) is None:
        raise NotificationReceiptInvalid(f"{field} contains unsupported identity characters")
    return result


def _exact_integer(value: Any, field: str, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bound = f" and <= {maximum}" if maximum is not None else ""
        raise NotificationReceiptInvalid(f"{field} must be an exact integer >= {minimum}{bound}")
    return value


def _timestamp(value: Any, field: str, *, required: bool = True) -> datetime | None:
    if value is None and not required:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except (TypeError, ValueError) as exc:
            raise NotificationReceiptInvalid(f"{field} must be an ISO timestamp") from exc
    else:
        raise NotificationReceiptInvalid(f"{field} must be a datetime or ISO timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    try:
        parsed = parsed.astimezone(UTC)
    except (TypeError, ValueError) as exc:
        raise NotificationReceiptInvalid(f"{field} timezone is invalid") from exc
    return parsed.replace(tzinfo=None, fold=0)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    moment = _timestamp(value, "timestamp")
    assert moment is not None
    return moment.replace(tzinfo=UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now(value: Any = None) -> datetime:
    return _timestamp(value, "now") if value is not None else datetime.utcnow()


def _reason(value: Any) -> str:
    result = _text(value, "reason", 512)
    if _SECRET_RE.search(result) or re.search(
        r"(?i)\\b(?:query|body|note|ticket|credential|token)\\b", result
    ):
        raise NotificationReceiptInvalid("reason contains unsafe evidence")
    return result


def _ensure_capability(session: Session) -> None:
    state, issues = inspect_enterprise_notification_center_capability(session.connection())
    if state != "ready":
        detail = "; ".join(issues) or "notification center schema is unavailable"
        raise NotificationReceiptUnavailable(detail)


def _scope_actor(
    session: Session, *, tenant_id: str, actor_id: str, lock: bool = False
) -> tuple[TenantMember, Account]:
    tenant_statement = select(Tenant).where(Tenant.id == tenant_id)
    member_statement = select(TenantMember).where(
        TenantMember.tenant_id == tenant_id,
        TenantMember.account_id == actor_id,
        TenantMember.status == "active",
    )
    if lock:
        tenant_statement = tenant_statement.with_for_update()
        member_statement = member_statement.with_for_update()
    tenant = session.scalar(tenant_statement)
    member = session.scalar(member_statement)
    account = session.get(Account, actor_id)
    if tenant is None or str(tenant.status) != "active" or member is None or account is None:
        raise NotificationReceiptForbidden("actor is not active in Tenant scope")
    return member, account


def _safe_scalar(value: Any, field: str) -> Any:
    if value is None or type(value) in {bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise NotificationReceiptUnavailable(f"{field} is unavailable")
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text or len(text) > 512 or any(ord(character) < 32 for character in text):
            raise NotificationReceiptUnavailable(f"{field} is unavailable")
        if _SECRET_RE.search(text):
            raise NotificationReceiptUnavailable(f"{field} is unavailable")
        return text
    raise NotificationReceiptUnavailable(f"{field} is unavailable")


def _safe_facts(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NotificationReceiptUnavailable(f"{field} is unavailable")
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip() or _FORBIDDEN_KEY_RE.search(key):
            raise NotificationReceiptUnavailable(f"{field} is unavailable")
        canonical = key.strip()
        result[canonical] = _safe_scalar(item, f"{field}.{canonical}")
    return {key: result[key] for key in sorted(result)}


def _safe_route(row: TenantNotification, tenant_id: str) -> tuple[dict[str, str], dict[str, Any]]:
    params = row.target_route_params_json
    if not isinstance(params, Mapping):
        raise NotificationReceiptUnavailable("notification route is unavailable")
    raw = dict(params)
    if any(not isinstance(key, str) for key in raw):
        raise NotificationReceiptUnavailable("notification route is unavailable")
    code = str(row.target_route_code)
    if code == "knowledge_quality_operations" and row.source_kind == "quality_alert":
        allowed_new = {"dataset_id", "section", "alert_id"}
        allowed_old = {"tenant_id", "dataset_id", "release_id", "channel_id", "alert_id"}
        if set(raw) == allowed_new:
            if raw.get("dataset_id") != row.source_dataset_id or raw.get("section") != "releases":
                raise NotificationReceiptUnavailable("notification route is unavailable")
            dataset_id = _identifier(raw["dataset_id"], "route.dataset_id", 64)
            section = _text(raw["section"], "route.section", 32)
            alert_id = _identifier(raw["alert_id"], "route.alert_id")
        elif set(raw) == allowed_old:
            if raw.get("tenant_id") != tenant_id or raw.get("dataset_id") != row.source_dataset_id:
                raise NotificationReceiptUnavailable("notification route is unavailable")
            dataset_id = _identifier(raw["dataset_id"], "route.dataset_id", 64)
            section = "releases"
            alert_id = _identifier(raw["alert_id"], "route.alert_id")
        else:
            raise NotificationReceiptUnavailable("notification route is unavailable")
        if alert_id != str(row.source_id):
            raise NotificationReceiptUnavailable("notification route is unavailable")
        query = {"dataset": dataset_id, "section": section, "alert": alert_id}
        route = {
            "code": code,
            "path": "/enterprise/knowledge-base",
            "query": query,
            "href": "/enterprise/knowledge-base?dataset="
            + dataset_id
            + "&section="
            + section
            + "&alert="
            + alert_id,
        }
        return {"dataset_id": dataset_id, "section": section, "alert_id": alert_id}, route
    if code == "enterprise_approval" and row.source_kind == "approval_pending_for_me":
        if set(raw) == {"request_id"}:
            request_id = _identifier(raw["request_id"], "route.request_id")
        elif set(raw) == {"tenant_id", "approval_request_id"}:
            if raw.get("tenant_id") != tenant_id:
                raise NotificationReceiptUnavailable("notification route is unavailable")
            request_id = _identifier(raw["approval_request_id"], "route.request_id")
        else:
            raise NotificationReceiptUnavailable("notification route is unavailable")
        if request_id != str(row.source_id):
            raise NotificationReceiptUnavailable("notification route is unavailable")
        route = {
            "code": code,
            "path": "/enterprise/approvals",
            "query": {"request": request_id},
            "href": "/enterprise/approvals?request=" + request_id,
        }
        return {"request_id": request_id}, route
    raise NotificationReceiptUnavailable("notification route is unavailable")


def _notification_payload(
    row: TenantNotification, tenant_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_kind = str(row.source_kind)
    category = str(row.category)
    if source_kind == "quality_alert":
        if category != "quality" or row.source_dataset_id is None:
            raise NotificationReceiptUnavailable("notification source is unavailable")
    elif source_kind == "approval_pending_for_me":
        if category != "approval" or row.source_dataset_id is not None:
            raise NotificationReceiptUnavailable("notification source is unavailable")
    else:
        raise NotificationReceiptUnavailable("notification source is unavailable")
    if not _DIGEST_RE.fullmatch(str(row.notification_key)) or not _DIGEST_RE.fullmatch(
        str(row.source_digest)
    ):
        raise NotificationReceiptUnavailable("notification digest is unavailable")
    if type(row.action_required) is not bool or type(row.mandatory) is not bool:
        raise NotificationReceiptUnavailable("notification flags are unavailable")
    params, route = _safe_route(row, tenant_id)
    payload = {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "source_kind": source_kind,
        "source_id": str(row.source_id),
        "source_revision": _exact_integer(row.source_revision, "source_revision", 1),
        "source_dataset_id": None
        if row.source_dataset_id is None
        else _identifier(row.source_dataset_id, "source_dataset_id", 64),
        "category": category,
        "severity": str(row.severity),
        "action_required": bool(row.action_required),
        "mandatory": bool(row.mandatory),
        "notification_key": str(row.notification_key),
        "source_digest": str(row.source_digest),
        "title_code": _text(row.title_code, "title_code", 64),
        "summary_code": _text(row.summary_code, "summary_code", 64),
        "safe_facts_json": _safe_facts(row.safe_facts_json, "safe_facts_json"),
        "target_route_code": str(row.target_route_code),
        "target_route_params_json": params,
        "occurred_at": _iso(row.occurred_at),
        "created_at": _iso(row.created_at),
        "created_by": _identifier(row.created_by, "created_by", 64),
    }
    if category not in _ALLOWED_CATEGORIES or str(row.severity) not in _ALLOWED_SEVERITIES:
        raise NotificationReceiptUnavailable("notification authority is unavailable")
    return payload, route


def _recipient_payload(row: TenantNotificationRecipient) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "notification_id": str(row.notification_id),
        "account_id": str(row.account_id),
        "recipient_reason": str(row.recipient_reason),
        "mandatory": bool(row.mandatory),
        "assignment_digest": str(row.assignment_digest),
        "assigned_at": _iso(row.assigned_at),
    }


def _receipt_payload(row: TenantNotificationReceipt) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "notification_id": str(row.notification_id),
        "account_id": str(row.account_id),
        "status": str(row.status),
        "revision": _exact_integer(row.revision, "receipt.revision", 1),
        "read_at": _iso(row.read_at),
        "archived_at": _iso(row.archived_at),
        "updated_at": _iso(row.updated_at),
    }


def _event_payload(row: TenantNotificationEvent) -> dict[str, Any]:
    if not _DIGEST_RE.fullmatch(str(row.event_digest)) or (
        row.previous_event_digest is not None
        and not _DIGEST_RE.fullmatch(str(row.previous_event_digest))
    ):
        raise NotificationReceiptUnavailable("notification event digest is unavailable")
    safe_snapshot = _safe_facts(row.safe_snapshot_json, "event.safe_snapshot_json")
    try:
        canonical_notification_event(
            {
                "tenant_id": str(row.tenant_id),
                "notification_id": str(row.notification_id),
                "account_id": str(row.account_id),
                "sequence": row.sequence,
                "event_type": str(row.event_type),
                "previous_event_digest": row.previous_event_digest,
                "event_digest": str(row.event_digest),
                "actor_id": str(row.actor_id),
                "request_id": str(row.request_id),
                "safe_snapshot": safe_snapshot,
                "occurred_at": row.occurred_at,
            }
        )
    except Exception as exc:
        raise NotificationReceiptUnavailable(
            "notification canonical event digest is unavailable"
        ) from exc
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "notification_id": str(row.notification_id),
        "account_id": str(row.account_id),
        "sequence": _exact_integer(row.sequence, "event.sequence", 1),
        "event_type": str(row.event_type),
        "previous_event_digest": row.previous_event_digest,
        "event_digest": str(row.event_digest),
        "actor_id": _identifier(row.actor_id, "event.actor_id", 64),
        "request_id": _identifier(row.request_id, "event.request_id", 128),
        "safe_snapshot_json": safe_snapshot,
        "occurred_at": _iso(row.occurred_at),
    }


def _quality_access_current(
    engine: Any, session: Session, member: TenantMember, dataset_id: str
) -> bool:
    if str(member.role) in {"owner", "admin"}:
        return True
    try:
        decision = evaluate_dataset_permissions(
            engine,
            str(member.tenant_id),
            str(member.account_id),
            str(member.role),
            dataset_id,
            session=session,
            lock_for_update=False,
        )
    except (DatasetAccessControlUnavailable, Exception):
        return False
    return KNOWLEDGE_READ in decision.effective_permissions


def _approval_eligible(
    session: Session, *, tenant_id: str, policy_id: str, member: TenantMember
) -> bool:
    rows = list(
        session.scalars(
            select(TenantApprovalPolicyApprover).where(
                TenantApprovalPolicyApprover.tenant_id == tenant_id,
                TenantApprovalPolicyApprover.policy_id == policy_id,
                TenantApprovalPolicyApprover.status == "active",
            )
        )
    )
    role = str(member.role).casefold()
    for row in rows:
        kind = str(row.approver_kind)
        reference = str(row.approver_ref)
        if kind == "account" and reference == str(member.account_id):
            return True
        if kind == "role" and reference.casefold() == role:
            return True
        if kind == "group":
            group = session.scalar(
                select(TenantGroup).where(
                    TenantGroup.tenant_id == tenant_id,
                    TenantGroup.id == reference,
                    TenantGroup.status == "active",
                )
            )
            if (
                group is not None
                and session.scalar(
                    select(TenantGroupMember.id).where(
                        TenantGroupMember.tenant_id == tenant_id,
                        TenantGroupMember.group_id == reference,
                        TenantGroupMember.account_id == member.account_id,
                        TenantGroupMember.status == "active",
                    )
                )
                is not None
            ):
                return True
    return False


def _handoff(
    engine: Any,
    session: Session,
    row: TenantNotification,
    member: TenantMember,
    *,
    route: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    def unavailable(reason: str) -> dict[str, Any]:
        return {
            "state": "unavailable",
            "reason_code": reason,
            "route": dict(route),
            "business_mutation_allowed": False,
        }

    def stale(reason: str) -> dict[str, Any]:
        return {
            "state": "stale",
            "reason_code": reason,
            "route": dict(route),
            "business_mutation_allowed": False,
        }

    if row.source_kind == "quality_alert":
        dataset_id = str(row.source_dataset_id or "")
        dataset = session.scalar(
            select(Dataset).where(Dataset.tenant_id == row.tenant_id, Dataset.id == dataset_id)
        )
        if dataset is None or str(dataset.status) != "active":
            return unavailable("dataset_access_unavailable")
        if not _quality_access_current(engine, session, member, dataset_id):
            return unavailable("dataset_access_unavailable")
        alert = session.scalar(
            select(DatasetReleaseQualityAlert).where(
                DatasetReleaseQualityAlert.tenant_id == row.tenant_id,
                DatasetReleaseQualityAlert.dataset_id == dataset_id,
                DatasetReleaseQualityAlert.id == row.source_id,
            )
        )
        if alert is None:
            return unavailable("quality_source_unavailable")
        if int(alert.revision) != int(row.source_revision) or str(
            alert.source_observation_digest
        ) != str(row.source_digest):
            return stale("quality_source_stale")
        return {
            "state": "current",
            "reason_code": None,
            "route": dict(route),
            "business_mutation_allowed": False,
        }
    request = session.scalar(
        select(TenantApprovalRequest).where(
            TenantApprovalRequest.tenant_id == row.tenant_id,
            TenantApprovalRequest.id == row.source_id,
        )
    )
    if request is None:
        return unavailable("approval_source_unavailable")
    if int(request.revision) != int(row.source_revision) or str(request.payload_hash) != str(
        row.source_digest
    ):
        return stale("approval_source_stale")
    expires_at = _timestamp(request.expires_at, "approval.expires_at")
    if str(request.status) != "pending" or (expires_at is not None and expires_at <= now):
        return stale("approval_source_stale")
    if not _approval_eligible(
        session, tenant_id=str(row.tenant_id), policy_id=str(request.policy_id), member=member
    ):
        return stale("approval_eligibility_stale")
    return {
        "state": "current",
        "reason_code": None,
        "route": dict(route),
        "business_mutation_allowed": False,
    }


def _item(
    engine: Any,
    session: Session,
    notification: TenantNotification,
    recipient: TenantNotificationRecipient,
    receipt: TenantNotificationReceipt,
    member: TenantMember,
    *,
    now: datetime,
) -> dict[str, Any]:
    payload, route = _notification_payload(notification, str(notification.tenant_id))
    if (
        str(recipient.tenant_id) != str(notification.tenant_id)
        or str(recipient.notification_id) != str(notification.id)
        or str(receipt.tenant_id) != str(notification.tenant_id)
        or str(receipt.notification_id) != str(notification.id)
        or str(receipt.account_id) != str(recipient.account_id)
    ):
        raise NotificationReceiptUnavailable(
            "notification recipient/receipt identity is unavailable"
        )
    return {
        "notification": payload,
        "recipient": _recipient_payload(recipient),
        "receipt": _receipt_payload(receipt),
        "handoff": _handoff(engine, session, notification, member, route=route, now=now),
    }


def _encode_cursor(
    *, tenant_id: str, actor_id: str, occurred_at: datetime, notification_id: str
) -> str:
    value = {
        "kind": _CURSOR_KIND,
        "tenant_id": tenant_id,
        "account_id": actor_id,
        "occurred_at": _iso(occurred_at),
        "notification_id": notification_id,
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: Any, *, tenant_id: str, actor_id: str) -> tuple[datetime, str] | None:
    if value is None:
        return None
    cursor = _text(value, "cursor", 2048)
    try:
        decoded = json.loads(urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("utf-8"))
    except Exception as exc:
        raise NotificationReceiptInvalid("cursor is invalid") from exc
    if (
        not isinstance(decoded, Mapping)
        or decoded.get("kind") != _CURSOR_KIND
        or decoded.get("tenant_id") != tenant_id
        or decoded.get("account_id") != actor_id
    ):
        raise NotificationReceiptInvalid("cursor scope is invalid")
    occurred = _timestamp(decoded.get("occurred_at"), "cursor.occurred_at")
    identifier = _identifier(decoded.get("notification_id"), "cursor.notification_id")
    assert occurred is not None
    return occurred, identifier


def _read_context(
    engine: Any, tenant_id: Any, actor_id: Any
) -> tuple[Session, TenantMember, Account]:
    raise RuntimeError("internal read context helper must not be used")


def list_notifications(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    cursor: str | None = None,
    limit: int = 50,
    status: str = "all",
    category: str | None = None,
    severity: str | None = None,
    now: Any = None,
) -> ServiceResult:
    tenant = _identifier(tenant_id, "tenant_id", 64)
    actor = _identifier(actor_id, "actor_id", 64)
    page_size = _exact_integer(limit, "limit", 1, 200)
    if status not in _ALLOWED_STATUSES:
        raise NotificationReceiptInvalid("status is invalid")
    if category is not None and category not in _ALLOWED_CATEGORIES:
        raise NotificationReceiptInvalid("category is invalid")
    if severity is not None and severity not in _ALLOWED_SEVERITIES:
        raise NotificationReceiptInvalid("severity is invalid")
    timestamp = _now(now)
    with Session(engine) as session:
        _ensure_capability(session)
        member, _account = _scope_actor(session, tenant_id=tenant, actor_id=actor)
        decoded = _decode_cursor(cursor, tenant_id=tenant, actor_id=actor)
        conditions = [
            TenantNotification.tenant_id == tenant,
            TenantNotificationRecipient.tenant_id == tenant,
            TenantNotificationRecipient.account_id == actor,
            TenantNotificationReceipt.tenant_id == tenant,
            TenantNotificationReceipt.account_id == actor,
        ]
        if status != "all":
            conditions.append(TenantNotificationReceipt.status == status)
        if category is not None:
            conditions.append(TenantNotification.category == category)
        if severity is not None:
            conditions.append(TenantNotification.severity == severity)
        base = (
            select(TenantNotification, TenantNotificationRecipient, TenantNotificationReceipt)
            .join(
                TenantNotificationRecipient,
                and_(
                    TenantNotificationRecipient.tenant_id == TenantNotification.tenant_id,
                    TenantNotificationRecipient.notification_id == TenantNotification.id,
                ),
            )
            .join(
                TenantNotificationReceipt,
                and_(
                    TenantNotificationReceipt.tenant_id == TenantNotificationRecipient.tenant_id,
                    TenantNotificationReceipt.notification_id
                    == TenantNotificationRecipient.notification_id,
                    TenantNotificationReceipt.account_id == TenantNotificationRecipient.account_id,
                ),
            )
            .where(*conditions)
        )
        count = int(
            session.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
        )
        if decoded is not None:
            last_at, last_id = decoded
            base = base.where(
                or_(
                    TenantNotification.occurred_at < last_at,
                    and_(
                        TenantNotification.occurred_at == last_at, TenantNotification.id < last_id
                    ),
                )
            )
        rows = list(
            session.execute(
                base.order_by(
                    TenantNotification.occurred_at.desc(), TenantNotification.id.desc()
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        page_rows = rows[:page_size]
        items: list[dict[str, Any]] = []
        invalid_count = 0
        for notification, recipient, receipt in page_rows:
            try:
                items.append(
                    _item(engine, session, notification, recipient, receipt, member, now=timestamp)
                )
            except NotificationReceiptUnavailable:
                invalid_count += 1
        next_cursor = None
        if has_more and page_rows:
            last_notification = page_rows[-1][0]
            next_cursor = _encode_cursor(
                tenant_id=tenant,
                actor_id=actor,
                occurred_at=last_notification.occurred_at,
                notification_id=str(last_notification.id),
            )
        return ServiceResult(
            {
                "items": items,
                "count": count,
                "next_cursor": next_cursor,
                "invalid_item_count": invalid_count,
            }
        )


def get_notification_summary(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    now: Any = None,
) -> ServiceResult:
    tenant = _identifier(tenant_id, "tenant_id", 64)
    actor = _identifier(actor_id, "actor_id", 64)
    timestamp = _now(now)
    with Session(engine) as session:
        try:
            _ensure_capability(session)
        except NotificationReceiptUnavailable:
            return ServiceResult(
                {
                    "state": "unavailable",
                    "tenant_id": tenant,
                    "account_id": actor,
                    "unread_count": None,
                    "unread_state": "unavailable",
                    "as_of": None,
                    "reason_code": "notification_center_unavailable",
                }
            )
        _scope_actor(session, tenant_id=tenant, actor_id=actor)
        statement = (
            select(func.count())
            .select_from(TenantNotificationReceipt)
            .join(
                TenantNotificationRecipient,
                and_(
                    TenantNotificationRecipient.tenant_id == TenantNotificationReceipt.tenant_id,
                    TenantNotificationRecipient.notification_id
                    == TenantNotificationReceipt.notification_id,
                    TenantNotificationRecipient.account_id == TenantNotificationReceipt.account_id,
                ),
            )
            .where(
                TenantNotificationReceipt.tenant_id == tenant,
                TenantNotificationReceipt.account_id == actor,
                TenantNotificationReceipt.status == "unread",
            )
        )
        count = int(session.scalar(statement) or 0)
        return ServiceResult(
            {
                "state": "ready",
                "tenant_id": tenant,
                "account_id": actor,
                "unread_count": count,
                "unread_state": "count" if count else "zero",
                "as_of": _iso(timestamp),
                "reason_code": None,
            }
        )


def get_notification(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    notification_id: str,
    now: Any = None,
) -> ServiceResult:
    tenant = _identifier(tenant_id, "tenant_id", 64)
    actor = _identifier(actor_id, "actor_id", 64)
    identifier = _identifier(notification_id, "notification_id")
    timestamp = _now(now)
    with Session(engine) as session:
        _ensure_capability(session)
        member, _account = _scope_actor(session, tenant_id=tenant, actor_id=actor)
        row = session.execute(
            select(TenantNotification, TenantNotificationRecipient, TenantNotificationReceipt)
            .join(
                TenantNotificationRecipient,
                and_(
                    TenantNotificationRecipient.tenant_id == TenantNotification.tenant_id,
                    TenantNotificationRecipient.notification_id == TenantNotification.id,
                    TenantNotificationRecipient.account_id == actor,
                ),
            )
            .join(
                TenantNotificationReceipt,
                and_(
                    TenantNotificationReceipt.tenant_id == TenantNotificationRecipient.tenant_id,
                    TenantNotificationReceipt.notification_id
                    == TenantNotificationRecipient.notification_id,
                    TenantNotificationReceipt.account_id == actor,
                ),
            )
            .where(TenantNotification.tenant_id == tenant, TenantNotification.id == identifier)
        ).one_or_none()
        if row is None:
            notification_exists = session.scalar(
                select(TenantNotification.id).where(
                    TenantNotification.tenant_id == tenant, TenantNotification.id == identifier
                )
            )
            if notification_exists is not None:
                raise NotificationReceiptUnavailable("notification receipt is unavailable")
            raise NotificationReceiptNotFound("notification was not found")
        notification, recipient, receipt = row
        events = list(
            session.scalars(
                select(TenantNotificationEvent)
                .where(
                    TenantNotificationEvent.tenant_id == tenant,
                    TenantNotificationEvent.notification_id == identifier,
                    TenantNotificationEvent.account_id == actor,
                )
                .order_by(TenantNotificationEvent.sequence.asc())
            )
        )
        projected_events = [_event_payload(event) for event in events]
        for index, event in enumerate(projected_events):
            if (
                int(event["sequence"]) != index + 1
                or (index == 0 and event["previous_event_digest"] is not None)
                or (
                    index > 0
                    and event["previous_event_digest"]
                    != projected_events[index - 1]["event_digest"]
                )
            ):
                raise NotificationReceiptUnavailable("notification event timeline is unavailable")
        result = _item(engine, session, notification, recipient, receipt, member, now=timestamp)
        result["events"] = projected_events
        return ServiceResult(result)


def _mutation_response(*, state: str, operation: str, resource_id: str | None) -> dict[str, Any]:
    return {
        "state": state,
        "operation": operation,
        "resource_id": resource_id,
        "message": None,
        "retryable": False,
    }


def _request_id(value: Any, *, fallback: str) -> str:
    if value is None or value == "":
        return fallback
    return _identifier(value, "request_id", 128)


def _request_ip(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise NotificationReceiptInvalid("request_ip is invalid")
    normalized = value.strip()
    if len(normalized) > 64 or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise NotificationReceiptInvalid("request_ip is invalid")
    return normalized


def _append_event(
    session: Session,
    receipt: TenantNotificationReceipt,
    *,
    event_type: str,
    actor_id: str,
    request_id: str,
    now: datetime,
    status: str,
) -> TenantNotificationEvent:
    latest = session.scalar(
        select(TenantNotificationEvent)
        .where(
            TenantNotificationEvent.tenant_id == receipt.tenant_id,
            TenantNotificationEvent.notification_id == receipt.notification_id,
            TenantNotificationEvent.account_id == receipt.account_id,
        )
        .order_by(TenantNotificationEvent.sequence.desc())
        .with_for_update()
    )
    sequence = 1 if latest is None else int(latest.sequence) + 1
    previous = None if latest is None else str(latest.event_digest)
    canonical = canonical_notification_event(
        tenant_id=str(receipt.tenant_id),
        notification_id=str(receipt.notification_id),
        account_id=str(receipt.account_id),
        sequence=sequence,
        event_type=event_type,
        previous_event_digest=previous,
        actor_id=actor_id,
        request_id=request_id,
        safe_snapshot={
            "status": status,
            "revision": int(receipt.revision),
            "receipt_id": str(receipt.id),
        },
        occurred_at=now,
    )
    event = TenantNotificationEvent(
        id=str(canonical["event_digest"]),
        tenant_id=str(receipt.tenant_id),
        notification_id=str(receipt.notification_id),
        account_id=str(receipt.account_id),
        sequence=int(canonical["sequence"]),
        event_type=str(canonical["event_type"]),
        previous_event_digest=canonical["previous_event_digest"],
        event_digest=str(canonical["event_digest"]),
        actor_id=str(canonical["actor_id"]),
        request_id=str(canonical["request_id"]),
        safe_snapshot_json=canonical["safe_snapshot"],
        occurred_at=now,
    )
    session.add(event)
    session.flush()
    return event


def _audit_receipt(
    session: Session,
    *,
    account: Account,
    receipt: TenantNotificationReceipt,
    actor_id: str,
    action: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    request_id: str,
    request_ip: str,
    now: datetime,
) -> None:
    session.add(
        TenantAuditEvent(
            id=f"tenant-audit-{uuid.uuid4().hex}",
            tenant_id=str(receipt.tenant_id),
            actor_id=actor_id,
            actor_name_snapshot=str(account.name)[:128],
            actor_email_snapshot=str(account.email)[:256],
            action=action,
            resource_type="tenant_notification_receipt",
            resource_id=str(receipt.id),
            target_account_id=actor_id,
            before_snapshot=sanitize_audit_snapshot(dict(before)),
            after_snapshot=sanitize_audit_snapshot(dict(after)),
            request_id=request_id,
            request_ip=request_ip,
            occurred_at=now,
        )
    )


def _transition(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    account_id: str | None,
    notification_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str | None,
    request_ip: Any,
    now: Any,
    target_status: str,
    event_type: str,
    operation: str,
) -> ServiceResult:
    tenant = _identifier(tenant_id, "tenant_id", 64)
    actor = _identifier(actor_id, "actor_id", 64)
    account = actor if account_id is None else _identifier(account_id, "account_id", 64)
    if account != actor:
        raise NotificationReceiptInvalid("account_id must match the authenticated actor")
    safe_request_ip = _request_ip(request_ip)
    notification = _identifier(notification_id, "notification_id")
    expected = _exact_integer(expected_revision, "expected_revision", 1)
    safe_reason = _reason(reason)
    timestamp = _now(now)
    try:
        key_digest = tenant_idempotency_key_digest(tenant, actor, idempotency_key)
        request_hash = tenant_request_hash(
            operation=operation,
            path_identity={
                "tenant_id": tenant,
                "account_id": actor,
                "notification_id": notification,
            },
            body={
                "expected_revision": expected,
                "reason": safe_reason,
                "target_status": target_status,
            },
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise NotificationReceiptInvalid(str(exc)) from exc
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant, actor, idempotency_key))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        transaction = stack.enter_context(session.begin())
        del transaction
        _ensure_capability(session)
        _tenant_member, account = _scope_actor(session, tenant_id=tenant, actor_id=actor, lock=True)
        try:
            reservation = reserve_tenant_mutation(
                session,
                tenant_id=tenant,
                actor_id=actor,
                raw_idempotency_key=idempotency_key,
                request_hash=request_hash,
                operation=operation,
                resource_type="tenant_notification_receipt",
            )
        except TenantMutationIdempotencyConflict as exc:
            raise NotificationReceiptConflict(
                "idempotency key was used for a different request"
            ) from exc
        except TenantMutationIdempotencyInProgress as exc:
            raise NotificationReceiptConflict("idempotency request is in progress") from exc
        except TenantMutationIdempotencyValidationError as exc:
            raise NotificationReceiptInvalid(str(exc)) from exc
        if reservation.replay is not None:
            return ServiceResult(reservation.replay.response, reservation.replay.http_status)
        receipt = session.scalar(
            select(TenantNotificationReceipt)
            .where(
                TenantNotificationReceipt.tenant_id == tenant,
                TenantNotificationReceipt.notification_id == notification,
                TenantNotificationReceipt.account_id == actor,
            )
            .with_for_update()
        )
        if receipt is None:
            raise NotificationReceiptUnavailable("notification receipt is unavailable")
        if int(receipt.revision) != expected:
            raise NotificationReceiptRevisionConflict(
                "notification receipt revision fence rejected"
            )
        if str(receipt.status) == "archived" and target_status != "archived":
            raise NotificationReceiptConflict("archived notification receipt cannot be reopened")
        current_status = str(receipt.status)
        if current_status == target_status:
            response = _mutation_response(
                state="unchanged", operation=operation, resource_id=str(receipt.id)
            )
            complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=response,
                http_status=200,
                resource_id=str(receipt.id),
            )
            return ServiceResult(response)
        before = {
            "notification_id": notification,
            "account_id": actor,
            "status": current_status,
            "revision": int(receipt.revision),
        }
        receipt.status = target_status
        receipt.revision = int(receipt.revision) + 1
        receipt.read_at = timestamp if target_status == "read" else None
        receipt.archived_at = timestamp if target_status == "archived" else None
        receipt.updated_at = timestamp
        session.flush()
        effective_request_id = _request_id(
            request_id, fallback=f"notification-receipt-{key_digest[:32]}"
        )
        event = _append_event(
            session,
            receipt,
            event_type=event_type,
            actor_id=actor,
            request_id=effective_request_id,
            now=timestamp,
            status=target_status,
        )
        after = {
            "notification_id": notification,
            "account_id": actor,
            "status": target_status,
            "revision": int(receipt.revision),
            "event_digest": str(event.event_digest),
        }
        _audit_receipt(
            session,
            account=account,
            receipt=receipt,
            actor_id=actor,
            action=f"notification.receipt.{event_type}",
            before=before,
            after=after,
            request_id=effective_request_id,
            request_ip=safe_request_ip,
            now=timestamp,
        )
        response = _mutation_response(
            state="applied", operation=operation, resource_id=str(receipt.id)
        )
        complete_tenant_mutation(
            session,
            reservation,
            response_for_replay=response,
            http_status=200,
            resource_id=str(receipt.id),
        )
        return ServiceResult(response)


def _receipt_transition_operation(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    notification_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: Any = "",
    now: Any = None,
    target_status: str,
    event_type: str,
    operation: str,
) -> ServiceResult:
    return _transition(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        account_id=account_id,
        notification_id=notification_id,
        expected_revision=expected_revision,
        reason=reason,
        idempotency_key=idempotency_key,
        request_id=request_id,
        request_ip=request_ip,
        now=now,
        target_status=target_status,
        event_type=event_type,
        operation=operation,
    )


def mark_notification_read(engine: Any, **kwargs: Any) -> ServiceResult:
    return _receipt_transition_operation(
        engine,
        target_status="read",
        event_type="marked_read",
        operation="mark_notification_read",
        **kwargs,
    )


def mark_notification_unread(engine: Any, **kwargs: Any) -> ServiceResult:
    return _receipt_transition_operation(
        engine,
        target_status="unread",
        event_type="marked_unread",
        operation="mark_notification_unread",
        **kwargs,
    )


def archive_notification(engine: Any, **kwargs: Any) -> ServiceResult:
    return _receipt_transition_operation(
        engine,
        target_status="archived",
        event_type="archived",
        operation="archive_notification",
        **kwargs,
    )


def bulk_mark_notifications_read(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    items: Sequence[Mapping[str, Any]],
    reason: str,
    idempotency_key: str,
    request_id: str | None = None,
    account_id: str | None = None,
    request_ip: Any = "",
    now: Any = None,
) -> ServiceResult:
    tenant = _identifier(tenant_id, "tenant_id", 64)
    actor = _identifier(actor_id, "actor_id", 64)
    account = actor if account_id is None else _identifier(account_id, "account_id", 64)
    if account != actor:
        raise NotificationReceiptInvalid("account_id must match the authenticated actor")
    safe_request_ip = _request_ip(request_ip)
    if (
        not isinstance(items, Sequence)
        or isinstance(items, (str, bytes))
        or not 1 <= len(items) <= 200
    ):
        raise NotificationReceiptInvalid("bulk notification read accepts between 1 and 200 items")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise NotificationReceiptInvalid("bulk notification item is invalid")
        identifier = _identifier(
            item.get("notification_id", item.get("notificationId")), "notification_id"
        )
        if identifier in seen:
            raise NotificationReceiptInvalid("bulk notification IDs must be unique")
        seen.add(identifier)
        normalized.append(
            {
                "notification_id": identifier,
                "expected_revision": _exact_integer(
                    item.get("expected_revision", item.get("expectedRevision")),
                    "expected_revision",
                    1,
                ),
            }
        )
    normalized.sort(key=lambda item: item["notification_id"])
    safe_reason = _reason(reason)
    timestamp = _now(now)
    operation = "bulk_mark_notifications_read"
    try:
        key_digest = tenant_idempotency_key_digest(tenant, actor, idempotency_key)
        request_hash = tenant_request_hash(
            operation=operation,
            path_identity={"tenant_id": tenant, "account_id": actor},
            body={"items": normalized, "reason": safe_reason},
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise NotificationReceiptInvalid(str(exc)) from exc
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant, actor, idempotency_key))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        transaction = stack.enter_context(session.begin())
        del transaction
        _ensure_capability(session)
        _tenant_member, account = _scope_actor(session, tenant_id=tenant, actor_id=actor, lock=True)
        try:
            reservation = reserve_tenant_mutation(
                session,
                tenant_id=tenant,
                actor_id=actor,
                raw_idempotency_key=idempotency_key,
                request_hash=request_hash,
                operation=operation,
                resource_type="tenant_notification_receipt",
            )
        except TenantMutationIdempotencyConflict as exc:
            raise NotificationReceiptConflict(
                "idempotency key was used for a different request"
            ) from exc
        except TenantMutationIdempotencyInProgress as exc:
            raise NotificationReceiptConflict("idempotency request is in progress") from exc
        except TenantMutationIdempotencyValidationError as exc:
            raise NotificationReceiptInvalid(str(exc)) from exc
        if reservation.replay is not None:
            return ServiceResult(reservation.replay.response, reservation.replay.http_status)
        identifiers = [item["notification_id"] for item in normalized]
        rows = list(
            session.scalars(
                select(TenantNotificationReceipt)
                .where(
                    TenantNotificationReceipt.tenant_id == tenant,
                    TenantNotificationReceipt.account_id == actor,
                    TenantNotificationReceipt.notification_id.in_(identifiers),
                )
                .order_by(
                    TenantNotificationReceipt.account_id.asc(),
                    TenantNotificationReceipt.notification_id.asc(),
                    TenantNotificationReceipt.id.asc(),
                )
                .with_for_update()
            )
        )
        by_id = {str(row.notification_id): row for row in rows}
        if set(by_id) != set(identifiers):
            raise NotificationReceiptUnavailable(
                "one or more notification receipts are unavailable"
            )
        expected_by_id = {item["notification_id"]: item["expected_revision"] for item in normalized}
        for identifier, row in by_id.items():
            if int(row.revision) != expected_by_id[identifier]:
                raise NotificationReceiptRevisionConflict(
                    "notification receipt revision fence rejected"
                )
            if str(row.status) == "archived":
                raise NotificationReceiptConflict(
                    "archived notification receipt cannot be reopened"
                )
        effective_request_id = _request_id(
            request_id, fallback=f"notification-bulk-read-{key_digest[:32]}"
        )
        changed = 0
        for identifier in identifiers:
            row = by_id[identifier]
            if str(row.status) == "read":
                continue
            before = {
                "notification_id": identifier,
                "account_id": actor,
                "status": str(row.status),
                "revision": int(row.revision),
            }
            row.status = "read"
            row.revision = int(row.revision) + 1
            row.read_at = timestamp
            row.archived_at = None
            row.updated_at = timestamp
            session.flush()
            event = _append_event(
                session,
                row,
                event_type="marked_read",
                actor_id=actor,
                request_id=effective_request_id,
                now=timestamp,
                status="read",
            )
            after = {
                "notification_id": identifier,
                "account_id": actor,
                "status": "read",
                "revision": int(row.revision),
                "event_digest": str(event.event_digest),
            }
            _audit_receipt(
                session,
                account=account,
                receipt=row,
                actor_id=actor,
                action="notification.receipt.marked_read",
                before=before,
                after=after,
                request_id=effective_request_id,
                request_ip=safe_request_ip,
                now=timestamp,
            )
            changed += 1
        response = _mutation_response(
            state="applied" if changed else "unchanged", operation=operation, resource_id=None
        )
        complete_tenant_mutation(
            session, reservation, response_for_replay=response, http_status=200, resource_id=None
        )
        return ServiceResult(response)


list_notification_receipts = list_notifications
get_notification_detail = get_notification
mark_read = mark_notification_read
mark_unread = mark_notification_unread
bulk_read = bulk_mark_notifications_read


__all__ = [
    "NotificationReceiptError",
    "NotificationReceiptInvalid",
    "NotificationReceiptForbidden",
    "NotificationReceiptNotFound",
    "NotificationReceiptUnavailable",
    "NotificationReceiptConflict",
    "NotificationReceiptRevisionConflict",
    "ReceiptError",
    "ReceiptInvalid",
    "ReceiptForbidden",
    "ReceiptNotFound",
    "ReceiptUnavailable",
    "ReceiptConflict",
    "ReceiptRevisionConflict",
    "archive_notification",
    "bulk_mark_notifications_read",
    "bulk_read",
    "get_notification",
    "get_notification_detail",
    "get_notification_summary",
    "list_notification_receipts",
    "list_notifications",
    "mark_notification_read",
    "mark_notification_unread",
    "mark_read",
    "mark_unread",
]
