"""Tenant-safe Alert Inbox lifecycle for Stage 21 Release Quality Operations.

Alerts are mutable projections over append-only DatasetReleaseQualityObservation
rows. Operator actions never alter Stage 20 quality-gate authority. The 0031
schema is authoritative: active_alert_key is the canonical
 dataset:release:channel:role:alert_type string, and resolved rows clear it.
"""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import binascii
import json
import re
from typing import Any
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from core.quality_alert_operations import (
    ACTIVE_ALERT_STATUSES,
    TERMINAL_ALERT_STATUSES,
    apply_operation,
    configure_alert_model,
    resolve_quality_alert_operation,
)
from core.catalog_schema import inspect_enterprise_release_quality_operations_capability
from core.enterprise_acl_idempotency import engine_serialization_lock, idempotency_key_lock
from core.enterprise_access_control import DatasetAccessControlUnavailable
from core.enterprise_knowledge_base_releases import (
    ReleaseManifestError,
    ReleaseManifestForbidden,
    ReleaseManifestInvalid,
    ReleaseManifestNotFound,
    ReleaseManifestUnavailable,
    ServiceResult,
    _actor,
    _require_manage,
    _require_read,
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
from core.quality_alert_types import ALERT_TYPES, derive_alert_type
from core.release_quality_gate_states import gate_states_storage
from models.orm import (
    Account,
    Dataset,
    DatasetReleaseQualityAlert,
    DatasetReleaseQualityObservation,
    Tenant,
    TenantAuditEvent,
    TenantMember,
)


UTC = timezone.utc
SYSTEM_QUALITY_SCANNER = "system:quality-scanner"
_SYSTEM_QUALITY_SCANNER_NAME = "Quality Operations Scanner"
_SYSTEM_QUALITY_SCANNER_EMAIL = ""
_ALERT_STATUSES = frozenset({"open", "acknowledged", "resolved", "suppressed"})
_ACTIVE_ALERT_STATUSES = ACTIVE_ALERT_STATUSES

# 声明里写到的列必须真的存在于模型上：SQLAlchemy 实例上 setattr 一个不存在的名字不会报错，
# 只会在 flush 时被丢掉 —— 那样一次"成功的"操作其实什么都没改。这里把整张表对着模型核一遍，
# 之后新注册的声明也在注册当场核。
configure_alert_model(lambda column: hasattr(DatasetReleaseQualityAlert, column))
_ALERT_SEVERITIES = frozenset({"warning", "critical"})
_OBSERVATION_SEVERITIES = frozenset({"healthy", "warning", "critical", "unavailable"})
# 校验对象是**库里的行**（见 :517 `str(row.gate_state)`），所以词表等于声明侧的存储集合。
# 改之前这里还并列着 `passed` —— 那条 CHECK 存不进它，是个永不命中的死成员。
_OBSERVATION_GATE_STATES = gate_states_storage()
# 词表只有一份，在 core/quality_alert_types.py：派生规则与它由同一处栅栏对着存储 CHECK 对账。
_ALERT_TYPES = ALERT_TYPES
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMENT_SECRET_RE = re.compile(
    r"(?i)(?:"
    r"password\\s*[=:]|passwd\\s*[=:]|secret\\s*[=:]|credential\\s*[=:]|"
    r"authorization\\s*[:=]|bearer\\s+|access[_ -]?token\\s*[=:]|"
    r"refresh[_ -]?token\\s*[=:]|api[_ -]?key\\s*[=:]|client[_ -]?secret\\s*[=:]|"
    r"idempotency[_ -]?key\\s*[=:]|sk_(?:live|test)[-_]\\S+|"
    r"(?:https?|mysql|mariadb|postgres(?:ql)?|redis|sqlite)://\\S+|"
    r"[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}"
    r")"
)
_COMMENT_FORBIDDEN_MARKERS = frozenset(
    {
        "query",
        "result_body",
        "body",
        "judgment_note",
        "judgement_note",
        "ticket",
        "idempotency_key",
        "credential",
        "password",
        "secret",
        "token",
        "api_key",
    }
)
_CURSOR_KIND = "quality-alerts-v1"


class QualityAlertError(RuntimeError):
    """Base class for safe, HTTP-shaped Alert service failures."""

    code = "quality_alert_error"
    status = 500

    def __init__(self, message: str = "Quality Alert operation failed") -> None:
        super().__init__(message)
        self.message = message


class QualityAlertInvalid(QualityAlertError, ValueError):
    code = "quality_alert_invalid"
    status = 422


class QualityAlertConflict(QualityAlertError):
    code = "quality_alert_conflict"
    status = 409


class QualityAlertRevisionConflict(QualityAlertConflict):
    code = "quality_alert_revision_conflict"


class QualityAlertNotFound(QualityAlertError):
    code = "quality_alert_not_found"
    status = 404


class QualityAlertForbidden(QualityAlertError):
    code = "quality_alert_forbidden"
    status = 403


class QualityAlertUnavailable(QualityAlertError):
    code = "quality_alert_unavailable"
    status = 503


ReleaseQualityAlertError = QualityAlertError
ReleaseQualityAlertInvalid = QualityAlertInvalid
ReleaseQualityAlertConflict = QualityAlertConflict
ReleaseQualityAlertRevisionConflict = QualityAlertRevisionConflict
ReleaseQualityAlertNotFound = QualityAlertNotFound
ReleaseQualityAlertForbidden = QualityAlertForbidden
ReleaseQualityAlertUnavailable = QualityAlertUnavailable


def _clean_key(value: Any, field: str, maximum: int = 128, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise QualityAlertInvalid(f"{field} must be a string")
    result = value.strip()
    if not result and not allow_empty:
        raise QualityAlertInvalid(f"{field} must not be empty")
    if len(result) > maximum or any(ord(character) < 32 for character in result):
        raise QualityAlertInvalid(f"{field} is invalid")
    return result


def _exact_integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise QualityAlertInvalid(f"{field} must be an exact integer >= {minimum}")
    return value


def _safe_comment(value: Any) -> str:
    if not isinstance(value, str):
        raise QualityAlertInvalid("comment must be a string")
    comment = value.strip()
    if len(comment) > 512:
        raise QualityAlertInvalid("comment is too long")
    if any(ord(character) < 32 and character not in "\\t\\n\\r" for character in comment):
        raise QualityAlertInvalid("comment contains unsupported control characters")
    normalized = re.sub(r"[^a-z0-9_]+", "_", comment.casefold())
    if _COMMENT_SECRET_RE.search(comment) or any(
        marker in normalized for marker in _COMMENT_FORBIDDEN_MARKERS
    ):
        raise QualityAlertInvalid("comment contains unsafe evidence")
    return comment


def _validate_persisted_comments(row: DatasetReleaseQualityAlert) -> None:
    for field in ("acknowledged_comment", "resolved_comment", "suppressed_comment"):
        value = getattr(row, field, None)
        if value is None:
            continue
        try:
            _safe_comment(value)
        except QualityAlertError as exc:
            raise QualityAlertUnavailable("quality alert comment is unavailable") from exc


def _utc_datetime(value: Any, field: str, *, required: bool = True) -> datetime | None:
    if value is None and not required:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise QualityAlertInvalid(f"{field} must be an ISO timestamp")
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except (TypeError, ValueError) as exc:
            raise QualityAlertInvalid(f"{field} must be an ISO timestamp") from exc
    else:
        raise QualityAlertInvalid(f"{field} must be a datetime or ISO timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        try:
            parsed = parsed.astimezone(UTC)
        except (TypeError, ValueError) as exc:
            raise QualityAlertInvalid(f"{field} timezone is invalid") from exc
    return parsed.replace(tzinfo=None, fold=0)


def _iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    parsed = _utc_datetime(value, "timestamp")
    assert parsed is not None
    return parsed.isoformat(timespec="microseconds") + "Z"


def _now(value: Any) -> datetime:
    parsed = _utc_datetime(value, "now", required=False)
    return parsed if parsed is not None else datetime.utcnow().replace(microsecond=0)


def canonical_quality_alert_key(
    *,
    dataset_id: str,
    release_id: str,
    channel_id: str,
    release_role: str,
    alert_type: str,
) -> str:
    """Return the exact canonical identity required by the 0031 CHECK."""

    dataset = _clean_key(dataset_id, "dataset_id", 64)
    release = _clean_key(release_id, "release_id", 64)
    channel = _clean_key(channel_id, "channel_id", 128)
    role = _clean_key(release_role, "release_role", 16)
    kind = _clean_key(alert_type, "alert_type", 40)
    if role not in {"active", "pinned"} or kind not in _ALERT_TYPES:
        raise QualityAlertInvalid("alert identity is invalid")
    key = f"{dataset}:{release}:{channel}:{role}:{kind}"
    if len(key) > 384:
        raise QualityAlertInvalid("alert identity is too long")
    return key


canonical_alert_key = canonical_quality_alert_key


def _translate_manifest_error(exc: ReleaseManifestError) -> QualityAlertError:
    if isinstance(exc, ReleaseManifestInvalid):
        return QualityAlertInvalid("quality alert request is invalid")
    if isinstance(exc, ReleaseManifestNotFound):
        return QualityAlertNotFound("quality alert scope was not found")
    if isinstance(exc, ReleaseManifestForbidden):
        return QualityAlertForbidden(str(exc))
    if isinstance(exc, ReleaseManifestUnavailable):
        return QualityAlertUnavailable("quality alert authorization is unavailable")
    return QualityAlertUnavailable("quality alert authorization is unavailable")


def _ensure_capability(connection: Any) -> None:
    state, issues = inspect_enterprise_release_quality_operations_capability(connection)
    if state != "ready":
        raise QualityAlertUnavailable(
            "; ".join(issues) or "quality operations schema is unavailable"
        )


def _actor_scope(session: Session, tenant_id: str, actor_id: str) -> tuple[TenantMember, Account]:
    try:
        return _actor(session, tenant_id, actor_id)
    except ReleaseManifestError as exc:
        raise _translate_manifest_error(exc) from exc


def _dataset_scope(
    engine: Any,
    session: Session,
    membership: TenantMember,
    dataset_id: str,
    *,
    manage: bool,
) -> None:
    dataset = session.scalar(
        select(Dataset).where(
            Dataset.tenant_id == str(membership.tenant_id),
            Dataset.id == dataset_id,
        )
    )
    if dataset is None:
        raise QualityAlertNotFound("quality alert dataset was not found")
    try:
        if manage:
            _require_manage(engine, session, membership, dataset_id)
        else:
            _require_read(engine, session, membership, dataset_id)
    except ReleaseManifestError as exc:
        raise _translate_manifest_error(exc) from exc
    except DatasetAccessControlUnavailable as exc:
        raise QualityAlertUnavailable("quality alert authorization is unavailable") from exc


@contextmanager
def _mutation_scope(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    idempotency_key: str,
    request_hash: str,
    operation: str,
) -> Iterator[tuple[Session, Account, Any]]:
    tenant = _clean_key(tenant_id, "tenant_id", 64)
    actor = _clean_key(actor_id, "actor_id", 64)
    dataset = _clean_key(dataset_id, "dataset_id", 64)
    try:
        key_digest = tenant_idempotency_key_digest(tenant, actor, idempotency_key)
    except TenantMutationIdempotencyValidationError as exc:
        raise QualityAlertInvalid(str(exc)) from exc
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant, actor, key_digest))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        stack.enter_context(session.begin())
        _ensure_capability(session.connection())
        tenant_row = session.scalar(select(Tenant).where(Tenant.id == tenant).with_for_update())
        if tenant_row is None or str(tenant_row.status) != "active":
            raise QualityAlertForbidden("Tenant is unavailable")
        membership, account = _actor_scope(session, tenant, actor)
        _dataset_scope(engine, session, membership, dataset, manage=True)
        try:
            reservation = reserve_tenant_mutation(
                session,
                tenant_id=tenant,
                actor_id=actor,
                raw_idempotency_key=idempotency_key,
                request_hash=request_hash,
                operation=operation,
                resource_type="release_quality_alert",
            )
        except TenantMutationIdempotencyConflict as exc:
            raise QualityAlertConflict("idempotency key conflict") from exc
        except TenantMutationIdempotencyInProgress as exc:
            raise QualityAlertConflict("idempotency request is in progress") from exc
        except TenantMutationIdempotencyValidationError as exc:
            raise QualityAlertInvalid(str(exc)) from exc
        try:
            yield session, account, reservation
        except IntegrityError as exc:
            raise QualityAlertConflict(
                "quality alert mutation violates an authority constraint"
            ) from exc
        except SQLAlchemyError as exc:
            raise QualityAlertUnavailable("quality alert storage is unavailable") from exc


def _complete(
    session: Session,
    reservation: Any,
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
        raise QualityAlertInvalid("quality alert replay payload is invalid") from exc
    return ServiceResult(dict(replay), status)


def _alert_identity(row: DatasetReleaseQualityAlert) -> dict[str, str]:
    return {
        "dataset_id": str(row.dataset_id),
        "release_id": str(row.release_id),
        "channel_id": str(row.channel_id),
        "release_role": str(row.release_role),
        "alert_type": str(row.alert_type),
    }


def _validate_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise QualityAlertUnavailable(f"quality alert {field} is unavailable")
    return value


def _validate_alert_identity(row: DatasetReleaseQualityAlert) -> None:
    status = str(row.status)
    if status not in _ALERT_STATUSES:
        raise QualityAlertUnavailable("quality alert status is unavailable")
    if type(row.revision) is not int or int(row.revision) <= 0:
        raise QualityAlertUnavailable("quality alert revision is unavailable")
    if type(row.occurrence_count) is not int or int(row.occurrence_count) <= 0:
        raise QualityAlertUnavailable("quality alert occurrence count is unavailable")
    _validate_persisted_comments(row)
    try:
        expected_key = canonical_quality_alert_key(**_alert_identity(row))
    except QualityAlertError as exc:
        raise QualityAlertUnavailable("quality alert identity is unavailable") from exc
    if status == "resolved":
        if row.active_alert_key is not None:
            raise QualityAlertUnavailable("resolved quality alert has active identity")
        if row.resolved_at is None or row.resolved_by is None:
            raise QualityAlertUnavailable("resolved quality alert lacks resolution evidence")
        if row.suppressed_until is not None or row.suppressed_by is not None:
            raise QualityAlertUnavailable("resolved quality alert has suppression evidence")
    else:
        if row.active_alert_key != expected_key:
            raise QualityAlertUnavailable("active quality alert identity is unavailable")
        if row.resolved_at is not None or row.resolved_by is not None:
            raise QualityAlertUnavailable("active quality alert has resolution evidence")
    if status == "open":
        if any(
            value is not None
            for value in (
                row.acknowledged_at,
                row.acknowledged_by,
                row.suppressed_until,
                row.suppressed_by,
            )
        ):
            raise QualityAlertUnavailable("open quality alert lifecycle is unavailable")
    elif status == "acknowledged":
        if row.acknowledged_at is None or row.acknowledged_by is None:
            raise QualityAlertUnavailable("acknowledged quality alert lacks evidence")
        if row.suppressed_until is not None or row.suppressed_by is not None:
            raise QualityAlertUnavailable("acknowledged quality alert has suppression evidence")
    elif status == "suppressed":
        if row.suppressed_until is None or row.suppressed_by is None:
            raise QualityAlertUnavailable("suppressed quality alert lacks evidence")
    _validate_digest(row.source_observation_digest, "observation digest")


def _validate_observation_row(
    session: Session,
    observation: DatasetReleaseQualityObservation,
) -> DatasetReleaseQualityObservation:
    if not isinstance(observation, DatasetReleaseQualityObservation):
        raise QualityAlertInvalid("observation must be a DatasetReleaseQualityObservation")
    tenant_id = _clean_key(observation.tenant_id, "observation.tenant_id", 64)
    dataset_id = _clean_key(observation.dataset_id, "observation.dataset_id", 64)
    observation_id = _clean_key(observation.id, "observation.id", 64)
    row = session.scalar(
        select(DatasetReleaseQualityObservation).where(
            DatasetReleaseQualityObservation.tenant_id == tenant_id,
            DatasetReleaseQualityObservation.dataset_id == dataset_id,
            DatasetReleaseQualityObservation.id == observation_id,
        )
    )
    if row is None:
        raise QualityAlertUnavailable("quality alert observation is unavailable")
    for field in (
        "release_id",
        "channel_id",
        "scan_run_id",
        "slo_policy_id",
        "slo_policy_revision",
        "release_role",
        "gate_state",
        "gate_reason",
        "severity",
        "observation_digest",
        "observed_at",
        "observed_by",
        "request_id",
    ):
        expected = getattr(observation, field, None)
        actual = getattr(row, field, None)
        if field == "observed_at":
            expected = _utc_datetime(expected, "observation.observed_at")
            actual = _utc_datetime(actual, "observation.observed_at")
        if expected != actual:
            raise QualityAlertUnavailable("quality alert observation authority is unavailable")
    if str(row.release_role) not in {"active", "pinned"}:
        raise QualityAlertUnavailable("quality alert observation role is unavailable")
    if str(row.gate_state) not in _OBSERVATION_GATE_STATES:
        raise QualityAlertUnavailable("quality alert observation gate is unavailable")
    if str(row.severity) not in _OBSERVATION_SEVERITIES:
        raise QualityAlertUnavailable("quality alert observation severity is unavailable")
    _validate_digest(row.observation_digest, "observation digest")
    if row.observed_at is None:
        raise QualityAlertUnavailable("quality alert observation timestamp is unavailable")
    return row


def _validate_alert_source(
    session: Session,
    alert: DatasetReleaseQualityAlert,
) -> DatasetReleaseQualityObservation:
    _validate_alert_identity(alert)
    observation = session.scalar(
        select(DatasetReleaseQualityObservation).where(
            DatasetReleaseQualityObservation.tenant_id == alert.tenant_id,
            DatasetReleaseQualityObservation.dataset_id == alert.dataset_id,
            DatasetReleaseQualityObservation.id == alert.source_observation_id,
        )
    )
    if observation is None:
        raise QualityAlertUnavailable("quality alert source observation is unavailable")
    if (
        observation.release_id != alert.release_id
        or observation.channel_id != alert.channel_id
        or observation.release_role != alert.release_role
        or observation.observation_digest != alert.source_observation_digest
    ):
        raise QualityAlertUnavailable("quality alert source observation does not match")
    return observation


def _alert_payload(row: DatasetReleaseQualityAlert) -> dict[str, Any]:
    _validate_alert_identity(row)
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "dataset_id": str(row.dataset_id),
        "release_id": str(row.release_id),
        "channel_id": str(row.channel_id),
        "release_role": str(row.release_role),
        "alert_type": str(row.alert_type),
        "severity": str(row.severity),
        "status": str(row.status),
        "active_alert_key": row.active_alert_key,
        "revision": int(row.revision),
        "source_observation_id": str(row.source_observation_id),
        "source_observation_digest": str(row.source_observation_digest),
        "occurrence_count": int(row.occurrence_count),
        "opened_at": _iso_datetime(row.opened_at),
        "last_observed_at": _iso_datetime(row.last_observed_at),
        "acknowledged_at": _iso_datetime(row.acknowledged_at),
        "acknowledged_by": row.acknowledged_by,
        "acknowledged_comment": row.acknowledged_comment,
        "resolved_at": _iso_datetime(row.resolved_at),
        "resolved_by": row.resolved_by,
        "resolved_comment": row.resolved_comment,
        "suppressed_until": _iso_datetime(row.suppressed_until),
        "suppressed_by": row.suppressed_by,
        "suppressed_comment": row.suppressed_comment,
        "created_at": _iso_datetime(row.created_at),
        "updated_at": _iso_datetime(row.updated_at),
    }


def _system_alert_snapshot(
    *,
    alert: DatasetReleaseQualityAlert | None,
    observation: DatasetReleaseQualityObservation,
    alert_type: str | None = None,
) -> dict[str, Any]:
    dataset_id = _clean_key(observation.dataset_id, "observation.dataset_id", 64)
    release_id = _clean_key(observation.release_id, "observation.release_id", 64)
    channel_id = _clean_key(observation.channel_id, "observation.channel_id", 128)
    release_role = _clean_key(observation.release_role, "observation.release_role", 16)
    observation_id = _clean_key(observation.id, "observation.id", 64)
    scan_run_id = _clean_key(observation.scan_run_id, "observation.scan_run_id", 64)
    observation_digest = _validate_digest(observation.observation_digest, "observation digest")

    if alert is None:
        alert_id = None
        resolved_alert_type = _clean_key(alert_type, "alert_type", 40)
        severity = _clean_key(observation.severity, "observation.severity", 16)
        status = None
        revision = 0
        source_observation_id = None
        source_observation_digest = None
    else:
        _validate_alert_identity(alert)
        identity = _alert_identity(alert)
        alert_id = _clean_key(alert.id, "alert.id", 64)
        resolved_alert_type = identity["alert_type"]
        severity = _clean_key(alert.severity, "alert.severity", 16)
        status = str(alert.status)
        revision = int(alert.revision)
        source_observation_id = _clean_key(
            alert.source_observation_id,
            "alert.source_observation_id",
            64,
        )
        source_observation_digest = _validate_digest(
            alert.source_observation_digest,
            "alert source observation digest",
        )

    return {
        "id": alert_id,
        "dataset_id": dataset_id,
        "release_id": release_id,
        "channel_id": channel_id,
        "release_role": release_role,
        "alert_type": resolved_alert_type,
        "severity": severity,
        "status": status,
        "revision": revision,
        "source_observation_id": source_observation_id,
        "source_observation_digest": source_observation_digest,
        "observation_id": observation_id,
        "observation_digest": observation_digest,
        "scan_run_id": scan_run_id,
    }


def _audit(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    account: Account | None,
    action: str,
    alert_id: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    request_id: str,
    request_ip: str,
    now: datetime,
) -> None:
    if actor_id == SYSTEM_QUALITY_SCANNER:
        actor_name_snapshot = _SYSTEM_QUALITY_SCANNER_NAME
        actor_email_snapshot = _SYSTEM_QUALITY_SCANNER_EMAIL
    elif account is not None:
        actor_name_snapshot = str(account.name)[:128]
        actor_email_snapshot = str(account.email)[:256]
    else:
        raise QualityAlertUnavailable("quality alert audit actor is unavailable")
    session.add(
        TenantAuditEvent(
            id=f"tenant-audit-{uuid.uuid4().hex}",
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_name_snapshot=actor_name_snapshot,
            actor_email_snapshot=actor_email_snapshot,
            action=action,
            resource_type="release_quality_alert",
            resource_id=alert_id,
            before_snapshot=sanitize_audit_snapshot(dict(before)),
            after_snapshot=sanitize_audit_snapshot(dict(after)),
            request_id=request_id,
            request_ip=request_ip,
            occurred_at=now,
        )
    )


def _request_metadata(*, request_id: Any, request_ip: Any) -> tuple[str, str]:
    return (
        _clean_key(request_id, "request_id", 128, allow_empty=True),
        _clean_key(request_ip, "request_ip", 64, allow_empty=True),
    )


def _mutate_alert(
    engine: Any,
    *,
    operation: str,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    alert_id: str,
    expected_revision: int,
    comment: str,
    request_id: str,
    request_ip: str,
    idempotency_key: str,
    now: Any,
    suppressed_until: Any = None,
) -> ServiceResult:
    tenant = _clean_key(tenant_id, "tenant_id", 64)
    actor = _clean_key(actor_id, "actor_id", 64)
    dataset = _clean_key(dataset_id, "dataset_id", 64)
    alert_key = _clean_key(alert_id, "alert_id", 64)
    expected = _exact_integer(expected_revision, "expected_revision", minimum=1)
    safe_comment = _safe_comment(comment)
    request, ip = _request_metadata(request_id=request_id, request_ip=request_ip)
    timestamp = _now(now)
    spec = resolve_quality_alert_operation(operation)
    if spec is None:
        raise QualityAlertInvalid("quality alert operation is invalid")
    until: datetime | None = None
    if spec.sets_bound is not None:
        until = _utc_datetime(suppressed_until, "suppressed_until")
        assert until is not None
        if until <= timestamp:
            raise QualityAlertInvalid("suppressed_until must be in the future")
    request_body: dict[str, Any] = {"expected_revision": expected, "comment": safe_comment}
    if until is not None:
        request_body["suppressed_until"] = _iso_datetime(until)
    try:
        request_hash = tenant_request_hash(
            operation=operation,
            path_identity={"dataset_id": dataset, "alert_id": alert_key},
            body=request_body,
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise QualityAlertInvalid("quality alert request cannot be canonicalized") from exc

    with _mutation_scope(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        dataset_id=dataset,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        operation=operation,
    ) as (session, account, reservation):
        if reservation.replay is not None:
            return ServiceResult(dict(reservation.replay.response), reservation.replay.http_status)
        alert = session.scalar(
            select(DatasetReleaseQualityAlert)
            .where(
                DatasetReleaseQualityAlert.tenant_id == tenant,
                DatasetReleaseQualityAlert.dataset_id == dataset,
                DatasetReleaseQualityAlert.id == alert_key,
            )
            .with_for_update()
        )
        if alert is None:
            raise QualityAlertNotFound("quality alert was not found")
        _validate_alert_source(session, alert)
        if int(alert.revision) != expected:
            raise QualityAlertRevisionConflict("quality alert revision fence rejected")
        status = str(alert.status)
        before = _alert_payload(alert)
        if spec.terminal_conflict_message is not None and status in TERMINAL_ALERT_STATUSES:
            raise QualityAlertConflict(spec.terminal_conflict_message)
        if status not in spec.allowed_from:
            raise QualityAlertConflict(spec.conflict_message)
        apply_operation(
            spec,
            alert,
            actor=actor,
            timestamp=timestamp,
            comment=safe_comment or None,
            bound=until,
        )
        message = spec.response_message
        action = spec.audit_action
        alert.revision = int(alert.revision) + 1
        alert.updated_at = timestamp
        session.flush()
        _validate_alert_source(session, alert)
        after = _alert_payload(alert)
        _audit(
            session,
            tenant_id=tenant,
            actor_id=actor,
            account=account,
            action=action,
            alert_id=alert_key,
            before=before,
            after=after,
            request_id=request,
            request_ip=ip,
            now=timestamp,
        )
        payload = {
            "state": "applied",
            "operation": operation,
            "resource_id": alert_key,
            "message": message,
            "retryable": False,
            "alert": after,
        }
        return _complete(session, reservation, payload, 200, alert_key)


def acknowledge_quality_alert(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    alert_id: str,
    expected_revision: int,
    comment: str = "",
    request_id: str = "",
    request_ip: str = "",
    idempotency_key: str = "",
    now: Any = None,
) -> ServiceResult:
    return _mutate_alert(
        engine,
        operation="acknowledge_quality_alert",
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        alert_id=alert_id,
        expected_revision=expected_revision,
        comment=comment,
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        now=now,
    )


def resolve_quality_alert(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    alert_id: str,
    expected_revision: int,
    comment: str = "",
    request_id: str = "",
    request_ip: str = "",
    idempotency_key: str = "",
    now: Any = None,
) -> ServiceResult:
    return _mutate_alert(
        engine,
        operation="resolve_quality_alert",
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        alert_id=alert_id,
        expected_revision=expected_revision,
        comment=comment,
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        now=now,
    )


def suppress_quality_alert(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    alert_id: str,
    expected_revision: int,
    suppressed_until: Any,
    comment: str = "",
    request_id: str = "",
    request_ip: str = "",
    idempotency_key: str = "",
    now: Any = None,
) -> ServiceResult:
    return _mutate_alert(
        engine,
        operation="suppress_quality_alert",
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        alert_id=alert_id,
        expected_revision=expected_revision,
        comment=comment,
        request_id=request_id,
        request_ip=request_ip,
        idempotency_key=idempotency_key,
        now=now,
        suppressed_until=suppressed_until,
    )


def _decode_cursor(
    cursor: str | None, *, tenant_id: str, dataset_id: str
) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    value = _clean_key(cursor, "cursor", 2048)
    padding = "=" * (-len(value) % 4)
    try:
        decoded = json.loads(urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8"))
    except (UnicodeError, ValueError, json.JSONDecodeError, binascii.Error) as exc:
        raise QualityAlertInvalid("cursor is invalid") from exc
    if not isinstance(decoded, Mapping):
        raise QualityAlertInvalid("cursor is invalid")
    if decoded.get("kind") != _CURSOR_KIND:
        raise QualityAlertInvalid("cursor is invalid")
    if decoded.get("tenant_id") != tenant_id or decoded.get("dataset_id") != dataset_id:
        raise QualityAlertInvalid("cursor scope is invalid")
    timestamp = _utc_datetime(decoded.get("last_observed_at"), "cursor.last_observed_at")
    item_id = _clean_key(decoded.get("id"), "cursor.id", 64)
    assert timestamp is not None
    return timestamp, item_id


def _encode_cursor(*, tenant_id: str, dataset_id: str, row: DatasetReleaseQualityAlert) -> str:
    value = {
        "kind": _CURSOR_KIND,
        "tenant_id": tenant_id,
        "dataset_id": dataset_id,
        "last_observed_at": _iso_datetime(row.last_observed_at),
        "id": str(row.id),
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _read_scope(
    engine: Any,
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
) -> tuple[str, str, TenantMember, Account]:
    tenant = _clean_key(tenant_id, "tenant_id", 64)
    actor = _clean_key(actor_id, "actor_id", 64)
    dataset = _clean_key(dataset_id, "dataset_id", 64)
    _ensure_capability(session.connection())
    tenant_row = session.scalar(select(Tenant).where(Tenant.id == tenant))
    if tenant_row is None or str(tenant_row.status) != "active":
        raise QualityAlertForbidden("Tenant is unavailable")
    membership, account = _actor_scope(session, tenant, actor)
    _dataset_scope(engine, session, membership, dataset, manage=False)
    return tenant, dataset, membership, account


def _alert_filters(
    statement: Any,
    *,
    status: str | None,
    severity: str | None,
    alert_type: str | None,
) -> Any:
    if status is not None:
        statement = statement.where(DatasetReleaseQualityAlert.status == status)
    if severity is not None:
        statement = statement.where(DatasetReleaseQualityAlert.severity == severity)
    if alert_type is not None:
        statement = statement.where(DatasetReleaseQualityAlert.alert_type == alert_type)
    return statement


def list_quality_alerts(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    status: str | None = None,
    severity: str | None = None,
    alert_type: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ServiceResult:
    page_size = _exact_integer(limit, "limit", minimum=1)
    if page_size > 200:
        raise QualityAlertInvalid("limit must be <= 200")
    status_filter = None if status is None else _clean_key(status, "status", 16)
    severity_filter = None if severity is None else _clean_key(severity, "severity", 16)
    type_filter = None if alert_type is None else _clean_key(alert_type, "alert_type", 40)
    if status_filter is not None and status_filter not in _ALERT_STATUSES:
        raise QualityAlertInvalid("status is invalid")
    if severity_filter is not None and severity_filter not in _ALERT_SEVERITIES:
        raise QualityAlertInvalid("severity is invalid")
    if type_filter is not None and type_filter not in _ALERT_TYPES:
        raise QualityAlertInvalid("alert_type is invalid")
    with Session(engine) as session:
        tenant, dataset, _membership, _account = _read_scope(
            engine,
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            dataset_id=dataset_id,
        )
        decoded = _decode_cursor(cursor, tenant_id=tenant, dataset_id=dataset)
        base = {
            "tenant_id": tenant,
            "dataset_id": dataset,
        }
        statement = _alert_filters(
            select(DatasetReleaseQualityAlert).where(
                DatasetReleaseQualityAlert.tenant_id == base["tenant_id"],
                DatasetReleaseQualityAlert.dataset_id == base["dataset_id"],
            ),
            status=status_filter,
            severity=severity_filter,
            alert_type=type_filter,
        )
        count_statement = _alert_filters(
            select(func.count())
            .select_from(DatasetReleaseQualityAlert)
            .where(
                DatasetReleaseQualityAlert.tenant_id == base["tenant_id"],
                DatasetReleaseQualityAlert.dataset_id == base["dataset_id"],
            ),
            status=status_filter,
            severity=severity_filter,
            alert_type=type_filter,
        )
        total = int(session.scalar(count_statement) or 0)
        if decoded is not None:
            last_time, last_id = decoded
            statement = statement.where(
                or_(
                    DatasetReleaseQualityAlert.last_observed_at < last_time,
                    (DatasetReleaseQualityAlert.last_observed_at == last_time)
                    & (DatasetReleaseQualityAlert.id < last_id),
                )
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    DatasetReleaseQualityAlert.last_observed_at.desc(),
                    DatasetReleaseQualityAlert.id.desc(),
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        items: list[dict[str, Any]] = []
        for row in rows:
            _validate_alert_source(session, row)
            items.append(_alert_payload(row))
        next_cursor = (
            _encode_cursor(tenant_id=tenant, dataset_id=dataset, row=rows[-1])
            if has_more and rows
            else None
        )
        return ServiceResult({"items": items, "count": total, "next_cursor": next_cursor})


def get_quality_alert(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    alert_id: str,
) -> ServiceResult:
    tenant = _clean_key(tenant_id, "tenant_id", 64)
    actor = _clean_key(actor_id, "actor_id", 64)
    dataset = _clean_key(dataset_id, "dataset_id", 64)
    alert_key = _clean_key(alert_id, "alert_id", 64)
    with Session(engine) as session:
        _read_scope(
            engine,
            session,
            tenant_id=tenant,
            actor_id=actor,
            dataset_id=dataset,
        )
        row = session.scalar(
            select(DatasetReleaseQualityAlert).where(
                DatasetReleaseQualityAlert.tenant_id == tenant,
                DatasetReleaseQualityAlert.dataset_id == dataset,
                DatasetReleaseQualityAlert.id == alert_key,
            )
        )
        if row is None:
            raise QualityAlertNotFound("quality alert was not found")
        _validate_alert_source(session, row)
        return ServiceResult({"alert": _alert_payload(row)})


def _derive_alert_type(observation: DatasetReleaseQualityObservation) -> str:
    return derive_alert_type(observation)


def _observation_alert_severity(observation: DatasetReleaseQualityObservation) -> str:
    severity = str(observation.severity)
    if severity == "unavailable":
        return "critical"
    if severity not in _ALERT_SEVERITIES:
        raise QualityAlertUnavailable("quality alert observation cannot create an alert")
    return severity


def create_or_update_quality_alert_from_observation(
    session: Session,
    observation: DatasetReleaseQualityObservation,
    *,
    alert_type: str | None = None,
    now: Any = None,
    expected_revision: int | None = None,
) -> DatasetReleaseQualityAlert | None:
    """Create or advance an active Alert from one immutable Observation."""

    row_observation = _validate_observation_row(session, observation)
    timestamp = _now(now)
    expected = (
        None
        if expected_revision is None
        else _exact_integer(expected_revision, "expected_revision", minimum=0)
    )
    if str(row_observation.severity) == "healthy":
        return None
    kind = (
        _derive_alert_type(row_observation)
        if alert_type is None
        else _clean_key(alert_type, "alert_type", 40)
    )
    if kind not in _ALERT_TYPES:
        raise QualityAlertInvalid("alert_type is invalid")
    severity = _observation_alert_severity(row_observation)
    active_key = canonical_quality_alert_key(
        dataset_id=row_observation.dataset_id,
        release_id=row_observation.release_id,
        channel_id=row_observation.channel_id,
        release_role=row_observation.release_role,
        alert_type=kind,
    )
    alert = session.scalar(
        select(DatasetReleaseQualityAlert)
        .where(
            DatasetReleaseQualityAlert.tenant_id == row_observation.tenant_id,
            DatasetReleaseQualityAlert.dataset_id == row_observation.dataset_id,
            DatasetReleaseQualityAlert.active_alert_key == active_key,
        )
        .with_for_update()
    )
    if alert is None:
        if expected not in {None, 0}:
            raise QualityAlertRevisionConflict("new quality alert has no matching revision")
        observed_at = _utc_datetime(row_observation.observed_at, "observation.observed_at")
        assert observed_at is not None
        before = _system_alert_snapshot(
            alert=None,
            observation=row_observation,
            alert_type=kind,
        )
        alert = DatasetReleaseQualityAlert(
            id=f"quality-alert-{uuid.uuid4().hex}",
            tenant_id=row_observation.tenant_id,
            dataset_id=row_observation.dataset_id,
            release_id=row_observation.release_id,
            channel_id=row_observation.channel_id,
            release_role=row_observation.release_role,
            alert_type=kind,
            severity=severity,
            status="open",
            active_alert_key=active_key,
            revision=1,
            source_observation_id=row_observation.id,
            source_observation_digest=row_observation.observation_digest,
            occurrence_count=1,
            opened_at=observed_at,
            last_observed_at=observed_at,
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(alert)
        session.flush()
        _validate_alert_source(session, alert)
        after = _system_alert_snapshot(alert=alert, observation=row_observation)
        _audit(
            session,
            tenant_id=row_observation.tenant_id,
            actor_id=SYSTEM_QUALITY_SCANNER,
            account=None,
            action="knowledge_base.release_quality.alert_created",
            alert_id=alert.id,
            before=before,
            after=after,
            request_id="",
            request_ip="",
            now=timestamp,
        )
        return alert

    source_observation = _validate_alert_source(session, alert)
    before = _system_alert_snapshot(alert=alert, observation=source_observation)
    if expected is not None and int(alert.revision) != expected:
        raise QualityAlertRevisionConflict("quality alert revision fence rejected")
    previous_observed = _utc_datetime(alert.last_observed_at, "alert.last_observed_at")
    current_observed = _utc_datetime(row_observation.observed_at, "observation.observed_at")
    assert previous_observed is not None and current_observed is not None
    if current_observed < previous_observed:
        raise QualityAlertConflict("quality alert observation is older than the active cycle")
    if str(alert.status) == "resolved":
        raise QualityAlertUnavailable("resolved quality alert has an active identity")
    if str(alert.status) == "suppressed" and alert.suppressed_until is not None:
        suppression_until = _utc_datetime(alert.suppressed_until, "alert.suppressed_until")
        if suppression_until is not None and suppression_until <= timestamp:
            alert.status = "open"
            alert.acknowledged_at = None
            alert.acknowledged_by = None
            alert.acknowledged_comment = None
            alert.suppressed_until = None
            alert.suppressed_by = None
            alert.suppressed_comment = None
    alert.severity = severity
    alert.source_observation_id = row_observation.id
    alert.source_observation_digest = row_observation.observation_digest
    alert.occurrence_count = int(alert.occurrence_count) + 1
    alert.last_observed_at = current_observed
    alert.revision = int(alert.revision) + 1
    alert.updated_at = timestamp
    session.flush()
    _validate_alert_source(session, alert)
    after = _system_alert_snapshot(alert=alert, observation=row_observation)
    _audit(
        session,
        tenant_id=row_observation.tenant_id,
        actor_id=SYSTEM_QUALITY_SCANNER,
        account=None,
        action="knowledge_base.release_quality.alert_updated",
        alert_id=alert.id,
        before=before,
        after=after,
        request_id="",
        request_ip="",
        now=timestamp,
    )
    return alert


def resolve_matching_quality_alert_from_observation(
    session: Session,
    observation: DatasetReleaseQualityObservation,
    *,
    resolved_by: str = SYSTEM_QUALITY_SCANNER,
    now: Any = None,
    expected_revision: int | None = None,
) -> list[DatasetReleaseQualityAlert]:
    """Resolve all active Alerts matching a healthy immutable Observation."""

    row_observation = _validate_observation_row(session, observation)
    if str(row_observation.severity) != "healthy":
        raise QualityAlertInvalid("only healthy observations can resolve quality alerts")
    scanner = _clean_key(resolved_by, "resolved_by", 64)
    timestamp = _now(now)
    expected = (
        None
        if expected_revision is None
        else _exact_integer(expected_revision, "expected_revision", minimum=1)
    )
    rows = list(
        session.scalars(
            select(DatasetReleaseQualityAlert)
            .where(
                DatasetReleaseQualityAlert.tenant_id == row_observation.tenant_id,
                DatasetReleaseQualityAlert.dataset_id == row_observation.dataset_id,
                DatasetReleaseQualityAlert.release_id == row_observation.release_id,
                DatasetReleaseQualityAlert.channel_id == row_observation.channel_id,
                DatasetReleaseQualityAlert.release_role == row_observation.release_role,
                DatasetReleaseQualityAlert.status.in_(_ACTIVE_ALERT_STATUSES),
            )
            .order_by(DatasetReleaseQualityAlert.id)
            .with_for_update()
        )
    )
    if expected is not None and (len(rows) != 1 or int(rows[0].revision) != expected):
        raise QualityAlertRevisionConflict("quality alert revision fence rejected")
    transitions: list[tuple[DatasetReleaseQualityAlert, dict[str, Any]]] = []
    for alert in rows:
        source_observation = _validate_alert_source(session, alert)
        transitions.append(
            (
                alert,
                _system_alert_snapshot(alert=alert, observation=source_observation),
            )
        )
        alert.status = "resolved"
        alert.active_alert_key = None
        alert.resolved_at = timestamp
        alert.resolved_by = scanner
        alert.resolved_comment = None
        alert.suppressed_until = None
        alert.suppressed_by = None
        alert.suppressed_comment = None
        alert.revision = int(alert.revision) + 1
        alert.updated_at = timestamp
    if rows:
        session.flush()
        for alert, before in transitions:
            _validate_alert_source(session, alert)
            after = _system_alert_snapshot(alert=alert, observation=row_observation)
            _audit(
                session,
                tenant_id=row_observation.tenant_id,
                actor_id=SYSTEM_QUALITY_SCANNER,
                account=None,
                action="knowledge_base.release_quality.alert_resolved",
                alert_id=alert.id,
                before=before,
                after=after,
                request_id="",
                request_ip="",
                now=timestamp,
            )
    return rows


# Explicit scan-service names; operator lifecycle remains the public mutation API.
create_or_update_quality_alert = create_or_update_quality_alert_from_observation
resolve_matching_quality_alert = resolve_matching_quality_alert_from_observation
_create_or_update_quality_alert = create_or_update_quality_alert_from_observation
_create_or_update_matching_quality_alert = create_or_update_quality_alert_from_observation
_resolve_matching_quality_alert = resolve_matching_quality_alert_from_observation
_resolve_matching_quality_alerts = resolve_matching_quality_alert_from_observation
list_release_quality_alerts = list_quality_alerts
get_release_quality_alert = get_quality_alert


__all__ = [
    "QualityAlertConflict",
    "QualityAlertError",
    "QualityAlertForbidden",
    "QualityAlertInvalid",
    "QualityAlertNotFound",
    "QualityAlertRevisionConflict",
    "QualityAlertUnavailable",
    "ReleaseQualityAlertConflict",
    "ReleaseQualityAlertError",
    "ReleaseQualityAlertForbidden",
    "ReleaseQualityAlertInvalid",
    "ReleaseQualityAlertNotFound",
    "ReleaseQualityAlertRevisionConflict",
    "ReleaseQualityAlertUnavailable",
    "SYSTEM_QUALITY_SCANNER",
    "acknowledge_quality_alert",
    "canonical_alert_key",
    "canonical_quality_alert_key",
    "create_or_update_quality_alert",
    "create_or_update_quality_alert_from_observation",
    "get_quality_alert",
    "get_release_quality_alert",
    "list_quality_alerts",
    "list_release_quality_alerts",
    "resolve_matching_quality_alert",
    "resolve_matching_quality_alert_from_observation",
    "resolve_quality_alert",
    "suppress_quality_alert",
]
