"""Transactional Stage 21 Tenant SLO Policy mutations.

The SLO Policy is a tenant-scoped control-plane authority. This module keeps
its write path deliberately small: authenticate the tenant actor, reserve a
generic tenant idempotency record, apply a revision-fenced change, append a
safe audit event, and persist only a server-computed policy digest.
"""

from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import json
import re
from collections.abc import Iterator, Mapping
from typing import Any
import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from core.catalog_schema import inspect_enterprise_release_quality_operations_capability
from core.enterprise_acl_idempotency import (
    engine_serialization_lock,
    idempotency_key_lock,
    normalize_idempotency_key,
)
from core.enterprise_knowledge_base_releases import (
    ReleaseManifestError,
    ServiceResult,
    _actor,
)
from core.enterprise_release_quality_operations import canonical_operations_digest
from core.enterprise_release_quality_service import (
    ReleaseQualityConflict,
    ReleaseQualityError,
    ReleaseQualityForbidden,
    ReleaseQualityInvalid,
    ReleaseQualityNotFound,
    ReleaseQualityUnavailable,
)
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
from core.knowledge_permissions import KNOWLEDGE_READ, role_allows
from models.orm import (
    Account,
    Tenant,
    TenantAuditEvent,
    TenantMember,
    TenantReleaseChannel,
    TenantReleaseQualitySloPolicy,
)


