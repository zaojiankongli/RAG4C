"""Tenant-safe Notification subscription reads and actor-owned mutations."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Mapping
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from core.catalog_schema import inspect_enterprise_notification_center_capability
from core.enterprise_acl_idempotency import engine_serialization_lock, idempotency_key_lock
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
from core.enterprise_knowledge_base_releases import ServiceResult
from models.orm import (
    Account,
    Tenant,
    TenantAuditEvent,
    TenantMember,
    TenantNotificationSubscription,
)

UTC = timezone.utc
_CURSOR_KIND = "notification-subscriptions-v1"
_ALLOWED_STATUS = frozenset({"active", "archived", "all"})
_ALLOWED_CATEGORIES = frozenset({"quality", "approval"})
_ALLOWED_PREFERENCES = frozenset({"subscribed", "muted"})
_ALLOWED_SEVERITIES = frozenset({"info", "warning", "critical"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SECRET_RE = re.compile(
    r"(?i)(?:password\\s*[:=]|passwd\\s*[:=]|secret\\s*[:=]|credential\\s*[:=]|"
    r"authorization\\s*[:=]|bearer\\s+|access[_ -]?token\\s*[:=]|"
    r"refresh[_ -]?token\\s*[:=]|api[_ -]?key\\s*[:=]|client[_ -]?secret\\s*[:=]|"
    r"idempotency[_ -]?key\\s*[:=]|sk_(?:live|test)[-_]\\S+|"
    r"(?:https?|ftp|file|mailto|javascript|data):\\S+|"
    r"(?:mysql|mariadb|postgres(?:ql)?|redis|sqlite):\\/\\/\\S+|"
    r"[A-Za-z0-9_-]{16,}\\.[A-Za-z0-9_-]{16,}\\.[A-Za-z0-9_-]{16,})"
)


class NotificationSubscriptionError(RuntimeError):
    code = "notification_subscription_error"
    status = 500

    def __init__(self, message: str = "Notification subscription operation failed") -> None:
        super().__init__(message)
        self.message = message


class NotificationSubscriptionInvalid(NotificationSubscriptionError, ValueError):
    code = "notification_subscription_invalid"
    status = 422


class NotificationSubscriptionForbidden(NotificationSubscriptionError):
    code = "notification_subscription_forbidden"
    status = 403


class NotificationSubscriptionNotFound(NotificationSubscriptionError):
    code = "notification_subscription_not_found"
    status = 404


class NotificationSubscriptionUnavailable(NotificationSubscriptionError):
    code = "notification_subscription_unavailable"
    status = 503


class NotificationSubscriptionConflict(NotificationSubscriptionError):
    code = "notification_subscription_conflict"
    status = 409


class NotificationSubscriptionRevisionConflict(NotificationSubscriptionConflict):
    code = "notification_subscription_revision_conflict"


SubscriptionError = NotificationSubscriptionError
SubscriptionInvalid = NotificationSubscriptionInvalid
SubscriptionForbidden = NotificationSubscriptionForbidden
SubscriptionNotFound = NotificationSubscriptionNotFound
SubscriptionUnavailable = NotificationSubscriptionUnavailable
SubscriptionConflict = NotificationSubscriptionConflict
SubscriptionRevisionConflict = NotificationSubscriptionRevisionConflict


def _text(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise NotificationSubscriptionInvalid(f"{field} must be a string")
    result = value.strip()
    if not result and not allow_empty:
        raise NotificationSubscriptionInvalid(f"{field} must not be empty")
    if len(result) > maximum or any(ord(character) < 32 for character in result):
        raise NotificationSubscriptionInvalid(f"{field} is invalid")
    return result


def _identifier(value: Any, field: str, maximum: int = 128) -> str:
    result = _text(value, field, maximum)
    if _ID_RE.fullmatch(result) is None:
        raise NotificationSubscriptionInvalid(f"{field} contains unsupported identity characters")
    return result


def _request_ip(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise NotificationSubscriptionInvalid("request_ip is invalid")
    normalized = value.strip()
    if len(normalized) > 64 or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise NotificationSubscriptionInvalid("request_ip is invalid")
    return normalized


def _exact_integer(value: Any, field: str, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bound = f" and <= {maximum}" if maximum is not None else ""
        raise NotificationSubscriptionInvalid(
            f"{field} must be an exact integer >= {minimum}{bound}"
        )
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
            raise NotificationSubscriptionInvalid(f"{field} must be an ISO timestamp") from exc
    else:
        raise NotificationSubscriptionInvalid(f"{field} must be a datetime or ISO timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    try:
        parsed = parsed.astimezone(UTC)
    except (TypeError, ValueError) as exc:
        raise NotificationSubscriptionInvalid(f"{field} timezone is invalid") from exc
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
        raise NotificationSubscriptionInvalid("reason contains unsafe evidence")
    return result


def _ensure_capability(session: Session) -> None:
    state, issues = inspect_enterprise_notification_center_capability(session.connection())
    if state != "ready":
        raise NotificationSubscriptionUnavailable(
            "; ".join(issues) or "notification center schema is unavailable"
        )


def _actor(
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
        raise NotificationSubscriptionForbidden("actor is not active in Tenant scope")
    return member, account


def _subscription_payload(row: TenantNotificationSubscription) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "account_id": str(row.account_id),
        "category": str(row.category),
        "status": str(row.status),
        "preference": str(row.preference),
        "active_subscription_key": row.active_subscription_key,
        "revision": _exact_integer(row.revision, "subscription.revision", 1),
        "minimum_severity": str(row.minimum_severity),
        "muted_until": _iso(row.muted_until),
        "created_at": _iso(row.created_at),
        "created_by": _identifier(row.created_by, "subscription.created_by", 64),
        "updated_at": _iso(row.updated_at),
        "updated_by": _identifier(row.updated_by, "subscription.updated_by", 64),
        "archived_at": _iso(row.archived_at),
        "archived_by": None
        if row.archived_by is None
        else _identifier(row.archived_by, "subscription.archived_by", 64),
    }


def _encode_cursor(*, tenant_id: str, actor_id: str, category: str, subscription_id: str) -> str:
    value = {
        "kind": _CURSOR_KIND,
        "tenant_id": tenant_id,
        "account_id": actor_id,
        "category": category,
        "subscription_id": subscription_id,
    }
    return (
        urlsafe_b64encode(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def _decode_cursor(value: Any, *, tenant_id: str, actor_id: str) -> tuple[str, str] | None:
    if value is None:
        return None
    cursor = _text(value, "cursor", 2048)
    try:
        decoded = json.loads(urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode())
    except Exception as exc:
        raise NotificationSubscriptionInvalid("cursor is invalid") from exc
    if (
        not isinstance(decoded, Mapping)
        or decoded.get("kind") != _CURSOR_KIND
        or decoded.get("tenant_id") != tenant_id
        or decoded.get("account_id") != actor_id
    ):
        raise NotificationSubscriptionInvalid("cursor scope is invalid")
    category = _text(decoded.get("category"), "cursor.category", 16)
    identifier = _identifier(decoded.get("subscription_id"), "cursor.subscription_id")
    return category, identifier


def list_notification_subscriptions(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    cursor: str | None = None,
    limit: int = 50,
    status: str = "active",
    category: str | None = None,
    now: Any = None,
) -> ServiceResult:
    del now
    tenant = _identifier(tenant_id, "tenant_id", 64)
    actor = _identifier(actor_id, "actor_id", 64)
    page_size = _exact_integer(limit, "limit", 1, 200)
    if status not in _ALLOWED_STATUS:
        raise NotificationSubscriptionInvalid("status is invalid")
    if category is not None and category not in _ALLOWED_CATEGORIES:
        raise NotificationSubscriptionInvalid("category is invalid")
    with Session(engine) as session:
        _ensure_capability(session)
        _actor(session, tenant_id=tenant, actor_id=actor)
        decoded = _decode_cursor(cursor, tenant_id=tenant, actor_id=actor)
        conditions = [
            TenantNotificationSubscription.tenant_id == tenant,
            TenantNotificationSubscription.account_id == actor,
        ]
        if status != "all":
            conditions.append(TenantNotificationSubscription.status == status)
        if category is not None:
            conditions.append(TenantNotificationSubscription.category == category)
        statement = select(TenantNotificationSubscription).where(*conditions)
        count = int(
            session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
            or 0
        )
        if decoded is not None:
            last_category, last_id = decoded
            statement = statement.where(
                (TenantNotificationSubscription.category > last_category)
                | and_(
                    TenantNotificationSubscription.category == last_category,
                    TenantNotificationSubscription.id > last_id,
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    TenantNotificationSubscription.category.asc(),
                    TenantNotificationSubscription.id.asc(),
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = _encode_cursor(
                tenant_id=tenant,
                actor_id=actor,
                category=str(last.category),
                subscription_id=str(last.id),
            )
        return ServiceResult(
            {
                "items": [_subscription_payload(row) for row in rows],
                "count": count,
                "next_cursor": next_cursor,
                "invalid_item_count": 0,
            }
        )


def _mutation_response(*, state: str, operation: str, resource_id: str | None) -> dict[str, Any]:
    return {
        "state": state,
        "operation": operation,
        "resource_id": resource_id,
        "message": None,
        "retryable": False,
    }


def update_notification_subscription(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    subscription_id: str,
    expected_revision: int,
    preference: str,
    minimum_severity: str,
    muted_until: Any,
    reason: str,
    idempotency_key: str,
    status: str | None = None,
    request_id: str | None = None,
    account_id: str | None = None,
    request_ip: Any = "",
    now: Any = None,
) -> ServiceResult:
    tenant = _identifier(tenant_id, "tenant_id", 64)
    actor = _identifier(actor_id, "actor_id", 64)
    account_id_value = actor if account_id is None else _identifier(account_id, "account_id", 64)
    if account_id_value != actor:
        raise NotificationSubscriptionInvalid("account_id must match the authenticated actor")
    safe_request_ip = _request_ip(request_ip)
    identifier = _identifier(subscription_id, "subscription_id")
    expected = _exact_integer(expected_revision, "expected_revision", 1)
    if preference not in _ALLOWED_PREFERENCES:
        raise NotificationSubscriptionInvalid("preference is invalid")
    if minimum_severity not in _ALLOWED_SEVERITIES:
        raise NotificationSubscriptionInvalid("minimum_severity is invalid")
    if status is not None and status not in {"active", "archived"}:
        raise NotificationSubscriptionInvalid("status is invalid")
    safe_reason = _reason(reason)
    timestamp = _now(now)
    muted = _timestamp(muted_until, "muted_until", required=False)
    if preference == "subscribed" and muted is not None:
        raise NotificationSubscriptionInvalid("muted_until must be null for subscribed preference")
    if preference == "muted" and (muted is None or muted <= timestamp):
        raise NotificationSubscriptionInvalid(
            "muted_until must be in the future for muted preference"
        )
    desired_status = status or "active"
    operation = "update_notification_subscription"
    try:
        key_digest = tenant_idempotency_key_digest(tenant, actor, idempotency_key)
        request_hash = tenant_request_hash(
            operation=operation,
            path_identity={"tenant_id": tenant, "account_id": actor, "subscription_id": identifier},
            body={
                "expected_revision": expected,
                "status": desired_status,
                "preference": preference,
                "minimum_severity": minimum_severity,
                "muted_until": _iso(muted),
                "reason": safe_reason,
            },
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise NotificationSubscriptionInvalid(str(exc)) from exc
    effective_request_id = request_id
    if effective_request_id is None or effective_request_id == "":
        effective_request_id = f"notification-subscription-{key_digest[:32]}"
    else:
        effective_request_id = _identifier(effective_request_id, "request_id", 128)
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant, actor, idempotency_key))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        transaction = stack.enter_context(session.begin())
        del transaction
        _ensure_capability(session)
        _member, account = _actor(session, tenant_id=tenant, actor_id=actor, lock=True)
        try:
            reservation = reserve_tenant_mutation(
                session,
                tenant_id=tenant,
                actor_id=actor,
                raw_idempotency_key=idempotency_key,
                request_hash=request_hash,
                operation=operation,
                resource_type="tenant_notification_subscription",
            )
        except TenantMutationIdempotencyConflict as exc:
            raise NotificationSubscriptionConflict(
                "idempotency key was used for a different request"
            ) from exc
        except TenantMutationIdempotencyInProgress as exc:
            raise NotificationSubscriptionConflict("idempotency request is in progress") from exc
        except TenantMutationIdempotencyValidationError as exc:
            raise NotificationSubscriptionInvalid(str(exc)) from exc
        if reservation.replay is not None:
            return ServiceResult(reservation.replay.response, reservation.replay.http_status)
        row = session.scalar(
            select(TenantNotificationSubscription)
            .where(
                TenantNotificationSubscription.tenant_id == tenant,
                TenantNotificationSubscription.id == identifier,
                TenantNotificationSubscription.account_id == actor,
            )
            .with_for_update()
        )
        if row is None:
            raise NotificationSubscriptionNotFound("notification subscription was not found")
        if int(row.revision) != expected:
            raise NotificationSubscriptionRevisionConflict(
                "notification subscription revision fence rejected"
            )
        desired_key = f"{actor}:{row.category}" if desired_status == "active" else None
        unchanged = (
            str(row.status) == desired_status
            and str(row.preference) == preference
            and str(row.minimum_severity) == minimum_severity
            and _timestamp(row.muted_until, "muted_until", required=False) == muted
            and row.active_subscription_key == desired_key
        )
        if unchanged:
            response = _mutation_response(
                state="unchanged", operation=operation, resource_id=identifier
            )
            complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=response,
                http_status=200,
                resource_id=identifier,
            )
            return ServiceResult(response)
        before = {
            "subscription_id": identifier,
            "account_id": actor,
            "status": str(row.status),
            "preference": str(row.preference),
            "minimum_severity": str(row.minimum_severity),
            "revision": int(row.revision),
        }
        row.status = desired_status
        row.preference = preference
        row.minimum_severity = minimum_severity
        row.muted_until = muted
        row.active_subscription_key = desired_key
        row.revision = int(row.revision) + 1
        row.updated_at = timestamp
        row.updated_by = actor
        if desired_status == "archived":
            row.archived_at = timestamp
            row.archived_by = actor
        else:
            row.archived_at = None
            row.archived_by = None
        session.flush()
        after = {
            "subscription_id": identifier,
            "account_id": actor,
            "status": desired_status,
            "preference": preference,
            "minimum_severity": minimum_severity,
            "muted_until": _iso(muted),
            "active_subscription_key": desired_key,
            "revision": int(row.revision),
        }
        session.add(
            TenantAuditEvent(
                id=f"tenant-audit-{sha256((identifier + effective_request_id).encode()).hexdigest()[:32]}",
                tenant_id=tenant,
                actor_id=actor,
                actor_name_snapshot=str(account.name)[:128],
                actor_email_snapshot=str(account.email)[:256],
                action="notification.subscription.updated",
                resource_type="tenant_notification_subscription",
                resource_id=identifier,
                target_account_id=actor,
                before_snapshot=sanitize_audit_snapshot(before),
                after_snapshot=sanitize_audit_snapshot(after),
                request_id=effective_request_id,
                request_ip=safe_request_ip,
                occurred_at=timestamp,
            )
        )
        response = _mutation_response(state="applied", operation=operation, resource_id=identifier)
        complete_tenant_mutation(
            session,
            reservation,
            response_for_replay=response,
            http_status=200,
            resource_id=identifier,
        )
        return ServiceResult(response)


list_subscriptions = list_notification_subscriptions
update_subscription = update_notification_subscription


__all__ = [
    "NotificationSubscriptionError",
    "NotificationSubscriptionInvalid",
    "NotificationSubscriptionForbidden",
    "NotificationSubscriptionNotFound",
    "NotificationSubscriptionUnavailable",
    "NotificationSubscriptionConflict",
    "NotificationSubscriptionRevisionConflict",
    "SubscriptionError",
    "SubscriptionInvalid",
    "SubscriptionForbidden",
    "SubscriptionNotFound",
    "SubscriptionUnavailable",
    "SubscriptionConflict",
    "SubscriptionRevisionConflict",
    "list_notification_subscriptions",
    "list_subscriptions",
    "update_notification_subscription",
    "update_subscription",
]
