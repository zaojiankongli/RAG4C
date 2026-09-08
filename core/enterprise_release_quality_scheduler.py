"""Dataset-scoped, revision-fenced Release Quality scan schedules.

This module owns only the Stage 21 Schedule authority. It deliberately does not
enqueue or execute a Scan Run. Every mutation is Tenant/Actor idempotent,
Dataset-scoped, audited and revision-fenced; the next-slot helper is a pure UTC
fixed-interval calculation for the later Run worker.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import base64
import json
import re
from typing import Any
import uuid

from sqlalchemy import MetaData, Table, and_, func, or_, select, update
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
    complete_tenant_mutation,
    engine_serialization_lock,
    idempotency_key_lock,
    reserve_tenant_mutation,
    tenant_idempotency_key_digest,
    tenant_request_hash,
)
from core.enterprise_release_quality_operations import (
    canonical_observation,
    canonical_operations_digest,
    evaluate_release_quality_slo,
)
from core.knowledge_governance import sanitize_audit_snapshot
from core.knowledge_permissions import KNOWLEDGE_MANAGE, KNOWLEDGE_READ, role_allows
from models.orm import (
    AppDatasetReference,
    Dataset,
    DatasetChannelRelease,
    DatasetReleaseManifest,
    DatasetQualityBaseline,
    DatasetReleaseQualityAlert,
    DatasetReleaseQualityCertification,
    DatasetReleaseQualityObservation,
    DatasetReleaseQualityWaiver,
    DatasetReleaseRecertificationJob,
    Tenant,
    TenantMember,
    TenantReleaseChannel,
    TenantReleaseQualityScanRun,
    TenantReleaseQualityScanSchedule,
    TenantReleaseQualitySloPolicy,
)


UTC = timezone.utc
MIN_INTERVAL_SECONDS = 300
MAX_INTERVAL_SECONDS = 604_800
QUALITY_SCHEDULE_CAPABILITY = "fixed_interval_utc"
QUALITY_SCHEDULE_RESOURCE_TYPE = "quality_scan_schedule"
QUALITY_SCAN_RUN_RESOURCE_TYPE = "quality_scan_run"
QUALITY_SCAN_ENQUEUE_OPERATION = "quality_scan_run.enqueue"
QUALITY_SCAN_CANCEL_OPERATION = "quality_scan_run.cancel"
QUALITY_SCHEDULE_READ_PERMISSION = KNOWLEDGE_READ
QUALITY_SCHEDULE_MUTATION_PERMISSION = KNOWLEDGE_MANAGE

_SCHEDULE_STATUSES = frozenset({"active", "paused", "archived"})
_MANAGER_ROLES = frozenset({"owner", "admin"})
_SAFE_TEXT = re.compile(r"^[^\x00-\x1f\x7f]*$")
_REQUIRED_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "tenants": frozenset({"id", "status"}),
    "accounts": frozenset({"id", "name", "email"}),
    "tenant_members": frozenset({"tenant_id", "account_id", "role", "status"}),
    "datasets": frozenset({"id", "tenant_id", "owner_id", "status"}),
    "tenant_release_quality_slo_policies": frozenset(
        {"id", "tenant_id", "status", "active_scope_key", "revision"}
    ),
    "tenant_release_quality_scan_schedules": frozenset(
        {
            "id",
            "tenant_id",
            "dataset_id",
            "slo_policy_id",
            "status",
            "active_policy_slot",
            "revision",
            "interval_seconds",
            "next_run_at",
            "last_enqueued_at",
            "created_at",
            "created_by",
            "updated_at",
            "updated_by",
            "paused_at",
            "paused_by",
            "archived_at",
            "archived_by",
        }
    ),
    "tenant_control_mutation_requests": frozenset(
        {
            "id",
            "tenant_id",
            "actor_id",
            "idempotency_key",
            "request_hash",
            "operation",
            "resource_type",
            "resource_id",
            "status",
            "response_json",
            "http_status",
            "created_at",
            "completed_at",
        }
    ),
    "tenant_audit_events": frozenset(
        {
            "id",
            "tenant_id",
            "actor_id",
            "actor_name_snapshot",
            "actor_email_snapshot",
            "action",
            "resource_type",
            "resource_id",
            "target_account_id",
            "before_snapshot",
            "after_snapshot",
            "request_id",
            "request_ip",
            "occurred_at",
        }
    ),
}


class QualityScheduleError(RuntimeError):
    """Base class for stable Schedule authority errors."""

    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class QualityScheduleMigrationRequired(QualityScheduleError):
    def __init__(self, message: str = "Quality Schedule schema is unavailable") -> None:
        super().__init__("quality_schedule_migration_required", message, 503)


class QualityScheduleUnavailable(QualityScheduleError):
    def __init__(self, message: str = "Quality Schedule authority is unavailable") -> None:
        super().__init__("quality_schedule_unavailable", message, 503)


class QualityScheduleForbidden(QualityScheduleError):
    def __init__(self, message: str = "当前身份无权操作该 Dataset 的 Quality Schedule") -> None:
        super().__init__("quality_schedule_forbidden", message, 403)


class QualityScheduleNotFound(QualityScheduleError):
    def __init__(self, message: str = "Quality Schedule resource does not exist") -> None:
        super().__init__("quality_schedule_not_found", message, 404)


class QualityScheduleValidation(QualityScheduleError):
    def __init__(self, message: str = "Quality Schedule request is invalid") -> None:
        super().__init__("quality_schedule_validation_error", message, 422)


class QualityScheduleConflict(QualityScheduleError):
    def __init__(
        self,
        message: str = "Quality Schedule request conflicts with current authority",
    ) -> None:
        super().__init__("quality_schedule_conflict", message, 409)


class QualityScheduleRevisionConflict(QualityScheduleConflict):
    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        self.expected_revision = expected_revision
        self.current_revision = current_revision
        super().__init__("quality schedule revision conflict")
        self.code = "quality_schedule_revision_conflict"


class QualityScheduleIdempotencyConflict(QualityScheduleConflict):
    def __init__(
        self,
        message: str = "idempotency key was already used for a different request",
    ) -> None:
        super().__init__(message)
        self.code = "quality_schedule_idempotency_conflict"


class QualityScheduleIdempotencyInProgress(QualityScheduleConflict):
    def __init__(
        self,
        message: str = "the same quality schedule mutation is already in progress",
    ) -> None:
        super().__init__(message)
        self.code = "quality_schedule_idempotency_in_progress"


@dataclass(frozen=True)
class ServiceResult:
    """Stable service return shape shared by Stage 21 control-plane mutations."""

    body: dict[str, Any]
    status: int = 200

    def __getitem__(self, key: str) -> Any:
        return self.body[key]


@dataclass(frozen=True)
class _Actor:
    tenant_id: str
    account_id: str
    role: str
    name: str
    email: str


@dataclass(frozen=True)
class _MutationContext:
    tenant_id: str
    actor_id: str
    dataset_id: str
    request_id: str
    request_ip: str
    reason: str
    now: datetime


Mutation = Callable[
    [Session, _MutationContext, _Actor, Mapping[str, Any], Table],
    tuple[dict[str, Any], int, str],
]


def _clean_text(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise QualityScheduleValidation(f"{field} must be a string")
    result = value.strip()
    if not result and not allow_empty:
        raise QualityScheduleValidation(f"{field} is required")
    if len(result) > maximum:
        raise QualityScheduleValidation(f"{field} is too long")
    if not _SAFE_TEXT.fullmatch(result):
        raise QualityScheduleValidation(f"{field} contains unsupported control characters")
    return result


def _clean_id(value: Any, field: str) -> str:
    return _clean_text(value, field, 64)


def _clean_request_id(value: Any) -> str:
    return _clean_text(value, "request_id", 128)


def _clean_ip(value: Any) -> str:
    return _clean_text(value, "request_ip", 64, allow_empty=True)


def _clean_reason(value: Any) -> str:
    return _clean_text(value, "reason", 512, allow_empty=True)


def _exact_interval(value: Any) -> int:
    if type(value) is not int:
        raise QualityScheduleValidation("interval_seconds must be an exact integer")
    if not MIN_INTERVAL_SECONDS <= value <= MAX_INTERVAL_SECONDS:
        raise QualityScheduleValidation(
            f"interval_seconds must be between {MIN_INTERVAL_SECONDS} and {MAX_INTERVAL_SECONDS}"
        )
    return value


def _expected_revision(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise QualityScheduleValidation("expected_revision must be an exact positive integer")
    return value


def _utc_naive(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise QualityScheduleValidation(f"{field} must be a datetime")
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    else:
        value = value.replace(tzinfo=None)
    return value


def _now(value: datetime | None) -> datetime:
    return _utc_naive(value if value is not None else datetime.now(UTC), "now")


def _iso(value: Any, field: str) -> str:
    if value is None:
        raise QualityScheduleUnavailable(f"{field} authority is missing")
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise QualityScheduleUnavailable(f"{field} authority is malformed") from exc
    return _utc_naive(value, field).isoformat(timespec="microseconds") + "Z"


def _iso_optional(value: Any, field: str) -> str | None:
    return None if value is None else _iso(value, field)


def next_quality_scan_slot(
    planned_at: datetime,
    now: datetime,
    interval_seconds: int,
) -> datetime:
    """Return the next UTC slot after a consumed fixed-interval slot.

    Naive datetimes are database UTC; aware datetimes are converted to UTC.
    Integer microseconds avoid floating-point drift. Missed intervals collapse
    to one future slot so a later worker cannot synthesize missed Runs.
    """

    planned = _utc_naive(planned_at, "planned_at")
    current = _utc_naive(now, "now")
    interval = _exact_interval(interval_seconds)
    delta = current - planned
    elapsed_microseconds = max(
        0, delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    )
    interval_microseconds = interval * 1_000_000
    steps = elapsed_microseconds // interval_microseconds + 1
    return planned + timedelta(microseconds=steps * interval_microseconds)


# Descriptive alias for the later Run worker; it is the same implementation.
next_fixed_interval_utc_slot = next_quality_scan_slot


def _table(session: Session, name: str) -> Table:
    required = _REQUIRED_TABLE_COLUMNS.get(name, frozenset())
    try:
        table = Table(name, MetaData(), autoload_with=session.connection())
    except SQLAlchemyError as exc:
        raise QualityScheduleMigrationRequired(f"missing Quality Schedule table: {name}") from exc
    missing = sorted(required - set(table.c.keys()))
    if missing:
        raise QualityScheduleMigrationRequired(
            f"Quality Schedule table {name} is missing required columns: {', '.join(missing)}"
        )
    return table


def _has_table(session: Session, name: str) -> bool:
    try:
        return bool(sqlalchemy_inspect(session.connection()).has_table(name))
    except SQLAlchemyError:
        return False


def _actor_and_dataset(
    session: Session,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
) -> tuple[_Actor, dict[str, Any]]:
    tenants = _table(session, "tenants")
    accounts = _table(session, "accounts")
    members = _table(session, "tenant_members")
    datasets = _table(session, "datasets")

    actor_row = (
        session.execute(
            select(
                tenants.c.status.label("tenant_status"),
                members.c.status.label("member_status"),
                members.c.role,
                accounts.c.name,
                accounts.c.email,
            )
            .select_from(
                members.join(accounts, accounts.c.id == members.c.account_id).join(
                    tenants, tenants.c.id == members.c.tenant_id
                )
            )
            .where(members.c.tenant_id == tenant_id, members.c.account_id == actor_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if actor_row is None:
        raise QualityScheduleForbidden("actor is not an active member of the requested Tenant")
    if str(actor_row["tenant_status"] or "").casefold() != "active":
        raise QualityScheduleForbidden("Tenant is not active")
    if str(actor_row["member_status"] or "").casefold() != "active":
        raise QualityScheduleForbidden("actor membership is not active")
    role = str(actor_row["role"] or "").strip().casefold()
    if role not in {"owner", "admin", "editor", "member"}:
        raise QualityScheduleUnavailable("actor role authority is malformed")

    dataset_columns = [datasets.c.id, datasets.c.owner_id, datasets.c.status]
    if "acl_mode" in datasets.c:
        dataset_columns.append(datasets.c.acl_mode)
    dataset_row = (
        session.execute(
            select(*dataset_columns)
            .where(datasets.c.tenant_id == tenant_id, datasets.c.id == dataset_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if dataset_row is None:
        # Do not disclose whether the Dataset belongs to another Tenant.
        raise QualityScheduleForbidden("Dataset is outside the actor Tenant scope")
    dataset = dict(dataset_row)
    if str(dataset["status"] or "").casefold() != "active":
        raise QualityScheduleForbidden("Dataset is not active")
    return (
        _Actor(
            tenant_id=tenant_id,
            account_id=actor_id,
            role=role,
            name=str(actor_row["name"] or ""),
            email=str(actor_row["email"] or ""),
        ),
        dataset,
    )


def _authorize_dataset(
    engine: Any,
    session: Session,
    actor: _Actor,
    dataset: Mapping[str, Any],
    *,
    permission: str,
) -> None:
    """Authorize against the Dataset after proving Tenant/Dataset identity."""

    if actor.role in _MANAGER_ROLES or str(dataset.get("owner_id") or "") == actor.account_id:
        return

    mode = str(dataset.get("acl_mode") or "tenant_role").strip().casefold()
    if mode == "tenant_role":
        if role_allows(actor.role, permission):
            return
        raise QualityScheduleForbidden(f"Dataset does not grant {permission}")
    if mode != "dataset_acl":
        raise QualityScheduleUnavailable("Dataset ACL mode authority is malformed")
    if not _has_table(session, "dataset_access_grants"):
        raise QualityScheduleUnavailable("Dataset ACL authority is unavailable")

    try:
        from core.enterprise_access_control import (
            DatasetAccessControlUnavailable,
            evaluate_dataset_permissions,
        )

        decision = evaluate_dataset_permissions(
            engine,
            actor.tenant_id,
            actor.account_id,
            actor.role,
            str(dataset["id"]),
            session=session,
        )
    except DatasetAccessControlUnavailable as exc:
        raise QualityScheduleUnavailable("Dataset ACL authority is unavailable") from exc
    if not decision.allows(permission):
        raise QualityScheduleForbidden(f"Dataset does not grant {permission}")


def _locked_policy(session: Session, tenant_id: str, policy_id: str) -> dict[str, Any]:
    policies = _table(session, "tenant_release_quality_slo_policies")
    row = (
        session.execute(
            select(policies)
            .where(policies.c.tenant_id == tenant_id, policies.c.id == policy_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise QualityScheduleNotFound("SLO Policy is outside the requested Tenant scope")
    return dict(row)


def _require_active_policy(policy: Mapping[str, Any]) -> None:
    status = str(policy.get("status") or "").strip().casefold()
    if status == "disabled":
        raise QualityScheduleConflict("disabled SLO Policy cannot have an active schedule")
    if status != "active" or not str(policy.get("active_scope_key") or "").strip():
        raise QualityScheduleUnavailable("SLO Policy active authority is malformed")


def _locked_schedule(
    session: Session,
    schedules: Table,
    *,
    tenant_id: str,
    dataset_id: str,
    schedule_id: str,
) -> dict[str, Any]:
    row = (
        session.execute(
            select(schedules)
            .where(
                schedules.c.tenant_id == tenant_id,
                schedules.c.dataset_id == dataset_id,
                schedules.c.id == schedule_id,
            )
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise QualityScheduleNotFound("Quality Schedule is not in the requested Dataset scope")
    return dict(row)


def _active_schedule_id(
    session: Session,
    schedules: Table,
    *,
    tenant_id: str,
    dataset_id: str,
    policy_id: str,
) -> str | None:
    row = session.execute(
        select(schedules.c.id)
        .where(
            schedules.c.tenant_id == tenant_id,
            schedules.c.dataset_id == dataset_id,
            schedules.c.status == "active",
            or_(
                schedules.c.slo_policy_id == policy_id, schedules.c.active_policy_slot == policy_id
            ),
        )
        .order_by(schedules.c.id)
        .with_for_update()
    ).first()
    return None if row is None else str(row[0])


def _schedule_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    return sanitize_audit_snapshot(
        {
            "id": str(row["id"]),
            "tenant_id": str(row["tenant_id"]),
            "dataset_id": str(row["dataset_id"]),
            "slo_policy_id": str(row["slo_policy_id"]),
            "status": str(row["status"]),
            "active_policy_slot": row.get("active_policy_slot"),
            "revision": int(row["revision"]),
            "interval_seconds": int(row["interval_seconds"]),
            "next_run_at": _iso(row["next_run_at"], "next_run_at"),
            "last_enqueued_at": _iso_optional(row.get("last_enqueued_at"), "last_enqueued_at"),
            "created_at": _iso(row["created_at"], "created_at"),
            "created_by": str(row["created_by"]),
            "updated_at": _iso(row["updated_at"], "updated_at"),
            "updated_by": str(row["updated_by"]),
            "paused_at": _iso_optional(row.get("paused_at"), "paused_at"),
            "paused_by": row.get("paused_by"),
            "archived_at": _iso_optional(row.get("archived_at"), "archived_at"),
            "archived_by": row.get("archived_by"),
            "capability": QUALITY_SCHEDULE_CAPABILITY,
        }
    )


def _audit(
    session: Session,
    audit_table: Table,
    *,
    actor: _Actor,
    action: str,
    schedule_id: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    request_id: str,
    request_ip: str,
    now: datetime,
    reason: str,
    resource_type: str = QUALITY_SCHEDULE_RESOURCE_TYPE,
) -> None:
    after_snapshot = dict(after) if after is not None else None
    if after_snapshot is not None and reason:
        after_snapshot["reason"] = reason
    session.execute(
        audit_table.insert().values(
            id=f"tenant-audit-{uuid.uuid4().hex}",
            tenant_id=actor.tenant_id,
            actor_id=actor.account_id,
            actor_name_snapshot=actor.name[:128],
            actor_email_snapshot=actor.email[:256],
            action=action,
            resource_type=resource_type,
            resource_id=schedule_id[:512],
            target_account_id=None,
            before_snapshot=sanitize_audit_snapshot(dict(before)) if before is not None else None,
            after_snapshot=(
                sanitize_audit_snapshot(after_snapshot) if after_snapshot is not None else None
            ),
            request_id=request_id[:128],
            request_ip=request_ip[:64],
            occurred_at=now,
        )
    )


def _mutation_context(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    reason: Any,
    request_id: Any,
    request_ip: Any,
    now: datetime | None,
) -> _MutationContext:
    # ``engine`` is accepted here to keep the validation boundary explicit;
    # actual process/database locks are acquired by _run_mutation.
    _ = engine
    return _MutationContext(
        tenant_id=_clean_id(tenant_id, "tenant_id"),
        actor_id=_clean_id(actor_id, "actor_id"),
        dataset_id=_clean_id(dataset_id, "dataset_id"),
        request_id=_clean_request_id(request_id),
        request_ip=_clean_ip(request_ip),
        reason=_clean_reason(reason),
        now=_now(now),
    )


def _reserve(
    session: Session,
    context: _MutationContext,
    *,
    idempotency_key: str,
    request_hash: str,
    operation: str,
    resource_type: str = QUALITY_SCHEDULE_RESOURCE_TYPE,
) -> Any:
    try:
        return reserve_tenant_mutation(
            session,
            tenant_id=context.tenant_id,
            actor_id=context.actor_id,
            raw_idempotency_key=idempotency_key,
            request_hash=request_hash,
            operation=operation,
            resource_type=resource_type,
        )
    except TenantMutationIdempotencyConflict as exc:
        raise QualityScheduleIdempotencyConflict() from exc
    except TenantMutationIdempotencyInProgress as exc:
        raise QualityScheduleIdempotencyInProgress() from exc
    except TenantMutationIdempotencyValidationError as exc:
        raise QualityScheduleValidation("Idempotency-Key is invalid") from exc


def _result_from_replay(reservation: Any) -> ServiceResult | None:
    replay = getattr(reservation, "replay", None)
    if replay is None:
        return None
    return ServiceResult(dict(replay.response), int(replay.http_status))


def _complete(
    session: Session,
    reservation: Any,
    *,
    payload: Mapping[str, Any],
    status: int,
    resource_id: str,
) -> ServiceResult:
    try:
        complete_tenant_mutation(
            session,
            reservation,
            response_for_replay=payload,
            http_status=status,
            resource_id=resource_id,
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        rendered = str(exc).casefold()
        if "active_policy" in rendered or "unique" in rendered:
            raise QualityScheduleConflict(
                "active schedule already exists for this Dataset and SLO Policy"
            ) from exc
        raise QualityScheduleUnavailable(
            "Quality Schedule transaction could not be committed"
        ) from exc
    except SQLAlchemyError as exc:
        session.rollback()
        raise QualityScheduleUnavailable(
            "Quality Schedule transaction could not be committed"
        ) from exc
    return ServiceResult(dict(payload), status)


def _run_mutation(
    engine: Any,
    *,
    context: _MutationContext,
    idempotency_key: Any,
    operation: str,
    path_identity: Mapping[str, str],
    body: Mapping[str, Any],
    mutate: Mutation,
) -> ServiceResult:
    try:
        clean_key = _clean_text(idempotency_key, "idempotency_key", 128)
        key_digest = tenant_idempotency_key_digest(context.tenant_id, context.actor_id, clean_key)
        request_hash = tenant_request_hash(
            operation=operation,
            path_identity=dict(path_identity),
            body=dict(body),
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise QualityScheduleValidation(str(exc)) from exc

    key_lock = idempotency_key_lock(context.tenant_id, context.actor_id, key_digest)
    engine_lock = engine_serialization_lock(engine)
    with key_lock, engine_lock, Session(engine, expire_on_commit=False) as session:
        _table(session, "tenant_release_quality_scan_schedules")
        actor, dataset = _actor_and_dataset(
            session, context.tenant_id, context.actor_id, context.dataset_id
        )
        _authorize_dataset(
            engine,
            session,
            actor,
            dataset,
            permission=QUALITY_SCHEDULE_MUTATION_PERMISSION,
        )
        reservation = _reserve(
            session,
            context,
            idempotency_key=clean_key,
            request_hash=request_hash,
            operation=operation,
        )
        replay = _result_from_replay(reservation)
        if replay is not None:
            return replay
        schedules = _table(session, "tenant_release_quality_scan_schedules")
        payload, status, resource_id = mutate(session, context, actor, dataset, schedules)
        return _complete(
            session,
            reservation,
            payload=payload,
            status=status,
            resource_id=resource_id,
        )


def _read_row(
    session: Session,
    schedules: Table,
    *,
    tenant_id: str,
    dataset_id: str,
    schedule_id: str,
) -> dict[str, Any]:
    return _locked_schedule(
        session,
        schedules,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        schedule_id=schedule_id,
    )


def _payload(
    *,
    operation: str,
    schedule: Mapping[str, Any],
    request_id: str,
    status: int,
) -> dict[str, Any]:
    projected = _schedule_snapshot(schedule)
    return {
        "state": "applied",
        "operation": operation,
        "resource_type": QUALITY_SCHEDULE_RESOURCE_TYPE,
        "resource_id": projected["id"],
        "revision": projected["revision"],
        "request_id": request_id,
        "schedule": projected,
        "capability": QUALITY_SCHEDULE_CAPABILITY,
        "message": "Quality Scan Schedule mutation applied",
        "http_status": status,
    }


def _create_mutation(
    session: Session,
    context: _MutationContext,
    actor: _Actor,
    dataset: Mapping[str, Any],
    schedules: Table,
    *,
    slo_policy_id: str,
    interval_seconds: int,
) -> tuple[dict[str, Any], int, str]:
    policy = _locked_policy(session, context.tenant_id, slo_policy_id)
    _require_active_policy(policy)
    duplicate = _active_schedule_id(
        session,
        schedules,
        tenant_id=context.tenant_id,
        dataset_id=context.dataset_id,
        policy_id=slo_policy_id,
    )
    if duplicate is not None:
        raise QualityScheduleConflict(
            "active schedule already exists for this Dataset and SLO Policy"
        )

    schedule_id = f"quality-schedule-{uuid.uuid4().hex}"
    session.execute(
        schedules.insert().values(
            id=schedule_id,
            tenant_id=context.tenant_id,
            dataset_id=context.dataset_id,
            slo_policy_id=slo_policy_id,
            status="active",
            active_policy_slot=slo_policy_id,
            revision=1,
            interval_seconds=interval_seconds,
            next_run_at=context.now + timedelta(seconds=interval_seconds),
            last_enqueued_at=None,
            created_at=context.now,
            created_by=actor.account_id,
            updated_at=context.now,
            updated_by=actor.account_id,
            paused_at=None,
            paused_by=None,
            archived_at=None,
            archived_by=None,
        )
    )
    row = _read_row(
        session,
        schedules,
        tenant_id=context.tenant_id,
        dataset_id=context.dataset_id,
        schedule_id=schedule_id,
    )
    after = _schedule_snapshot(row)
    _audit(
        session,
        _table(session, "tenant_audit_events"),
        actor=actor,
        action="quality_scan_schedule.created",
        schedule_id=schedule_id,
        before=None,
        after=after,
        request_id=context.request_id,
        request_ip=context.request_ip,
        now=context.now,
        reason=context.reason,
    )
    return (
        _payload(
            operation="quality_scan_schedule.create",
            schedule=row,
            request_id=context.request_id,
            status=201,
        ),
        201,
        schedule_id,
    )


def create_quality_scan_schedule(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    slo_policy_id: str,
    interval_seconds: int,
    idempotency_key: str,
    request_id: str,
    reason: str = "",
    request_ip: str = "",
    now: datetime | None = None,
) -> ServiceResult:
    context = _mutation_context(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        reason=reason,
        request_id=request_id,
        request_ip=request_ip,
        now=now,
    )
    policy = _clean_id(slo_policy_id, "slo_policy_id")
    interval = _exact_interval(interval_seconds)
    return _run_mutation(
        engine,
        context=context,
        idempotency_key=idempotency_key,
        operation="quality_scan_schedule.create",
        path_identity={
            "tenant_id": context.tenant_id,
            "dataset_id": context.dataset_id,
            "slo_policy_id": policy,
        },
        body={"interval_seconds": interval, "reason": context.reason},
        mutate=lambda session, ctx, actor, dataset, schedules: _create_mutation(
            session,
            ctx,
            actor,
            dataset,
            schedules,
            slo_policy_id=policy,
            interval_seconds=interval,
        ),
    )


def _update_mutation(
    session: Session,
    context: _MutationContext,
    actor: _Actor,
    dataset: Mapping[str, Any],
    schedules: Table,
    *,
    schedule_id: str,
    expected_revision: int,
    interval_seconds: int,
) -> tuple[dict[str, Any], int, str]:
    row = _locked_schedule(
        session,
        schedules,
        tenant_id=context.tenant_id,
        dataset_id=context.dataset_id,
        schedule_id=schedule_id,
    )
    current_revision = int(row["revision"])
    if current_revision != expected_revision:
        raise QualityScheduleRevisionConflict(
            expected_revision=expected_revision,
            current_revision=current_revision,
        )
    status = str(row["status"]).casefold()
    if status not in {"active", "paused"}:
        raise QualityScheduleConflict("archived schedule cannot be updated")
    policy = _locked_policy(session, context.tenant_id, str(row["slo_policy_id"]))
    if status == "active":
        _require_active_policy(policy)
    before = _schedule_snapshot(row)
    result = session.execute(
        update(schedules)
        .where(
            schedules.c.tenant_id == context.tenant_id,
            schedules.c.dataset_id == context.dataset_id,
            schedules.c.id == schedule_id,
            schedules.c.revision == expected_revision,
        )
        .values(
            revision=expected_revision + 1,
            interval_seconds=interval_seconds,
            next_run_at=context.now + timedelta(seconds=interval_seconds),
            updated_at=context.now,
            updated_by=actor.account_id,
        )
    )
    if result.rowcount != 1:
        raise QualityScheduleRevisionConflict(
            expected_revision=expected_revision,
            current_revision=current_revision,
        )
    after_row = _read_row(
        session,
        schedules,
        tenant_id=context.tenant_id,
        dataset_id=context.dataset_id,
        schedule_id=schedule_id,
    )
    after = _schedule_snapshot(after_row)
    _audit(
        session,
        _table(session, "tenant_audit_events"),
        actor=actor,
        action="quality_scan_schedule.updated",
        schedule_id=schedule_id,
        before=before,
        after=after,
        request_id=context.request_id,
        request_ip=context.request_ip,
        now=context.now,
        reason=context.reason,
    )
    return (
        _payload(
            operation="quality_scan_schedule.update",
            schedule=after_row,
            request_id=context.request_id,
            status=200,
        ),
        200,
        schedule_id,
    )


def update_quality_scan_schedule(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    schedule_id: str,
    expected_revision: int,
    interval_seconds: int,
    idempotency_key: str,
    request_id: str,
    reason: str = "",
    request_ip: str = "",
    now: datetime | None = None,
) -> ServiceResult:
    context = _mutation_context(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        reason=reason,
        request_id=request_id,
        request_ip=request_ip,
        now=now,
    )
    schedule = _clean_id(schedule_id, "schedule_id")
    expected = _expected_revision(expected_revision)
    interval = _exact_interval(interval_seconds)
    return _run_mutation(
        engine,
        context=context,
        idempotency_key=idempotency_key,
        operation="quality_scan_schedule.update",
        path_identity={
            "tenant_id": context.tenant_id,
            "dataset_id": context.dataset_id,
            "schedule_id": schedule,
        },
        body={
            "expected_revision": expected,
            "interval_seconds": interval,
            "reason": context.reason,
        },
        mutate=lambda session, ctx, actor, dataset, schedules: _update_mutation(
            session,
            ctx,
            actor,
            dataset,
            schedules,
            schedule_id=schedule,
            expected_revision=expected,
            interval_seconds=interval,
        ),
    )


def _transition_mutation(
    session: Session,
    context: _MutationContext,
    actor: _Actor,
    dataset: Mapping[str, Any],
    schedules: Table,
    *,
    schedule_id: str,
    expected_revision: int,
    target_status: str,
) -> tuple[dict[str, Any], int, str]:
    row = _locked_schedule(
        session,
        schedules,
        tenant_id=context.tenant_id,
        dataset_id=context.dataset_id,
        schedule_id=schedule_id,
    )
    current_revision = int(row["revision"])
    if current_revision != expected_revision:
        raise QualityScheduleRevisionConflict(
            expected_revision=expected_revision,
            current_revision=current_revision,
        )
    current_status = str(row["status"]).casefold()
    required_statuses = {
        "paused": {"active"},
        "active": {"paused"},
        "archived": {"active", "paused"},
    }[target_status]
    if current_status not in required_statuses:
        required = " or ".join(sorted(required_statuses))
        raise QualityScheduleConflict(f"quality schedule must be {required} before {target_status}")

    policy = _locked_policy(session, context.tenant_id, str(row["slo_policy_id"]))
    if target_status == "active":
        _require_active_policy(policy)
        duplicate = _active_schedule_id(
            session,
            schedules,
            tenant_id=context.tenant_id,
            dataset_id=context.dataset_id,
            policy_id=str(row["slo_policy_id"]),
        )
        if duplicate is not None and duplicate != schedule_id:
            raise QualityScheduleConflict(
                "active schedule already exists for this Dataset and SLO Policy"
            )

    before = _schedule_snapshot(row)
    values: dict[str, Any] = {
        "revision": expected_revision + 1,
        "status": target_status,
        "active_policy_slot": (str(row["slo_policy_id"]) if target_status == "active" else None),
        "updated_at": context.now,
        "updated_by": actor.account_id,
    }
    if target_status == "paused":
        values.update({"paused_at": context.now, "paused_by": actor.account_id})
    elif target_status == "active":
        values.update(
            {
                "next_run_at": context.now + timedelta(seconds=int(row["interval_seconds"])),
                "paused_at": None,
                "paused_by": None,
                "archived_at": None,
                "archived_by": None,
            }
        )
    else:
        values.update({"archived_at": context.now, "archived_by": actor.account_id})
        if current_status == "active":
            values.update({"paused_at": None, "paused_by": None})

    result = session.execute(
        update(schedules)
        .where(
            schedules.c.tenant_id == context.tenant_id,
            schedules.c.dataset_id == context.dataset_id,
            schedules.c.id == schedule_id,
            schedules.c.revision == expected_revision,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        raise QualityScheduleRevisionConflict(
            expected_revision=expected_revision,
            current_revision=current_revision,
        )
    after_row = _read_row(
        session,
        schedules,
        tenant_id=context.tenant_id,
        dataset_id=context.dataset_id,
        schedule_id=schedule_id,
    )
    after = _schedule_snapshot(after_row)
    _audit(
        session,
        _table(session, "tenant_audit_events"),
        actor=actor,
        action=f"quality_scan_schedule.{target_status}",
        schedule_id=schedule_id,
        before=before,
        after=after,
        request_id=context.request_id,
        request_ip=context.request_ip,
        now=context.now,
        reason=context.reason,
    )
    return (
        _payload(
            operation=f"quality_scan_schedule.{target_status}",
            schedule=after_row,
            request_id=context.request_id,
            status=200,
        ),
        200,
        schedule_id,
    )


def _transition(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    schedule_id: str,
    expected_revision: int,
    idempotency_key: str,
    request_id: str,
    reason: str,
    request_ip: str,
    now: datetime | None,
    target_status: str,
) -> ServiceResult:
    context = _mutation_context(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        reason=reason,
        request_id=request_id,
        request_ip=request_ip,
        now=now,
    )
    schedule = _clean_id(schedule_id, "schedule_id")
    expected = _expected_revision(expected_revision)
    return _run_mutation(
        engine,
        context=context,
        idempotency_key=idempotency_key,
        operation=f"quality_scan_schedule.{target_status}",
        path_identity={
            "tenant_id": context.tenant_id,
            "dataset_id": context.dataset_id,
            "schedule_id": schedule,
        },
        body={"expected_revision": expected, "reason": context.reason},
        mutate=lambda session, ctx, actor, dataset, schedules: _transition_mutation(
            session,
            ctx,
            actor,
            dataset,
            schedules,
            schedule_id=schedule,
            expected_revision=expected,
            target_status=target_status,
        ),
    )


def pause_quality_scan_schedule(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    schedule_id: str,
    expected_revision: int,
    idempotency_key: str,
    request_id: str,
    reason: str = "",
    request_ip: str = "",
    now: datetime | None = None,
) -> ServiceResult:
    return _transition(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        schedule_id=schedule_id,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        request_id=request_id,
        reason=reason,
        request_ip=request_ip,
        now=now,
        target_status="paused",
    )


def resume_quality_scan_schedule(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    schedule_id: str,
    expected_revision: int,
    idempotency_key: str,
    request_id: str,
    reason: str = "",
    request_ip: str = "",
    now: datetime | None = None,
) -> ServiceResult:
    return _transition(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        schedule_id=schedule_id,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        request_id=request_id,
        reason=reason,
        request_ip=request_ip,
        now=now,
        target_status="active",
    )


def archive_quality_scan_schedule(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    schedule_id: str,
    expected_revision: int,
    idempotency_key: str,
    request_id: str,
    reason: str = "",
    request_ip: str = "",
    now: datetime | None = None,
) -> ServiceResult:
    return _transition(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        schedule_id=schedule_id,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        request_id=request_id,
        reason=reason,
        request_ip=request_ip,
        now=now,
        target_status="archived",
    )


__all__ = [
    "MAX_INTERVAL_SECONDS",
    "MIN_INTERVAL_SECONDS",
    "QUALITY_SCHEDULE_CAPABILITY",
    "QUALITY_SCHEDULE_MUTATION_PERMISSION",
    "QUALITY_SCHEDULE_READ_PERMISSION",
    "QualityScheduleConflict",
    "QualityScheduleError",
    "QualityScheduleForbidden",
    "QualityScheduleIdempotencyConflict",
    "QualityScheduleIdempotencyInProgress",
    "QualityScheduleMigrationRequired",
    "QualityScheduleNotFound",
    "QualityScheduleRevisionConflict",
    "QualityScheduleUnavailable",
    "QualityScheduleValidation",
    "ServiceResult",
    "archive_quality_scan_schedule",
    "create_quality_scan_schedule",
    "next_fixed_interval_utc_slot",
    "next_quality_scan_slot",
    "pause_quality_scan_schedule",
    "resume_quality_scan_schedule",
    "update_quality_scan_schedule",
]

# ---------------------------------------------------------------------------
# Stage 21 Dataset-scoped Scan Run and immutable Observation authority
# ---------------------------------------------------------------------------

_RUN_SYSTEM_ACTOR = "system:quality-scanner"
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:query|body|note|ticket|credential|password|passwd|secret|token|api[_-]?key)"
    r"\s*[:=]\s*[^\s,;]+"
)


class QualityScanRunError(QualityScheduleError):
    """Base class for Dataset-scoped Scan Run failures."""


class QualityScanRunNotFound(QualityScanRunError):
    def __init__(
        self, message: str = "Quality Scan Run is outside the requested Dataset scope"
    ) -> None:
        super().__init__("quality_scan_run_not_found", message, 404)


class QualityScanRunConflict(QualityScanRunError):
    def __init__(self, message: str = "Quality Scan Run authority changed") -> None:
        super().__init__("quality_scan_run_conflict", message, 409)


class QualityObservationConflict(QualityScanRunConflict):
    def __init__(self, message: str = "Quality Observation authority changed") -> None:
        super().__init__(message)
        self.code = "quality_observation_conflict"


@dataclass(frozen=True)
class QualityScanRunResult:
    id: str
    tenant_id: str
    dataset_id: str
    schedule_id: str
    slo_policy_id: str
    slo_policy_revision: int
    status: str
    planned_at: datetime
    claim_owner: str | None
    claim_lease_until: datetime | None
    heartbeat_at: datetime | None
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    observation_count: int
    alert_count: int
    recertification_job_count: int
    idempotency_key_digest: str
    request_hash: str
    summary_digest: str | None
    safe_error_code: str | None
    safe_error: str | None


@dataclass(frozen=True)
class QualityScanTarget:
    tenant_id: str
    dataset_id: str
    release_id: str
    channel_id: str
    release_role: str
    manifest_digest: str
    channel_revision: int
    binding_revision: int | None = None


@dataclass(frozen=True)
class QualityObservationResult:
    id: str
    tenant_id: str
    dataset_id: str
    release_id: str
    channel_id: str
    scan_run_id: str
    release_role: str
    observation_digest: str
    created: bool


def _begin_run_write(session: Session) -> None:
    if session.bind is not None and session.bind.dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def _positive_seconds(value: Any, field: str) -> int:
    if type(value) is not int or not 1 <= value <= 86_400:
        raise QualityScheduleValidation(f"{field} must be an exact integer between 1 and 86400")
    return value


def _nonnegative(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise QualityScheduleValidation(f"{field} must be an exact nonnegative integer")
    return value


def _as_utc_naive(value: Any, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise QualityScanRunConflict(f"{field} authority is malformed") from exc
    return _utc_naive(value, field)


def _digest(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value != value.casefold()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise QualityObservationConflict(f"{field} must be a lowercase SHA-256 digest")
    return value


def _sanitize_scan_error(value: Any) -> str:
    return (
        _SECRET_ASSIGNMENT.sub("[REDACTED]", str(value or "").strip())[:512]
        or "Quality scan failed"
    )


def _run_result(row: TenantReleaseQualityScanRun) -> QualityScanRunResult:
    return QualityScanRunResult(
        id=row.id,
        tenant_id=row.tenant_id,
        dataset_id=row.dataset_id,
        schedule_id=row.schedule_id,
        slo_policy_id=row.slo_policy_id,
        slo_policy_revision=int(row.slo_policy_revision),
        status=row.status,
        planned_at=_as_utc_naive(row.planned_at, "planned_at"),
        claim_owner=row.claim_owner,
        claim_lease_until=(
            None
            if row.claim_lease_until is None
            else _as_utc_naive(row.claim_lease_until, "claim_lease_until")
        ),
        heartbeat_at=(
            None if row.heartbeat_at is None else _as_utc_naive(row.heartbeat_at, "heartbeat_at")
        ),
        attempt_count=int(row.attempt_count),
        max_attempts=int(row.max_attempts),
        next_attempt_at=(
            None
            if row.next_attempt_at is None
            else _as_utc_naive(row.next_attempt_at, "next_attempt_at")
        ),
        started_at=None if row.started_at is None else _as_utc_naive(row.started_at, "started_at"),
        finished_at=None
        if row.finished_at is None
        else _as_utc_naive(row.finished_at, "finished_at"),
        observation_count=int(row.observation_count),
        alert_count=int(row.alert_count),
        recertification_job_count=int(row.recertification_job_count),
        idempotency_key_digest=row.idempotency_key_digest,
        request_hash=row.request_hash,
        summary_digest=row.summary_digest,
        safe_error_code=row.safe_error_code,
        safe_error=row.safe_error,
    )


def _run_result_payload(result: QualityScanRunResult) -> dict[str, Any]:
    return {
        "id": result.id,
        "tenant_id": result.tenant_id,
        "dataset_id": result.dataset_id,
        "schedule_id": result.schedule_id,
        "slo_policy_id": result.slo_policy_id,
        "slo_policy_revision": result.slo_policy_revision,
        "status": result.status,
        "planned_at": _iso(result.planned_at, "planned_at"),
        "claim_owner": result.claim_owner,
        "claim_lease_until": _iso_optional(result.claim_lease_until, "claim_lease_until"),
        "heartbeat_at": _iso_optional(result.heartbeat_at, "heartbeat_at"),
        "attempt_count": result.attempt_count,
        "max_attempts": result.max_attempts,
        "next_attempt_at": _iso_optional(result.next_attempt_at, "next_attempt_at"),
        "started_at": _iso_optional(result.started_at, "started_at"),
        "finished_at": _iso_optional(result.finished_at, "finished_at"),
        "observation_count": result.observation_count,
        "alert_count": result.alert_count,
        "recertification_job_count": result.recertification_job_count,
        "idempotency_key_digest": result.idempotency_key_digest,
        "request_hash": result.request_hash,
        "summary_digest": result.summary_digest,
        "safe_error_code": result.safe_error_code,
        "safe_error": result.safe_error,
    }


def _manager_actor_id(session: Session, tenant_id: str) -> str:
    actor_id = session.scalar(
        select(TenantMember.account_id)
        .where(
            TenantMember.tenant_id == tenant_id,
            TenantMember.status == "active",
            TenantMember.role.in_(("owner", "admin")),
        )
        .order_by(TenantMember.account_id)
    )
    if not actor_id:
        raise QualityScheduleForbidden("Tenant has no active manager for Scan Run mutation")
    return str(actor_id)


def _manager_actor_and_dataset(
    engine: Any, session: Session, *, tenant_id: str, dataset_id: str
) -> tuple[_Actor, Mapping[str, Any]]:
    actor_id = _manager_actor_id(session, tenant_id)
    actor, dataset = _actor_and_dataset(session, tenant_id, actor_id, dataset_id)
    _authorize_dataset(
        engine,
        session,
        actor,
        dataset,
        permission=QUALITY_SCHEDULE_MUTATION_PERMISSION,
    )
    return actor, dataset


def _locked_run(
    session: Session, tenant_id: str, dataset_id: str, run_id: str
) -> TenantReleaseQualityScanRun:
    row = session.scalar(
        select(TenantReleaseQualityScanRun)
        .where(
            TenantReleaseQualityScanRun.tenant_id == tenant_id,
            TenantReleaseQualityScanRun.dataset_id == dataset_id,
            TenantReleaseQualityScanRun.id == run_id,
        )
        .with_for_update()
    )
    if row is None:
        raise QualityScanRunNotFound()
    return row


def _assert_running_lease(
    run: TenantReleaseQualityScanRun, *, worker_id: str, now: datetime
) -> None:
    if (
        run.status != "running"
        or run.claim_owner != worker_id
        or run.claim_lease_until is None
        or _as_utc_naive(run.claim_lease_until, "claim_lease_until") <= now
    ):
        raise QualityScanRunConflict("Quality Scan Run lease ownership changed")


def _scan_intent(
    schedule: TenantReleaseQualityScanSchedule, planned_at: datetime
) -> tuple[str, str, str]:
    authority = {
        "tenant_id": schedule.tenant_id,
        "dataset_id": schedule.dataset_id,
        "schedule_id": schedule.id,
        "slo_policy_id": schedule.slo_policy_id,
        "schedule_revision": int(schedule.revision),
        "planned_at": planned_at,
    }
    key_digest = canonical_operations_digest("quality-scan-slot", authority)
    request_hash = canonical_operations_digest("quality-scan-request", authority)
    return f"quality-run-{key_digest[:24]}", key_digest, request_hash


def _enqueue_due_quality_scans_in_session(
    session: Session,
    *,
    tenant: str,
    dataset_key: str,
    current: datetime,
    limit: int,
) -> list[QualityScanRunResult]:
    dataset = session.scalar(
        select(Dataset)
        .where(Dataset.tenant_id == tenant, Dataset.id == dataset_key)
        .with_for_update()
    )
    if dataset is None or dataset.status != "active":
        raise QualityScanRunNotFound("Dataset is outside the active Tenant scope")
    results: list[QualityScanRunResult] = []
    schedules = list(
        session.scalars(
            select(TenantReleaseQualityScanSchedule)
            .where(
                TenantReleaseQualityScanSchedule.tenant_id == tenant,
                TenantReleaseQualityScanSchedule.dataset_id == dataset_key,
                TenantReleaseQualityScanSchedule.status == "active",
                TenantReleaseQualityScanSchedule.next_run_at <= current,
            )
            .order_by(
                TenantReleaseQualityScanSchedule.next_run_at,
                TenantReleaseQualityScanSchedule.id,
            )
            .limit(limit)
            .with_for_update()
        )
    )
    for schedule in schedules:
        policy = session.scalar(
            select(TenantReleaseQualitySloPolicy)
            .where(
                TenantReleaseQualitySloPolicy.tenant_id == tenant,
                TenantReleaseQualitySloPolicy.id == schedule.slo_policy_id,
            )
            .with_for_update()
        )
        if policy is None or policy.status != "active" or not policy.active_scope_key:
            raise QualityScanRunConflict("SLO Policy authority is unavailable")
        planned_at = _as_utc_naive(schedule.next_run_at, "next_run_at")
        run_id, key_digest, request_hash = _scan_intent(schedule, planned_at)
        run = session.scalar(
            select(TenantReleaseQualityScanRun).where(
                TenantReleaseQualityScanRun.tenant_id == tenant,
                TenantReleaseQualityScanRun.dataset_id == dataset_key,
                TenantReleaseQualityScanRun.schedule_id == schedule.id,
                TenantReleaseQualityScanRun.planned_at == planned_at,
            )
        )
        if run is None:
            run = TenantReleaseQualityScanRun(
                id=run_id,
                tenant_id=tenant,
                dataset_id=dataset_key,
                schedule_id=schedule.id,
                slo_policy_id=schedule.slo_policy_id,
                slo_policy_revision=int(policy.revision),
                status="pending",
                planned_at=planned_at,
                attempt_count=0,
                max_attempts=3,
                next_attempt_at=current,
                observation_count=0,
                alert_count=0,
                recertification_job_count=0,
                idempotency_key_digest=key_digest,
                request_hash=request_hash,
                created_at=current,
                updated_at=current,
            )
            session.add(run)
            session.flush()
        schedule.next_run_at = next_quality_scan_slot(
            planned_at, current, int(schedule.interval_seconds)
        )
        schedule.last_enqueued_at = current
        schedule.updated_at = current
        schedule.updated_by = _RUN_SYSTEM_ACTOR
        results.append(_run_result(run))
    return results


def _enqueue_due_quality_scans_idempotent(
    engine: Any,
    *,
    tenant: str,
    dataset_key: str,
    current: datetime,
    limit: int,
    request_id: str | None,
    idempotency_key: str,
) -> ServiceResult:
    clean_key = _clean_text(idempotency_key, "idempotency_key", 128)
    request = _clean_request_id(request_id or f"quality-scan-enqueue:{dataset_key}")
    try:
        with Session(engine) as probe:
            actor_id = _manager_actor_id(probe, tenant)
        key_digest = tenant_idempotency_key_digest(tenant, actor_id, clean_key)
        request_hash = tenant_request_hash(
            operation=QUALITY_SCAN_ENQUEUE_OPERATION,
            path_identity={"tenant_id": tenant, "dataset_id": dataset_key},
            body={"limit": limit},
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise QualityScheduleValidation(str(exc)) from exc
    context = _MutationContext(
        tenant_id=tenant,
        actor_id=actor_id,
        dataset_id=dataset_key,
        request_id=request,
        request_ip="",
        reason="",
        now=current,
    )
    with idempotency_key_lock(tenant, actor_id, key_digest), engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            _begin_run_write(session)
            actor, _dataset = _manager_actor_and_dataset(
                engine, session, tenant_id=tenant, dataset_id=dataset_key
            )
            reservation = _reserve(
                session,
                context,
                idempotency_key=clean_key,
                request_hash=request_hash,
                operation=QUALITY_SCAN_ENQUEUE_OPERATION,
                resource_type=QUALITY_SCAN_RUN_RESOURCE_TYPE,
            )
            replay = _result_from_replay(reservation)
            if replay is not None:
                return replay
            results = _enqueue_due_quality_scans_in_session(
                session,
                tenant=tenant,
                dataset_key=dataset_key,
                current=current,
                limit=limit,
            )
            items = [_run_result_payload(result) for result in results]
            payload = {
                "state": "applied",
                "operation": QUALITY_SCAN_ENQUEUE_OPERATION,
                "resource_type": QUALITY_SCAN_RUN_RESOURCE_TYPE,
                "resource_id": items[0]["id"] if items else dataset_key,
                "request_id": request,
                "items": items,
                "message": "Quality Scan Run enqueue accepted",
            }
            audit_table = _table(session, "tenant_audit_events")
            for item in items:
                _audit(
                    session,
                    audit_table,
                    actor=actor,
                    action="quality_scan_run.enqueued",
                    schedule_id=str(item["id"]),
                    before=None,
                    after=item,
                    request_id=request,
                    request_ip="",
                    now=current,
                    reason="",
                    resource_type=QUALITY_SCAN_RUN_RESOURCE_TYPE,
                )
            return _complete(
                session,
                reservation,
                payload=payload,
                status=202,
                resource_id=str(payload["resource_id"]),
            )


def enqueue_due_quality_scans(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    now: datetime | None = None,
    limit: int = 50,
    request_id: str | None = None,
    idempotency_key: str | None = None,
    body: Any = None,
) -> list[QualityScanRunResult] | ServiceResult:
    del body
    tenant = _clean_id(tenant_id, "tenant_id")
    dataset_key = _clean_id(dataset_id, "dataset_id")
    if type(limit) is not int or not 1 <= limit <= 200:
        raise QualityScheduleValidation("limit must be an exact integer between 1 and 200")
    current = _now(now)
    if idempotency_key is not None:
        return _enqueue_due_quality_scans_idempotent(
            engine,
            tenant=tenant,
            dataset_key=dataset_key,
            current=current,
            limit=limit,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
    with engine_serialization_lock(engine), Session(engine, expire_on_commit=False) as session:
        _begin_run_write(session)
        results = _enqueue_due_quality_scans_in_session(
            session,
            tenant=tenant,
            dataset_key=dataset_key,
            current=current,
            limit=limit,
        )
        session.commit()
        return results


def claim_quality_scan_run(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    run_id: str,
    owner_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> QualityScanRunResult:
    tenant = _clean_id(tenant_id, "tenant_id")
    dataset = _clean_id(dataset_id, "dataset_id")
    run_key = _clean_id(run_id, "run_id")
    owner = _clean_text(owner_id, "owner_id", 128)
    lease = _positive_seconds(lease_seconds, "lease_seconds")
    current = _now(now)
    with engine_serialization_lock(engine), Session(engine, expire_on_commit=False) as session:
        _begin_run_write(session)
        run = _locked_run(session, tenant, dataset, run_key)
        if run.status in {"completed", "failed", "cancelled"}:
            raise QualityScanRunConflict("terminal Quality Scan Run cannot be claimed")
        live = (
            run.claim_lease_until is not None
            and _as_utc_naive(run.claim_lease_until, "claim_lease_until") > current
        )
        if run.status in {"claimed", "running"} and live:
            if run.claim_owner == owner:
                return _run_result(run)
            raise QualityScanRunConflict("Quality Scan Run lease is owned by another worker")
        if (
            run.status == "pending"
            and run.next_attempt_at is not None
            and _as_utc_naive(run.next_attempt_at, "next_attempt_at") > current
        ):
            raise QualityScanRunConflict("Quality Scan Run retry is not due yet")
        if int(run.attempt_count) >= int(run.max_attempts):
            raise QualityScanRunConflict("Quality Scan Run retry budget is exhausted")
        run.status = "claimed"
        run.claim_owner = owner
        run.claim_lease_until = current + timedelta(seconds=lease)
        run.heartbeat_at = None
        run.attempt_count = int(run.attempt_count) + 1
        run.started_at = None
        run.finished_at = None
        run.next_attempt_at = None
        run.safe_error_code = None
        run.safe_error = None
        run.updated_at = current
        session.commit()
        return _run_result(run)


def heartbeat_quality_scan_run(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    run_id: str,
    owner_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> QualityScanRunResult:
    tenant = _clean_id(tenant_id, "tenant_id")
    dataset = _clean_id(dataset_id, "dataset_id")
    run_key = _clean_id(run_id, "run_id")
    owner = _clean_text(owner_id, "owner_id", 128)
    lease = _positive_seconds(lease_seconds, "lease_seconds")
    current = _now(now)
    with engine_serialization_lock(engine), Session(engine, expire_on_commit=False) as session:
        _begin_run_write(session)
        run = _locked_run(session, tenant, dataset, run_key)
        if (
            run.status not in {"claimed", "running"}
            or run.claim_owner != owner
            or run.claim_lease_until is None
            or _as_utc_naive(run.claim_lease_until, "claim_lease_until") <= current
        ):
            raise QualityScanRunConflict("Quality Scan Run lease ownership changed")
        run.status = "running"
        run.heartbeat_at = current
        run.claim_lease_until = current + timedelta(seconds=lease)
        run.started_at = run.started_at or current
        run.updated_at = current
        session.commit()
        return _run_result(run)


def finish_quality_scan_run(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    run_id: str,
    owner_id: str,
    outcome: str,
    safe_error_code: str | None = None,
    safe_error: str | None = None,
    observation_count: int = 0,
    alert_count: int = 0,
    recertification_job_count: int = 0,
    now: datetime | None = None,
) -> QualityScanRunResult:
    tenant = _clean_id(tenant_id, "tenant_id")
    dataset = _clean_id(dataset_id, "dataset_id")
    run_key = _clean_id(run_id, "run_id")
    owner = _clean_text(owner_id, "owner_id", 128)
    result_status = str(outcome).casefold()
    if result_status not in {"completed", "failed"}:
        raise QualityScheduleValidation("outcome must be completed or failed")
    current = _now(now)
    _nonnegative(observation_count, "observation_count")
    _nonnegative(alert_count, "alert_count")
    _nonnegative(recertification_job_count, "recertification_job_count")
    with engine_serialization_lock(engine), Session(engine, expire_on_commit=False) as session:
        _begin_run_write(session)
        run = _locked_run(session, tenant, dataset, run_key)
        if (
            run.status not in {"claimed", "running"}
            or run.claim_owner != owner
            or run.claim_lease_until is None
            or _as_utc_naive(run.claim_lease_until, "claim_lease_until") <= current
        ):
            raise QualityScanRunConflict("Quality Scan Run lease ownership changed")
        counts = _persisted_run_counts(session, run)
        retryable = result_status == "failed" and int(run.attempt_count) < int(run.max_attempts)
        persisted_status = "pending" if retryable else result_status
        run.status = persisted_status
        run.observation_count = counts["observation_count"]
        run.alert_count = counts["alert_count"]
        run.recertification_job_count = counts["recertification_job_count"]
        run.summary_digest = canonical_operations_digest(
            "quality-scan-summary",
            {"status": persisted_status, **counts, "finished_at": current},
        )
        run.claim_owner = None
        run.claim_lease_until = None
        run.heartbeat_at = None
        run.started_at = None if retryable else run.started_at
        run.next_attempt_at = (
            current + timedelta(seconds=min(300, 5 * (2 ** max(0, int(run.attempt_count) - 1))))
            if retryable
            else None
        )
        run.finished_at = None if retryable else current
        run.updated_at = current
        if result_status == "failed":
            run.safe_error_code = _clean_text(
                safe_error_code or "quality_scan_failed", "safe_error_code", 64
            )
            run.safe_error = _sanitize_scan_error(safe_error)
        else:
            run.safe_error_code = None
            run.safe_error = None
        session.commit()
        return _run_result(run)


def _cancel_quality_scan_run_in_session(
    session: Session,
    *,
    tenant: str,
    dataset: str,
    run_key: str,
    owner: str,
    current: datetime,
) -> TenantReleaseQualityScanRun:
    run = _locked_run(session, tenant, dataset, run_key)
    if run.status == "cancelled":
        return run
    if run.status in {"completed", "failed"}:
        raise QualityScanRunConflict("terminal Quality Scan Run cannot be cancelled")
    if run.status in {"claimed", "running"}:
        if (
            run.claim_owner != owner
            or run.claim_lease_until is None
            or _as_utc_naive(run.claim_lease_until, "claim_lease_until") <= current
        ):
            raise QualityScanRunConflict("Quality Scan Run lease ownership changed")
    run.status = "cancelled"
    run.claim_owner = None
    run.claim_lease_until = None
    run.heartbeat_at = None
    run.next_attempt_at = None
    run.finished_at = current
    run.safe_error_code = None
    run.safe_error = None
    run.updated_at = current
    return run


def _cancel_quality_scan_run_idempotent(
    engine: Any,
    *,
    tenant: str,
    dataset: str,
    run_key: str,
    owner: str,
    reason: str,
    request_id: str | None,
    idempotency_key: str,
    current: datetime,
) -> ServiceResult:
    clean_key = _clean_text(idempotency_key, "idempotency_key", 128)
    request = _clean_request_id(request_id or f"quality-scan-cancel:{run_key}")
    try:
        with Session(engine) as probe:
            actor_id = _manager_actor_id(probe, tenant)
        key_digest = tenant_idempotency_key_digest(tenant, actor_id, clean_key)
        request_hash = tenant_request_hash(
            operation=QUALITY_SCAN_CANCEL_OPERATION,
            path_identity={
                "tenant_id": tenant,
                "dataset_id": dataset,
                "run_id": run_key,
            },
            body={"owner_id": owner, "reason": reason},
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise QualityScheduleValidation(str(exc)) from exc
    context = _MutationContext(
        tenant_id=tenant,
        actor_id=actor_id,
        dataset_id=dataset,
        request_id=request,
        request_ip="",
        reason=reason,
        now=current,
    )
    with idempotency_key_lock(tenant, actor_id, key_digest), engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            _begin_run_write(session)
            actor, _dataset = _manager_actor_and_dataset(
                engine, session, tenant_id=tenant, dataset_id=dataset
            )
            reservation = _reserve(
                session,
                context,
                idempotency_key=clean_key,
                request_hash=request_hash,
                operation=QUALITY_SCAN_CANCEL_OPERATION,
                resource_type=QUALITY_SCAN_RUN_RESOURCE_TYPE,
            )
            replay = _result_from_replay(reservation)
            if replay is not None:
                return replay
            run = _cancel_quality_scan_run_in_session(
                session,
                tenant=tenant,
                dataset=dataset,
                run_key=run_key,
                owner=owner,
                current=current,
            )
            projected = _run_result_payload(_run_result(run))
            payload = {
                "state": "applied",
                "operation": QUALITY_SCAN_CANCEL_OPERATION,
                "resource_type": QUALITY_SCAN_RUN_RESOURCE_TYPE,
                "resource_id": run.id,
                "request_id": request,
                "run": projected,
                "message": "Quality Scan Run cancelled",
            }
            _audit(
                session,
                _table(session, "tenant_audit_events"),
                actor=actor,
                action="quality_scan_run.cancelled",
                schedule_id=run.id,
                before=None,
                after=projected,
                request_id=request,
                request_ip="",
                now=current,
                reason=reason,
                resource_type=QUALITY_SCAN_RUN_RESOURCE_TYPE,
            )
            return _complete(
                session,
                reservation,
                payload=payload,
                status=200,
                resource_id=run.id,
            )


def cancel_quality_scan_run(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    run_id: str,
    owner_id: str,
    reason: str = "",
    request_id: str | None = None,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> QualityScanRunResult | ServiceResult:
    tenant = _clean_id(tenant_id, "tenant_id")
    dataset = _clean_id(dataset_id, "dataset_id")
    run_key = _clean_id(run_id, "run_id")
    owner = _clean_text(owner_id, "owner_id", 128)
    clean_reason = _clean_reason(reason)
    current = _now(now)
    if idempotency_key is not None:
        return _cancel_quality_scan_run_idempotent(
            engine,
            tenant=tenant,
            dataset=dataset,
            run_key=run_key,
            owner=owner,
            reason=clean_reason,
            request_id=request_id,
            idempotency_key=idempotency_key,
            current=current,
        )
    with engine_serialization_lock(engine), Session(engine, expire_on_commit=False) as session:
        _begin_run_write(session)
        run = _cancel_quality_scan_run_in_session(
            session,
            tenant=tenant,
            dataset=dataset,
            run_key=run_key,
            owner=owner,
            current=current,
        )
        session.commit()
        return _run_result(run)


def resolve_quality_scan_targets(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
) -> list[QualityScanTarget]:
    tenant = _clean_id(tenant_id, "tenant_id")
    dataset_key = _clean_id(dataset_id, "dataset_id")
    with Session(engine, expire_on_commit=False) as session:
        dataset = session.scalar(
            select(Dataset).where(Dataset.tenant_id == tenant, Dataset.id == dataset_key)
        )
        if dataset is None or dataset.status != "active":
            raise QualityScanRunNotFound("Dataset is outside the active Tenant scope")
        targets: dict[tuple[str, str, str], QualityScanTarget] = {}
        active_rows = session.execute(
            select(DatasetChannelRelease, TenantReleaseChannel, DatasetReleaseManifest)
            .join(
                TenantReleaseChannel,
                (TenantReleaseChannel.tenant_id == DatasetChannelRelease.tenant_id)
                & (TenantReleaseChannel.id == DatasetChannelRelease.channel_id),
            )
            .join(
                DatasetReleaseManifest,
                (DatasetReleaseManifest.tenant_id == DatasetChannelRelease.tenant_id)
                & (DatasetReleaseManifest.dataset_id == DatasetChannelRelease.dataset_id)
                & (DatasetReleaseManifest.id == DatasetChannelRelease.active_release_id),
            )
            .where(
                DatasetChannelRelease.tenant_id == tenant,
                DatasetChannelRelease.dataset_id == dataset_key,
                DatasetChannelRelease.status == "active",
                DatasetChannelRelease.active_slot == "active",
                TenantReleaseChannel.status == "active",
            )
            .order_by(DatasetChannelRelease.channel_id, DatasetChannelRelease.id)
        ).all()
        for binding, channel, release in active_rows:
            target = QualityScanTarget(
                tenant_id=tenant,
                dataset_id=dataset_key,
                release_id=release.id,
                channel_id=channel.id,
                release_role="active",
                manifest_digest=release.manifest_digest,
                channel_revision=int(channel.revision),
                binding_revision=int(binding.revision),
            )
            targets[(target.release_id, target.channel_id, target.release_role)] = target
        pinned_ids = list(
            session.scalars(
                select(AppDatasetReference.pinned_release_id)
                .where(
                    AppDatasetReference.tenant_id == tenant,
                    AppDatasetReference.dataset_id == dataset_key,
                    AppDatasetReference.status == "active",
                    AppDatasetReference.active_slot == "active",
                    AppDatasetReference.release_mode == "pinned",
                    AppDatasetReference.pinned_release_id.is_not(None),
                )
                .distinct()
                .order_by(AppDatasetReference.pinned_release_id)
            )
        )
        if pinned_ids:
            defaults = list(
                session.scalars(
                    select(TenantReleaseChannel)
                    .where(
                        TenantReleaseChannel.tenant_id == tenant,
                        TenantReleaseChannel.status == "active",
                        TenantReleaseChannel.is_default_serving.is_(True),
                        TenantReleaseChannel.active_default_slot == "default",
                    )
                    .order_by(TenantReleaseChannel.id)
                )
            )
            if len(defaults) != 1:
                raise QualityScanRunConflict(
                    "Pinned Application targets require one active default-serving Channel"
                )
            default = defaults[0]
            releases = list(
                session.scalars(
                    select(DatasetReleaseManifest).where(
                        DatasetReleaseManifest.tenant_id == tenant,
                        DatasetReleaseManifest.dataset_id == dataset_key,
                        DatasetReleaseManifest.id.in_(pinned_ids),
                    )
                )
            )
            if len(releases) != len(pinned_ids):
                raise QualityScanRunConflict("Pinned Application Release authority is stale")
            for release in releases:
                target = QualityScanTarget(
                    tenant_id=tenant,
                    dataset_id=dataset_key,
                    release_id=release.id,
                    channel_id=default.id,
                    release_role="pinned",
                    manifest_digest=release.manifest_digest,
                    channel_revision=int(default.revision),
                )
                targets[(target.release_id, target.channel_id, target.release_role)] = target
        return sorted(
            targets.values(), key=lambda item: (item.release_role, item.channel_id, item.release_id)
        )


def _observation_result(
    row: DatasetReleaseQualityObservation, *, created: bool
) -> QualityObservationResult:
    return QualityObservationResult(
        id=row.id,
        tenant_id=row.tenant_id,
        dataset_id=row.dataset_id,
        release_id=row.release_id,
        channel_id=row.channel_id,
        scan_run_id=row.scan_run_id,
        release_role=row.release_role,
        observation_digest=row.observation_digest,
        created=created,
    )


def _revalidate_stage20_evidence(
    session: Session,
    canonical: Mapping[str, Any],
    manifest: DatasetReleaseManifest,
    channel: TenantReleaseChannel,
) -> None:
    certification_id = canonical.get("certification_id")
    if certification_id is not None:
        certification = session.scalar(
            select(DatasetReleaseQualityCertification)
            .where(
                DatasetReleaseQualityCertification.tenant_id == canonical["tenant_id"],
                DatasetReleaseQualityCertification.dataset_id == canonical["dataset_id"],
                DatasetReleaseQualityCertification.release_id == manifest.id,
                DatasetReleaseQualityCertification.id == certification_id,
            )
            .with_for_update()
        )
        if (
            certification is None
            or certification.certification_digest != canonical["certification_digest"]
            or _as_utc_naive(certification.valid_until, "certification.valid_until")
            != _as_utc_naive(canonical["certification_valid_until"], "certification_valid_until")
        ):
            raise QualityObservationConflict("Certification authority changed")
    waiver_id = canonical.get("waiver_id")
    if waiver_id is not None:
        waiver = session.scalar(
            select(DatasetReleaseQualityWaiver)
            .where(
                DatasetReleaseQualityWaiver.tenant_id == canonical["tenant_id"],
                DatasetReleaseQualityWaiver.dataset_id == canonical["dataset_id"],
                DatasetReleaseQualityWaiver.release_id == manifest.id,
                DatasetReleaseQualityWaiver.channel_id == channel.id,
                DatasetReleaseQualityWaiver.id == waiver_id,
            )
            .with_for_update()
        )
        if (
            waiver is None
            or waiver.waiver_digest != canonical["waiver_digest"]
            or _as_utc_naive(waiver.expires_at, "waiver.expires_at")
            != _as_utc_naive(canonical["waiver_expires_at"], "waiver_expires_at")
        ):
            raise QualityObservationConflict("Waiver authority changed")


def _apply_alert_lifecycle(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    scan_run_id: str,
    observation_id: str,
    worker_id: str,
    now: datetime,
) -> set[str]:
    from core.enterprise_release_quality_alerts import (
        create_or_update_quality_alert_from_observation,
        resolve_matching_quality_alert_from_observation,
    )

    with engine_serialization_lock(engine), Session(engine, expire_on_commit=False) as session:
        _begin_run_write(session)
        run = _locked_run(session, tenant_id, dataset_id, scan_run_id)
        _assert_running_lease(run, worker_id=worker_id, now=now)
        observation = session.scalar(
            select(DatasetReleaseQualityObservation)
            .where(
                DatasetReleaseQualityObservation.tenant_id == tenant_id,
                DatasetReleaseQualityObservation.dataset_id == dataset_id,
                DatasetReleaseQualityObservation.scan_run_id == scan_run_id,
                DatasetReleaseQualityObservation.id == observation_id,
            )
            .with_for_update()
        )
        if observation is None:
            raise QualityObservationConflict("Quality Observation is outside the Scan Run scope")
        if str(observation.severity) == "healthy":
            alerts = resolve_matching_quality_alert_from_observation(
                session,
                observation,
                now=now,
            )
        else:
            alert = create_or_update_quality_alert_from_observation(
                session,
                observation,
                now=now,
            )
            alerts = [] if alert is None else [alert]
        _assert_running_lease(run, worker_id=worker_id, now=now)
        session.flush()
        alert_ids = {str(alert.id) for alert in alerts}
        session.commit()
        return alert_ids


def _persisted_effect_count(
    engine: Any,
    model: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    row_ids: set[str],
) -> int:
    if not row_ids:
        return 0
    with Session(engine) as session:
        return int(
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(
                    model.tenant_id == tenant_id,
                    model.dataset_id == dataset_id,
                    model.id.in_(sorted(row_ids)),
                )
            )
            or 0
        )


def _persisted_run_counts(session: Session, run: TenantReleaseQualityScanRun) -> dict[str, int]:
    observations = list(
        session.scalars(
            select(DatasetReleaseQualityObservation).where(
                DatasetReleaseQualityObservation.tenant_id == run.tenant_id,
                DatasetReleaseQualityObservation.dataset_id == run.dataset_id,
                DatasetReleaseQualityObservation.scan_run_id == run.id,
            )
        )
    )
    observation_ids = {str(row.id) for row in observations}
    alerts = (
        list(
            session.scalars(
                select(DatasetReleaseQualityAlert).where(
                    DatasetReleaseQualityAlert.tenant_id == run.tenant_id,
                    DatasetReleaseQualityAlert.dataset_id == run.dataset_id,
                    DatasetReleaseQualityAlert.source_observation_id.in_(sorted(observation_ids)),
                )
            )
        )
        if observation_ids
        else []
    )
    authorities = {
        (str(row.release_id), str(row.channel_id), str(row.release_role)) for row in observations
    }
    jobs = []
    if authorities:
        jobs = [
            row
            for row in session.scalars(
                select(DatasetReleaseRecertificationJob).where(
                    DatasetReleaseRecertificationJob.tenant_id == run.tenant_id,
                    DatasetReleaseRecertificationJob.dataset_id == run.dataset_id,
                )
            )
            if (str(row.release_id), str(row.channel_id), str(row.release_role)) in authorities
        ]
    return {
        "observation_count": len(observations),
        "alert_count": len(alerts),
        "recertification_job_count": len(jobs),
    }


def _queue_recertification_for_observation(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    scan_run_id: str,
    observation_id: str,
    worker_id: str,
    now: datetime,
    target: QualityScanTarget,
    slo_policy: TenantReleaseQualitySloPolicy,
    gate: Mapping[str, Any],
    evaluation: Mapping[str, Any],
) -> str | None:
    if slo_policy.auto_queue_recertification is not True:
        return None
    if str(evaluation.get("severity")) == "healthy":
        return None
    gate_policy = gate.get("policy")
    certification = gate.get("certification")
    if not isinstance(gate_policy, Mapping) or not isinstance(certification, Mapping):
        return None
    baseline_id = gate_policy.get("baseline_id", certification.get("baseline_id"))
    evidence_digest = certification.get("evidence_digest")
    policy_id = gate_policy.get("id")
    policy_revision = gate_policy.get("revision")
    if (
        not isinstance(baseline_id, str)
        or not baseline_id.strip()
        or not isinstance(evidence_digest, str)
        or not evidence_digest.strip()
        or not isinstance(policy_id, str)
        or not policy_id.strip()
        or type(policy_revision) is not int
        or policy_revision < 1
    ):
        return None
    try:
        baseline_id = _clean_id(baseline_id, "baseline_id")
        evidence_digest = _digest(evidence_digest, "expected_evidence_digest")
        policy_id = _clean_id(policy_id, "policy_id")
        manifest_digest = _digest(target.manifest_digest, "expected_manifest_digest")
        channel_revision = target.channel_revision
        if type(channel_revision) is not int or channel_revision < 1:
            return None
    except (QualityScheduleValidation, QualityObservationConflict):
        return None
    trigger = {
        "certification_expiring": "certification_warning",
        "certification_expired": "certification_expired",
        "certification_stale": "stale_evidence",
    }.get(str(evaluation.get("reason")), "alert_escalation")
    request_id = f"quality-scan-recert:{scan_run_id}:{observation_id}"[:128]
    from core.enterprise_release_quality_recertification import queue_recertification_job

    with engine_serialization_lock(engine), Session(engine, expire_on_commit=False) as session:
        _begin_run_write(session)
        run = _locked_run(session, tenant_id, dataset_id, scan_run_id)
        _assert_running_lease(run, worker_id=worker_id, now=now)
        dataset = session.scalar(
            select(Dataset)
            .where(Dataset.tenant_id == tenant_id, Dataset.id == dataset_id)
            .with_for_update()
        )
        if dataset is None or dataset.status != "active":
            return None
        actor_id = dataset.owner_id
        if not actor_id:
            actor_id = session.scalar(
                select(TenantMember.account_id)
                .where(
                    TenantMember.tenant_id == tenant_id,
                    TenantMember.status == "active",
                    TenantMember.role.in_(("owner", "admin")),
                )
                .order_by(TenantMember.account_id)
            )
        if not actor_id:
            return None
        baseline = session.scalar(
            select(DatasetQualityBaseline)
            .where(
                DatasetQualityBaseline.tenant_id == tenant_id,
                DatasetQualityBaseline.dataset_id == dataset_id,
                DatasetQualityBaseline.id == baseline_id,
            )
            .with_for_update()
        )
        if baseline is None:
            return None
        try:
            evidence_digest = _digest(baseline.baseline_digest, "expected_evidence_digest")
        except QualityObservationConflict:
            return None
        queue_authority = {
            "tenant_id": tenant_id,
            "dataset_id": dataset_id,
            "release_id": target.release_id,
            "channel_id": target.channel_id,
            "release_role": target.release_role,
            "baseline_id": baseline_id,
            "policy_id": policy_id,
            "policy_revision": policy_revision,
            "slo_policy_id": slo_policy.id,
            "slo_policy_revision": int(slo_policy.revision),
            "expected_manifest_digest": manifest_digest,
            "expected_evidence_digest": evidence_digest,
            "expected_channel_revision": channel_revision,
            "trigger": trigger,
        }
        idempotency_key = f"quality-scan-recert-{canonical_operations_digest('quality-scan-recertification', queue_authority)}"
        queued = queue_recertification_job(
            session.connection(),
            tenant_id=tenant_id,
            actor_id=str(actor_id),
            dataset_id=dataset_id,
            release_id=target.release_id,
            channel_id=target.channel_id,
            release_role=target.release_role,
            baseline_id=baseline_id,
            policy_id=policy_id,
            expected_policy_revision=policy_revision,
            slo_policy_id=slo_policy.id,
            expected_slo_policy_revision=int(slo_policy.revision),
            trigger=trigger,
            expected_manifest_digest=manifest_digest,
            expected_evidence_digest=evidence_digest,
            expected_channel_revision=channel_revision,
            reason=str(evaluation.get("reason") or "release quality SLO breach"),
            request_id=request_id,
            request_ip="",
            idempotency_key=idempotency_key,
            now=now,
        )
        body = getattr(queued, "body", queued)
        resource_id: str | None = None
        if isinstance(body, Mapping):
            candidate = body.get("resource_id")
            if isinstance(candidate, str) and candidate:
                resource_id = candidate
            else:
                job = body.get("job")
                if isinstance(job, Mapping):
                    candidate = job.get("id")
                    if isinstance(candidate, str) and candidate:
                        resource_id = candidate
        session.commit()
        return resource_id


def append_quality_observation(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    scan_run_id: str,
    worker_id: str,
    observation: Mapping[str, Any],
    now: datetime | None = None,
) -> QualityObservationResult:
    """Append one body-free Observation after fixed-order authority revalidation."""

    tenant_id = _clean_id(tenant_id, "tenant_id")
    dataset_id = _clean_id(dataset_id, "dataset_id")
    scan_run_id = _clean_id(scan_run_id, "scan_run_id")
    worker_id = _clean_text(worker_id, "worker_id", 128)
    current = _now(now)
    if not isinstance(observation, Mapping):
        raise QualityScheduleValidation("observation must be an object")
    raw = dict(observation)
    if (
        raw.get("tenant_id") != tenant_id
        or raw.get("dataset_id") != dataset_id
        or raw.get("scan_run_id") != scan_run_id
    ):
        raise QualityObservationConflict("Observation scope conflicts with request scope")
    canonical = canonical_observation(raw)
    with engine_serialization_lock(engine), Session(engine, expire_on_commit=False) as session:
        _begin_run_write(session)
        preview = session.scalar(
            select(TenantReleaseQualityScanRun).where(
                TenantReleaseQualityScanRun.tenant_id == tenant_id,
                TenantReleaseQualityScanRun.dataset_id == dataset_id,
                TenantReleaseQualityScanRun.id == scan_run_id,
            )
        )
        if preview is None:
            raise QualityScanRunNotFound()
        tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        if tenant is None or tenant.status != "active":
            raise QualityObservationConflict("Tenant authority changed")
        policy = session.scalar(
            select(TenantReleaseQualitySloPolicy)
            .where(
                TenantReleaseQualitySloPolicy.tenant_id == tenant_id,
                TenantReleaseQualitySloPolicy.id == preview.slo_policy_id,
            )
            .with_for_update()
        )
        if policy is None or policy.status != "active":
            raise QualityObservationConflict("SLO Policy authority changed")
        schedule = session.scalar(
            select(TenantReleaseQualityScanSchedule)
            .where(
                TenantReleaseQualityScanSchedule.tenant_id == tenant_id,
                TenantReleaseQualityScanSchedule.dataset_id == dataset_id,
                TenantReleaseQualityScanSchedule.id == preview.schedule_id,
                TenantReleaseQualityScanSchedule.slo_policy_id == preview.slo_policy_id,
            )
            .with_for_update()
        )
        if schedule is None:
            raise QualityObservationConflict("Scan Schedule authority changed")
        run = _locked_run(session, tenant_id, dataset_id, scan_run_id)
        try:
            _assert_running_lease(run, worker_id=worker_id, now=current)
        except QualityScanRunConflict as exc:
            raise QualityObservationConflict(str(exc)) from exc
        if (
            canonical["slo_policy_id"] != run.slo_policy_id
            or int(canonical["slo_policy_revision"]) != int(run.slo_policy_revision)
            or int(policy.revision) != int(run.slo_policy_revision)
        ):
            raise QualityObservationConflict("SLO Policy revision changed before Observation write")
        dataset = session.scalar(
            select(Dataset)
            .where(Dataset.tenant_id == tenant_id, Dataset.id == dataset_id)
            .with_for_update()
        )
        if dataset is None or dataset.status != "active":
            raise QualityObservationConflict("Dataset authority changed")
        manifest = session.scalar(
            select(DatasetReleaseManifest)
            .where(
                DatasetReleaseManifest.tenant_id == tenant_id,
                DatasetReleaseManifest.dataset_id == dataset_id,
                DatasetReleaseManifest.id == canonical["release_id"],
            )
            .with_for_update()
        )
        channel = session.scalar(
            select(TenantReleaseChannel)
            .where(
                TenantReleaseChannel.tenant_id == tenant_id,
                TenantReleaseChannel.id == canonical["channel_id"],
            )
            .with_for_update()
        )
        if manifest is None or channel is None or channel.status != "active":
            raise QualityObservationConflict("Release or Channel authority changed")
        actual_manifest = _digest(manifest.manifest_digest, "manifest_digest")
        if (
            _digest(raw.get("manifest_digest"), "manifest_digest") != actual_manifest
            or _digest(raw.get("expected_manifest_digest"), "expected_manifest_digest")
            != actual_manifest
        ):
            raise QualityObservationConflict("Release manifest digest changed")
        if (
            type(raw.get("channel_revision")) is not int
            or type(raw.get("expected_channel_revision")) is not int
            or raw["channel_revision"] != int(channel.revision)
            or raw["expected_channel_revision"] != int(channel.revision)
        ):
            raise QualityObservationConflict("Release Channel revision changed")
        if canonical["release_role"] == "active":
            binding = session.scalar(
                select(DatasetChannelRelease)
                .where(
                    DatasetChannelRelease.tenant_id == tenant_id,
                    DatasetChannelRelease.dataset_id == dataset_id,
                    DatasetChannelRelease.channel_id == channel.id,
                    DatasetChannelRelease.active_release_id == manifest.id,
                    DatasetChannelRelease.status == "active",
                    DatasetChannelRelease.active_slot == "active",
                )
                .with_for_update()
            )
            if binding is None:
                raise QualityObservationConflict("Active Channel binding changed")
        else:
            pinned = session.scalar(
                select(AppDatasetReference.id)
                .where(
                    AppDatasetReference.tenant_id == tenant_id,
                    AppDatasetReference.dataset_id == dataset_id,
                    AppDatasetReference.pinned_release_id == manifest.id,
                    AppDatasetReference.release_mode == "pinned",
                    AppDatasetReference.status == "active",
                    AppDatasetReference.active_slot == "active",
                )
                .with_for_update()
            )
            if pinned is None or not channel.is_default_serving:
                raise QualityObservationConflict("Pinned Application authority changed")
        _revalidate_stage20_evidence(session, canonical, manifest, channel)
        existing = session.scalar(
            select(DatasetReleaseQualityObservation).where(
                DatasetReleaseQualityObservation.tenant_id == tenant_id,
                DatasetReleaseQualityObservation.scan_run_id == scan_run_id,
                DatasetReleaseQualityObservation.dataset_id == dataset_id,
                DatasetReleaseQualityObservation.release_id == canonical["release_id"],
                DatasetReleaseQualityObservation.channel_id == canonical["channel_id"],
                DatasetReleaseQualityObservation.release_role == canonical["release_role"],
            )
        )
        if existing is not None:
            if existing.observation_digest != canonical["observation_digest"]:
                raise QualityObservationConflict(
                    "Observation replay conflicts with immutable authority"
                )
            session.commit()
            return _observation_result(existing, created=False)
        row = DatasetReleaseQualityObservation(
            id=f"quality-observation-{canonical['observation_digest'][:24]}",
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            release_id=canonical["release_id"],
            channel_id=canonical["channel_id"],
            scan_run_id=scan_run_id,
            slo_policy_id=canonical["slo_policy_id"],
            slo_policy_revision=canonical["slo_policy_revision"],
            release_role=canonical["release_role"],
            gate_state=(
                "passing" if canonical["gate_state"] == "passed" else canonical["gate_state"]
            ),
            gate_reason=canonical["gate_reason"],
            certification_id=canonical["certification_id"],
            certification_digest=canonical["certification_digest"],
            certification_valid_until=(
                None
                if canonical["certification_valid_until"] is None
                else _as_utc_naive(
                    canonical["certification_valid_until"], "certification_valid_until"
                )
            ),
            waiver_id=canonical["waiver_id"],
            waiver_digest=canonical["waiver_digest"],
            waiver_expires_at=(
                None
                if canonical["waiver_expires_at"] is None
                else _as_utc_naive(canonical["waiver_expires_at"], "waiver_expires_at")
            ),
            minutes_to_certification_expiry=canonical["minutes_to_certification_expiry"],
            minutes_to_waiver_expiry=canonical["minutes_to_waiver_expiry"],
            severity=canonical["severity"],
            observation_digest=canonical["observation_digest"],
            observed_at=_as_utc_naive(canonical["observed_at"], "observed_at"),
            observed_by=canonical["observed_by"],
            request_id=canonical["request_id"],
        )
        session.add(row)
        session.flush()
        session.commit()
        return _observation_result(row, created=True)


def execute_quality_scan_run(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    run_id: str,
    worker_id: str,
    now: datetime | None = None,
) -> QualityScanRunResult:
    """Evaluate current Stage 20 authority for each target and finish the Run."""

    tenant_id = _clean_id(tenant_id, "tenant_id")
    dataset_id = _clean_id(dataset_id, "dataset_id")
    run_id = _clean_id(run_id, "run_id")
    worker_id = _clean_text(worker_id, "worker_id", 128)
    current = _now(now)
    observation_ids: set[str] = set()
    alert_ids: set[str] = set()
    recertification_job_ids: set[str] = set()
    try:
        with engine_serialization_lock(engine):
            with Session(engine, expire_on_commit=False) as session:
                _begin_run_write(session)
                run = _locked_run(session, tenant_id, dataset_id, run_id)
                _assert_running_lease(run, worker_id=worker_id, now=current)
                session.rollback()

            targets = resolve_quality_scan_targets(
                engine, tenant_id=tenant_id, dataset_id=dataset_id
            )
            from core.enterprise_release_quality_service import (
                ReleaseQualityError,
                resolve_release_quality_gate,
            )

            for target in targets:
                with Session(engine, expire_on_commit=False) as session:
                    _begin_run_write(session)
                    run = _locked_run(session, tenant_id, dataset_id, run_id)
                    _assert_running_lease(run, worker_id=worker_id, now=current)
                    policy = session.scalar(
                        select(TenantReleaseQualitySloPolicy).where(
                            TenantReleaseQualitySloPolicy.tenant_id == tenant_id,
                            TenantReleaseQualitySloPolicy.id == run.slo_policy_id,
                        )
                    )
                    if policy is None:
                        raise QualityObservationConflict("SLO Policy authority changed")
                    try:
                        gate = resolve_release_quality_gate(
                            session,
                            tenant_id=tenant_id,
                            dataset_id=dataset_id,
                            release_id=target.release_id,
                            channel_id=target.channel_id,
                            now=current,
                            lock_evidence=True,
                        )
                    except ReleaseQualityError:
                        gate = {
                            "state": "unavailable",
                            "reason": "quality_authority_unavailable",
                        }
                    evaluation = evaluate_release_quality_slo(
                        current,
                        {
                            column.name: getattr(policy, column.name)
                            for column in policy.__table__.columns
                        },
                        gate,
                    )

                result = append_quality_observation(
                    engine,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    scan_run_id=run_id,
                    worker_id=worker_id,
                    now=current,
                    observation={
                        "tenant_id": tenant_id,
                        "dataset_id": dataset_id,
                        "release_id": target.release_id,
                        "channel_id": target.channel_id,
                        "scan_run_id": run_id,
                        "slo_policy_id": run.slo_policy_id,
                        "slo_policy_revision": int(run.slo_policy_revision),
                        "release_role": target.release_role,
                        "observed_at": current,
                        "observed_by": _RUN_SYSTEM_ACTOR,
                        "request_id": f"quality-scan-{run_id}",
                        "gate": gate,
                        "evaluation": evaluation,
                        "manifest_digest": target.manifest_digest,
                        "expected_manifest_digest": target.manifest_digest,
                        "channel_revision": target.channel_revision,
                        "expected_channel_revision": target.channel_revision,
                    },
                )
                observation_ids.add(result.id)
                alert_ids.update(
                    _apply_alert_lifecycle(
                        engine,
                        tenant_id=tenant_id,
                        dataset_id=dataset_id,
                        scan_run_id=run_id,
                        observation_id=result.id,
                        worker_id=worker_id,
                        now=current,
                    )
                )
                recertification_job_id = _queue_recertification_for_observation(
                    engine,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    scan_run_id=run_id,
                    observation_id=result.id,
                    worker_id=worker_id,
                    now=current,
                    target=target,
                    slo_policy=policy,
                    gate=gate,
                    evaluation=evaluation,
                )
                if recertification_job_id is not None:
                    recertification_job_ids.add(recertification_job_id)

            counts = {
                "observation_count": _persisted_effect_count(
                    engine,
                    DatasetReleaseQualityObservation,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    row_ids=observation_ids,
                ),
                "alert_count": _persisted_effect_count(
                    engine,
                    DatasetReleaseQualityAlert,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    row_ids=alert_ids,
                ),
                "recertification_job_count": _persisted_effect_count(
                    engine,
                    DatasetReleaseRecertificationJob,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    row_ids=recertification_job_ids,
                ),
            }
            return finish_quality_scan_run(
                engine,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                run_id=run_id,
                owner_id=worker_id,
                outcome="completed",
                **counts,
                now=current,
            )
    except Exception as exc:
        try:
            failed_counts = {
                "observation_count": _persisted_effect_count(
                    engine,
                    DatasetReleaseQualityObservation,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    row_ids=observation_ids,
                ),
                "alert_count": _persisted_effect_count(
                    engine,
                    DatasetReleaseQualityAlert,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    row_ids=alert_ids,
                ),
                "recertification_job_count": _persisted_effect_count(
                    engine,
                    DatasetReleaseRecertificationJob,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    row_ids=recertification_job_ids,
                ),
            }
            finish_quality_scan_run(
                engine,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                run_id=run_id,
                owner_id=worker_id,
                outcome="failed",
                **failed_counts,
                safe_error_code="quality_scan_failed",
                safe_error=str(exc),
                now=current,
            )
        except Exception:
            pass
        raise


def _read_cursor_encode(kind: str, moment: datetime, row_id: str) -> str:
    payload = json.dumps(
        {"kind": kind, "time": _as_utc_naive(moment, "cursor.time").isoformat(), "id": row_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _read_cursor_decode(value: str | None, kind: str) -> tuple[datetime, str] | None:
    if value is None:
        return None
    raw = _clean_text(value, "cursor", 2048)
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, Mapping) or payload.get("kind") != kind:
            raise ValueError("cursor kind")
        moment = _as_utc_naive(payload.get("time"), "cursor.time")
        row_id = _clean_id(payload.get("id"), "cursor.id")
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualityScheduleValidation("cursor is invalid") from exc
    return moment, row_id


def _read_authority(
    engine: Any,
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
) -> tuple[_Actor, Mapping[str, Any]]:
    actor, dataset = _actor_and_dataset(session, tenant_id, actor_id, dataset_id)
    _authorize_dataset(engine, session, actor, dataset, permission=QUALITY_SCHEDULE_READ_PERMISSION)
    return actor, dataset


def list_quality_scan_schedules(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ServiceResult:
    tenant = _clean_id(tenant_id, "tenant_id")
    actor_id = _clean_id(actor_id, "actor_id")
    dataset = _clean_id(dataset_id, "dataset_id")
    page_size = _exact_page_size(limit)
    state = None if status is None else _clean_text(status, "status", 16).casefold()
    if state is not None and state not in _SCHEDULE_STATUSES:
        raise QualityScheduleValidation("status is invalid")
    after = _read_cursor_decode(cursor, "quality_scan_schedule")
    with Session(engine, expire_on_commit=False) as session:
        _read_authority(engine, session, tenant_id=tenant, actor_id=actor_id, dataset_id=dataset)
        schedules = _table(session, "tenant_release_quality_scan_schedules")
        statement = select(schedules).where(
            schedules.c.tenant_id == tenant,
            schedules.c.dataset_id == dataset,
        )
        if state is not None:
            statement = statement.where(schedules.c.status == state)
        if after is not None:
            moment, row_id = after
            statement = statement.where(
                or_(
                    schedules.c.updated_at < moment,
                    and_(schedules.c.updated_at == moment, schedules.c.id < row_id),
                )
            )
        rows = list(
            session.execute(
                statement.order_by(schedules.c.updated_at.desc(), schedules.c.id.desc()).limit(
                    page_size + 1
                )
            ).mappings()
        )
        visible = rows[:page_size]
        return ServiceResult(
            {
                "items": [_schedule_snapshot(row) for row in visible],
                "next_cursor": (
                    _read_cursor_encode(
                        "quality_scan_schedule",
                        _utc_naive(visible[-1]["updated_at"], "updated_at"),
                        str(visible[-1]["id"]),
                    )
                    if len(rows) > page_size and visible
                    else None
                ),
            }
        )


def _exact_page_size(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= 200:
        raise QualityScheduleValidation("limit must be an exact integer between 1 and 200")
    return value


def list_quality_scan_runs(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    schedule_id: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ServiceResult:
    tenant = _clean_id(tenant_id, "tenant_id")
    actor = _clean_id(actor_id, "actor_id")
    dataset = _clean_id(dataset_id, "dataset_id")
    schedule = None if schedule_id is None else _clean_id(schedule_id, "schedule_id")
    state = None if status is None else _clean_text(status, "status", 16).casefold()
    if state is not None and state not in {
        "pending",
        "claimed",
        "running",
        "completed",
        "failed",
        "cancelled",
    }:
        raise QualityScheduleValidation("status is invalid")
    page_size = _exact_page_size(limit)
    after = _read_cursor_decode(cursor, "quality_scan_run")
    with Session(engine, expire_on_commit=False) as session:
        _read_authority(engine, session, tenant_id=tenant, actor_id=actor, dataset_id=dataset)
        statement = select(TenantReleaseQualityScanRun).where(
            TenantReleaseQualityScanRun.tenant_id == tenant,
            TenantReleaseQualityScanRun.dataset_id == dataset,
        )
        if schedule is not None:
            statement = statement.where(TenantReleaseQualityScanRun.schedule_id == schedule)
        if state is not None:
            statement = statement.where(TenantReleaseQualityScanRun.status == state)
        if after is not None:
            moment, row_id = after
            statement = statement.where(
                or_(
                    TenantReleaseQualityScanRun.updated_at < moment,
                    and_(
                        TenantReleaseQualityScanRun.updated_at == moment,
                        TenantReleaseQualityScanRun.id < row_id,
                    ),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    TenantReleaseQualityScanRun.updated_at.desc(),
                    TenantReleaseQualityScanRun.id.desc(),
                ).limit(page_size + 1)
            )
        )
        visible = rows[:page_size]
        return ServiceResult(
            {
                "items": [_run_result_payload(_run_result(row)) for row in visible],
                "next_cursor": (
                    _read_cursor_encode("quality_scan_run", visible[-1].updated_at, visible[-1].id)
                    if len(rows) > page_size and visible
                    else None
                ),
            }
        )


def _observation_payload(row: DatasetReleaseQualityObservation) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "dataset_id": row.dataset_id,
        "release_id": row.release_id,
        "channel_id": row.channel_id,
        "scan_run_id": row.scan_run_id,
        "slo_policy_id": row.slo_policy_id,
        "slo_policy_revision": int(row.slo_policy_revision),
        "release_role": row.release_role,
        "gate_state": "passed" if row.gate_state == "passing" else row.gate_state,
        "gate_reason": row.gate_reason,
        "certification_id": row.certification_id,
        "certification_digest": row.certification_digest,
        "certification_valid_until": _iso_optional(
            row.certification_valid_until, "certification_valid_until"
        ),
        "waiver_id": row.waiver_id,
        "waiver_digest": row.waiver_digest,
        "waiver_expires_at": _iso_optional(row.waiver_expires_at, "waiver_expires_at"),
        "minutes_to_certification_expiry": row.minutes_to_certification_expiry,
        "minutes_to_waiver_expiry": row.minutes_to_waiver_expiry,
        "severity": row.severity,
        "observation_digest": row.observation_digest,
        "observed_at": _iso(row.observed_at, "observed_at"),
        "observed_by": row.observed_by,
        "request_id": row.request_id,
    }


def list_quality_observations(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    release_id: str | None = None,
    channel_id: str | None = None,
    release_role: str | None = None,
    severity: str | None = None,
    gate_state: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ServiceResult:
    tenant = _clean_id(tenant_id, "tenant_id")
    actor = _clean_id(actor_id, "actor_id")
    dataset = _clean_id(dataset_id, "dataset_id")
    page_size = _exact_page_size(limit)
    filters = {
        "release_id": None if release_id is None else _clean_id(release_id, "release_id"),
        "channel_id": None if channel_id is None else _clean_id(channel_id, "channel_id"),
        "release_role": None
        if release_role is None
        else _clean_text(release_role, "release_role", 16),
        "severity": None if severity is None else _clean_text(severity, "severity", 16),
        "gate_state": None if gate_state is None else _clean_text(gate_state, "gate_state", 24),
    }
    if filters["release_role"] not in {None, "active", "pinned"}:
        raise QualityScheduleValidation("release_role is invalid")
    if filters["severity"] not in {None, "healthy", "warning", "critical", "unavailable"}:
        raise QualityScheduleValidation("severity is invalid")
    if filters["gate_state"] == "passed":
        filters["gate_state"] = "passing"
    if filters["gate_state"] not in {
        None,
        "passing",
        "waived",
        "not_required",
        "blocked",
        "unavailable",
    }:
        raise QualityScheduleValidation("gate_state is invalid")
    after = _read_cursor_decode(cursor, "quality_observation")
    with Session(engine, expire_on_commit=False) as session:
        _read_authority(engine, session, tenant_id=tenant, actor_id=actor, dataset_id=dataset)
        statement = select(DatasetReleaseQualityObservation).where(
            DatasetReleaseQualityObservation.tenant_id == tenant,
            DatasetReleaseQualityObservation.dataset_id == dataset,
        )
        for name, value in filters.items():
            if value is not None:
                statement = statement.where(
                    getattr(DatasetReleaseQualityObservation, name) == value
                )
        if after is not None:
            moment, row_id = after
            statement = statement.where(
                or_(
                    DatasetReleaseQualityObservation.observed_at < moment,
                    and_(
                        DatasetReleaseQualityObservation.observed_at == moment,
                        DatasetReleaseQualityObservation.id < row_id,
                    ),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    DatasetReleaseQualityObservation.observed_at.desc(),
                    DatasetReleaseQualityObservation.id.desc(),
                ).limit(page_size + 1)
            )
        )
        visible = rows[:page_size]
        return ServiceResult(
            {
                "items": [_observation_payload(row) for row in visible],
                "next_cursor": (
                    _read_cursor_encode(
                        "quality_observation", visible[-1].observed_at, visible[-1].id
                    )
                    if len(rows) > page_size and visible
                    else None
                ),
            }
        )


def _horizon_band(row: DatasetReleaseQualityObservation) -> str:
    if row.severity == "unavailable":
        return "unavailable"
    minutes = row.minutes_to_certification_expiry
    if minutes is None:
        minutes = row.minutes_to_waiver_expiry
    if minutes is None:
        return "healthy" if row.severity == "healthy" else "unavailable"
    if int(minutes) <= 0:
        return "expired"
    if int(minutes) <= 1_440:
        return "24_hours"
    if int(minutes) <= 10_080:
        return "7_days"
    if int(minutes) <= 43_200:
        return "30_days"
    return "healthy"


def get_quality_operations_summary(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
) -> ServiceResult:
    tenant = _clean_id(tenant_id, "tenant_id")
    actor = _clean_id(actor_id, "actor_id")
    dataset = _clean_id(dataset_id, "dataset_id")
    with Session(engine, expire_on_commit=False) as session:
        _read_authority(engine, session, tenant_id=tenant, actor_id=actor, dataset_id=dataset)
        observations = list(
            session.scalars(
                select(DatasetReleaseQualityObservation)
                .where(
                    DatasetReleaseQualityObservation.tenant_id == tenant,
                    DatasetReleaseQualityObservation.dataset_id == dataset,
                )
                .order_by(
                    DatasetReleaseQualityObservation.observed_at.desc(),
                    DatasetReleaseQualityObservation.id.desc(),
                )
            )
        )
        latest: dict[tuple[str, str, str], DatasetReleaseQualityObservation] = {}
        for row in observations:
            latest.setdefault((row.release_id, row.channel_id, row.release_role), row)
        channels = {
            row.id: row
            for row in session.scalars(
                select(TenantReleaseChannel).where(TenantReleaseChannel.tenant_id == tenant)
            )
        }
        release_ids = sorted({row.release_id for row in latest.values()})
        releases = (
            {
                row.id: row
                for row in session.scalars(
                    select(DatasetReleaseManifest).where(
                        DatasetReleaseManifest.tenant_id == tenant,
                        DatasetReleaseManifest.dataset_id == dataset,
                        DatasetReleaseManifest.id.in_(release_ids),
                    )
                )
            }
            if release_ids
            else {}
        )
        active_alerts = list(
            session.scalars(
                select(DatasetReleaseQualityAlert).where(
                    DatasetReleaseQualityAlert.tenant_id == tenant,
                    DatasetReleaseQualityAlert.dataset_id == dataset,
                    DatasetReleaseQualityAlert.status.in_(("open", "acknowledged", "suppressed")),
                )
            )
        )
        active_jobs = list(
            session.scalars(
                select(DatasetReleaseRecertificationJob).where(
                    DatasetReleaseRecertificationJob.tenant_id == tenant,
                    DatasetReleaseRecertificationJob.dataset_id == dataset,
                    DatasetReleaseRecertificationJob.status.in_(
                        ("pending", "claimed", "awaiting_evidence", "ready_to_certify")
                    ),
                )
            )
        )
        alert_counts: dict[tuple[str, str, str], int] = {}
        for alert in active_alerts:
            key = (alert.release_id, alert.channel_id, alert.release_role)
            alert_counts[key] = alert_counts.get(key, 0) + 1
        job_status = {
            (job.release_id, job.channel_id, job.release_role): job.status for job in active_jobs
        }
        counts = {
            "expired": 0,
            "24_hours": 0,
            "7_days": 0,
            "30_days": 0,
            "healthy": 0,
            "unavailable": 0,
        }
        authorities: list[dict[str, Any]] = []
        for key, row in latest.items():
            band = _horizon_band(row)
            counts[band] += 1
            channel = channels.get(row.channel_id)
            release = releases.get(row.release_id)
            authorities.append(
                {
                    **_observation_payload(row),
                    "channel_name": (
                        str(channel.name) if channel is not None else str(row.channel_id)
                    ),
                    "release_number": (
                        int(release.release_number) if release is not None else None
                    ),
                    "horizon_band": band,
                    "active_alert_count": alert_counts.get(key, 0),
                    "recertification_job_status": job_status.get(key),
                    "last_observed_at": _iso(row.observed_at, "observed_at"),
                }
            )
        last_completed = session.scalar(
            select(func.max(TenantReleaseQualityScanRun.finished_at)).where(
                TenantReleaseQualityScanRun.tenant_id == tenant,
                TenantReleaseQualityScanRun.dataset_id == dataset,
                TenantReleaseQualityScanRun.status == "completed",
            )
        )
        return ServiceResult(
            {
                "tenant_id": tenant,
                "dataset_id": dataset,
                "generated_at": _iso(_now(None), "generated_at"),
                "last_completed_scan_at": _iso_optional(last_completed, "last_completed_scan_at"),
                "horizon_counts": counts,
                "authorities": authorities,
            }
        )


__all__ += [
    "QualityObservationConflict",
    "QualityObservationResult",
    "QualityScanRunConflict",
    "QualityScanRunError",
    "QualityScanRunNotFound",
    "QualityScanRunResult",
    "QualityScanTarget",
    "append_quality_observation",
    "cancel_quality_scan_run",
    "claim_quality_scan_run",
    "enqueue_due_quality_scans",
    "execute_quality_scan_run",
    "finish_quality_scan_run",
    "get_quality_operations_summary",
    "heartbeat_quality_scan_run",
    "list_quality_observations",
    "list_quality_scan_runs",
    "list_quality_scan_schedules",
    "resolve_quality_scan_targets",
]