_SCOPE_TYPES = frozenset({"global", "risk_tier", "channel"})
_RISK_TIERS = frozenset({"low", "medium", "high"})
_STATUSES = frozenset({"active", "disabled"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:"
    r"(?:password|passwd|secret|credential|authorization|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|api[_-]?key|client[_-]?secret|token)\s*[:=]\s*\S+|"
    r"bearer\s+\S+|secret://\S+|sk_(?:live|test)[-_]\S+|ghp_\S+|xox[baprs]-\S+"
    r")"
)
_DATABASE_URL_RE = re.compile(
    r"(?i)\b(?:mysql(?:\+[a-z0-9_]+)?|mariadb|postgres(?:ql)?(?:\+[a-z0-9_]+)?|"
    r"mongodb(?:\+[a-z0-9_]+)?|redis(?:s)?|sqlite):\/\/\S+"
)
_JWT_LIKE_RE = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_CURSOR_KIND = "quality_slo_policy"


_THRESHOLD_FIELDS = (
    "certification_warning_minutes",
    "certification_critical_minutes",
    "waiver_warning_minutes",
    "max_open_alerts",
    "auto_queue_recertification",
    "require_passing_certification",
    "allow_active_waiver",
)


def _text(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ReleaseQualityInvalid(f"{field} must be a string")
    result = value.strip()
    if not result and not allow_empty:
        raise ReleaseQualityInvalid(f"{field} is required")
    if len(result) > maximum:
        raise ReleaseQualityInvalid(f"{field} must be at most {maximum} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in result):
        raise ReleaseQualityInvalid(f"{field} contains unsupported control characters")
    if (
        _SECRET_VALUE_RE.search(result)
        or _DATABASE_URL_RE.search(result)
        or _JWT_LIKE_RE.search(result)
    ):
        raise ReleaseQualityInvalid(f"{field} contains credential-like text")
    return result


def _key(value: Any, field: str, maximum: int = 128) -> str:
    return _text(value, field, maximum)


def _reason(value: Any) -> str:
    # The reason participates in the request hash, but is intentionally never
    # copied into a response, replay ledger or audit snapshot.
    return _text(value, "reason", 512)


def _exact_integer(value: Any, field: str, *, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ReleaseQualityInvalid(f"{field} must be an exact integer >= {minimum}")
    return value


def _exact_boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ReleaseQualityInvalid(f"{field} must be an exact boolean")
    return value


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ReleaseQualityUnavailable(f"{field} digest is invalid")
    return value


def _ensure_capability(connection: Any) -> None:
    state, issues = inspect_enterprise_release_quality_operations_capability(connection)
    if state != "ready":
        detail = "; ".join(issues) if issues else "schema capability is unavailable"
        raise ReleaseQualityUnavailable(detail)


def _begin_database_transaction(session: Session) -> None:
    """Make generic savepoint reservation rollback-safe on SQLite.

    SQLite treats the first SAVEPOINT as the outermost transaction when no
    driver transaction has started yet; releasing it would commit the
    idempotency reservation before a later revision fence can fail.
    """
    connection = session.connection()
    if str(connection.dialect.name).casefold() != "sqlite":
        return
    raw_connection = connection.connection
    driver_connection = getattr(raw_connection, "driver_connection", raw_connection)
    if not bool(getattr(driver_connection, "in_transaction", False)):
        connection.exec_driver_sql("BEGIN")


def _actor_scope(session: Session, tenant_id: str, actor_id: str) -> tuple[TenantMember, Account]:
    try:
        return _actor(session, tenant_id, actor_id)
    except ReleaseManifestError as exc:
        raise ReleaseQualityForbidden(exc.message) from exc


def _require_tenant_manager(member: TenantMember) -> None:
    if str(member.role).casefold() not in {"owner", "admin"}:
        raise ReleaseQualityForbidden("Tenant owner or admin is required")


def _require_read(member: TenantMember) -> None:
    if not role_allows(str(member.role), KNOWLEDGE_READ):
        raise ReleaseQualityForbidden("Actor lacks knowledge.read")


def _normalize_scope(
    scope_type: Any,
    scope_value: Any,
    channel_id: Any,
) -> tuple[str, str, str | None, str]:
    scope = _text(scope_type, "scope_type", 16).casefold()
    value = _text(scope_value, "scope_value", 128)
    channel = None if channel_id is None else _text(channel_id, "channel_id", 128)
    if scope == "global":
        if value != "*" or channel is not None:
            raise ReleaseQualityInvalid("global policy scope must use '*' and no channel_id")
    elif scope == "risk_tier":
        value = value.casefold()
        if value not in _RISK_TIERS or channel is not None:
            raise ReleaseQualityInvalid("risk_tier policy scope is invalid")
    elif scope == "channel":
        if channel is None or value != channel:
            raise ReleaseQualityInvalid("channel policy scope must match channel_id")
    else:
        raise ReleaseQualityInvalid("scope_type is invalid")
    return scope, value, channel, f"{scope}:{value}"


def _normalize_thresholds(
    *,
    certification_warning_minutes: Any,
    certification_critical_minutes: Any,
    waiver_warning_minutes: Any,
    max_open_alerts: Any,
    auto_queue_recertification: Any,
    require_passing_certification: Any,
    allow_active_waiver: Any,
) -> dict[str, Any]:
    warning = _exact_integer(certification_warning_minutes, "certification_warning_minutes")
    critical = _exact_integer(certification_critical_minutes, "certification_critical_minutes")
    if warning <= critical:
        raise ReleaseQualityInvalid(
            "certification_warning_minutes must be greater than certification_critical_minutes"
        )
    return {
        "certification_warning_minutes": warning,
        "certification_critical_minutes": critical,
        "waiver_warning_minutes": _exact_integer(waiver_warning_minutes, "waiver_warning_minutes"),
        "max_open_alerts": _exact_integer(max_open_alerts, "max_open_alerts"),
        "auto_queue_recertification": _exact_boolean(
            auto_queue_recertification, "auto_queue_recertification"
        ),
        "require_passing_certification": _exact_boolean(
            require_passing_certification, "require_passing_certification"
        ),
        "allow_active_waiver": _exact_boolean(allow_active_waiver, "allow_active_waiver"),
    }


def _policy_digest(values: Mapping[str, Any]) -> str:
    return canonical_operations_digest(
        "slo_policy",
        {
            "scope_type": values["scope_type"],
            "scope_value": values["scope_value"],
            "channel_id": values["channel_id"],
            "revision": values["revision"],
            "certification_warning_minutes": values["certification_warning_minutes"],
            "certification_critical_minutes": values["certification_critical_minutes"],
            "waiver_warning_minutes": values["waiver_warning_minutes"],
            "max_open_alerts": values["max_open_alerts"],
            "auto_queue_recertification": values["auto_queue_recertification"],
            "require_passing_certification": values["require_passing_certification"],
            "allow_active_waiver": values["allow_active_waiver"],
        },
    )


def _utc_iso(value: Any, field: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, datetime):
        raise ReleaseQualityUnavailable(f"{field} timestamp is invalid")
    moment = value
    if moment.tzinfo is not None:
        try:
            moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError) as exc:
            raise ReleaseQualityUnavailable(f"{field} timestamp is invalid") from exc
    return moment.replace(fold=0).isoformat(timespec="microseconds") + "Z"


def _payload(row: TenantReleaseQualitySloPolicy, *, tenant_id: str) -> dict[str, Any]:
    """Validate and project one persisted policy without leaking write inputs."""

    try:
        row_tenant = _key(row.tenant_id, "policy.tenant_id", 64)
        if row_tenant != tenant_id:
            raise ReleaseQualityUnavailable("SLO policy tenant scope is invalid")
        policy_id = _key(row.id, "policy.id", 64)
        name = _text(row.name, "policy.name", 128)
        scope, value, channel, canonical_active_key = _normalize_scope(
            row.scope_type, row.scope_value, row.channel_id
        )
        status = _text(row.status, "policy.status", 16).casefold()
        if status not in _STATUSES:
            raise ReleaseQualityUnavailable("SLO policy status is invalid")
        if type(row.revision) is not int or row.revision < 1:
            raise ReleaseQualityUnavailable("SLO policy revision is invalid")
        thresholds = _normalize_thresholds(
            certification_warning_minutes=row.certification_warning_minutes,
            certification_critical_minutes=row.certification_critical_minutes,
            waiver_warning_minutes=row.waiver_warning_minutes,
            max_open_alerts=row.max_open_alerts,
            auto_queue_recertification=row.auto_queue_recertification,
            require_passing_certification=row.require_passing_certification,
            allow_active_waiver=row.allow_active_waiver,
        )
        if status == "active":
            if row.active_scope_key != canonical_active_key:
                raise ReleaseQualityUnavailable("SLO policy active scope key is invalid")
            if row.disabled_at is not None or row.disabled_by is not None:
                raise ReleaseQualityUnavailable("SLO policy active lifecycle is invalid")
        else:
            if (
                row.active_scope_key is not None
                or row.disabled_at is None
                or row.disabled_by is None
            ):
                raise ReleaseQualityUnavailable("SLO policy disabled lifecycle is invalid")
        policy_values = {
            "scope_type": scope,
            "scope_value": value,
            "channel_id": channel,
            "revision": row.revision,
            **thresholds,
        }
        stored_digest = _digest(row.policy_digest, "policy")
        if stored_digest != _policy_digest(policy_values):
            raise ReleaseQualityUnavailable("SLO policy digest is invalid")
        return {
            "id": policy_id,
            "tenant_id": row_tenant,
            "name": name,
            "scope_type": scope,
            "scope_value": value,
            "channel_id": channel,
            "active_scope_key": canonical_active_key if status == "active" else None,
            "status": status,
            "revision": row.revision,
            **thresholds,
            "policy_digest": stored_digest,
            "created_at": _utc_iso(row.created_at, "policy.created_at"),
            "created_by": _key(row.created_by, "policy.created_by", 64),
            "updated_at": _utc_iso(row.updated_at, "policy.updated_at"),
            "updated_by": _key(row.updated_by, "policy.updated_by", 64),
            "disabled_at": _utc_iso(row.disabled_at, "policy.disabled_at", required=False),
            "disabled_by": (
                None if row.disabled_by is None else _key(row.disabled_by, "policy.disabled_by", 64)
            ),
        }
    except ReleaseQualityUnavailable:
        raise
    except ReleaseQualityError as exc:
        raise ReleaseQualityUnavailable("SLO policy authority is unavailable") from exc
    except (TypeError, ValueError, AttributeError) as exc:
        raise ReleaseQualityUnavailable("SLO policy authority is unavailable") from exc


def _request_fields(
    *, request_id: Any, request_ip: Any, idempotency_key: Any, reason: Any
) -> tuple[str, str, str, str]:
    request = _text(request_id, "request_id", 128, allow_empty=True)
    request_ip_value = _text(request_ip, "request_ip", 64, allow_empty=True)
    try:
        key = normalize_idempotency_key(idempotency_key)
    except TenantMutationIdempotencyValidationError as exc:
        raise ReleaseQualityInvalid(str(exc)) from exc
    return request, request_ip_value, key, _reason(reason)


@contextmanager
def _mutation_scope(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
    request_hash: str,
    operation: str,
) -> Iterator[tuple[Session, Account, Any]]:
    try:
        key_digest = tenant_idempotency_key_digest(tenant_id, actor_id, idempotency_key)
    except TenantMutationIdempotencyValidationError as exc:
        raise ReleaseQualityInvalid(str(exc)) from exc
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant_id, actor_id, key_digest))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        stack.enter_context(session.begin())
        _begin_database_transaction(session)
        _ensure_capability(session.connection())
        tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if tenant is None or tenant.status != "active":
            raise ReleaseQualityForbidden("Tenant is unavailable")
        member, account = _actor_scope(session, tenant_id, actor_id)
        _require_tenant_manager(member)
        try:
            reservation = reserve_tenant_mutation(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                raw_idempotency_key=idempotency_key,
                request_hash=request_hash,
                operation=operation,
                resource_type="release_quality_slo_policy",
            )
        except TenantMutationIdempotencyConflict as exc:
            raise ReleaseQualityConflict("idempotency key conflict") from exc
        except TenantMutationIdempotencyInProgress as exc:
            raise ReleaseQualityConflict("idempotency request is in progress") from exc
        except TenantMutationIdempotencyValidationError as exc:
            raise ReleaseQualityInvalid(str(exc)) from exc
        try:
            yield session, account, reservation
        except ReleaseQualityError:
            session.rollback()
            raise
        except IntegrityError as exc:
            session.rollback()
            raise ReleaseQualityConflict(
                "Release quality SLO Policy violates an authority constraint"
            ) from exc
        except SQLAlchemyError as exc:
            session.rollback()
            raise ReleaseQualityUnavailable() from exc


def _complete(
    session: Session,
    reservation: Any,
    *,
    payload: Mapping[str, Any],
    status: int,
    resource_id: str,
) -> ServiceResult:
    try:
        replay = complete_tenant_mutation(
            session,
            reservation,
            response_for_replay=payload,
            http_status=status,
            resource_id=resource_id,
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise ReleaseQualityInvalid(str(exc)) from exc
    return ServiceResult(dict(replay), status)


def _audit(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    account: Account,
    action: str,
    resource_id: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any],
    request_id: str,
    request_ip: str,
    now: datetime,
) -> None:
    session.add(
        TenantAuditEvent(
            id=f"tenant-audit-{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_name_snapshot=str(account.name)[:128],
            actor_email_snapshot=str(account.email)[:256],
            action=action,
            resource_type="release_quality_slo_policy",
            resource_id=resource_id,
            before_snapshot=(sanitize_audit_snapshot(dict(before)) if before is not None else None),
            after_snapshot=sanitize_audit_snapshot(dict(after)),
            request_id=request_id,
            request_ip=request_ip,
            occurred_at=now,
        )
    )


def create_quality_slo_policy(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    name: str,
    scope_type: str,
    scope_value: str,
    channel_id: str | None,
    certification_warning_minutes: int,
    certification_critical_minutes: int,
    waiver_warning_minutes: int,
    max_open_alerts: int,
    auto_queue_recertification: bool,
    require_passing_certification: bool,
    allow_active_waiver: bool,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
) -> ServiceResult:
    tenant = _key(tenant_id, "tenant_id", 64)
    actor = _key(actor_id, "actor_id", 64)
    clean_name = _text(name, "name", 128)
    scope, value, channel, active_scope_key = _normalize_scope(scope_type, scope_value, channel_id)
    thresholds = _normalize_thresholds(
        certification_warning_minutes=certification_warning_minutes,
        certification_critical_minutes=certification_critical_minutes,
        waiver_warning_minutes=waiver_warning_minutes,
        max_open_alerts=max_open_alerts,
        auto_queue_recertification=auto_queue_recertification,
        require_passing_certification=require_passing_certification,
        allow_active_waiver=allow_active_waiver,
    )
    request, request_ip_value, key, clean_reason = _request_fields(
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        reason=reason,
    )
    request_hash = tenant_request_hash(
        operation="create_quality_slo_policy",
        path_identity={"tenant_id": tenant},
        body={
            "name": clean_name,
            "scope_type": scope,
            "scope_value": value,
            "channel_id": channel,
            **thresholds,
            "reason": clean_reason,
        },
    )
    with _mutation_scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        idempotency_key=key,
        request_hash=request_hash,
        operation="create_quality_slo_policy",
    ) as (session, account, reservation):
        if reservation.replay is not None:
            return ServiceResult(dict(reservation.replay.response), reservation.replay.http_status)
        if channel is not None:
            channel_row = session.scalar(
                select(TenantReleaseChannel)
                .where(
                    TenantReleaseChannel.tenant_id == tenant,
                    TenantReleaseChannel.id == channel,
                    TenantReleaseChannel.status == "active",
                )
                .with_for_update()
            )
            if channel_row is None:
                raise ReleaseQualityNotFound("Release Channel is unavailable")
        duplicate = session.scalar(
            select(TenantReleaseQualitySloPolicy.id)
            .where(
                TenantReleaseQualitySloPolicy.tenant_id == tenant,
                TenantReleaseQualitySloPolicy.active_scope_key == active_scope_key,
            )
            .with_for_update()
        )
        if duplicate is not None:
            raise ReleaseQualityConflict("An active quality policy already exists for this scope")
        now = datetime.utcnow()
        digest_values = {
            "scope_type": scope,
            "scope_value": value,
            "channel_id": channel,
            "revision": 1,
            **thresholds,
        }
        row = TenantReleaseQualitySloPolicy(
            id=f"slo-policy-{uuid.uuid4().hex}",
            tenant_id=tenant,
            name=clean_name,
            scope_type=scope,
            scope_value=value,
            channel_id=channel,
            active_scope_key=active_scope_key,
            status="active",
            revision=1,
            **thresholds,
            policy_digest=_policy_digest(digest_values),
            created_at=now,
            created_by=actor,
            updated_at=now,
            updated_by=actor,
        )
        session.add(row)
        session.flush()
        policy = _payload(row, tenant_id=tenant)
        payload = {
            "state": "applied",
            "operation": "quality_slo_policy_create",
            "resource_id": row.id,
            "policy": policy,
            "message": "Release quality SLO policy created",
            "retryable": False,
        }
        _audit(
            session,
            tenant_id=tenant,
            actor_id=actor,
            account=account,
            action="knowledge_base.release_quality.slo_policy_created",
            resource_id=row.id,
            before=None,
            after={"policy": policy},
            request_id=request,
            request_ip=request_ip_value,
            now=now,
        )
        session.flush()
        return _complete(
            session,
            reservation,
            payload=payload,
            status=201,
            resource_id=row.id,
        )


