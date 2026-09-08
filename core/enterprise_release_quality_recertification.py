"""Tenant-safe Stage 21 recertification job lifecycle.

A recertification Job is an auditable queue item, not a second retrieval
experiment runner. It freezes the Stage 20 authority context, revalidates that
context before every worker/operator transition, and delegates the final
Certification action to Stage 20 only after an explicit operator request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
from typing import Any, Mapping
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
    ReleaseManifestConflict,
    ReleaseManifestError,
    ReleaseManifestForbidden,
    ReleaseManifestInvalid,
    ReleaseManifestNotFound,
    ReleaseManifestUnavailable,
    ServiceResult,
    _actor,
    _release_revisions,
    _require_manage,
    _safe_release_reason,
)
from core.enterprise_release_quality_evidence import canonical_quality_digest
from core.enterprise_release_quality_operations import canonical_operations_digest
from core.enterprise_release_quality_service import (
    ReleaseQualityConflict,
    ReleaseQualityError,
    ReleaseQualityForbidden,
    ReleaseQualityInvalid,
    ReleaseQualityNotFound,
    ReleaseQualityUnavailable,
    _baseline_authority_digest,
    _baseline_item_digest,
    _decode_cursor,
    _encode_cursor,
    _policy_authority_digest,
    _read_scope,
    _release_evidence,
    _release_manifest_authority_current,
    _resolve_policy,
    _thresholds,
    certify_release,
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
from models.orm import (
    Dataset,
    DatasetQualityBaseline,
    DatasetQualityBaselineItem,
    DatasetReleaseEvent,
    DatasetReleaseManifest,
    DatasetReleaseRecertificationJob,
    Tenant,
    TenantAuditEvent,
    TenantReleaseChannel,
    TenantReleaseQualityGatePolicy,
    TenantReleaseQualitySloPolicy,
)


# The alias is deliberately part of the module boundary. Tests can replace it
# to verify that this stage delegates to Stage 20 without exercising a real
# Certification write.
_certify_release = certify_release

UTC = timezone.utc
QUALITY_RECERTIFICATION_RESOURCE_TYPE = "quality_recertification_job"
QUEUE_OPERATION = "quality_recertification.queue"
COMPLETE_OPERATION = "quality_recertification.complete"
CANCEL_OPERATION = "quality_recertification.cancel"
ACTIVE_STATUSES = frozenset({"pending", "claimed", "awaiting_evidence", "ready_to_certify"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
CLAIMABLE_STATUSES = frozenset({"pending", "awaiting_evidence", "claimed"})
ALLOWED_ROLES = frozenset({"active", "pinned"})
ALLOWED_TRIGGERS = frozenset(
    {
        "manual",
        "certification_warning",
        "certification_expired",
        "stale_evidence",
        "alert_escalation",
    }
)
ALLOWED_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES
MIN_LEASE_SECONDS = 1
MAX_LEASE_SECONDS = 86_400
DEFAULT_MAX_ATTEMPTS = 3

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]*$")
_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)(?:"
    r"(?:password|passwd|secret|credential|authorization|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|api[_-]?key|client[_-]?secret|token|ticket|query|result[_-]?body|"
    r"judg(?:ment)?[_-]?note|comment)\s*[:=]\s*\S+|"
    r"bearer\s+\S+|secret://\S+|"
    r"(?:mysql|mariadb|postgres(?:ql)?|mongodb|redis|sqlite):\/\/\S+|"
    r"sk_(?:live|test)[-_]\S+|ghp_\S+|xox[baprs]-\S+"
    r")"
)


@dataclass(frozen=True)
class _Operator:
    tenant_id: str
    actor_id: str
    name: str
    email: str


@dataclass(frozen=True)
class _Authority:
    release: DatasetReleaseManifest
    channel: TenantReleaseChannel
    policy: TenantReleaseQualityGatePolicy
    slo_policy: TenantReleaseQualitySloPolicy
    baseline: DatasetQualityBaseline
    baseline_digest: str
    policy_digest: str
    slo_policy_digest: str


@dataclass(frozen=True)
class _JobArguments:
    tenant_id: str
    actor_id: str
    dataset_id: str
    release_id: str
    channel_id: str
    release_role: str
    baseline_id: str
    policy_id: str
    policy_revision: int
    slo_policy_id: str
    slo_policy_revision: int
    expected_manifest_digest: str
    expected_evidence_digest: str | None
    expected_channel_revision: int
    job_id: str
    cycle_key: str


class _DelegateResultInvalid(ValueError):
    """Internal marker for a malformed Stage 20 delegate response."""


def _translate_manifest_error(exc: ReleaseManifestError) -> None:
    if isinstance(exc, ReleaseManifestInvalid):
        raise ReleaseQualityInvalid(exc.message) from exc
    if isinstance(exc, ReleaseManifestNotFound):
        raise ReleaseQualityNotFound(exc.message) from exc
    if isinstance(exc, ReleaseManifestForbidden):
        raise ReleaseQualityForbidden(exc.message) from exc
    if isinstance(exc, ReleaseManifestUnavailable):
        raise ReleaseQualityUnavailable(exc.message) from exc
    if isinstance(exc, ReleaseManifestConflict):
        raise ReleaseQualityConflict(exc.message) from exc
    raise ReleaseQualityConflict("Release quality authority conflicts with current state") from exc


def _text(value: Any, field: str, maximum: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ReleaseQualityInvalid(f"{field} must be a string")
    result = value.strip()
    if not result and not allow_empty:
        raise ReleaseQualityInvalid(f"{field} is required")
    if len(result) > maximum:
        raise ReleaseQualityInvalid(f"{field} must be at most {maximum} characters")
    if not _SAFE_TEXT_RE.fullmatch(result):
        raise ReleaseQualityInvalid(f"{field} contains unsupported control characters")
    if _SENSITIVE_TEXT_RE.search(result):
        raise ReleaseQualityInvalid(f"{field} contains credential-like or unsafe material")
    return result


def _id(value: Any, field: str, maximum: int = 128) -> str:
    return _text(value, field, maximum)


def _reason(value: Any, *, raw_key: str | None = None) -> str:
    result = _text(value, "reason", 512)
    try:
        result = _safe_release_reason(result)
    except ReleaseManifestError as exc:
        _translate_manifest_error(exc)
    if any(
        marker in result.casefold() for marker in ("ticket", "query", "body", "judgment", "comment")
    ):
        raise ReleaseQualityInvalid("reason contains unsafe material")
    if raw_key and raw_key.casefold() in result.casefold():
        raise ReleaseQualityInvalid("reason must not echo the raw idempotency key")
    return result


def _request_id(value: Any, *, raw_key: str | None = None) -> str:
    result = _text(value, "request_id", 128, allow_empty=True)
    if raw_key and result and raw_key.casefold() in result.casefold():
        raise ReleaseQualityInvalid("request_id must not echo the raw idempotency key")
    return result


def _request_ip(value: Any) -> str:
    return _text(value, "request_ip", 64, allow_empty=True)


def _exact_integer(value: Any, field: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        suffix = f" and <= {maximum}" if maximum is not None else ""
        raise ReleaseQualityInvalid(f"{field} must be an exact integer >= {minimum}{suffix}")
    return value


def _exact_boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise ReleaseQualityInvalid(f"{field} must be an exact boolean")
    return value


def _digest(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ReleaseQualityInvalid(f"{field} must be a lowercase SHA-256 digest")
    return value


def _stored_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ReleaseQualityUnavailable(f"{field} authority is malformed")
    return value


def _utc_naive(value: Any, field: str, *, allow_none: bool = False) -> datetime | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, datetime):
        raise ReleaseQualityInvalid(f"{field} must be a datetime")
    moment = value
    if moment.tzinfo is not None:
        try:
            moment = moment.astimezone(UTC).replace(tzinfo=None)
        except (TypeError, ValueError) as exc:
            raise ReleaseQualityInvalid(f"{field} timezone is invalid") from exc
    return moment.replace(fold=0)


def _now(value: Any) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(tzinfo=None, fold=0)
    result = _utc_naive(value, "now")
    assert result is not None
    return result


def _iso(value: Any, field: str, *, allow_none: bool = False) -> str | None:
    moment = _utc_naive(value, field, allow_none=allow_none)
    if moment is None:
        return None
    return moment.isoformat(timespec="microseconds") + "Z"


def _ensure_capability(connection: Any) -> None:
    try:
        state, issues = inspect_enterprise_release_quality_operations_capability(connection)
    except Exception as exc:
        raise ReleaseQualityUnavailable(
            "Release quality operations schema inspection failed"
        ) from exc
    if state != "ready":
        detail = "; ".join(issues) if issues else "Release quality operations schema is unavailable"
        raise ReleaseQualityUnavailable(detail)


def _authorize_operator(
    engine: Any, session: Session, *, tenant_id: str, actor_id: str, dataset_id: str
) -> _Operator:
    tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if tenant is None or tenant.status != "active":
        raise ReleaseQualityForbidden("Tenant is unavailable")
    try:
        membership, account = _actor(session, tenant_id, actor_id)
        dataset = session.scalar(
            select(Dataset)
            .where(Dataset.tenant_id == tenant_id, Dataset.id == dataset_id)
            .with_for_update()
        )
        if dataset is None or dataset.status != "active":
            raise ReleaseManifestForbidden("Dataset is outside the actor Tenant scope")
        _require_manage(engine, session, membership, dataset_id)
    except ReleaseManifestError as exc:
        _translate_manifest_error(exc)
    return _Operator(
        tenant_id=tenant_id,
        actor_id=actor_id,
        name=str(account.name or "")[:128],
        email=str(account.email or "")[:256],
    )


def _lock_job(
    session: Session, *, tenant_id: str, dataset_id: str, job_id: str
) -> DatasetReleaseRecertificationJob:
    row = session.scalar(
        select(DatasetReleaseRecertificationJob)
        .where(
            DatasetReleaseRecertificationJob.tenant_id == tenant_id,
            DatasetReleaseRecertificationJob.dataset_id == dataset_id,
            DatasetReleaseRecertificationJob.id == job_id,
        )
        .with_for_update()
    )
    if row is None:
        raise ReleaseQualityForbidden("Recertification Job is outside the requested Dataset scope")
    return row


def _lock_worker_job(session: Session, job_id: str) -> DatasetReleaseRecertificationJob:
    row = session.scalar(
        select(DatasetReleaseRecertificationJob)
        .where(DatasetReleaseRecertificationJob.id == job_id)
        .with_for_update()
    )
    if row is None:
        raise ReleaseQualityNotFound("Recertification Job does not exist")
    return row


def _reserve(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    raw_key: str,
    request_hash: str,
    operation: str,
) -> Any:
    try:
        return reserve_tenant_mutation(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            raw_idempotency_key=raw_key,
            request_hash=request_hash,
            operation=operation,
            resource_type=QUALITY_RECERTIFICATION_RESOURCE_TYPE,
        )
    except TenantMutationIdempotencyConflict as exc:
        raise ReleaseQualityConflict("idempotency key conflict") from exc
    except TenantMutationIdempotencyInProgress as exc:
        raise ReleaseQualityConflict("idempotency request is in progress") from exc
    except TenantMutationIdempotencyValidationError as exc:
        raise ReleaseQualityInvalid(str(exc)) from exc


def _replay(reservation: Any) -> ServiceResult | None:
    replay = getattr(reservation, "replay", None)
    if replay is None:
        return None
    return ServiceResult(dict(replay.response), int(replay.http_status))


def _complete(
    session: Session, reservation: Any, payload: Mapping[str, Any], status: int, resource_id: str
) -> ServiceResult:
    try:
        complete_tenant_mutation(
            session,
            reservation,
            response_for_replay=payload,
            http_status=status,
            resource_id=resource_id,
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise ReleaseQualityInvalid(str(exc)) from exc
    return ServiceResult(dict(payload), status)


def _audit(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    actor_name: str,
    actor_email: str,
    action: str,
    resource_id: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    request_id: str,
    request_ip: str,
    now: datetime,
) -> None:
    session.add(
        TenantAuditEvent(
            id=f"tenant-audit-{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_name_snapshot=actor_name[:128],
            actor_email_snapshot=actor_email[:256],
            action=action,
            resource_type=QUALITY_RECERTIFICATION_RESOURCE_TYPE,
            resource_id=resource_id,
            before_snapshot=(sanitize_audit_snapshot(dict(before)) if before is not None else None),
            after_snapshot=(sanitize_audit_snapshot(dict(after)) if after is not None else None),
            request_id=request_id,
            request_ip=request_ip,
            occurred_at=now,
        )
    )


def _worker_audit(
    session: Session,
    *,
    job: DatasetReleaseRecertificationJob,
    action: str,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    worker_id: str,
    now: datetime,
) -> None:
    _audit(
        session,
        tenant_id=str(job.tenant_id),
        actor_id=worker_id,
        actor_name="Quality Recertifier",
        actor_email="",
        action=action,
        resource_id=str(job.id),
        before=before,
        after=after,
        request_id=f"quality-recertification:{action.rsplit('.', 1)[-1]}:{job.id}"[:128],
        request_ip="",
        now=now,
    )


def _job_snapshot(row: DatasetReleaseRecertificationJob) -> dict[str, Any]:
    status = _text(row.status, "job.status", 24)
    if status not in ALLOWED_STATUSES:
        raise ReleaseQualityUnavailable("Recertification Job status authority is malformed")
    release_role = _text(row.release_role, "job.release_role", 16)
    if release_role not in ALLOWED_ROLES:
        raise ReleaseQualityUnavailable("Recertification Job release role authority is malformed")
    trigger = _text(row.trigger, "job.trigger", 32)
    if trigger not in ALLOWED_TRIGGERS:
        raise ReleaseQualityUnavailable("Recertification Job trigger authority is malformed")
    return {
        "id": _id(row.id, "job.id", 64),
        "tenant_id": _id(row.tenant_id, "job.tenant_id", 64),
        "dataset_id": _id(row.dataset_id, "job.dataset_id", 64),
        "release_id": _id(row.release_id, "job.release_id", 64),
        "channel_id": _id(row.channel_id, "job.channel_id", 128),
        "release_role": release_role,
        "baseline_id": _id(row.baseline_id, "job.baseline_id", 64),
        "policy_id": _id(row.policy_id, "job.policy_id", 64),
        "policy_revision": _exact_integer(row.policy_revision, "job.policy_revision", 1),
        "slo_policy_id": _id(row.slo_policy_id, "job.slo_policy_id", 64),
        "slo_policy_revision": _exact_integer(
            row.slo_policy_revision, "job.slo_policy_revision", 1
        ),
        "trigger": trigger,
        "status": status,
        "active_job_key": (
            None
            if row.active_job_key is None
            else _text(row.active_job_key, "job.active_job_key", 384)
        ),
        "cycle_key": _stored_digest(row.cycle_key, "job.cycle_key"),
        "expected_manifest_digest": _stored_digest(
            row.expected_manifest_digest, "job.expected_manifest_digest"
        ),
        "expected_evidence_digest": (
            None
            if row.expected_evidence_digest is None
            else _stored_digest(row.expected_evidence_digest, "job.expected_evidence_digest")
        ),
        "expected_channel_revision": _exact_integer(
            row.expected_channel_revision, "job.expected_channel_revision", 1
        ),
        "claim_owner": (
            None if row.claim_owner is None else _text(row.claim_owner, "job.claim_owner", 128)
        ),
        "claim_lease_until": _iso(row.claim_lease_until, "job.claim_lease_until", allow_none=True),
        "heartbeat_at": _iso(row.heartbeat_at, "job.heartbeat_at", allow_none=True),
        "attempt_count": _exact_integer(row.attempt_count, "job.attempt_count", 0),
        "max_attempts": _exact_integer(row.max_attempts, "job.max_attempts", 1),
        "next_attempt_at": _iso(row.next_attempt_at, "job.next_attempt_at", allow_none=True),
        "result_certification_id": (
            None
            if row.result_certification_id is None
            else _id(row.result_certification_id, "job.result_certification_id", 64)
        ),
        "idempotency_key_digest": _stored_digest(
            row.idempotency_key_digest, "job.idempotency_key_digest"
        ),
        "request_hash": _stored_digest(row.request_hash, "job.request_hash"),
        "safe_error_code": (
            None
            if row.safe_error_code is None
            else _text(row.safe_error_code, "job.safe_error_code", 64)
        ),
        "safe_error": (
            None if row.safe_error is None else _text(row.safe_error, "job.safe_error", 512)
        ),
        "created_at": _iso(row.created_at, "job.created_at"),
        "created_by": _id(row.created_by, "job.created_by", 64),
        "updated_at": _iso(row.updated_at, "job.updated_at"),
        "completed_at": _iso(row.completed_at, "job.completed_at", allow_none=True),
        "cancelled_at": _iso(row.cancelled_at, "job.cancelled_at", allow_none=True),
        "cancelled_by": (
            None if row.cancelled_by is None else _id(row.cancelled_by, "job.cancelled_by", 64)
        ),
        "request_id": _text(row.request_id, "job.request_id", 128, allow_empty=True),
        "reason": _text(row.reason, "job.reason", 512),
    }


def _canonical_scope_key(row: TenantReleaseQualitySloPolicy) -> str:
    scope = _text(row.scope_type, "slo_policy.scope_type", 16).casefold()
    value = _text(row.scope_value, "slo_policy.scope_value", 128)
    channel_id = (
        None if row.channel_id is None else _id(row.channel_id, "slo_policy.channel_id", 128)
    )
    if scope == "global":
        if value != "*" or channel_id is not None:
            raise ReleaseQualityUnavailable("SLO policy scope authority is malformed")
    elif scope == "risk_tier":
        if value.casefold() not in {"low", "medium", "high"} or channel_id is not None:
            raise ReleaseQualityUnavailable("SLO policy scope authority is malformed")
        value = value.casefold()
    elif scope == "channel":
        if channel_id is None or value != channel_id:
            raise ReleaseQualityUnavailable("SLO policy scope authority is malformed")
    else:
        raise ReleaseQualityUnavailable("SLO policy scope authority is malformed")
    return f"{scope}:{value}"


def _slo_digest_candidates(row: TenantReleaseQualitySloPolicy) -> frozenset[str]:
    scope = _text(row.scope_type, "slo_policy.scope_type", 16).casefold()
    value = _text(row.scope_value, "slo_policy.scope_value", 128)
    channel_id = (
        None if row.channel_id is None else _id(row.channel_id, "slo_policy.channel_id", 128)
    )
    revision = _exact_integer(row.revision, "slo_policy.revision", 1)
    thresholds = {
        "certification_warning_minutes": _exact_integer(
            row.certification_warning_minutes,
            "slo_policy.certification_warning_minutes",
            1,
        ),
        "certification_critical_minutes": _exact_integer(
            row.certification_critical_minutes,
            "slo_policy.certification_critical_minutes",
            1,
        ),
        "waiver_warning_minutes": _exact_integer(
            row.waiver_warning_minutes, "slo_policy.waiver_warning_minutes", 1
        ),
        "max_open_alerts": _exact_integer(row.max_open_alerts, "slo_policy.max_open_alerts", 1),
        "auto_queue_recertification": _exact_boolean(
            row.auto_queue_recertification, "slo_policy.auto_queue_recertification"
        ),
        "require_passing_certification": _exact_boolean(
            row.require_passing_certification, "slo_policy.require_passing_certification"
        ),
        "allow_active_waiver": _exact_boolean(
            row.allow_active_waiver, "slo_policy.allow_active_waiver"
        ),
    }
    if thresholds["certification_warning_minutes"] <= thresholds["certification_critical_minutes"]:
        raise ReleaseQualityUnavailable("SLO policy thresholds are malformed")
    canonical = {
        "scope_type": scope,
        "scope_value": value,
        "channel_id": channel_id,
        "revision": revision,
        **thresholds,
    }
    # The Stage 21 mutation service uses the compact authority projection. A
    # compatibility projection is accepted for direct 0031 fixtures that
    # included safe persisted metadata in their digest.
    legacy = {
        "tenant_id": _id(row.tenant_id, "slo_policy.tenant_id", 64),
        "name": _text(row.name, "slo_policy.name", 128),
        **canonical,
        "active_scope_key": _text(row.active_scope_key, "slo_policy.active_scope_key", 192),
        "status": _text(row.status, "slo_policy.status", 16).casefold(),
    }
    try:
        return frozenset(
            {
                canonical_operations_digest("slo_policy", canonical),
                canonical_operations_digest("slo_policy", legacy),
            }
        )
    except ValueError as exc:
        raise ReleaseQualityUnavailable("SLO policy digest authority is malformed") from exc


def _resolve_slo_policy(
    session: Session, *, tenant_id: str, channel: TenantReleaseChannel
) -> TenantReleaseQualitySloPolicy | None:
    for scope_type, scope_value in (
        ("channel", channel.id),
        ("risk_tier", channel.risk_tier),
        ("global", "*"),
    ):
        rows = list(
            session.scalars(
                select(TenantReleaseQualitySloPolicy)
                .where(
                    TenantReleaseQualitySloPolicy.tenant_id == tenant_id,
                    TenantReleaseQualitySloPolicy.scope_type == scope_type,
                    TenantReleaseQualitySloPolicy.scope_value == scope_value,
                    TenantReleaseQualitySloPolicy.status == "active",
                )
                .order_by(
                    TenantReleaseQualitySloPolicy.revision.desc(),
                    TenantReleaseQualitySloPolicy.updated_at.desc(),
                    TenantReleaseQualitySloPolicy.id.desc(),
                )
                .with_for_update()
            )
        )
        if len(rows) > 1:
            raise ReleaseQualityConflict("multiple active SLO policies claim the same scope")
        if rows:
            return rows[0]
    return None


def _validate_slo_policy(
    session: Session,
    *,
    tenant_id: str,
    channel: TenantReleaseChannel,
    slo_policy_id: str,
    expected_revision: int,
) -> tuple[TenantReleaseQualitySloPolicy, str]:
    row = session.scalar(
        select(TenantReleaseQualitySloPolicy)
        .where(
            TenantReleaseQualitySloPolicy.tenant_id == tenant_id,
            TenantReleaseQualitySloPolicy.id == slo_policy_id,
        )
        .with_for_update()
    )
    if row is None:
        raise ReleaseQualityNotFound("SLO policy is outside the requested Tenant scope")
    if _text(row.status, "slo_policy.status", 16).casefold() != "active":
        raise ReleaseQualityConflict("SLO policy is not active")
    revision = _exact_integer(row.revision, "slo_policy.revision", 1)
    if revision != expected_revision:
        raise ReleaseQualityConflict("SLO policy revision changed")
    expected_scope_key = _canonical_scope_key(row)
    if (
        row.active_scope_key != expected_scope_key
        or row.disabled_at is not None
        or row.disabled_by is not None
    ):
        raise ReleaseQualityUnavailable("SLO policy lifecycle authority is malformed")
    stored_digest = _stored_digest(row.policy_digest, "slo_policy.policy_digest")
    if stored_digest not in _slo_digest_candidates(row):
        raise ReleaseQualityUnavailable("SLO policy digest is stale or malformed")
    authoritative = _resolve_slo_policy(session, tenant_id=tenant_id, channel=channel)
    if authoritative is None or authoritative.id != row.id:
        raise ReleaseQualityConflict("SLO policy is not authoritative for this Channel")
    return row, stored_digest


def _validate_gate_policy(
    session: Session,
    *,
    tenant_id: str,
    channel: TenantReleaseChannel,
    policy_id: str,
    expected_revision: int,
) -> tuple[TenantReleaseQualityGatePolicy, str]:
    row = session.scalar(
        select(TenantReleaseQualityGatePolicy)
        .where(
            TenantReleaseQualityGatePolicy.tenant_id == tenant_id,
            TenantReleaseQualityGatePolicy.id == policy_id,
        )
        .with_for_update()
    )
    if row is None:
        raise ReleaseQualityNotFound("Quality policy is outside the requested Tenant scope")
    if _text(row.status, "quality_policy.status", 16).casefold() != "active":
        raise ReleaseQualityConflict("Quality policy is not active")
    revision = _exact_integer(row.revision, "quality_policy.revision", 1)
    if revision != expected_revision:
        raise ReleaseQualityConflict("Quality policy revision changed")
    if row.disabled_at is not None or row.disabled_by is not None:
        raise ReleaseQualityUnavailable("Quality policy lifecycle authority is malformed")
    try:
        scope_type = _text(row.scope_type, "quality_policy.scope_type", 16)
        scope_value = _text(row.scope_value, "quality_policy.scope_value", 128)
        channel_binding = (
            None
            if row.channel_id is None
            else _id(row.channel_id, "quality_policy.channel_id", 128)
        )
        thresholds = _thresholds(row)
        for key, value in thresholds.items():
            if key.startswith("require_"):
                _exact_boolean(value, f"quality_policy.{key}")
            else:
                _exact_integer(value, f"quality_policy.{key}", 0)
        max_age = _exact_integer(
            row.max_certification_age_minutes,
            "quality_policy.max_certification_age_minutes",
            1,
        )
        stored_digest = _stored_digest(row.policy_digest, "quality_policy.policy_digest")
        computed_digest = _policy_authority_digest(
            scope_type=scope_type,
            scope_value=scope_value,
            channel_id=channel_binding,
            thresholds=thresholds,
            max_certification_age_minutes=max_age,
        )
    except (TypeError, ValueError) as exc:
        raise ReleaseQualityUnavailable("Quality policy digest authority is malformed") from exc
    if stored_digest != computed_digest:
        raise ReleaseQualityUnavailable("Quality policy digest is stale")
    authoritative = _resolve_policy(session, tenant_id=tenant_id, channel=channel)
    if authoritative is None or authoritative.id != row.id:
        raise ReleaseQualityConflict("Quality policy is not authoritative for this Channel")
    return row, stored_digest


def _validate_release(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    release_id: str,
    expected_manifest_digest: str,
) -> DatasetReleaseManifest:
    try:
        dataset, ownership, workspace = _release_revisions(session, tenant_id, dataset_id)
    except ReleaseManifestError as exc:
        _translate_manifest_error(exc)
    release = session.scalar(
        select(DatasetReleaseManifest)
        .where(
            DatasetReleaseManifest.tenant_id == tenant_id,
            DatasetReleaseManifest.dataset_id == dataset_id,
            DatasetReleaseManifest.id == release_id,
        )
        .with_for_update()
    )
    if release is None:
        raise ReleaseQualityNotFound("Release is outside the requested Dataset scope")
    if _text(release.readiness_state, "release.readiness_state", 16) != "ready":
        raise ReleaseQualityConflict("Release Manifest is not ready")
    stored_digest = _stored_digest(release.manifest_digest, "release.manifest_digest")
    if stored_digest != expected_manifest_digest:
        raise ReleaseQualityConflict(
            "Release Manifest digest is stale; manifest authority is stale"
        )
    try:
        current_authority = (
            _exact_integer(dataset.profile_revision, "dataset.profile_revision", 1),
            _exact_integer(dataset.mutation_generation, "dataset.mutation_generation", 0),
            _exact_integer(dataset.serving_generation, "dataset.serving_generation", 0),
            _exact_integer(ownership.revision, "ownership.revision", 1),
            _id(workspace.id, "workspace.id", 128),
            _exact_integer(workspace.revision, "workspace.revision", 1),
        )
        release_authority = (
            _exact_integer(release.profile_revision, "release.profile_revision", 1),
            _exact_integer(release.mutation_generation, "release.mutation_generation", 0),
            _exact_integer(release.serving_generation, "release.serving_generation", 0),
            _exact_integer(release.ownership_revision, "release.ownership_revision", 1),
            _id(release.workspace_id, "release.workspace_id", 128),
            _exact_integer(release.workspace_revision, "release.workspace_revision", 1),
        )
    except ReleaseQualityInvalid as exc:
        raise ReleaseQualityUnavailable("Release revision authority is malformed") from exc
    if current_authority != release_authority:
        raise ReleaseQualityConflict("Release authority revisions are stale")
    retired = session.scalar(
        select(DatasetReleaseEvent.id).where(
            DatasetReleaseEvent.tenant_id == tenant_id,
            DatasetReleaseEvent.dataset_id == dataset_id,
            DatasetReleaseEvent.release_id == release_id,
            DatasetReleaseEvent.event_type == "retired",
        )
    )
    if retired is not None:
        raise ReleaseQualityConflict("Release is retired")
    try:
        current = _release_manifest_authority_current(session, release)
    except ReleaseManifestError as exc:
        _translate_manifest_error(exc)
    if not current:
        raise ReleaseQualityConflict("Release Manifest authority is stale")
    return release


def _validate_baseline_and_evidence(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    release_id: str,
    baseline_id: str,
    expected_evidence_digest: str | None,
) -> tuple[DatasetQualityBaseline, str]:
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
        raise ReleaseQualityNotFound("Quality Baseline is outside the requested Dataset scope")
    baseline_digest = _stored_digest(baseline.baseline_digest, "baseline.baseline_digest")
    if expected_evidence_digest is not None and baseline_digest != expected_evidence_digest:
        raise ReleaseQualityConflict("Baseline evidence digest is stale")
    experiment_count = _exact_integer(baseline.experiment_count, "baseline.experiment_count", 1)
    query_count = _exact_integer(baseline.query_count, "baseline.query_count", 1)
    items = list(
        session.scalars(
            select(DatasetQualityBaselineItem)
            .where(
                DatasetQualityBaselineItem.tenant_id == tenant_id,
                DatasetQualityBaselineItem.dataset_id == dataset_id,
                DatasetQualityBaselineItem.baseline_id == baseline.id,
            )
            .order_by(DatasetQualityBaselineItem.ordinal, DatasetQualityBaselineItem.id)
            .with_for_update()
        )
    )
    if len(items) != experiment_count:
        raise ReleaseQualityUnavailable("Quality Baseline authority is incomplete")
    if _baseline_authority_digest(baseline, items) != baseline_digest:
        raise ReleaseQualityConflict("Quality Baseline root digest is stale")
    if len({item.query_hash for item in items}) != query_count:
        raise ReleaseQualityConflict("Quality Baseline query authority is stale")
    try:
        evidence = _release_evidence(
            session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            release_id=release_id,
            experiment_ids=[item.experiment_id for item in items],
            lock_for_update=True,
        )
    except ReleaseQualityUnavailable:
        raise
    except ReleaseQualityError as exc:
        raise ReleaseQualityConflict("Baseline evidence is stale") from exc
    by_experiment = {item["experiment_id"]: item for item in evidence}
    if len(by_experiment) != len(items):
        raise ReleaseQualityConflict("Baseline evidence is stale")
    for baseline_item in items:
        current = by_experiment.get(baseline_item.experiment_id)
        if current is None:
            raise ReleaseQualityConflict("Baseline evidence is stale")
        expected = (
            _exact_integer(
                baseline_item.experiment_sequence, "baseline_item.experiment_sequence", 0
            ),
            _stored_digest(baseline_item.query_hash, "baseline_item.query_hash"),
            _exact_integer(
                baseline_item.experiment_serving_generation,
                "baseline_item.experiment_serving_generation",
                0,
            ),
            _stored_digest(baseline_item.strategy_digest, "baseline_item.strategy_digest"),
            _stored_digest(baseline_item.result_digest, "baseline_item.result_digest"),
            _stored_digest(baseline_item.evidence_digest, "baseline_item.evidence_digest"),
            _stored_digest(baseline_item.judgment_digest, "baseline_item.judgment_digest"),
        )
        actual = (
            _exact_integer(current["experiment_sequence"], "evidence.experiment_sequence", 0),
            _stored_digest(current["query_hash"], "evidence.query_hash"),
            _exact_integer(
                current["dataset_serving_generation"],
                "evidence.dataset_serving_generation",
                0,
            ),
            _stored_digest(current["strategy_digest"], "evidence.strategy_digest"),
            _stored_digest(current["result_digest"], "evidence.result_digest"),
            _baseline_item_digest(current),
            _stored_digest(current["judgment_digest"], "evidence.judgment_digest"),
        )
        if expected != actual:
            raise ReleaseQualityConflict("Baseline evidence is stale")
    return baseline, baseline_digest


def _validate_authority(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    release_id: str,
    channel_id: str,
    release_role: str,
    baseline_id: str,
    policy_id: str,
    policy_revision: int,
    slo_policy_id: str,
    slo_policy_revision: int,
    expected_manifest_digest: str,
    expected_evidence_digest: str | None,
    expected_channel_revision: int,
) -> _Authority:
    if release_role not in ALLOWED_ROLES:
        raise ReleaseQualityInvalid("release_role is invalid")
    release = _validate_release(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        release_id=release_id,
        expected_manifest_digest=expected_manifest_digest,
    )
    channel = session.scalar(
        select(TenantReleaseChannel)
        .where(TenantReleaseChannel.tenant_id == tenant_id, TenantReleaseChannel.id == channel_id)
        .with_for_update()
    )
    if channel is None or _text(channel.status, "channel.status", 16).casefold() != "active":
        raise ReleaseQualityNotFound("Release Channel is unavailable")
    channel_revision = _exact_integer(channel.revision, "channel.revision", 1)
    if channel_revision != expected_channel_revision:
        raise ReleaseQualityConflict("Release Channel revision changed")
    policy, policy_digest = _validate_gate_policy(
        session,
        tenant_id=tenant_id,
        channel=channel,
        policy_id=policy_id,
        expected_revision=policy_revision,
    )
    slo_policy, slo_digest = _validate_slo_policy(
        session,
        tenant_id=tenant_id,
        channel=channel,
        slo_policy_id=slo_policy_id,
        expected_revision=slo_policy_revision,
    )
    baseline, baseline_digest = _validate_baseline_and_evidence(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        release_id=release_id,
        baseline_id=baseline_id,
        expected_evidence_digest=expected_evidence_digest,
    )
    return _Authority(
        release=release,
        channel=channel,
        policy=policy,
        slo_policy=slo_policy,
        baseline=baseline,
        baseline_digest=baseline_digest,
        policy_digest=policy_digest,
        slo_policy_digest=slo_digest,
    )


def _active_job_key(
    *, dataset_id: str, release_id: str, channel_id: str, release_role: str, policy_id: str
) -> str:
    return f"{dataset_id}:{release_id}:{channel_id}:{release_role}:{policy_id}"


def _cycle_key(*, authority: _Authority, tenant_id: str, dataset_id: str, release_role: str) -> str:
    try:
        return canonical_quality_digest(
            "recertification_cycle",
            {
                "tenant_id": tenant_id,
                "dataset_id": dataset_id,
                "release_id": authority.release.id,
                "channel_id": authority.channel.id,
                "release_role": release_role,
                "baseline_id": authority.baseline.id,
                "baseline_digest": authority.baseline_digest,
                "quality_policy_id": authority.policy.id,
                "quality_policy_revision": int(authority.policy.revision),
                "quality_policy_digest": authority.policy_digest,
                "slo_policy_id": authority.slo_policy.id,
                "slo_policy_revision": int(authority.slo_policy.revision),
                "slo_policy_digest": authority.slo_policy_digest,
                "expected_manifest_digest": authority.release.manifest_digest,
                "expected_evidence_digest": authority.baseline_digest,
                "expected_channel_revision": int(authority.channel.revision),
            },
        )
    except (TypeError, ValueError) as exc:
        raise ReleaseQualityUnavailable("Recertification cycle authority is malformed") from exc


def _claim_fenced(row: DatasetReleaseRecertificationJob, *, worker_id: str, now: datetime) -> None:
    if row.status != "claimed":
        raise ReleaseQualityConflict("Recertification Job is not claimed")
    if row.claim_owner != worker_id:
        raise ReleaseQualityConflict("Recertification Job claim owner is invalid")
    lease_until = _utc_naive(row.claim_lease_until, "job.claim_lease_until")
    assert lease_until is not None
    if lease_until <= now:
        raise ReleaseQualityConflict("Recertification Job claim lease has expired")


def _mark_failed(
    row: DatasetReleaseRecertificationJob,
    *,
    now: datetime,
    code: str,
    message: str,
) -> None:
    row.status = "failed"
    row.active_job_key = None
    row.claim_owner = None
    row.claim_lease_until = None
    row.heartbeat_at = None
    row.next_attempt_at = None
    row.result_certification_id = None
    row.safe_error_code = _text(code, "safe_error_code", 64)
    row.safe_error = _text(message, "safe_error", 512)
    row.completed_at = None
    row.cancelled_at = None
    row.cancelled_by = None
    row.updated_at = now


def _job_arguments(row: DatasetReleaseRecertificationJob) -> _JobArguments:
    expected_evidence = _digest(
        row.expected_evidence_digest, "job.expected_evidence_digest", optional=True
    )
    expected_manifest = _digest(row.expected_manifest_digest, "job.expected_manifest_digest")
    cycle_key = _digest(row.cycle_key, "job.cycle_key")
    assert expected_manifest is not None
    assert cycle_key is not None
    return _JobArguments(
        tenant_id=_id(row.tenant_id, "job.tenant_id", 64),
        actor_id=_id(row.created_by, "job.created_by", 64),
        dataset_id=_id(row.dataset_id, "job.dataset_id", 64),
        release_id=_id(row.release_id, "job.release_id", 64),
        channel_id=_id(row.channel_id, "job.channel_id", 128),
        release_role=_text(row.release_role, "job.release_role", 16),
        baseline_id=_id(row.baseline_id, "job.baseline_id", 64),
        policy_id=_id(row.policy_id, "job.policy_id", 64),
        policy_revision=_exact_integer(row.policy_revision, "job.policy_revision", 1),
        slo_policy_id=_id(row.slo_policy_id, "job.slo_policy_id", 64),
        slo_policy_revision=_exact_integer(row.slo_policy_revision, "job.slo_policy_revision", 1),
        expected_manifest_digest=expected_manifest,
        expected_evidence_digest=expected_evidence,
        expected_channel_revision=_exact_integer(
            row.expected_channel_revision, "job.expected_channel_revision", 1
        ),
        job_id=_id(row.id, "job.id", 64),
        cycle_key=cycle_key,
    )


def _internal_stage20_key(job: _JobArguments) -> str:
    material = "|".join((job.tenant_id, job.dataset_id, job.job_id, job.cycle_key)).encode("utf-8")
    return "recertification-stage20-" + sha256(material).hexdigest()


def _safe_delegate_exception(exc: BaseException) -> ReleaseQualityError:
    if isinstance(exc, ReleaseQualityConflict):
        return ReleaseQualityConflict("Stage 20 Certification authority changed")
    if isinstance(exc, ReleaseQualityForbidden):
        return ReleaseQualityForbidden("Stage 20 Certification operator authority was rejected")
    if isinstance(exc, ReleaseQualityNotFound):
        return ReleaseQualityNotFound("Stage 20 Certification authority is unavailable")
    if isinstance(exc, ReleaseQualityInvalid):
        return ReleaseQualityInvalid("Stage 20 Certification request is invalid")
    return ReleaseQualityUnavailable("Stage 20 Certification delegate failed")


def queue_recertification_job(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    release_id: str,
    channel_id: str,
    release_role: str,
    baseline_id: str,
    policy_id: str,
    expected_policy_revision: int,
    slo_policy_id: str,
    expected_slo_policy_revision: int,
    trigger: str,
    expected_manifest_digest: str,
    expected_evidence_digest: str | None,
    expected_channel_revision: int,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> ServiceResult:
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    dataset = _id(dataset_id, "dataset_id", 64)
    release = _id(release_id, "release_id", 64)
    channel = _id(channel_id, "channel_id", 128)
    role = _text(release_role, "release_role", 16)
    baseline = _id(baseline_id, "baseline_id", 64)
    policy = _id(policy_id, "policy_id", 64)
    policy_revision = _exact_integer(expected_policy_revision, "expected_policy_revision", 1)
    slo_policy = _id(slo_policy_id, "slo_policy_id", 64)
    slo_revision = _exact_integer(expected_slo_policy_revision, "expected_slo_policy_revision", 1)
    trigger_value = _text(trigger, "trigger", 32)
    if role not in ALLOWED_ROLES:
        raise ReleaseQualityInvalid("release_role is invalid")
    if trigger_value not in ALLOWED_TRIGGERS:
        raise ReleaseQualityInvalid("trigger is invalid")
    manifest_digest = _digest(expected_manifest_digest, "expected_manifest_digest")
    evidence_digest = _digest(expected_evidence_digest, "expected_evidence_digest", optional=True)
    channel_revision = _exact_integer(expected_channel_revision, "expected_channel_revision", 1)
    assert manifest_digest is not None
    key = _text(idempotency_key, "idempotency_key", 128)
    request = _request_id(request_id, raw_key=key)
    request_ip_value = _request_ip(request_ip)
    clean_reason = _reason(reason, raw_key=key)
    moment = _now(now)
    try:
        normalized_key = normalize_idempotency_key(key)
        key_digest = tenant_idempotency_key_digest(tenant, actor, normalized_key)
        request_hash = tenant_request_hash(
            operation=QUEUE_OPERATION,
            path_identity={
                "tenant_id": tenant,
                "dataset_id": dataset,
                "release_id": release,
                "channel_id": channel,
            },
            body={
                "release_role": role,
                "baseline_id": baseline,
                "policy_id": policy,
                "expected_policy_revision": policy_revision,
                "slo_policy_id": slo_policy,
                "expected_slo_policy_revision": slo_revision,
                "trigger": trigger_value,
                "expected_manifest_digest": manifest_digest,
                "expected_evidence_digest": evidence_digest,
                "expected_channel_revision": channel_revision,
                "reason": clean_reason,
            },
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise ReleaseQualityInvalid(str(exc)) from exc
    with idempotency_key_lock(tenant, actor, key_digest), engine_serialization_lock(engine):
        try:
            with Session(engine, expire_on_commit=False) as session:
                with session.begin():
                    _ensure_capability(session.connection())
                    operator = _authorize_operator(
                        engine,
                        session,
                        tenant_id=tenant,
                        actor_id=actor,
                        dataset_id=dataset,
                    )
                    reservation = _reserve(
                        session,
                        tenant_id=tenant,
                        actor_id=actor,
                        raw_key=key,
                        request_hash=request_hash,
                        operation=QUEUE_OPERATION,
                    )
                    replay = _replay(reservation)
                    if replay is not None:
                        return replay
                    authority = _validate_authority(
                        session,
                        tenant_id=tenant,
                        dataset_id=dataset,
                        release_id=release,
                        channel_id=channel,
                        release_role=role,
                        baseline_id=baseline,
                        policy_id=policy,
                        policy_revision=policy_revision,
                        slo_policy_id=slo_policy,
                        slo_policy_revision=slo_revision,
                        expected_manifest_digest=manifest_digest,
                        expected_evidence_digest=evidence_digest,
                        expected_channel_revision=channel_revision,
                    )
                    active_key = _active_job_key(
                        dataset_id=dataset,
                        release_id=release,
                        channel_id=channel,
                        release_role=role,
                        policy_id=policy,
                    )
                    cycle_key = _cycle_key(
                        authority=authority,
                        tenant_id=tenant,
                        dataset_id=dataset,
                        release_role=role,
                    )
                    existing_active = session.scalar(
                        select(DatasetReleaseRecertificationJob)
                        .where(
                            DatasetReleaseRecertificationJob.tenant_id == tenant,
                            DatasetReleaseRecertificationJob.active_job_key == active_key,
                        )
                        .with_for_update()
                    )
                    if existing_active is not None:
                        if existing_active.cycle_key != cycle_key:
                            raise ReleaseQualityConflict(
                                "another recertification cycle is active for this Release authority"
                            )
                        if existing_active.status not in ACTIVE_STATUSES:
                            raise ReleaseQualityUnavailable(
                                "Recertification Job active identity is malformed"
                            )
                        before = _job_snapshot(existing_active)
                        payload = {
                            "state": "coalesced",
                            "operation": QUEUE_OPERATION,
                            "resource_type": QUALITY_RECERTIFICATION_RESOURCE_TYPE,
                            "resource_id": existing_active.id,
                            "request_id": request,
                            "job": before,
                            "message": "Release quality recertification Job already exists",
                            "retryable": False,
                        }
                        _audit(
                            session,
                            tenant_id=operator.tenant_id,
                            actor_id=operator.actor_id,
                            actor_name=operator.name,
                            actor_email=operator.email,
                            action="knowledge_base.release_quality.recertification_coalesced",
                            resource_id=str(existing_active.id),
                            before=before,
                            after=before,
                            request_id=request,
                            request_ip=request_ip_value,
                            now=moment,
                        )
                        return _complete(session, reservation, payload, 200, existing_active.id)
                    existing_cycle = session.scalar(
                        select(DatasetReleaseRecertificationJob)
                        .where(
                            DatasetReleaseRecertificationJob.tenant_id == tenant,
                            DatasetReleaseRecertificationJob.cycle_key == cycle_key,
                        )
                        .with_for_update()
                    )
                    if existing_cycle is not None:
                        raise ReleaseQualityConflict(
                            "recertification cycle already has a terminal Job"
                        )
                    job = DatasetReleaseRecertificationJob(
                        id=f"quality-recertification-{uuid.uuid4().hex}",
                        tenant_id=tenant,
                        dataset_id=dataset,
                        release_id=release,
                        channel_id=channel,
                        release_role=role,
                        baseline_id=baseline,
                        policy_id=policy,
                        policy_revision=policy_revision,
                        slo_policy_id=slo_policy,
                        slo_policy_revision=slo_revision,
                        trigger=trigger_value,
                        status="pending",
                        active_job_key=active_key,
                        cycle_key=cycle_key,
                        expected_manifest_digest=manifest_digest,
                        expected_evidence_digest=evidence_digest,
                        expected_channel_revision=channel_revision,
                        claim_owner=None,
                        claim_lease_until=None,
                        heartbeat_at=None,
                        attempt_count=0,
                        max_attempts=DEFAULT_MAX_ATTEMPTS,
                        next_attempt_at=None,
                        result_certification_id=None,
                        idempotency_key_digest=key_digest,
                        request_hash=request_hash,
                        safe_error_code=None,
                        safe_error=None,
                        created_at=moment,
                        created_by=actor,
                        updated_at=moment,
                        completed_at=None,
                        cancelled_at=None,
                        cancelled_by=None,
                        request_id=request,
                        reason=clean_reason,
                    )
                    session.add(job)
                    session.flush()
                    projected = _job_snapshot(job)
                    payload = {
                        "state": "applied",
                        "operation": QUEUE_OPERATION,
                        "resource_type": QUALITY_RECERTIFICATION_RESOURCE_TYPE,
                        "resource_id": job.id,
                        "request_id": request,
                        "job": projected,
                        "message": "Release quality recertification Job queued",
                        "retryable": False,
                    }
                    _audit(
                        session,
                        tenant_id=operator.tenant_id,
                        actor_id=operator.actor_id,
                        actor_name=operator.name,
                        actor_email=operator.email,
                        action="knowledge_base.release_quality.recertification_queued",
                        resource_id=str(job.id),
                        before=None,
                        after=projected,
                        request_id=request,
                        request_ip=request_ip_value,
                        now=moment,
                    )
                    return _complete(session, reservation, payload, 201, job.id)
        except ReleaseQualityError:
            raise
        except IntegrityError as exc:
            raise ReleaseQualityConflict(
                "Recertification Job violates an authority uniqueness constraint"
            ) from exc
        except SQLAlchemyError as exc:
            raise ReleaseQualityUnavailable(
                "Recertification Job transaction could not be committed"
            ) from exc


def claim_recertification_job(
    engine: Any,
    *,
    worker_id: str,
    now: datetime | None = None,
    lease_seconds: int = 60,
) -> dict[str, Any] | None:
    worker = _id(worker_id, "worker_id", 128)
    moment = _now(now)
    lease = _exact_integer(lease_seconds, "lease_seconds", MIN_LEASE_SECONDS, MAX_LEASE_SECONDS)
    with engine_serialization_lock(engine):
        try:
            with Session(engine, expire_on_commit=False) as session:
                with session.begin():
                    _ensure_capability(session.connection())
                    rows = list(
                        session.scalars(
                            select(DatasetReleaseRecertificationJob)
                            .where(DatasetReleaseRecertificationJob.status.in_(CLAIMABLE_STATUSES))
                            .where(
                                or_(
                                    DatasetReleaseRecertificationJob.status == "claimed",
                                    DatasetReleaseRecertificationJob.next_attempt_at.is_(None),
                                    DatasetReleaseRecertificationJob.next_attempt_at <= moment,
                                )
                            )
                            .order_by(
                                DatasetReleaseRecertificationJob.created_at,
                                DatasetReleaseRecertificationJob.id,
                            )
                            .with_for_update()
                        )
                    )
                    for row in rows:
                        status = _text(row.status, "job.status", 24)
                        attempt_count = _exact_integer(row.attempt_count, "job.attempt_count", 0)
                        max_attempts = _exact_integer(row.max_attempts, "job.max_attempts", 1)
                        if attempt_count > max_attempts:
                            raise ReleaseQualityUnavailable(
                                "Recertification Job retry authority is malformed"
                            )
                        if status == "claimed":
                            lease_until = _utc_naive(
                                row.claim_lease_until,
                                "job.claim_lease_until",
                                allow_none=True,
                            )
                            if lease_until is None:
                                raise ReleaseQualityUnavailable(
                                    "Recertification Job lease authority is malformed"
                                )
                            if lease_until > moment:
                                continue
                        if attempt_count >= max_attempts:
                            before = _job_snapshot(row)
                            _mark_failed(
                                row,
                                now=moment,
                                code="recertification_max_attempts",
                                message="Recertification Job retry limit reached",
                            )
                            session.flush()
                            after = _job_snapshot(row)
                            _worker_audit(
                                session,
                                job=row,
                                action="knowledge_base.release_quality.recertification_failed",
                                before=before,
                                after=after,
                                worker_id=worker,
                                now=moment,
                            )
                            continue
                        before = _job_snapshot(row)
                        row.status = "claimed"
                        row.claim_owner = worker
                        row.claim_lease_until = moment + timedelta(seconds=lease)
                        row.heartbeat_at = moment
                        row.attempt_count = attempt_count + 1
                        row.updated_at = moment
                        session.flush()
                        after = _job_snapshot(row)
                        _worker_audit(
                            session,
                            job=row,
                            action="knowledge_base.release_quality.recertification_claimed",
                            before=before,
                            after=after,
                            worker_id=worker,
                            now=moment,
                        )
                        return after
                    return None
        except ReleaseQualityError:
            raise
        except SQLAlchemyError as exc:
            raise ReleaseQualityUnavailable(
                "Recertification Job claim transaction could not be committed"
            ) from exc


def heartbeat_recertification_job(
    engine: Any,
    *,
    job_id: str,
    worker_id: str,
    now: datetime | None = None,
    lease_seconds: int = 60,
) -> dict[str, Any]:
    job_key = _id(job_id, "job_id", 64)
    worker = _id(worker_id, "worker_id", 128)
    moment = _now(now)
    lease = _exact_integer(lease_seconds, "lease_seconds", MIN_LEASE_SECONDS, MAX_LEASE_SECONDS)
    with engine_serialization_lock(engine):
        try:
            with Session(engine, expire_on_commit=False) as session:
                with session.begin():
                    _ensure_capability(session.connection())
                    row = _lock_worker_job(session, job_key)
                    before = _job_snapshot(row)
                    _claim_fenced(row, worker_id=worker, now=moment)
                    row.claim_lease_until = moment + timedelta(seconds=lease)
                    row.heartbeat_at = moment
                    row.updated_at = moment
                    session.flush()
                    after = _job_snapshot(row)
                    _worker_audit(
                        session,
                        job=row,
                        action="knowledge_base.release_quality.recertification_heartbeat",
                        before=before,
                        after=after,
                        worker_id=worker,
                        now=moment,
                    )
                    return after
        except ReleaseQualityError:
            raise
        except SQLAlchemyError as exc:
            raise ReleaseQualityUnavailable(
                "Recertification Job heartbeat transaction could not be committed"
            ) from exc


def mark_recertification_ready(
    engine: Any,
    *,
    job_id: str,
    worker_id: str,
    expected_manifest_digest: str,
    expected_evidence_digest: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    job_key = _id(job_id, "job_id", 64)
    worker = _id(worker_id, "worker_id", 128)
    manifest_digest = _digest(expected_manifest_digest, "expected_manifest_digest")
    evidence_digest = _digest(expected_evidence_digest, "expected_evidence_digest", optional=True)
    assert manifest_digest is not None
    moment = _now(now)
    with engine_serialization_lock(engine):
        try:
            with Session(engine, expire_on_commit=False) as session:
                with session.begin():
                    _ensure_capability(session.connection())
                    row = _lock_worker_job(session, job_key)
                    before = _job_snapshot(row)
                    _claim_fenced(row, worker_id=worker, now=moment)
                    args = _job_arguments(row)
                    if args.expected_manifest_digest != manifest_digest:
                        raise ReleaseQualityConflict("Release Manifest digest changed")
                    if (
                        args.expected_evidence_digest is not None
                        and args.expected_evidence_digest != evidence_digest
                    ):
                        raise ReleaseQualityConflict("Baseline evidence digest changed")
                    authority = _validate_authority(
                        session,
                        tenant_id=args.tenant_id,
                        dataset_id=args.dataset_id,
                        release_id=args.release_id,
                        channel_id=args.channel_id,
                        release_role=args.release_role,
                        baseline_id=args.baseline_id,
                        policy_id=args.policy_id,
                        policy_revision=args.policy_revision,
                        slo_policy_id=args.slo_policy_id,
                        slo_policy_revision=args.slo_policy_revision,
                        expected_manifest_digest=manifest_digest,
                        expected_evidence_digest=args.expected_evidence_digest,
                        expected_channel_revision=args.expected_channel_revision,
                    )
                    if evidence_digest is not None and evidence_digest != authority.baseline_digest:
                        raise ReleaseQualityConflict("Baseline evidence digest changed")
                    row.status = "ready_to_certify"
                    row.claim_owner = None
                    row.claim_lease_until = None
                    row.heartbeat_at = None
                    row.updated_at = moment
                    session.flush()
                    after = _job_snapshot(row)
                    _worker_audit(
                        session,
                        job=row,
                        action="knowledge_base.release_quality.recertification_ready",
                        before=before,
                        after=after,
                        worker_id=worker,
                        now=moment,
                    )
                    return after
        except ReleaseQualityError:
            raise
        except SQLAlchemyError as exc:
            raise ReleaseQualityUnavailable(
                "Recertification Job readiness transaction could not be committed"
            ) from exc


def cancel_recertification_job(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    job_id: str,
    expected_status: str,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> ServiceResult:
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    dataset = _id(dataset_id, "dataset_id", 64)
    job_key = _id(job_id, "job_id", 64)
    expected = _text(expected_status, "expected_status", 24)
    if expected not in ACTIVE_STATUSES:
        raise ReleaseQualityInvalid("expected_status is invalid")
    key = _text(idempotency_key, "idempotency_key", 128)
    request = _request_id(request_id, raw_key=key)
    request_ip_value = _request_ip(request_ip)
    clean_reason = _reason(reason, raw_key=key)
    moment = _now(now)
    try:
        normalized_key = normalize_idempotency_key(key)
        key_digest = tenant_idempotency_key_digest(tenant, actor, normalized_key)
        request_hash = tenant_request_hash(
            operation=CANCEL_OPERATION,
            path_identity={"tenant_id": tenant, "dataset_id": dataset, "job_id": job_key},
            body={"expected_status": expected, "reason": clean_reason},
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise ReleaseQualityInvalid(str(exc)) from exc
    with idempotency_key_lock(tenant, actor, key_digest), engine_serialization_lock(engine):
        try:
            with Session(engine, expire_on_commit=False) as session:
                with session.begin():
                    _ensure_capability(session.connection())
                    operator = _authorize_operator(
                        engine,
                        session,
                        tenant_id=tenant,
                        actor_id=actor,
                        dataset_id=dataset,
                    )
                    reservation = _reserve(
                        session,
                        tenant_id=tenant,
                        actor_id=actor,
                        raw_key=key,
                        request_hash=request_hash,
                        operation=CANCEL_OPERATION,
                    )
                    replay = _replay(reservation)
                    if replay is not None:
                        return replay
                    row = _lock_job(
                        session,
                        tenant_id=tenant,
                        dataset_id=dataset,
                        job_id=job_key,
                    )
                    if row.status != expected:
                        raise ReleaseQualityConflict("Recertification Job status changed")
                    before = _job_snapshot(row)
                    row.status = "cancelled"
                    row.active_job_key = None
                    row.claim_owner = None
                    row.claim_lease_until = None
                    row.heartbeat_at = None
                    row.result_certification_id = None
                    row.safe_error_code = None
                    row.safe_error = None
                    row.completed_at = None
                    row.cancelled_at = moment
                    row.cancelled_by = actor
                    row.next_attempt_at = None
                    row.updated_at = moment
                    session.flush()
                    after = _job_snapshot(row)
                    payload = {
                        "state": "applied",
                        "operation": CANCEL_OPERATION,
                        "resource_type": QUALITY_RECERTIFICATION_RESOURCE_TYPE,
                        "resource_id": row.id,
                        "request_id": request,
                        "job": after,
                        "message": "Release quality recertification Job cancelled",
                        "retryable": False,
                    }
                    _audit(
                        session,
                        tenant_id=operator.tenant_id,
                        actor_id=operator.actor_id,
                        actor_name=operator.name,
                        actor_email=operator.email,
                        action="knowledge_base.release_quality.recertification_cancelled",
                        resource_id=str(row.id),
                        before=before,
                        after=after,
                        request_id=request,
                        request_ip=request_ip_value,
                        now=moment,
                    )
                    return _complete(session, reservation, payload, 200, row.id)
        except ReleaseQualityError:
            raise
        except IntegrityError as exc:
            raise ReleaseQualityConflict(
                "Recertification Job cancellation violates an authority constraint"
            ) from exc
        except SQLAlchemyError as exc:
            raise ReleaseQualityUnavailable(
                "Recertification Job cancellation transaction could not be committed"
            ) from exc


def _finalize_failed_completion(
    engine: Any,
    *,
    reservation: Any,
    operator: _Operator,
    args: _JobArguments,
    request: str,
    request_ip_value: str,
    now: datetime,
    code: str,
    message: str,
) -> ServiceResult:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            _ensure_capability(session.connection())
            row = _lock_job(
                session,
                tenant_id=args.tenant_id,
                dataset_id=args.dataset_id,
                job_id=args.job_id,
            )
            before = _job_snapshot(row)
            if row.status not in TERMINAL_STATUSES:
                _mark_failed(row, now=now, code=code, message=message)
            after = _job_snapshot(row)
            payload = {
                "state": "failed",
                "operation": COMPLETE_OPERATION,
                "resource_type": QUALITY_RECERTIFICATION_RESOURCE_TYPE,
                "resource_id": row.id,
                "request_id": request,
                "job": after,
                "message": "Stage 20 Certification was not completed",
                "error_code": code,
                "retryable": False,
            }
            _audit(
                session,
                tenant_id=operator.tenant_id,
                actor_id=operator.actor_id,
                actor_name=operator.name,
                actor_email=operator.email,
                action="knowledge_base.release_quality.recertification_failed",
                resource_id=str(row.id),
                before=before,
                after=after,
                request_id=request,
                request_ip=request_ip_value,
                now=now,
            )
            return _complete(session, reservation, payload, 503, row.id)


def _finalize_successful_completion(
    engine: Any,
    *,
    reservation: Any,
    tenant: str,
    actor: str,
    dataset: str,
    job_key: str,
    args: _JobArguments,
    certification_id: str,
    request: str,
    request_ip_value: str,
    now: datetime,
) -> ServiceResult:
    with Session(engine, expire_on_commit=False) as session:
        with session.begin():
            _ensure_capability(session.connection())
            operator = _authorize_operator(
                engine,
                session,
                tenant_id=tenant,
                actor_id=actor,
                dataset_id=dataset,
            )
            row = _lock_job(session, tenant_id=tenant, dataset_id=dataset, job_id=job_key)
            if row.status != "ready_to_certify":
                raise ReleaseQualityConflict(
                    "Recertification Job changed before Certification completion"
                )
            _validate_authority(
                session,
                tenant_id=tenant,
                dataset_id=dataset,
                release_id=args.release_id,
                channel_id=args.channel_id,
                release_role=args.release_role,
                baseline_id=args.baseline_id,
                policy_id=args.policy_id,
                policy_revision=args.policy_revision,
                slo_policy_id=args.slo_policy_id,
                slo_policy_revision=args.slo_policy_revision,
                expected_manifest_digest=args.expected_manifest_digest,
                expected_evidence_digest=args.expected_evidence_digest,
                expected_channel_revision=args.expected_channel_revision,
            )
            before = _job_snapshot(row)
            row.status = "completed"
            row.active_job_key = None
            row.claim_owner = None
            row.claim_lease_until = None
            row.heartbeat_at = None
            row.result_certification_id = certification_id
            row.safe_error_code = None
            row.safe_error = None
            row.completed_at = now
            row.cancelled_at = None
            row.cancelled_by = None
            row.next_attempt_at = None
            row.updated_at = now
            session.flush()
            after = _job_snapshot(row)
            payload = {
                "state": "applied",
                "operation": COMPLETE_OPERATION,
                "resource_type": QUALITY_RECERTIFICATION_RESOURCE_TYPE,
                "resource_id": row.id,
                "request_id": request,
                "job": after,
                "message": "Release quality recertification Certification completed",
                "retryable": False,
            }
            _audit(
                session,
                tenant_id=operator.tenant_id,
                actor_id=operator.actor_id,
                actor_name=operator.name,
                actor_email=operator.email,
                action="knowledge_base.release_quality.recertification_completed",
                resource_id=str(row.id),
                before=before,
                after=after,
                request_id=request,
                request_ip=request_ip_value,
                now=now,
            )
            return _complete(session, reservation, payload, 201, row.id)


def complete_recertification_job(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    job_id: str,
    reason: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> ServiceResult:
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    dataset = _id(dataset_id, "dataset_id", 64)
    job_key = _id(job_id, "job_id", 64)
    key = _text(idempotency_key, "idempotency_key", 128)
    request = _request_id(request_id, raw_key=key)
    request_ip_value = _request_ip(request_ip)
    clean_reason = _reason(reason, raw_key=key)
    moment = _now(now)
    try:
        normalized_key = normalize_idempotency_key(key)
        key_digest = tenant_idempotency_key_digest(tenant, actor, normalized_key)
        request_hash = tenant_request_hash(
            operation=COMPLETE_OPERATION,
            path_identity={"tenant_id": tenant, "dataset_id": dataset, "job_id": job_key},
            body={"reason": clean_reason},
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise ReleaseQualityInvalid(str(exc)) from exc
    with idempotency_key_lock(tenant, actor, key_digest), engine_serialization_lock(engine):
        reservation: Any | None = None
        operator: _Operator | None = None
        args: _JobArguments | None = None
        try:
            with Session(engine, expire_on_commit=False) as session:
                with session.begin():
                    _ensure_capability(session.connection())
                    operator = _authorize_operator(
                        engine,
                        session,
                        tenant_id=tenant,
                        actor_id=actor,
                        dataset_id=dataset,
                    )
                    reservation = _reserve(
                        session,
                        tenant_id=tenant,
                        actor_id=actor,
                        raw_key=key,
                        request_hash=request_hash,
                        operation=COMPLETE_OPERATION,
                    )
                    replay = _replay(reservation)
                    if replay is not None:
                        return replay
                    row = _lock_job(
                        session,
                        tenant_id=tenant,
                        dataset_id=dataset,
                        job_id=job_key,
                    )
                    if row.status != "ready_to_certify":
                        raise ReleaseQualityConflict("Recertification Job is not ready to certify")
                    args = _job_arguments(row)
                    _validate_authority(
                        session,
                        tenant_id=tenant,
                        dataset_id=dataset,
                        release_id=args.release_id,
                        channel_id=args.channel_id,
                        release_role=args.release_role,
                        baseline_id=args.baseline_id,
                        policy_id=args.policy_id,
                        policy_revision=args.policy_revision,
                        slo_policy_id=args.slo_policy_id,
                        slo_policy_revision=args.slo_policy_revision,
                        expected_manifest_digest=args.expected_manifest_digest,
                        expected_evidence_digest=args.expected_evidence_digest,
                        expected_channel_revision=args.expected_channel_revision,
                    )
            assert reservation is not None
            assert operator is not None
            assert args is not None
            certification_result = _certify_release(
                engine,
                tenant_id=args.tenant_id,
                actor_id=actor,
                dataset_id=args.dataset_id,
                release_id=args.release_id,
                channel_id=args.channel_id,
                baseline_id=args.baseline_id,
                policy_id=args.policy_id,
                expected_policy_revision=args.policy_revision,
                expected_channel_revision=args.expected_channel_revision,
                reason=clean_reason,
                request_id=request or f"recertification:{args.job_id}",
                request_ip=request_ip_value,
                idempotency_key=_internal_stage20_key(args),
            )
            body = getattr(certification_result, "body", None)
            if not isinstance(body, Mapping) or not isinstance(body.get("certification"), Mapping):
                raise _DelegateResultInvalid("Stage 20 Certification response is malformed")
            certification_id = _id(
                body["certification"].get("id"),
                "certification.id",
                64,
            )
            return _finalize_successful_completion(
                engine,
                reservation=reservation,
                tenant=tenant,
                actor=actor,
                dataset=dataset,
                job_key=job_key,
                args=args,
                certification_id=certification_id,
                request=request,
                request_ip_value=request_ip_value,
                now=moment,
            )
        except ReleaseQualityError as exc:
            if reservation is None or operator is None or args is None:
                raise
            safe_error = _safe_delegate_exception(exc)
            try:
                _finalize_failed_completion(
                    engine,
                    reservation=reservation,
                    operator=operator,
                    args=args,
                    request=request,
                    request_ip_value=request_ip_value,
                    now=moment,
                    code="stage20_certification_failed",
                    message="Stage 20 Certification delegate failed",
                )
            except ReleaseQualityError as record_exc:
                raise ReleaseQualityUnavailable(
                    "Recertification Job failure could not be recorded"
                ) from record_exc
            raise safe_error from exc
        except (ValueError, TypeError, AttributeError) as exc:
            if reservation is None or operator is None or args is None:
                raise ReleaseQualityUnavailable("Stage 20 Certification delegate failed") from exc
            try:
                _finalize_failed_completion(
                    engine,
                    reservation=reservation,
                    operator=operator,
                    args=args,
                    request=request,
                    request_ip_value=request_ip_value,
                    now=moment,
                    code="stage20_certification_failed",
                    message="Stage 20 Certification delegate failed",
                )
            except ReleaseQualityError as record_exc:
                raise ReleaseQualityUnavailable(
                    "Recertification Job failure could not be recorded"
                ) from record_exc
            raise ReleaseQualityUnavailable("Stage 20 Certification delegate failed") from exc
        except SQLAlchemyError as exc:
            if reservation is not None and operator is not None and args is not None:
                try:
                    _finalize_failed_completion(
                        engine,
                        reservation=reservation,
                        operator=operator,
                        args=args,
                        request=request,
                        request_ip_value=request_ip_value,
                        now=moment,
                        code="stage20_certification_failed",
                        message="Stage 20 Certification delegate failed",
                    )
                except (ReleaseQualityError, SQLAlchemyError):
                    pass
            raise ReleaseQualityUnavailable(
                "Recertification Job transaction could not be committed"
            ) from exc


def list_recertification_jobs(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ServiceResult:
    tenant = _id(tenant_id, "tenant_id", 64)
    actor = _id(actor_id, "actor_id", 64)
    dataset = _id(dataset_id, "dataset_id", 64)
    page_size = _exact_integer(limit, "limit", 1, 200)
    state = None if status is None else _text(status, "status", 24).casefold()
    if state is not None and state not in ALLOWED_STATUSES:
        raise ReleaseQualityInvalid("status is invalid")
    after = _decode_cursor(cursor, "quality_recertification_job")
    session: Session | None = None
    try:
        session, _membership = _read_scope(engine, tenant, actor, dataset)
        _ensure_capability(session.connection())
        statement = select(DatasetReleaseRecertificationJob).where(
            DatasetReleaseRecertificationJob.tenant_id == tenant,
            DatasetReleaseRecertificationJob.dataset_id == dataset,
        )
        if state is not None:
            statement = statement.where(DatasetReleaseRecertificationJob.status == state)
        if after is not None:
            moment, row_id = after
            statement = statement.where(
                or_(
                    DatasetReleaseRecertificationJob.updated_at < moment,
                    and_(
                        DatasetReleaseRecertificationJob.updated_at == moment,
                        DatasetReleaseRecertificationJob.id < row_id,
                    ),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    DatasetReleaseRecertificationJob.updated_at.desc(),
                    DatasetReleaseRecertificationJob.id.desc(),
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        visible = rows[:page_size]
        items = [_job_snapshot(row) for row in visible]
        for item in items:
            if item["tenant_id"] != tenant or item["dataset_id"] != dataset:
                raise ReleaseQualityUnavailable(
                    "Recertification Job authority escaped Tenant/Dataset scope"
                )
        next_cursor = (
            _encode_cursor(
                "quality_recertification_job",
                visible[-1].updated_at,
                visible[-1].id,
            )
            if has_more and visible
            else None
        )
        return ServiceResult({"items": items, "next_cursor": next_cursor}, status=200)
    except ReleaseQualityError:
        raise
    except SQLAlchemyError as exc:
        raise ReleaseQualityUnavailable("Recertification Job list could not be read") from exc
    finally:
        if session is not None:
            session.close()


__all__ = [
    "cancel_recertification_job",
    "claim_recertification_job",
    "complete_recertification_job",
    "heartbeat_recertification_job",
    "list_recertification_jobs",
    "mark_recertification_ready",
    "queue_recertification_job",
]