def update_quality_slo_policy(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    policy_id: str,
    expected_revision: int,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
    name: str | None = None,
    certification_warning_minutes: int | None = None,
    certification_critical_minutes: int | None = None,
    waiver_warning_minutes: int | None = None,
    max_open_alerts: int | None = None,
    auto_queue_recertification: bool | None = None,
    require_passing_certification: bool | None = None,
    allow_active_waiver: bool | None = None,
    status: str | None = None,
) -> ServiceResult:
    tenant = _key(tenant_id, "tenant_id", 64)
    actor = _key(actor_id, "actor_id", 64)
    policy_key = _key(policy_id, "policy_id", 64)
    expected = _exact_integer(expected_revision, "expected_revision")
    raw_changes = {
        "name": name,
        "certification_warning_minutes": certification_warning_minutes,
        "certification_critical_minutes": certification_critical_minutes,
        "waiver_warning_minutes": waiver_warning_minutes,
        "max_open_alerts": max_open_alerts,
        "auto_queue_recertification": auto_queue_recertification,
        "require_passing_certification": require_passing_certification,
        "allow_active_waiver": allow_active_waiver,
        "status": status,
    }
    if not any(value is not None for value in raw_changes.values()):
        raise ReleaseQualityInvalid("quality SLO policy patch must include a changed field")
    changes: dict[str, Any] = {}
    if name is not None:
        changes["name"] = _text(name, "name", 128)
    for field in (
        "certification_warning_minutes",
        "certification_critical_minutes",
        "waiver_warning_minutes",
        "max_open_alerts",
    ):
        value = raw_changes[field]
        if value is not None:
            changes[field] = _exact_integer(value, field)
    for field in (
        "auto_queue_recertification",
        "require_passing_certification",
        "allow_active_waiver",
    ):
        value = raw_changes[field]
        if value is not None:
            changes[field] = _exact_boolean(value, field)
    if status is not None:
        normalized_status = _text(status, "status", 16).casefold()
        if normalized_status not in _STATUSES:
            raise ReleaseQualityInvalid("status is invalid")
        changes["status"] = normalized_status
    request, request_ip_value, key, clean_reason = _request_fields(
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        reason=reason,
    )
    request_hash = tenant_request_hash(
        operation="update_quality_slo_policy",
        path_identity={"tenant_id": tenant, "policy_id": policy_key},
        body={"expected_revision": expected, "changes": changes, "reason": clean_reason},
    )
    with _mutation_scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        idempotency_key=key,
        request_hash=request_hash,
        operation="update_quality_slo_policy",
    ) as (session, account, reservation):
        if reservation.replay is not None:
            return ServiceResult(dict(reservation.replay.response), reservation.replay.http_status)
        row = session.scalar(
            select(TenantReleaseQualitySloPolicy)
            .where(
                TenantReleaseQualitySloPolicy.tenant_id == tenant,
                TenantReleaseQualitySloPolicy.id == policy_key,
            )
            .with_for_update()
        )
        if row is None:
            raise ReleaseQualityNotFound("Quality SLO policy does not exist")
        before = _payload(row, tenant_id=tenant)
        if row.revision != expected:
            raise ReleaseQualityConflict("Quality SLO policy revision changed")
        combined = {field: getattr(row, field) for field in _THRESHOLD_FIELDS}
        combined.update(
            {field: value for field, value in changes.items() if field in _THRESHOLD_FIELDS}
        )
        normalized_thresholds = _normalize_thresholds(**combined)
        target_status = changes.get("status", before["status"])
        for field, value in changes.items():
            if field != "status":
                setattr(row, field, value)
        now = datetime.utcnow()
        canonical_active_key = f"{before['scope_type']}:{before['scope_value']}"
        if target_status == "active":
            duplicate = session.scalar(
                select(TenantReleaseQualitySloPolicy.id)
                .where(
                    TenantReleaseQualitySloPolicy.tenant_id == tenant,
                    TenantReleaseQualitySloPolicy.active_scope_key == canonical_active_key,
                    TenantReleaseQualitySloPolicy.id != row.id,
                )
                .with_for_update()
            )
            if duplicate is not None:
                raise ReleaseQualityConflict(
                    "An active quality policy already exists for this scope"
                )
            row.active_scope_key = canonical_active_key
            row.disabled_at = None
            row.disabled_by = None
        else:
            row.active_scope_key = None
            row.disabled_at = now
            row.disabled_by = actor
        row.status = target_status
        row.revision = expected + 1
        row.updated_at = now
        row.updated_by = actor
        for field, value in normalized_thresholds.items():
            setattr(row, field, value)
        row.policy_digest = _policy_digest(
            {
                "scope_type": before["scope_type"],
                "scope_value": before["scope_value"],
                "channel_id": before["channel_id"],
                "revision": row.revision,
                **normalized_thresholds,
            }
        )
        session.flush()
        policy = _payload(row, tenant_id=tenant)
        payload = {
            "state": "applied",
            "operation": "quality_slo_policy_update",
            "resource_id": row.id,
            "policy": policy,
            "message": "Release quality SLO policy updated",
            "retryable": False,
        }
        _audit(
            session,
            tenant_id=tenant,
            actor_id=actor,
            account=account,
            action="knowledge_base.release_quality.slo_policy_updated",
            resource_id=row.id,
            before={"policy": before},
            after={"policy": policy},
            request_id=request,
            request_ip=request_ip_value,
            now=now,
        )
        session.flush()
        return _complete(
            session,
            reservation,
            payload=payload,
            status=200,
            resource_id=row.id,
        )


def _encode_cursor(moment: datetime, row_id: str) -> str:
    payload = json.dumps(
        {"kind": _CURSOR_KIND, "time": moment.isoformat(timespec="microseconds"), "id": row_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(value: Any) -> tuple[datetime, str] | None:
    if value is None:
        return None
    encoded = _text(value, "cursor", 2048)
    try:
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, Mapping) or payload.get("kind") != _CURSOR_KIND:
            raise ValueError("cursor kind")
        raw_time = payload.get("time")
        if not isinstance(raw_time, str):
            raise ValueError("cursor time")
        if raw_time.endswith(("Z", "z")):
            raw_time = raw_time[:-1] + "+00:00"
        moment = datetime.fromisoformat(raw_time)
        if moment.tzinfo is not None:
            moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
        row_id = _key(payload.get("id"), "cursor.id", 64)
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseQualityInvalid("cursor is invalid") from exc
    return moment, row_id


def _read_scope(engine: Any, tenant_id: str, actor_id: str) -> Session:
    session = Session(engine, expire_on_commit=False)
    try:
        _ensure_capability(session.connection())
        tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id))
        if tenant is None or tenant.status != "active":
            raise ReleaseQualityForbidden("Tenant is unavailable")
        member, _account = _actor_scope(session, tenant_id, actor_id)
        _require_read(member)
        return session
    except Exception:
        session.close()
        raise


def list_quality_slo_policies(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    scope_type: str | None = None,
    scope_value: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ServiceResult:
    tenant = _key(tenant_id, "tenant_id", 64)
    actor = _key(actor_id, "actor_id", 64)
    page_size = _exact_integer(limit, "limit", minimum=1)
    if page_size > 200:
        raise ReleaseQualityInvalid("limit must be <= 200")
    scope = None if scope_type is None else _text(scope_type, "scope_type", 16).casefold()
    if scope is not None and scope not in _SCOPE_TYPES:
        raise ReleaseQualityInvalid("scope_type is invalid")
    value = None if scope_value is None else _text(scope_value, "scope_value", 128)
    if scope == "global" and value is not None and value != "*":
        raise ReleaseQualityInvalid("global scope filter must use '*'")
    if scope == "risk_tier" and value is not None and value.casefold() not in _RISK_TIERS:
        raise ReleaseQualityInvalid("risk_tier scope filter is invalid")
    if scope == "risk_tier" and value is not None:
        value = value.casefold()
    state = None if status is None else _text(status, "status", 16).casefold()
    if state is not None and state not in _STATUSES:
        raise ReleaseQualityInvalid("status is invalid")
    after = _decode_cursor(cursor)
    session: Session | None = None
    try:
        session = _read_scope(engine, tenant, actor)
        statement = select(TenantReleaseQualitySloPolicy).where(
            TenantReleaseQualitySloPolicy.tenant_id == tenant
        )
        if scope is not None:
            statement = statement.where(TenantReleaseQualitySloPolicy.scope_type == scope)
        if value is not None:
            statement = statement.where(TenantReleaseQualitySloPolicy.scope_value == value)
        if state is not None:
            statement = statement.where(TenantReleaseQualitySloPolicy.status == state)
        if after is not None:
            moment, row_id = after
            statement = statement.where(
                or_(
                    TenantReleaseQualitySloPolicy.updated_at < moment,
                    and_(
                        TenantReleaseQualitySloPolicy.updated_at == moment,
                        TenantReleaseQualitySloPolicy.id < row_id,
                    ),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    TenantReleaseQualitySloPolicy.updated_at.desc(),
                    TenantReleaseQualitySloPolicy.id.desc(),
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        visible = rows[:page_size]
        items = [_payload(row, tenant_id=tenant) for row in visible]
        next_cursor = (
            _encode_cursor(visible[-1].updated_at, visible[-1].id) if has_more and visible else None
        )
        return ServiceResult({"items": items, "next_cursor": next_cursor})
    except ReleaseQualityError:
        raise
    except SQLAlchemyError as exc:
        raise ReleaseQualityUnavailable() from exc
    finally:
        if session is not None:
            session.close()


__all__ = [
    "ReleaseQualityConflict",
    "ReleaseQualityError",
    "ReleaseQualityForbidden",
    "ReleaseQualityInvalid",
    "ReleaseQualityNotFound",
    "ReleaseQualityUnavailable",
    "create_quality_slo_policy",
    "list_quality_slo_policies",
    "update_quality_slo_policy",
]
