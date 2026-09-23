"""Tenant-safe Stage 24 Enterprise Task Operations service.

The service is deliberately a read-model authority only. The seven source
adapters read existing source tables and return canonical, body-free facts.
Reconciliation writes only the five Stage 24 tables plus the shared Tenant
mutation idempotency ledger; retry/cancel requests are recorded as safe
requests and never execute a source queue or worker operation here.
"""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Any
import uuid

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from core.enterprise_acl_idempotency import engine_serialization_lock, idempotency_key_lock
from core.enterprise_task_operations import (
    TASK_EVENT_TYPES,
    TASK_NORMALIZED_STATUSES,
    TASK_SOURCE_KINDS,
    canonical_task_digest,
    canonical_task_event,
    canonical_task_projection_digest,
    canonical_task_saved_view,
    canonical_task_source,
    canonical_task_source_digest,
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
from models.orm import (
    Account,
    DatasetReleaseRecertificationJob,
    Document,
    DocumentDeleteOperation,
    DocumentIngestAttempt,
    IndexOperation,
    SourceSyncRun,
    TenantAuditExportJob,
    TenantMember,
    TenantReleaseQualityScanRun,
    TenantTaskEvent,
    TenantTaskOperatorAction,
    TenantTaskProjection,
    TenantTaskReconciliationRun,
    TenantTaskSavedView,
)

UTC = timezone.utc
# 顺序不再是这里的一份手写 tuple。它与文件末尾的 SOURCE_ADAPTER_REGISTRY 平行维护过，
# 而 _validate_source_kinds 既拿它当上限又拿它做过滤器 —— 加第 8 种来源时 API 层放行、
# 服务层回 422。派生定义放在注册表之后。

SourceAdapter = Callable[..., Iterable[Mapping[str, Any]]]
ActionAdapter = Callable[..., bool]


class EnterpriseTaskOperationsError(RuntimeError):
    """Base class for safe service errors exposed by the HTTP boundary."""

    code = "enterprise_task_operations_error"
    status = 500

    def __init__(self, message: str = "Enterprise Task Operations failed") -> None:
        super().__init__(message)
        self.message = message


class EnterpriseTaskOperationsUnavailable(EnterpriseTaskOperationsError):
    code = "enterprise_task_operations_unavailable"
    status = 503

    def __init__(
        self, message: str = "Enterprise Task Operations authority is unavailable"
    ) -> None:
        super().__init__(message)


class EnterpriseTaskOperationsInvalid(EnterpriseTaskOperationsError):
    code = "enterprise_task_operations_invalid"
    status = 422


class EnterpriseTaskOperationsNotFound(EnterpriseTaskOperationsError):
    code = "enterprise_task_operations_not_found"
    status = 404


class EnterpriseTaskOperationsConflict(EnterpriseTaskOperationsError):
    code = "enterprise_task_operations_conflict"
    status = 409


class EnterpriseTaskOperationsBlocked(EnterpriseTaskOperationsError):
    code = "enterprise_task_operations_blocked"
    status = 409


TaskOperationsServiceError = EnterpriseTaskOperationsError
TaskOperationsUnavailable = EnterpriseTaskOperationsUnavailable
TaskOperationsInvalid = EnterpriseTaskOperationsInvalid
TaskOperationsNotFound = EnterpriseTaskOperationsNotFound
TaskOperationsConflict = EnterpriseTaskOperationsConflict
TaskOperationsBlocked = EnterpriseTaskOperationsBlocked


@dataclass(frozen=True)
class ServiceResult:
    body: dict[str, Any]
    status: int = 200


def _request_only_adapter(**_: Any) -> bool:
    return True


ACTION_ADAPTER_REGISTRY: dict[tuple[str, str], ActionAdapter | None] = {
    ("source_sync", "retry"): _request_only_adapter,
    ("release_recertification", "cancel"): _request_only_adapter,
    ("audit_export", "cancel"): None,
}

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_URL_RE = re.compile(r"(?i)(?:[a-z][a-z0-9+.-]{1,31}://|(?:^|\s)www\.)\S+")
_SECRET_RE = re.compile(
    r"(?i)(?:password|passwd|secret|credential|authorization|bearer|access[_ -]?token|"
    r"refresh[_ -]?token|api[_ -]?key|client[_ -]?secret|idempotency[_ -]?key|ticket|token)"
    r"\s*[:=]\s*\S+"
)
_CURSOR_MAX = 2048
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200
_ACTION_EXPIRY = timedelta(hours=24)

_PUBLIC_CATEGORY_BY_SOURCE_KIND = {
    "document_ingest": "documents",
    "index_operation": "indexing",
    "source_sync": "sources",
    "document_delete": "documents",
    "audit_export": "compliance",
    "release_quality_scan": "quality",
    "release_recertification": "quality",
}
_STORAGE_CATEGORY_BY_SOURCE_KIND = {
    "document_ingest": "documents",
    "index_operation": "documents",
    "source_sync": "sources",
    "document_delete": "documents",
    "audit_export": "compliance",
    "release_quality_scan": "quality",
    "release_recertification": "quality",
}
_PUBLIC_ROUTE_BY_SOURCE_KIND = {
    "document_ingest": "document_operations",
    "index_operation": "index_operations",
    "source_sync": "source_control",
    "document_delete": "document_deletion",
    "audit_export": "audit_compliance",
    "release_quality_scan": "release_quality",
    "release_recertification": "release_quality",
}
_STORAGE_ROUTE_BY_SOURCE_KIND = {
    "document_ingest": "documents",
    "index_operation": "documents",
    "source_sync": "sources",
    "document_delete": "documents",
    "audit_export": "compliance",
    "release_quality_scan": "quality",
    "release_recertification": "quality",
}
_STATUS_ALIASES = {
    "queued": "queued",
    "pending": "queued",
    "created": "queued",
    "waiting": "queued",
    "scheduled": "queued",
    "running": "running",
    "in_progress": "running",
    "processing": "running",
    "claimed": "running",
    "started": "running",
    "primary_ready": "running",
    "finalizing": "running",
    "succeeded": "succeeded",
    "success": "succeeded",
    "completed": "succeeded",
    "complete": "succeeded",
    "done": "succeeded",
    "failed": "failed",
    "failure": "failed",
    "error": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "aborted": "cancelled",
    "blocked": "blocked",
    "paused": "blocked",
    "awaiting_evidence": "blocked",
    "ready_to_certify": "blocked",
    "unavailable": "unavailable",
    "unknown": "unavailable",
    "stale": "unavailable",
    "expired": "unavailable",
    "rejected": "blocked",
    "superseded": "unavailable",
}


@dataclass(frozen=True)
class _CollectedSources:
    sources: dict[tuple[str, str], dict[str, Any]]
    invalid_count: int
    adapter_error: str | None


# Validation and time helpers


def _id(value: Any, field: str, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise EnterpriseTaskOperationsInvalid(f"{field} is invalid")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or not _ID_RE.fullmatch(normalized)
        or ".." in normalized
    ):
        raise EnterpriseTaskOperationsInvalid(f"{field} is invalid")
    return normalized


def _safe_text(value: Any, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise EnterpriseTaskOperationsInvalid(f"{field} is invalid")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or _CONTROL_RE.search(normalized)
        or _URL_RE.search(normalized)
        or _SECRET_RE.search(normalized)
    ):
        raise EnterpriseTaskOperationsInvalid(f"{field} is invalid")
    return normalized


def _code(value: Any, field: str, maximum: int = 64) -> str:
    if not isinstance(value, str):
        raise EnterpriseTaskOperationsInvalid(f"{field} is invalid")
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    if not normalized or len(normalized) > maximum or not _CODE_RE.fullmatch(normalized):
        raise EnterpriseTaskOperationsInvalid(f"{field} is invalid")
    return normalized


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value.strip().casefold()):
        raise EnterpriseTaskOperationsInvalid(f"{field} is invalid")
    return value.strip().casefold()


def _exact_int(value: Any, field: str, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise EnterpriseTaskOperationsInvalid(f"{field} is invalid")
    return value


def _exact_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise EnterpriseTaskOperationsInvalid(f"{field} is invalid")
    return value


def _now(value: Any = None) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(tzinfo=None)
    if not isinstance(value, datetime):
        raise EnterpriseTaskOperationsInvalid("now is invalid")
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _as_datetime(value: Any, field: str = "timestamp") -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value
    if not isinstance(value, str):
        raise EnterpriseTaskOperationsInvalid(f"{field} is invalid")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise EnterpriseTaskOperationsInvalid(f"{field} is invalid") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.replace(tzinfo=UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize_kind(value: Any) -> str:
    if not isinstance(value, str) or value not in TASK_SOURCE_KINDS:
        raise EnterpriseTaskOperationsInvalid("source_kind is invalid")
    return value


def _normalize_status(value: Any) -> str:
    if not isinstance(value, str):
        return "unavailable"
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    return _STATUS_ALIASES.get(normalized, "unavailable")


def _normalize_action(value: Any) -> str:
    if not isinstance(value, str) or value.strip().casefold() not in {
        "retry",
        "cancel",
        "acknowledge",
    }:
        raise EnterpriseTaskOperationsInvalid("action_type is invalid")
    return value.strip().casefold()


def _safe_error_pair(code_value: Any, message_value: Any) -> tuple[str | None, str | None]:
    if code_value in (None, "") and message_value in (None, ""):
        return None, None
    if isinstance(code_value, str):
        normalized_code = code_value.strip().casefold().replace("-", "_").replace(" ", "_")
    else:
        normalized_code = "source_error"
    if not _CODE_RE.fullmatch(normalized_code or ""):
        normalized_code = "source_error"
    candidate = (
        message_value.strip()
        if isinstance(message_value, str)
        else "source task reported a safe failure"
    )
    if (
        not candidate
        or len(candidate) > 512
        or _CONTROL_RE.search(candidate)
        or _URL_RE.search(candidate)
        or _SECRET_RE.search(candidate)
    ):
        candidate = "source task reported a safe failure"
    return normalized_code, candidate


def _percent(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= number <= 1:
        number *= 100
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return max(0, min(100, int(round(number))))


def _stable(prefix: str, *parts: str) -> str:
    encoded = "\x00".join(parts).encode("utf-8")
    return prefix + sha256(encoded).hexdigest()[: max(1, 64 - len(prefix))]


def _task_id(tenant_id: str, source_kind: str, source_id: str) -> str:
    return _stable("task-", tenant_id, source_kind, source_id)


def _action_id(tenant_id: str, actor_id: str, key: str) -> str:
    return _stable("task-action-", tenant_id, actor_id, key)


def _run_id(tenant_id: str, actor_id: str, request_id: str) -> str:
    return _stable("task-run-", tenant_id, actor_id, request_id, uuid.uuid4().hex)


def _view_id(tenant_id: str, actor_id: str, key: str) -> str:
    return _stable("task-view-", tenant_id, actor_id, key, uuid.uuid4().hex)


def _request_id(value: Any, fallback: str) -> str:
    if value is None or value == "":
        return fallback
    return _id(value, "request_id")


def _validate_actor_account(account_id: Any, actor_id: str) -> None:
    if account_id is not None and _id(account_id, "account_id", 64) != actor_id:
        raise EnterpriseTaskOperationsInvalid("account_id must match actor_id")


def _validate_limit(value: Any) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_LIMIT:
        raise EnterpriseTaskOperationsInvalid("limit is invalid")
    return value


def _validate_source_kinds(value: Any) -> tuple[str, ...]:
    # 上限与过滤都活读注册表：读那份手写顺序的快照会出现「注册了新适配器但只生效一半」，
    # 而 F1 的实际症状更糟 —— 第 8 种来源先被上限拒掉。
    adapters = SOURCE_ADAPTER_REGISTRY
    if value is None:
        return tuple(adapters)
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise EnterpriseTaskOperationsInvalid("source_kinds is invalid")
    if not 1 <= len(value) <= len(adapters):
        raise EnterpriseTaskOperationsInvalid("source_kinds is invalid")
    normalized = tuple(_normalize_kind(item) for item in value)
    if len(set(normalized)) != len(normalized):
        raise EnterpriseTaskOperationsInvalid("source_kinds must not contain duplicates")
    selected = set(normalized)
    return tuple(kind for kind in adapters if kind in selected)


def _normalize_filter_category(value: Any) -> str:
    if not isinstance(value, str):
        raise EnterpriseTaskOperationsInvalid("filters.categories is invalid")
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    normalized = {
        "document": "documents",
        "content": "documents",
        "ingest": "documents",
        "index": "indexing",
        "source": "sources",
        "audit": "compliance",
        "release_quality": "quality",
    }.get(normalized, normalized)
    if normalized not in {"documents", "indexing", "sources", "compliance", "quality"}:
        raise EnterpriseTaskOperationsInvalid("filters.categories is invalid")
    return normalized


def _cursor_encode(kind: str, values: Mapping[str, Any]) -> str:
    payload = {"kind": kind, **dict(values)}
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    token = urlsafe_b64encode(encoded).decode("ascii").rstrip("=")
    if len(token) > _CURSOR_MAX:
        raise EnterpriseTaskOperationsInvalid("cursor is invalid")
    return token


def _cursor_decode(value: Any, expected_kind: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > _CURSOR_MAX:
        raise EnterpriseTaskOperationsInvalid("cursor is invalid")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise EnterpriseTaskOperationsInvalid("cursor is invalid") from exc
    if not isinstance(decoded, dict) or decoded.get("kind") != expected_kind:
        raise EnterpriseTaskOperationsInvalid("cursor is invalid")
    return decoded


# Explicit source adapter implementations


def _source_fact(
    *,
    tenant_id: str,
    source_kind: str,
    source_id: str,
    source_revision: int,
    dataset_id: str | None,
    workspace_id: str | None,
    normalized_status: str,
    action_required: bool,
    progress_percent: int | None,
    attempt_number: int,
    max_attempts: int,
    lease_owner: str | None = None,
    lease_until: datetime | None = None,
    safe_error_code: str | None = None,
    safe_error: str | None = None,
    occurred_at: datetime | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    updated_at: datetime | None = None,
    safe_facts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    attempt = max(0, int(attempt_number))
    maximum = max(1, int(max_attempts), attempt)
    source: dict[str, Any] = {
        "tenant_id": tenant_id,
        "source_kind": source_kind,
        "source_id": source_id,
        "source_revision": max(1, int(source_revision)),
        "dataset_id": dataset_id,
        "workspace_id": workspace_id,
        "category": _PUBLIC_CATEGORY_BY_SOURCE_KIND[source_kind],
        "normalized_status": _normalize_status(normalized_status),
        "action_required": bool(action_required),
        "progress_percent": progress_percent,
        "attempt_number": attempt,
        "max_attempts": maximum,
        "safe_error_code": safe_error_code,
        "safe_error": safe_error,
        "occurred_at": occurred_at or updated_at or datetime.now(UTC).replace(tzinfo=None),
        "started_at": started_at,
        "finished_at": finished_at,
        "updated_at": updated_at or occurred_at or datetime.now(UTC).replace(tzinfo=None),
        "safe_facts": dict(safe_facts or {}),
    }
    if lease_owner is not None and lease_until is not None:
        source["lease_owner"] = lease_owner
        source["lease_until"] = lease_until
    source["source_digest"] = canonical_task_source_digest(source)
    return canonical_task_source(source)


def _document_ingest_adapter(
    *, session: Session, tenant_id: str, now: datetime
) -> list[dict[str, Any]]:
    del now
    rows = session.scalars(
        select(DocumentIngestAttempt)
        .where(DocumentIngestAttempt.tenant_id == tenant_id)
        .order_by(DocumentIngestAttempt.id)
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        document = session.scalar(
            select(Document).where(
                Document.tenant_id == tenant_id,
                Document.id == row.document_id,
                Document.dataset_id == row.dataset_id,
            )
        )
        status = _normalize_status(row.state)
        if document is not None and status == "running":
            progress = _percent(document.progress)
        elif status == "succeeded":
            progress = 100
        else:
            progress = None
        error_code, error_text = _safe_error_pair(row.error_code, row.error_message)
        lease_owner = str(row.worker_id or "").strip() or None
        attempt_no = max(0, int(row.attempt_no or 0))
        result.append(
            _source_fact(
                tenant_id=tenant_id,
                source_kind="document_ingest",
                source_id=str(row.id),
                source_revision=max(
                    int(row.input_revision or 0), int(row.document_generation or 0), attempt_no, 1
                ),
                dataset_id=str(row.dataset_id),
                workspace_id=None,
                normalized_status=status,
                action_required=status in {"failed", "blocked", "unavailable"},
                progress_percent=progress,
                attempt_number=attempt_no,
                max_attempts=max(1, attempt_no),
                lease_owner=lease_owner,
                lease_until=row.lease_until if lease_owner is not None else None,
                safe_error_code=error_code,
                safe_error=error_text,
                occurred_at=row.updated_at or row.created_at,
                started_at=row.started_at,
                finished_at=row.finished_at,
                updated_at=row.updated_at or row.created_at,
                safe_facts={
                    "document_id": str(row.document_id),
                    "attempt_kind": str(row.attempt_kind),
                    "attempt_no": attempt_no,
                },
            )
        )
    return result


def _index_operation_adapter(
    *, session: Session, tenant_id: str, now: datetime
) -> list[dict[str, Any]]:
    del now
    rows = session.scalars(
        select(IndexOperation)
        .where(IndexOperation.tenant_id == tenant_id)
        .order_by(IndexOperation.id)
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        status = _normalize_status(row.status)
        attempt = max(0, int(row.retry_count or 0))
        maximum = max(1, int(row.max_retries or 0), attempt)
        error_code, error_text = _safe_error_pair(row.last_error_code, row.last_error)
        lease_owner = str(row.claimed_by or "").strip() or None
        result.append(
            _source_fact(
                tenant_id=tenant_id,
                source_kind="index_operation",
                source_id=str(row.id),
                source_revision=max(
                    int(row.target_revision or 0), int(row.document_generation or 0), 1
                ),
                dataset_id=str(row.dataset_id),
                workspace_id=None,
                normalized_status=status,
                action_required=status in {"failed", "blocked", "unavailable"},
                progress_percent=100 if status == "succeeded" else None,
                attempt_number=attempt,
                max_attempts=maximum,
                lease_owner=lease_owner,
                lease_until=row.lease_until if lease_owner is not None else None,
                safe_error_code=error_code,
                safe_error=error_text,
                occurred_at=row.updated_at or row.created_at,
                started_at=row.created_at,
                finished_at=row.finished_at,
                updated_at=row.updated_at or row.created_at,
                safe_facts={
                    "document_id": str(row.document_id),
                    "target_store": str(row.target_store),
                    "operation": str(row.operation),
                    "target_revision": int(row.target_revision or 0),
                },
            )
        )
    return result


def _source_sync_adapter(
    *, session: Session, tenant_id: str, now: datetime
) -> list[dict[str, Any]]:
    del now
    rows = session.scalars(
        select(SourceSyncRun).where(SourceSyncRun.tenant_id == tenant_id).order_by(SourceSyncRun.id)
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        status = _normalize_status(row.status)
        attempt = max(0, int(row.execution_attempts or 0))
        error_code, error_text = _safe_error_pair(
            "source_sync_failed" if row.fetch_error or row.execution_last_error else None,
            row.fetch_error or row.execution_last_error,
        )
        lease_owner = str(row.execution_owner or "").strip() or None
        result.append(
            _source_fact(
                tenant_id=tenant_id,
                source_kind="source_sync",
                source_id=str(row.id),
                source_revision=max(
                    int(row.source_generation or 0), int(row.dataset_generation or 0), 1
                ),
                dataset_id=str(row.dataset_id),
                workspace_id=None,
                normalized_status=status,
                action_required=status in {"failed", "blocked", "unavailable"},
                progress_percent=100 if status == "succeeded" else None,
                attempt_number=attempt,
                max_attempts=max(1, attempt),
                lease_owner=lease_owner,
                lease_until=row.execution_lease_until if lease_owner is not None else None,
                safe_error_code=error_code,
                safe_error=error_text,
                occurred_at=row.finished_at or row.started_at or row.created_at,
                started_at=row.started_at,
                finished_at=row.finished_at,
                updated_at=row.finished_at or row.started_at or row.created_at,
                safe_facts={
                    "data_source_id": str(row.source_id),
                    "trigger": str(row.trigger),
                    "fetched": int(row.fetched or 0),
                    "ingested": int(row.ingested or 0),
                    "skipped": int(row.skipped or 0),
                    "removed": int(row.removed or 0),
                    "failed": int(row.failed or 0),
                },
            )
        )
    return result


def _document_delete_adapter(
    *, session: Session, tenant_id: str, now: datetime
) -> list[dict[str, Any]]:
    del now
    rows = session.scalars(
        select(DocumentDeleteOperation)
        .where(DocumentDeleteOperation.tenant_id == tenant_id)
        .order_by(DocumentDeleteOperation.id)
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        status = _normalize_status(row.status)
        required = max(0, int(row.required_store_count or 0))
        completed = max(0, int(row.completed_store_count or 0))
        progress = (
            100
            if status == "succeeded"
            else (int(completed * 100 / required) if required else None)
        )
        error_code, error_text = _safe_error_pair(row.result_code, row.result_message)
        document_id = row.document_id or row.requested_document_id
        result.append(
            _source_fact(
                tenant_id=tenant_id,
                source_kind="document_delete",
                source_id=str(row.id),
                source_revision=max(
                    int(row.delete_generation or 0), int(row.expected_generation or 0), 1
                ),
                dataset_id=str(row.dataset_id),
                workspace_id=None,
                normalized_status=status,
                action_required=status in {"failed", "blocked", "unavailable"},
                progress_percent=progress,
                attempt_number=0,
                max_attempts=1,
                safe_error_code=error_code,
                safe_error=error_text,
                occurred_at=row.updated_at or row.created_at,
                started_at=row.started_at,
                finished_at=row.finalized_at,
                updated_at=row.updated_at or row.created_at,
                safe_facts={
                    "document_id": str(document_id),
                    "origin": str(row.origin),
                    "request_index": int(row.request_index or 0),
                },
            )
        )
    return result


def _audit_export_adapter(
    *, session: Session, tenant_id: str, now: datetime
) -> list[dict[str, Any]]:
    del now
    rows = session.scalars(
        select(TenantAuditExportJob)
        .where(TenantAuditExportJob.tenant_id == tenant_id)
        .order_by(TenantAuditExportJob.id)
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        status = _normalize_status(row.status)
        result.append(
            _source_fact(
                tenant_id=tenant_id,
                source_kind="audit_export",
                source_id=str(row.id),
                source_revision=max(int(row.revision or 0), 1),
                dataset_id=None,
                workspace_id=None,
                normalized_status=status,
                action_required=status in {"failed", "blocked", "unavailable"},
                progress_percent=100 if status == "succeeded" else None,
                attempt_number=0,
                max_attempts=1,
                occurred_at=row.requested_at,
                started_at=row.requested_at,
                finished_at=None,
                updated_at=row.requested_at,
                safe_facts={"format": str(row.format), "requested_by": str(row.requested_by)},
            )
        )
    return result


def _release_quality_scan_adapter(
    *, session: Session, tenant_id: str, now: datetime
) -> list[dict[str, Any]]:
    del now
    rows = session.scalars(
        select(TenantReleaseQualityScanRun)
        .where(TenantReleaseQualityScanRun.tenant_id == tenant_id)
        .order_by(TenantReleaseQualityScanRun.id)
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        status = _normalize_status(row.status)
        attempt = max(0, int(row.attempt_count or 0))
        maximum = max(1, int(row.max_attempts or 0), attempt)
        error_code, error_text = _safe_error_pair(row.safe_error_code, row.safe_error)
        lease_owner = str(row.claim_owner or "").strip() or None
        result.append(
            _source_fact(
                tenant_id=tenant_id,
                source_kind="release_quality_scan",
                source_id=str(row.id),
                source_revision=max(int(row.slo_policy_revision or 0), attempt, 1),
                dataset_id=str(row.dataset_id),
                workspace_id=None,
                normalized_status=status,
                action_required=status in {"failed", "blocked", "unavailable"},
                progress_percent=100 if status == "succeeded" else None,
                attempt_number=attempt,
                max_attempts=maximum,
                lease_owner=lease_owner,
                lease_until=row.claim_lease_until if lease_owner is not None else None,
                safe_error_code=error_code,
                safe_error=error_text,
                occurred_at=row.finished_at or row.started_at or row.planned_at,
                started_at=row.started_at or row.planned_at,
                finished_at=row.finished_at,
                updated_at=row.updated_at or row.planned_at,
                safe_facts={
                    "schedule_id": str(row.schedule_id),
                    "observation_count": int(row.observation_count or 0),
                    "alert_count": int(row.alert_count or 0),
                    "recertification_job_count": int(row.recertification_job_count or 0),
                },
            )
        )
    return result


def _release_recertification_adapter(
    *, session: Session, tenant_id: str, now: datetime
) -> list[dict[str, Any]]:
    del now
    rows = session.scalars(
        select(DatasetReleaseRecertificationJob)
        .where(DatasetReleaseRecertificationJob.tenant_id == tenant_id)
        .order_by(DatasetReleaseRecertificationJob.id)
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        status = _normalize_status(row.status)
        attempt = max(0, int(row.attempt_count or 0))
        maximum = max(1, int(row.max_attempts or 0), attempt)
        error_code, error_text = _safe_error_pair(row.safe_error_code, row.safe_error)
        lease_owner = str(row.claim_owner or "").strip() or None
        result.append(
            _source_fact(
                tenant_id=tenant_id,
                source_kind="release_recertification",
                source_id=str(row.id),
                source_revision=max(
                    int(row.policy_revision or 0),
                    int(row.slo_policy_revision or 0),
                    int(row.expected_channel_revision or 0),
                    attempt,
                    1,
                ),
                dataset_id=str(row.dataset_id),
                workspace_id=None,
                normalized_status=status,
                action_required=status in {"failed", "blocked", "unavailable"},
                progress_percent=100 if status == "succeeded" else None,
                attempt_number=attempt,
                max_attempts=maximum,
                lease_owner=lease_owner,
                lease_until=row.claim_lease_until if lease_owner is not None else None,
                safe_error_code=error_code,
                safe_error=error_text,
                occurred_at=row.completed_at or row.updated_at or row.created_at,
                started_at=row.created_at,
                finished_at=row.completed_at,
                updated_at=row.updated_at or row.created_at,
                safe_facts={
                    "release_id": str(row.release_id),
                    "channel_id": str(row.channel_id),
                    "release_role": str(row.release_role),
                    "trigger": str(row.trigger),
                },
            )
        )
    return result


SOURCE_ADAPTER_REGISTRY: dict[str, SourceAdapter] = {
    "document_ingest": _document_ingest_adapter,
    "index_operation": _index_operation_adapter,
    "source_sync": _source_sync_adapter,
    "document_delete": _document_delete_adapter,
    "audit_export": _audit_export_adapter,
    "release_quality_scan": _release_quality_scan_adapter,
    "release_recertification": _release_recertification_adapter,
}

# 顺序 = 注册表的插入序（Python 保证）。适配器函数得先存在，所以派生式只能在注册表之后。
SOURCE_ADAPTER_ORDER: tuple[str, ...] = tuple(SOURCE_ADAPTER_REGISTRY)


def _canonical_adapter_source(raw: Mapping[str, Any], tenant_id: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise EnterpriseTaskOperationsInvalid("source adapter returned an invalid item")
    value = dict(raw)
    if value.get("tenant_id") != tenant_id:
        raise EnterpriseTaskOperationsInvalid("source adapter crossed Tenant scope")
    value.pop("source_digest", None)
    value["source_digest"] = canonical_task_source_digest(value)
    return canonical_task_source(value)


def _collect_sources(
    session: Session, tenant_id: str, kinds: tuple[str, ...], now: datetime
) -> _CollectedSources:
    sources: dict[tuple[str, str], dict[str, Any]] = {}
    invalid_count = 0
    adapter_errors: list[str] = []
    for source_kind in kinds:
        # This direct registry lookup is the only source dispatch path.
        adapter = SOURCE_ADAPTER_REGISTRY[source_kind]
        try:
            raw_items = adapter(session=session, tenant_id=tenant_id, now=now)
            if raw_items is None:
                raw_items = ()
            if isinstance(raw_items, Mapping):
                raw_items = (raw_items,)
            for raw in raw_items:
                try:
                    source = _canonical_adapter_source(raw, tenant_id)
                    if source["source_kind"] != source_kind:
                        raise EnterpriseTaskOperationsInvalid("source adapter kind mismatch")
                    key = (source_kind, str(source["source_id"]))
                    if key in sources:
                        raise EnterpriseTaskOperationsInvalid(
                            "source adapter returned duplicate identity"
                        )
                    sources[key] = source
                except Exception as exc:
                    invalid_count += 1
                    if not adapter_errors:
                        adapter_errors.append("source adapter returned an invalid safe fact")
                    del exc
        except Exception as exc:
            invalid_count += 1
            adapter_errors.append("source adapter is unavailable")
            del exc
    return _CollectedSources(sources, invalid_count, adapter_errors[0] if adapter_errors else None)


# Projection, event-chain and reconciliation helpers


def _source_inventory_digest(
    tenant_id: str,
    kinds: tuple[str, ...],
    sources: Mapping[tuple[str, str], Mapping[str, Any]],
) -> str:
    inventory = [
        {
            "source_kind": kind,
            "source_id": source_id,
            "source_revision": int(source["source_revision"]),
            "source_digest": str(source["source_digest"]),
        }
        for (kind, source_id), source in sorted(sources.items())
    ]
    return canonical_task_digest(
        "task-inventory",
        {"tenant_id": tenant_id, "source_kinds": list(kinds), "sources": inventory},
    )


def _route_params(source: Mapping[str, Any]) -> dict[str, str]:
    tenant_id = str(source["tenant_id"])
    kind = str(source["source_kind"])
    source_id = str(source["source_id"])
    params: dict[str, str] = {"tenant_id": tenant_id}
    dataset_id = source.get("dataset_id")
    if dataset_id is not None:
        params["dataset_id"] = str(dataset_id)
    facts = source.get("safe_facts") or {}
    if not isinstance(facts, Mapping):
        facts = {}
    if kind in {"document_ingest", "document_delete"}:
        params["document_id"] = str(facts.get("document_id") or source_id)
    elif kind == "index_operation":
        params["operation_id"] = source_id
    elif kind == "source_sync":
        params["source_id"] = str(facts.get("data_source_id") or source_id)
        params["run_id"] = source_id
    elif kind == "audit_export":
        params["export_id"] = source_id
    elif kind == "release_quality_scan":
        params["scan_id"] = source_id
    elif kind == "release_recertification":
        params["job_id"] = source_id
    return {key: params[key] for key in sorted(params)}


def _digest_ready_values(values: Mapping[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for key, value in values.items():
        projected[key] = _iso(value) if isinstance(value, datetime) else value
    return projected


def _projection_values(
    source: Mapping[str, Any],
    task_id: str,
    *,
    now: datetime,
    source_current: bool = True,
    stale: bool = False,
) -> dict[str, Any]:
    kind = str(source["source_kind"])
    normalized_status = (
        "unavailable" if stale else _normalize_status(source.get("normalized_status"))
    )
    action_required = True if stale else bool(source.get("action_required", False))
    error_code_value = source.get("safe_error_code")
    error_text_value = source.get("safe_error")
    if stale:
        error_code_value, error_text_value = "source_stale", "source task is no longer current"
    error_code, error_text = _safe_error_pair(error_code_value, error_text_value)
    occurred_at = _as_datetime(source.get("occurred_at") or now, "occurred_at")
    started_at = (
        _as_datetime(source["started_at"], "started_at") if source.get("started_at") else None
    )
    finished_at = (
        _as_datetime(source["finished_at"], "finished_at") if source.get("finished_at") else None
    )
    updated_at = (
        now if stale else _as_datetime(source.get("updated_at") or occurred_at, "updated_at")
    )
    if stale and finished_at is None:
        finished_at = updated_at
    lease_owner = None if stale else source.get("lease_owner")
    lease_until = None
    if not stale and lease_owner is not None and source.get("lease_until") is not None:
        lease_until = _as_datetime(source["lease_until"], "lease_until")
    attempt_number = max(0, int(source.get("attempt_number") or 0))
    max_attempts = max(1, int(source.get("max_attempts") or 1), attempt_number)
    progress = None if stale else source.get("progress_percent")
    if progress is not None:
        progress = _exact_int(progress, "progress_percent", 0, 100)
    route_params = _route_params(source)
    values: dict[str, Any] = {
        "id": task_id,
        "tenant_id": str(source["tenant_id"]),
        "source_kind": kind,
        "source_id": str(source["source_id"]),
        "source_revision": max(1, int(source["source_revision"])),
        "source_digest": str(source["source_digest"]),
        "dataset_id": str(source["dataset_id"]) if source.get("dataset_id") is not None else None,
        "workspace_id": str(source["workspace_id"])
        if source.get("workspace_id") is not None
        else None,
        "category": _STORAGE_CATEGORY_BY_SOURCE_KIND[kind],
        "normalized_status": normalized_status,
        "action_required": action_required,
        "progress_percent": progress,
        "attempt_number": attempt_number,
        "max_attempts": max_attempts,
        "lease_owner": str(lease_owner) if lease_owner is not None else None,
        "lease_until": lease_until,
        "safe_error_code": error_code,
        "safe_error": error_text,
        "target_route_code": _STORAGE_ROUTE_BY_SOURCE_KIND[kind],
        "target_route_params_json": route_params,
        "source_current": bool(source_current and not stale),
        "occurred_at": occurred_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "updated_at": updated_at,
    }
    values["projection_digest"] = canonical_task_projection_digest(_digest_ready_values(values))
    return values


def _projection_changed(row: TenantTaskProjection, values: Mapping[str, Any]) -> bool:
    for key in (
        "source_kind",
        "source_id",
        "source_revision",
        "source_digest",
        "dataset_id",
        "workspace_id",
        "category",
        "normalized_status",
        "action_required",
        "progress_percent",
        "attempt_number",
        "max_attempts",
        "lease_owner",
        "lease_until",
        "safe_error_code",
        "safe_error",
        "target_route_code",
        "target_route_params_json",
        "source_current",
        "projection_digest",
        "occurred_at",
        "started_at",
        "finished_at",
        "updated_at",
    ):
        if getattr(row, key) != values.get(key):
            return True
    return False


def _event_snapshot(
    row: TenantTaskProjection,
    event_type: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "event_type": event_type,
        "source_kind": str(row.source_kind),
        "source_id": str(row.source_id),
        "source_revision": int(row.source_revision),
        "source_digest": str(row.source_digest),
        "normalized_status": str(row.normalized_status),
        "source_current": bool(row.source_current),
        "action_required": bool(row.action_required),
    }
    if extra:
        for key, value in extra.items():
            if isinstance(value, (str, int, bool)) or value is None:
                snapshot[str(key)] = value
    return snapshot


def _append_event(
    session: Session,
    row: TenantTaskProjection,
    *,
    event_type: str,
    actor_id: str,
    request_id: str,
    occurred_at: datetime,
    extra_snapshot: Mapping[str, Any] | None = None,
) -> TenantTaskEvent:
    if event_type not in TASK_EVENT_TYPES:
        raise EnterpriseTaskOperationsInvalid("event_type is invalid")
    latest = session.scalar(
        select(TenantTaskEvent)
        .where(
            TenantTaskEvent.tenant_id == row.tenant_id,
            TenantTaskEvent.task_id == row.id,
        )
        .order_by(TenantTaskEvent.sequence.desc())
        .limit(1)
        .with_for_update()
    )
    sequence = 1 if latest is None else int(latest.sequence) + 1
    previous = None if latest is None else str(latest.event_digest)
    snapshot = _event_snapshot(row, event_type, extra_snapshot)
    canonical = canonical_task_event(
        tenant_id=str(row.tenant_id),
        task_id=str(row.id),
        source_kind=str(row.source_kind),
        source_id=str(row.source_id),
        source_revision=int(row.source_revision),
        source_digest=str(row.source_digest),
        sequence=sequence,
        event_type=event_type,
        previous_event_digest=previous,
        actor_id=actor_id,
        request_id=request_id,
        safe_snapshot=snapshot,
        occurred_at=occurred_at,
    )
    event = TenantTaskEvent(
        id=_stable(
            "task-event-", str(row.tenant_id), str(row.id), str(sequence), canonical["event_digest"]
        ),
        tenant_id=str(row.tenant_id),
        task_id=str(row.id),
        sequence=sequence,
        event_type=event_type,
        previous_event_digest=previous,
        event_digest=canonical["event_digest"],
        actor_id=actor_id,
        request_id=request_id,
        safe_snapshot_json=dict(canonical["safe_snapshot"]),
        occurred_at=occurred_at,
    )
    session.add(event)
    session.flush()
    return event


def _reconcile_in_session(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    request_id: str,
    kinds: tuple[str, ...],
    now: datetime,
) -> tuple[TenantTaskReconciliationRun, dict[str, Any]]:
    collected = _collect_sources(session, tenant_id, kinds, now)
    inventory_digest = _source_inventory_digest(tenant_id, kinds, collected.sources)
    run_id = _run_id(tenant_id, actor_id, request_id)
    run = TenantTaskReconciliationRun(
        id=run_id,
        tenant_id=tenant_id,
        status="running",
        source_kinds_json=list(kinds),
        source_inventory_digest=inventory_digest,
        source_count=len(collected.sources),
        created_count=0,
        updated_count=0,
        stale_count=0,
        invalid_count=collected.invalid_count,
        started_at=now,
        completed_at=None,
        safe_error_code=None,
        safe_error=None,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()
    for key in sorted(
        collected.sources, key=lambda item: (SOURCE_ADAPTER_ORDER.index(item[0]), item[1])
    ):
        source = collected.sources[key]
        task_id = _task_id(tenant_id, key[0], key[1])
        row = session.scalar(
            select(TenantTaskProjection)
            .where(
                TenantTaskProjection.tenant_id == tenant_id,
                TenantTaskProjection.source_kind == key[0],
                TenantTaskProjection.source_id == key[1],
            )
            .with_for_update()
        )
        values = _projection_values(source, task_id, now=now)
        if row is None:
            row = TenantTaskProjection(**values, created_at=now)
            session.add(row)
            session.flush()
            _append_event(
                session,
                row,
                event_type="materialized",
                actor_id=actor_id,
                request_id=request_id,
                occurred_at=now,
            )
            run.created_count = int(run.created_count) + 1
            continue
        if _projection_changed(row, values):
            for field, value in values.items():
                if field != "id":
                    setattr(row, field, value)
            session.flush()
            _append_event(
                session,
                row,
                event_type="status_changed",
                actor_id=actor_id,
                request_id=request_id,
                occurred_at=now,
            )
            run.updated_count = int(run.updated_count) + 1

    existing_rows = list(
        session.scalars(
            select(TenantTaskProjection)
            .where(
                TenantTaskProjection.tenant_id == tenant_id,
                TenantTaskProjection.source_kind.in_(kinds),
                TenantTaskProjection.source_current.is_(True),
            )
            .order_by(TenantTaskProjection.source_kind, TenantTaskProjection.source_id)
            .with_for_update()
        )
    )
    for row in existing_rows:
        if (str(row.source_kind), str(row.source_id)) in collected.sources:
            continue
        source = {
            "tenant_id": tenant_id,
            "source_kind": str(row.source_kind),
            "source_id": str(row.source_id),
            "source_revision": int(row.source_revision),
            "source_digest": str(row.source_digest),
            "dataset_id": row.dataset_id,
            "workspace_id": row.workspace_id,
            "category": _PUBLIC_CATEGORY_BY_SOURCE_KIND[str(row.source_kind)],
            "normalized_status": "unavailable",
            "action_required": True,
            "progress_percent": row.progress_percent,
            "attempt_number": int(row.attempt_number),
            "max_attempts": int(row.max_attempts),
            "occurred_at": row.occurred_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "updated_at": now,
            "safe_facts": {},
        }
        values = _projection_values(source, str(row.id), now=now, source_current=False, stale=True)
        for field, value in values.items():
            if field != "id":
                setattr(row, field, value)
        session.flush()
        _append_event(
            session,
            row,
            event_type="source_stale",
            actor_id=actor_id,
            request_id=request_id,
            occurred_at=now,
        )
        run.stale_count = int(run.stale_count) + 1
    run.status = "completed"
    run.completed_at = now
    run.updated_at = now
    if collected.adapter_error:
        run.safe_error_code, run.safe_error = _safe_error_pair(
            "source_adapter_partial", collected.adapter_error
        )
    session.flush()
    return run, {
        "source_count": int(run.source_count),
        "created_count": int(run.created_count),
        "updated_count": int(run.updated_count),
        "stale_count": int(run.stale_count),
        "invalid_count": int(run.invalid_count),
        "source_inventory_digest": inventory_digest,
    }


# Read projections


def _scope_actor(session: Session, tenant_id: str, actor_id: str) -> TenantMember:
    member = session.scalar(
        select(TenantMember).where(
            TenantMember.tenant_id == tenant_id,
            TenantMember.account_id == actor_id,
            TenantMember.status == "active",
        )
    )
    account = session.scalar(select(Account).where(Account.id == actor_id))
    if member is None or account is None:
        raise EnterpriseTaskOperationsUnavailable("active Tenant actor is unavailable")
    return member


def _require_manager(member: TenantMember) -> None:
    if str(member.role) not in {"owner", "admin", "editor"}:
        raise EnterpriseTaskOperationsBlocked("actor cannot manage Enterprise Task Operations")


def _action_supported(source_kind: str, action_type: str) -> bool:
    if action_type == "acknowledge":
        return True
    return callable(ACTION_ADAPTER_REGISTRY.get((source_kind, action_type)))


def _action_state_allowed(source_kind: str, action_type: str, normalized_status: str) -> bool:
    del source_kind
    if action_type == "retry":
        return normalized_status in {"failed", "blocked"}
    if action_type == "cancel":
        return normalized_status in {"queued", "running", "blocked"}
    return True


def _duration_ms(row: TenantTaskProjection) -> int | None:
    if row.started_at is None or row.finished_at is None:
        return None
    delta = _as_datetime(row.finished_at) - _as_datetime(row.started_at)
    return max(0, int(delta.total_seconds() * 1000))


def _safe_snapshot(row: TenantTaskProjection) -> dict[str, Any]:
    return {
        "source_kind": str(row.source_kind),
        "source_id": str(row.source_id),
        "source_revision": int(row.source_revision),
        "source_digest": str(row.source_digest),
        "normalized_status": str(row.normalized_status),
        "source_current": bool(row.source_current),
        "action_required": bool(row.action_required),
    }


def _route_params_for_response(row: TenantTaskProjection) -> dict[str, str]:
    raw = row.target_route_params_json or {}
    if not isinstance(raw, Mapping):
        raise EnterpriseTaskOperationsUnavailable("task route authority is unavailable")
    return {str(key): str(value) for key, value in sorted(raw.items())}


def _task_body(row: TenantTaskProjection) -> dict[str, Any]:
    kind = str(row.source_kind)
    status = str(row.normalized_status)
    retryable = bool(
        row.source_current
        and _action_supported(kind, "retry")
        and _action_state_allowed(kind, "retry", status)
        and int(row.attempt_number) < int(row.max_attempts)
    )
    cancellable = bool(
        row.source_current
        and _action_supported(kind, "cancel")
        and _action_state_allowed(kind, "cancel", status)
    )
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "source_kind": kind,
        "source_id": str(row.source_id),
        "source_revision": int(row.source_revision),
        "source_digest": str(row.source_digest),
        "dataset_id": str(row.dataset_id) if row.dataset_id is not None else None,
        "workspace_id": str(row.workspace_id) if row.workspace_id is not None else None,
        "category": _PUBLIC_CATEGORY_BY_SOURCE_KIND[kind],
        "normalized_status": status,
        "status": "completed" if status == "succeeded" else status,
        "action_required": bool(row.action_required),
        "progress_percent": int(row.progress_percent) if row.progress_percent is not None else None,
        "progress": int(row.progress_percent) if row.progress_percent is not None else None,
        "attempt_number": int(row.attempt_number),
        "max_attempts": int(row.max_attempts),
        "lease_owner": str(row.lease_owner) if row.lease_owner is not None else None,
        "lease_until": _iso(row.lease_until),
        "safe_error_code": str(row.safe_error_code) if row.safe_error_code is not None else None,
        "safe_error": str(row.safe_error) if row.safe_error is not None else None,
        "target_route_code": _PUBLIC_ROUTE_BY_SOURCE_KIND[kind],
        "target_route_params_json": _route_params_for_response(row),
        "source_current": bool(row.source_current),
        "projection_digest": str(row.projection_digest),
        "occurred_at": _iso(row.occurred_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "updated_at": _iso(row.updated_at),
        "task_label": f"{kind}:{row.source_id}"[:160],
        "task_type": kind,
        "source_label": str(row.source_id),
        "queue_name": kind.replace("_", "-"),
        "created_at": _iso(row.created_at),
        "next_retry_at": None,
        "owner_label": None,
        "duration_ms": _duration_ms(row),
        "error_code": str(row.safe_error_code) if row.safe_error_code is not None else None,
        "retryable": retryable,
        "cancellable": cancellable,
        "mutation_generation": int(row.source_revision),
        "safe_snapshot_json": _safe_snapshot(row),
    }


def _event_body(row: TenantTaskEvent) -> dict[str, Any]:
    snapshot = row.safe_snapshot_json if isinstance(row.safe_snapshot_json, Mapping) else {}
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "task_id": str(row.task_id),
        "sequence": int(row.sequence),
        "event_type": str(row.event_type),
        "previous_event_digest": str(row.previous_event_digest)
        if row.previous_event_digest is not None
        else None,
        "event_digest": str(row.event_digest),
        "actor_id": str(row.actor_id),
        "request_id": str(row.request_id),
        "safe_snapshot_json": dict(snapshot),
        "occurred_at": _iso(row.occurred_at),
    }


def _view_filters(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EnterpriseTaskOperationsInvalid("filters is invalid")
    raw = dict(value)
    allowed = {
        "source_kinds",
        "categories",
        "statuses",
        "normalized_statuses",
        "action_required",
        "dataset_id",
        "dataset_ids",
        "workspace_id",
        "workspace_ids",
        "occurred_from",
        "occurred_to",
    }
    if set(raw) - allowed:
        raise EnterpriseTaskOperationsInvalid("filters contain unsupported fields")
    core_raw: dict[str, Any] = {}
    for key in ("source_kinds", "categories", "occurred_from", "occurred_to"):
        if key in raw and raw[key] is not None:
            core_raw[key] = raw[key]
    statuses = raw.get("normalized_statuses", raw.get("statuses"))
    if statuses is not None:
        core_raw["normalized_statuses"] = statuses
    if raw.get("action_required") is not None:
        core_raw["action_required"] = _exact_bool(raw["action_required"], "filters.action_required")
    for singular, plural in (("dataset_id", "dataset_ids"), ("workspace_id", "workspace_ids")):
        if singular in raw and raw[singular] is not None:
            core_raw[plural] = [raw[singular]]
        elif plural in raw and raw[plural] is not None:
            core_raw[plural] = raw[plural]
    canonical = canonical_task_saved_view(
        tenant_id="tenant-a",
        account_id="owner-a",
        view_id="view-validation",
        name="Task View",
        view_status="active",
        filters=core_raw,
    )["filters"]
    dataset_ids = list(canonical.get("dataset_ids", []))
    workspace_ids = list(canonical.get("workspace_ids", []))
    if len(dataset_ids) > 1 or len(workspace_ids) > 1:
        raise EnterpriseTaskOperationsInvalid("filters allow one dataset and one workspace")
    return {
        "source_kinds": list(canonical.get("source_kinds", [])),
        "categories": list(canonical.get("categories", [])),
        "statuses": list(canonical.get("normalized_statuses", [])),
        "action_required": canonical.get("action_required"),
        "dataset_id": dataset_ids[0] if dataset_ids else None,
        "workspace_id": workspace_ids[0] if workspace_ids else None,
        "occurred_from": canonical.get("occurred_from"),
        "occurred_to": canonical.get("occurred_to"),
    }


def _view_body(row: TenantTaskSavedView) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "account_id": str(row.account_id),
        "name": str(row.name),
        "status": str(row.status),
        "filters_json": _view_filters(row.filters_json),
        "revision": int(row.revision),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "archived_at": _iso(row.archived_at),
        "created_by": str(row.created_by),
        "updated_by": str(row.updated_by),
    }


def _run_body(row: TenantTaskReconciliationRun) -> dict[str, Any]:
    try:
        kinds = _validate_source_kinds(row.source_kinds_json)
    except EnterpriseTaskOperationsError as exc:
        raise EnterpriseTaskOperationsUnavailable(
            "reconciliation source scope is unavailable"
        ) from exc
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "source_kinds": list(kinds),
        "source_inventory_digest": str(row.source_inventory_digest),
        "status": "started" if str(row.status) == "running" else str(row.status),
        "created_count": int(row.created_count),
        "updated_count": int(row.updated_count),
        "stale_count": int(row.stale_count),
        "invalid_count": int(row.invalid_count),
        "started_at": _iso(row.started_at),
        "completed_at": _iso(row.completed_at),
        "safe_error_code": str(row.safe_error_code) if row.safe_error_code is not None else None,
        "safe_error": str(row.safe_error) if row.safe_error is not None else None,
    }


def _page(
    items: list[dict[str, Any]], next_cursor: str | None, invalid_item_count: int = 0
) -> ServiceResult:
    return ServiceResult(
        {"items": items, "next_cursor": next_cursor, "invalid_item_count": int(invalid_item_count)}
    )


def _mutation_outcome(
    *,
    state: str,
    operation: str,
    resource_id: str | None,
    action_id: str | None = None,
    revision: int | None = None,
    message: str | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "state": state,
        "operation": operation,
        "resource_id": resource_id,
        "action_id": action_id,
        "revision": revision,
        "message": message,
        "retryable": bool(retryable),
    }


def _validate_event_chain(events: Iterable[TenantTaskEvent]) -> None:
    previous: str | None = None
    expected_sequence = 1
    for event in events:
        if int(event.sequence) != expected_sequence or event.previous_event_digest != previous:
            raise EnterpriseTaskOperationsUnavailable("task event chain is unavailable")
        if expected_sequence == 1 and str(event.event_type) != "materialized":
            raise EnterpriseTaskOperationsUnavailable("task event chain is unavailable")
        previous = str(event.event_digest)
        expected_sequence += 1


def get_task_summary(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_id, request_ip
    tenant, actor, moment = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _now(now),
    )
    _validate_actor_account(account_id, actor)
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        rows = list(
            session.scalars(
                select(TenantTaskProjection).where(TenantTaskProjection.tenant_id == tenant)
            )
        )
        counts = {status: 0 for status in TASK_NORMALIZED_STATUSES}
        stale = action_required = retryable = 0
        for row in rows:
            status = (
                str(row.normalized_status)
                if str(row.normalized_status) in TASK_NORMALIZED_STATUSES
                else "unavailable"
            )
            counts[status] += 1
            stale += int(not bool(row.source_current))
            action_required += int(bool(row.action_required))
            retryable += int(
                _action_supported(str(row.source_kind), "retry")
                and _action_state_allowed(str(row.source_kind), "retry", status)
                and bool(row.source_current)
                and int(row.attempt_number) < int(row.max_attempts)
            )
        reconciliation_count = int(
            session.scalar(
                select(func.count())
                .select_from(TenantTaskReconciliationRun)
                .where(TenantTaskReconciliationRun.tenant_id == tenant)
            )
            or 0
        )
        return ServiceResult(
            {
                "tenant_id": tenant,
                "state": "ready",
                "queued_count": counts["queued"],
                "running_count": counts["running"],
                "succeeded_count": counts["succeeded"],
                "failed_count": counts["failed"],
                "cancelled_count": counts["cancelled"],
                "blocked_count": counts["blocked"],
                "stale_count": stale,
                "action_required_count": action_required,
                "reconciliation_count": reconciliation_count,
                "retryable_count": retryable,
                "as_of": _iso(moment),
                "reason_code": None,
            }
        )


def list_tasks(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    status: str | None = None,
    source_kind: str | None = None,
    action_required: bool | None = None,
    category: str | None = None,
    dataset_id: str | None = None,
    workspace_id: str | None = None,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del now, request_id, request_ip
    tenant, actor = _id(tenant_id, "tenant_id", 64), _id(actor_id, "actor_id", 64)
    _validate_actor_account(account_id, actor)
    page_size = _validate_limit(limit)
    status_value = None if status is None else _normalize_status(status)
    if status_value is not None and status_value not in TASK_NORMALIZED_STATUSES:
        raise EnterpriseTaskOperationsInvalid("status is invalid")
    kind_value = None if source_kind is None else _normalize_kind(source_kind)
    action_value = (
        None if action_required is None else _exact_bool(action_required, "action_required")
    )
    category_value = None if category is None else _normalize_filter_category(category)
    dataset_value = None if dataset_id is None else _id(dataset_id, "dataset_id", 64)
    workspace_value = None if workspace_id is None else _id(workspace_id, "workspace_id", 64)
    decoded = _cursor_decode(cursor, "tasks")
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        query = select(TenantTaskProjection).where(TenantTaskProjection.tenant_id == tenant)
        if status_value is not None:
            query = query.where(TenantTaskProjection.normalized_status == status_value)
        if kind_value is not None:
            query = query.where(TenantTaskProjection.source_kind == kind_value)
        if action_value is not None:
            query = query.where(TenantTaskProjection.action_required.is_(action_value))
        if category_value is not None:
            if category_value == "indexing":
                query = query.where(TenantTaskProjection.source_kind == "index_operation")
            elif category_value == "documents":
                query = query.where(
                    TenantTaskProjection.source_kind.in_(("document_ingest", "document_delete"))
                )
            else:
                query = query.where(
                    TenantTaskProjection.source_kind
                    == {
                        "sources": "source_sync",
                        "compliance": "audit_export",
                        "quality": "release_quality_scan",
                    }[category_value]
                )
        if dataset_value is not None:
            query = query.where(TenantTaskProjection.dataset_id == dataset_value)
        if workspace_value is not None:
            query = query.where(TenantTaskProjection.workspace_id == workspace_value)
        if decoded is not None:
            stamp = _as_datetime(decoded.get("updated_at"), "cursor.updated_at")
            row_id = _id(decoded.get("id"), "cursor.id")
            query = query.where(
                or_(
                    TenantTaskProjection.updated_at < stamp,
                    and_(
                        TenantTaskProjection.updated_at == stamp, TenantTaskProjection.id < row_id
                    ),
                )
            )
        rows = list(
            session.scalars(
                query.order_by(
                    TenantTaskProjection.updated_at.desc(), TenantTaskProjection.id.desc()
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        items: list[dict[str, Any]] = []
        invalid = 0
        for row in rows:
            try:
                items.append(_task_body(row))
            except EnterpriseTaskOperationsError:
                invalid += 1
        next_cursor = None
        if has_more and rows:
            next_cursor = _cursor_encode(
                "tasks", {"updated_at": _iso(rows[-1].updated_at), "id": str(rows[-1].id)}
            )
        return _page(items, next_cursor, invalid)


def get_task(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    task_id: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del now, request_id, request_ip
    tenant, actor, identifier = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _id(task_id, "task_id"),
    )
    _validate_actor_account(account_id, actor)
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        row = session.scalar(
            select(TenantTaskProjection).where(
                TenantTaskProjection.tenant_id == tenant, TenantTaskProjection.id == identifier
            )
        )
        if row is None:
            raise EnterpriseTaskOperationsNotFound("task was not found")
        events = list(
            session.scalars(
                select(TenantTaskEvent)
                .where(TenantTaskEvent.tenant_id == tenant, TenantTaskEvent.task_id == identifier)
                .order_by(TenantTaskEvent.sequence.asc())
            )
        )
        _validate_event_chain(events)
        return ServiceResult(
            {"task": _task_body(row), "events": [_event_body(event) for event in events]}
        )


def list_task_events(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    task_id: str,
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del now, request_id, request_ip
    tenant, actor, identifier = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _id(task_id, "task_id"),
    )
    _validate_actor_account(account_id, actor)
    page_size = _validate_limit(limit)
    decoded = _cursor_decode(cursor, "events")
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        if (
            session.scalar(
                select(TenantTaskProjection.id).where(
                    TenantTaskProjection.tenant_id == tenant, TenantTaskProjection.id == identifier
                )
            )
            is None
        ):
            raise EnterpriseTaskOperationsNotFound("task was not found")
        query = select(TenantTaskEvent).where(
            TenantTaskEvent.tenant_id == tenant, TenantTaskEvent.task_id == identifier
        )
        if decoded is not None:
            sequence = _exact_int(decoded.get("sequence"), "cursor.sequence", 1)
            event_id = _id(decoded.get("id"), "cursor.id")
            query = query.where(
                or_(
                    TenantTaskEvent.sequence > sequence,
                    and_(TenantTaskEvent.sequence == sequence, TenantTaskEvent.id > event_id),
                )
            )
        rows = list(
            session.scalars(
                query.order_by(TenantTaskEvent.sequence.asc(), TenantTaskEvent.id.asc()).limit(
                    page_size + 1
                )
            )
        )
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        all_events = list(
            session.scalars(
                select(TenantTaskEvent)
                .where(TenantTaskEvent.tenant_id == tenant, TenantTaskEvent.task_id == identifier)
                .order_by(TenantTaskEvent.sequence.asc())
            )
        )
        _validate_event_chain(all_events)
        next_cursor = None
        if has_more and rows:
            next_cursor = _cursor_encode(
                "events", {"sequence": int(rows[-1].sequence), "id": str(rows[-1].id)}
            )
        return _page([_event_body(event) for event in rows], next_cursor)


def list_saved_task_views(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    account_id: str | None = None,
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    status: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del now, request_id, request_ip
    tenant, actor = _id(tenant_id, "tenant_id", 64), _id(actor_id, "actor_id", 64)
    account = actor if account_id is None else _id(account_id, "account_id", 64)
    if account != actor:
        raise EnterpriseTaskOperationsInvalid("account_id must match actor_id")
    page_size = _validate_limit(limit)
    status_value = None if status is None else str(status).strip().casefold()
    if status_value == "all":
        status_value = None
    if status_value is not None and status_value not in {"active", "archived"}:
        raise EnterpriseTaskOperationsInvalid("status is invalid")
    decoded = _cursor_decode(cursor, "views")
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        query = select(TenantTaskSavedView).where(
            TenantTaskSavedView.tenant_id == tenant,
            TenantTaskSavedView.account_id == account,
        )
        if status_value is not None:
            query = query.where(TenantTaskSavedView.status == status_value)
        if decoded is not None:
            stamp = _as_datetime(decoded.get("updated_at"), "cursor.updated_at")
            row_id = _id(decoded.get("id"), "cursor.id")
            query = query.where(
                or_(
                    TenantTaskSavedView.updated_at < stamp,
                    and_(TenantTaskSavedView.updated_at == stamp, TenantTaskSavedView.id < row_id),
                )
            )
        rows = list(
            session.scalars(
                query.order_by(
                    TenantTaskSavedView.updated_at.desc(), TenantTaskSavedView.id.desc()
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        items: list[dict[str, Any]] = []
        invalid = 0
        for row in rows:
            try:
                items.append(_view_body(row))
            except EnterpriseTaskOperationsError:
                invalid += 1
        next_cursor = None
        if has_more and rows:
            next_cursor = _cursor_encode(
                "views", {"updated_at": _iso(rows[-1].updated_at), "id": str(rows[-1].id)}
            )
        return _page(items, next_cursor, invalid)


def list_reconciliation_runs(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    status: str | None = None,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del now, request_id, request_ip
    tenant, actor = _id(tenant_id, "tenant_id", 64), _id(actor_id, "actor_id", 64)
    _validate_actor_account(account_id, actor)
    page_size = _validate_limit(limit)
    status_value = None if status is None else str(status).strip().casefold()
    if status_value in {"started", "running"}:
        status_value = "running"
    elif status_value == "all":
        status_value = None
    elif status_value is not None and status_value not in {"completed", "failed"}:
        raise EnterpriseTaskOperationsInvalid("status is invalid")
    decoded = _cursor_decode(cursor, "runs")
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        query = select(TenantTaskReconciliationRun).where(
            TenantTaskReconciliationRun.tenant_id == tenant
        )
        if status_value is not None:
            query = query.where(TenantTaskReconciliationRun.status == status_value)
        if decoded is not None:
            stamp = _as_datetime(decoded.get("started_at"), "cursor.started_at")
            row_id = _id(decoded.get("id"), "cursor.id")
            query = query.where(
                or_(
                    TenantTaskReconciliationRun.started_at < stamp,
                    and_(
                        TenantTaskReconciliationRun.started_at == stamp,
                        TenantTaskReconciliationRun.id < row_id,
                    ),
                )
            )
        rows = list(
            session.scalars(
                query.order_by(
                    TenantTaskReconciliationRun.started_at.desc(),
                    TenantTaskReconciliationRun.id.desc(),
                ).limit(page_size + 1)
            )
        )
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        items: list[dict[str, Any]] = []
        invalid = 0
        for row in rows:
            try:
                items.append(_run_body(row))
            except EnterpriseTaskOperationsError:
                invalid += 1
        next_cursor = None
        if has_more and rows:
            next_cursor = _cursor_encode(
                "runs", {"started_at": _iso(rows[-1].started_at), "id": str(rows[-1].id)}
            )
        return _page(items, next_cursor, invalid)


def _reserve(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
    operation: str,
    resource_type: str,
    path_identity: Mapping[str, Any],
    body: Mapping[str, Any],
) -> tuple[Any, ServiceResult | None]:
    try:
        request_hash = tenant_request_hash(
            operation=operation,
            path_identity={str(key): str(value) for key, value in path_identity.items()},
            body=body,
        )
        reservation = reserve_tenant_mutation(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            raw_idempotency_key=idempotency_key,
            request_hash=request_hash,
            operation=operation,
            resource_type=resource_type,
        )
    except TenantMutationIdempotencyConflict as exc:
        raise EnterpriseTaskOperationsConflict(
            "idempotency key was used for a different request"
        ) from exc
    except TenantMutationIdempotencyInProgress as exc:
        raise EnterpriseTaskOperationsConflict("request is already in progress") from exc
    except TenantMutationIdempotencyValidationError as exc:
        raise EnterpriseTaskOperationsInvalid(str(exc)) from exc
    if reservation.replay is not None:
        return reservation, ServiceResult(
            dict(reservation.replay.response), int(reservation.replay.http_status)
        )
    return reservation, None


def _complete(
    session: Session,
    reservation: Any,
    response: Mapping[str, Any],
    resource_id: str | None,
    status: int = 200,
) -> ServiceResult:
    complete_tenant_mutation(
        session,
        reservation,
        response_for_replay=dict(response),
        http_status=status,
        resource_id=resource_id,
    )
    return ServiceResult(dict(response), status)


# Reconciliation mutations


def _preview_diff(
    session: Session,
    *,
    tenant_id: str,
    kinds: tuple[str, ...],
    now: datetime,
) -> dict[str, Any]:
    collected = _collect_sources(session, tenant_id, kinds, now)
    existing = {
        (str(row.source_kind), str(row.source_id)): row
        for row in session.scalars(
            select(TenantTaskProjection).where(
                TenantTaskProjection.tenant_id == tenant_id,
                TenantTaskProjection.source_kind.in_(kinds),
            )
        )
    }
    created = updated = stale = 0
    for key, source in collected.sources.items():
        row = existing.get(key)
        if row is None:
            created += 1
        elif _projection_changed(row, _projection_values(source, str(row.id), now=now)):
            updated += 1
    for key, row in existing.items():
        if bool(row.source_current) and key not in collected.sources:
            stale += 1
    return {
        "source_count": len(collected.sources),
        "created_count": created,
        "updated_count": updated,
        "stale_count": stale,
        "invalid_count": collected.invalid_count,
        "source_inventory_digest": _source_inventory_digest(tenant_id, kinds, collected.sources),
    }


def preview_task_reconciliation(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    source_kinds: Any = None,
    dry_run: bool = True,
    reason: str,
    idempotency_key: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_id, request_ip, account_id, idempotency_key
    tenant, actor, moment = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _now(now),
    )
    if dry_run is not True:
        raise EnterpriseTaskOperationsInvalid("preview requires dry_run=true")
    _safe_text(reason, "reason")
    kinds = _validate_source_kinds(source_kinds)
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        diff = _preview_diff(session, tenant_id=tenant, kinds=kinds, now=moment)
    message = (
        f"preview source_count={diff['source_count']} created={diff['created_count']} "
        f"updated={diff['updated_count']} stale={diff['stale_count']} invalid={diff['invalid_count']}"
    )
    return ServiceResult(
        _mutation_outcome(
            state="applied",
            operation="preview_task_reconciliation",
            resource_id=None,
            message=message,
            retryable=False,
        )
    )


def reconcile_enterprise_tasks(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    source_kinds: Any = None,
    dry_run: bool = False,
    reason: str,
    idempotency_key: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip
    tenant, actor, moment = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _now(now),
    )
    _validate_actor_account(account_id, actor)
    if dry_run is not False:
        raise EnterpriseTaskOperationsInvalid("reconcile requires dry_run=false")
    safe_reason = _safe_text(reason, "reason")
    key = _safe_text(idempotency_key, "idempotency_key", 128)
    kinds = _validate_source_kinds(source_kinds)
    req = _request_id(request_id, f"task-reconcile-request-{sha256(key.encode()).hexdigest()[:24]}")
    operation_name = "reconcile_enterprise_tasks"
    with idempotency_key_lock(tenant, actor, key), engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                member = _scope_actor(session, tenant, actor)
                _require_manager(member)
                reservation, replay = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=key,
                    operation=operation_name,
                    resource_type="tenant_task_reconciliation_run",
                    path_identity={"tenant_id": tenant, "source_kinds": ",".join(kinds)},
                    body={"source_kinds": list(kinds), "dry_run": False, "reason": safe_reason},
                )
                if replay is not None:
                    return replay
                run, diff = _reconcile_in_session(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    request_id=req,
                    kinds=kinds,
                    now=moment,
                )
                response = _mutation_outcome(
                    state="applied",
                    operation=operation_name,
                    resource_id=str(run.id),
                    message=(
                        f"reconciled source_count={diff['source_count']} created={diff['created_count']} "
                        f"updated={diff['updated_count']} stale={diff['stale_count']} invalid={diff['invalid_count']}"
                    ),
                    retryable=False,
                )
                return _complete(session, reservation, response, str(run.id))


# Safe operator action mutations


def _request_task_action(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    task_id: str,
    action_type: str,
    expected_source_revision: int,
    expected_source_digest: str,
    reason: str,
    idempotency_key: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip
    tenant, actor, task, moment = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _id(task_id, "task_id"),
        _now(now),
    )
    _validate_actor_account(account_id, actor)
    action = _normalize_action(action_type)
    expected_revision = _exact_int(expected_source_revision, "expected_source_revision", 1)
    expected_digest = _digest(expected_source_digest, "expected_source_digest")
    safe_reason = _safe_text(reason, "reason")
    key = _safe_text(idempotency_key, "idempotency_key", 128)
    req = _request_id(request_id, f"task-action-request-{sha256(key.encode()).hexdigest()[:24]}")
    operation_name = {
        "retry": "request_task_retry",
        "cancel": "request_task_cancel",
        "acknowledge": "acknowledge_task_attention",
    }[action]
    with idempotency_key_lock(tenant, actor, key), engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                member = _scope_actor(session, tenant, actor)
                _require_manager(member)
                reservation, replay = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=key,
                    operation=operation_name,
                    resource_type="tenant_task_operator_action",
                    path_identity={"tenant_id": tenant, "task_id": task, "action_type": action},
                    body={
                        "expected_source_revision": expected_revision,
                        "expected_source_digest": expected_digest,
                        "reason": safe_reason,
                    },
                )
                if replay is not None:
                    return replay
                row = session.scalar(
                    select(TenantTaskProjection)
                    .where(
                        TenantTaskProjection.tenant_id == tenant, TenantTaskProjection.id == task
                    )
                    .with_for_update()
                )
                if row is None:
                    raise EnterpriseTaskOperationsNotFound("task was not found")
                action_row = TenantTaskOperatorAction(
                    id=_action_id(tenant, actor, key),
                    tenant_id=tenant,
                    task_id=task,
                    action_type=action,
                    status="requested",
                    expected_source_revision=expected_revision,
                    expected_source_digest=expected_digest,
                    idempotency_key_digest=tenant_idempotency_key_digest(tenant, actor, key),
                    actor_id=actor,
                    request_id=req,
                    safe_reason=safe_reason,
                    result_code=None,
                    requested_at=moment,
                    dispatched_at=None,
                    applied_at=None,
                    rejected_at=None,
                    expires_at=moment + _ACTION_EXPIRY,
                    created_at=moment,
                    updated_at=moment,
                )
                session.add(action_row)
                session.flush()
                _append_event(
                    session,
                    row,
                    event_type="action_requested",
                    actor_id=actor,
                    request_id=req,
                    occurred_at=moment,
                    extra_snapshot={"action_type": action, "action_status": "requested"},
                )

                rejection_state: str | None = None
                rejection_code: str | None = None
                if not bool(row.source_current):
                    rejection_state, rejection_code = "conflict", "source_stale"
                elif (
                    int(row.source_revision) != expected_revision
                    or str(row.source_digest) != expected_digest
                ):
                    rejection_state, rejection_code = "conflict", "source_fence_conflict"
                elif not _action_supported(str(row.source_kind), action):
                    rejection_state, rejection_code = "blocked", "unsupported_action"
                elif not _action_state_allowed(
                    str(row.source_kind), action, str(row.normalized_status)
                ):
                    rejection_state, rejection_code = "blocked", "action_state_not_allowed"

                if rejection_state is not None:
                    action_row.status = "rejected"
                    action_row.result_code = rejection_code
                    action_row.rejected_at = moment
                    action_row.updated_at = moment
                    session.flush()
                    _append_event(
                        session,
                        row,
                        event_type="action_rejected",
                        actor_id=actor,
                        request_id=req,
                        occurred_at=moment,
                        extra_snapshot={
                            "action_type": action,
                            "action_status": "rejected",
                            "outcome_code": rejection_code,
                        },
                    )
                    response = _mutation_outcome(
                        state=rejection_state,
                        operation=operation_name,
                        resource_id=task,
                        action_id=str(action_row.id),
                        revision=int(row.source_revision),
                        message=rejection_code,
                        retryable=False,
                    )
                    return _complete(session, reservation, response, task)

                if action == "acknowledge":
                    source = {
                        "tenant_id": tenant,
                        "source_kind": str(row.source_kind),
                        "source_id": str(row.source_id),
                        "source_revision": int(row.source_revision),
                        "source_digest": str(row.source_digest),
                        "dataset_id": row.dataset_id,
                        "workspace_id": row.workspace_id,
                        "category": _PUBLIC_CATEGORY_BY_SOURCE_KIND[str(row.source_kind)],
                        "normalized_status": str(row.normalized_status),
                        "action_required": False,
                        "progress_percent": row.progress_percent,
                        "attempt_number": int(row.attempt_number),
                        "max_attempts": int(row.max_attempts),
                        "lease_owner": row.lease_owner,
                        "lease_until": row.lease_until,
                        "safe_error_code": row.safe_error_code,
                        "safe_error": row.safe_error,
                        "occurred_at": row.occurred_at,
                        "started_at": row.started_at,
                        "finished_at": row.finished_at,
                        "updated_at": moment,
                        "safe_facts": {},
                    }
                    values = _projection_values(source, task, now=moment)
                    for field, value in values.items():
                        if field != "id":
                            setattr(row, field, value)
                    action_row.status = "applied"
                    action_row.result_code = "acknowledged"
                    action_row.applied_at = moment
                    action_row.updated_at = moment
                    session.flush()
                    _append_event(
                        session,
                        row,
                        event_type="attention_acknowledged",
                        actor_id=actor,
                        request_id=req,
                        occurred_at=moment,
                        extra_snapshot={"action_type": action, "action_status": "applied"},
                    )
                    response = _mutation_outcome(
                        state="applied",
                        operation=operation_name,
                        resource_id=task,
                        action_id=str(action_row.id),
                        revision=int(row.source_revision),
                        message=None,
                        retryable=False,
                    )
                    return _complete(session, reservation, response, task)

                # Retry/cancel are accepted as durable requests only. They do
                # not dispatch, mutate a source row, enqueue work, or claim a
                # worker lease in Stage 24.
                response = _mutation_outcome(
                    state="applied",
                    operation=operation_name,
                    resource_id=task,
                    action_id=str(action_row.id),
                    revision=int(row.source_revision),
                    message=None,
                    retryable=False,
                )
                return _complete(session, reservation, response, task)


def request_task_retry(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    task_id: str,
    expected_source_revision: int,
    expected_source_digest: str,
    reason: str,
    idempotency_key: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    return _request_task_action(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        task_id=task_id,
        action_type="retry",
        expected_source_revision=expected_source_revision,
        expected_source_digest=expected_source_digest,
        reason=reason,
        idempotency_key=idempotency_key,
        account_id=account_id,
        request_id=request_id,
        request_ip=request_ip,
        now=now,
    )


def request_task_cancel(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    task_id: str,
    expected_source_revision: int,
    expected_source_digest: str,
    reason: str,
    idempotency_key: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    return _request_task_action(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        task_id=task_id,
        action_type="cancel",
        expected_source_revision=expected_source_revision,
        expected_source_digest=expected_source_digest,
        reason=reason,
        idempotency_key=idempotency_key,
        account_id=account_id,
        request_id=request_id,
        request_ip=request_ip,
        now=now,
    )


def acknowledge_task_attention(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    task_id: str,
    expected_source_revision: int,
    expected_source_digest: str,
    reason: str,
    idempotency_key: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    return _request_task_action(
        engine,
        tenant_id=tenant_id,
        actor_id=actor_id,
        task_id=task_id,
        action_type="acknowledge",
        expected_source_revision=expected_source_revision,
        expected_source_digest=expected_source_digest,
        reason=reason,
        idempotency_key=idempotency_key,
        account_id=account_id,
        request_id=request_id,
        request_ip=request_ip,
        now=now,
    )


# Saved View mutations


def create_saved_task_view(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    account_id: str | None = None,
    name: str,
    filters: Mapping[str, Any],
    reason: str,
    idempotency_key: str,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip
    tenant, actor, moment = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _now(now),
    )
    _validate_actor_account(account_id, actor)
    safe_name = _safe_text(name, "name", 128)
    safe_reason = _safe_text(reason, "reason")
    normalized_filters = _view_filters(filters)
    key = _safe_text(idempotency_key, "idempotency_key", 128)
    req = _request_id(request_id, f"task-view-request-{sha256(key.encode()).hexdigest()[:24]}")
    del req
    operation_name = "create_saved_task_view"
    with idempotency_key_lock(tenant, actor, key), engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                member = _scope_actor(session, tenant, actor)
                _require_manager(member)
                reservation, replay = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=key,
                    operation=operation_name,
                    resource_type="tenant_task_saved_view",
                    path_identity={"tenant_id": tenant, "account_id": actor},
                    body={"name": safe_name, "filters": normalized_filters, "reason": safe_reason},
                )
                if replay is not None:
                    return replay
                normalized_name = safe_name.casefold()
                active_key = f"{actor}:{normalized_name}"
                existing = session.scalar(
                    select(TenantTaskSavedView).where(
                        TenantTaskSavedView.tenant_id == tenant,
                        TenantTaskSavedView.active_view_key == active_key,
                    )
                )
                if existing is not None:
                    raise EnterpriseTaskOperationsConflict(
                        "an active task view with this name already exists"
                    )
                view = TenantTaskSavedView(
                    id=_view_id(tenant, actor, key),
                    tenant_id=tenant,
                    account_id=actor,
                    name=safe_name,
                    normalized_name=normalized_name,
                    status="active",
                    active_view_key=active_key,
                    revision=1,
                    filters_json=normalized_filters,
                    filter_digest=canonical_task_digest("task-view-filters", normalized_filters),
                    created_at=moment,
                    created_by=actor,
                    updated_at=moment,
                    updated_by=actor,
                    archived_at=None,
                    archived_by=None,
                )
                session.add(view)
                session.flush()
                response = _mutation_outcome(
                    state="applied",
                    operation=operation_name,
                    resource_id=str(view.id),
                    revision=1,
                    retryable=False,
                )
                return _complete(session, reservation, response, str(view.id), status=201)


def update_saved_task_view(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    view_id: str,
    expected_revision: int,
    name: str | None = None,
    filters: Mapping[str, Any] | None = None,
    status: str | None = None,
    reason: str,
    idempotency_key: str,
    account_id: str | None = None,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip, request_id
    tenant, actor, identifier, moment = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _id(view_id, "view_id"),
        _now(now),
    )
    _validate_actor_account(account_id, actor)
    expected = _exact_int(expected_revision, "expected_revision", 1)
    safe_reason = _safe_text(reason, "reason")
    safe_name = None if name is None else _safe_text(name, "name", 128)
    normalized_filters = None if filters is None else _view_filters(filters)
    status_value = None if status is None else str(status).strip().casefold()
    if status_value is not None and status_value not in {"active", "archived"}:
        raise EnterpriseTaskOperationsInvalid("status is invalid")
    key = _safe_text(idempotency_key, "idempotency_key", 128)
    operation_name = "update_saved_task_view"
    with idempotency_key_lock(tenant, actor, key), engine_serialization_lock(engine):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                member = _scope_actor(session, tenant, actor)
                _require_manager(member)
                reservation, replay = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=key,
                    operation=operation_name,
                    resource_type="tenant_task_saved_view",
                    path_identity={"tenant_id": tenant, "view_id": identifier},
                    body={
                        "expected_revision": expected,
                        "name": safe_name,
                        "filters": normalized_filters,
                        "status": status_value,
                        "reason": safe_reason,
                    },
                )
                if replay is not None:
                    return replay
                view = session.scalar(
                    select(TenantTaskSavedView)
                    .where(
                        TenantTaskSavedView.tenant_id == tenant,
                        TenantTaskSavedView.account_id == actor,
                        TenantTaskSavedView.id == identifier,
                    )
                    .with_for_update()
                )
                if view is None:
                    raise EnterpriseTaskOperationsNotFound("task view was not found")
                if int(view.revision) != expected:
                    raise EnterpriseTaskOperationsConflict(
                        "task view revision fence rejected update"
                    )
                next_name = safe_name if safe_name is not None else str(view.name)
                next_filters = (
                    normalized_filters
                    if normalized_filters is not None
                    else _view_filters(view.filters_json)
                )
                next_status = status_value or str(view.status)
                normalized_name = next_name.casefold()
                next_active_key = f"{actor}:{normalized_name}" if next_status == "active" else None
                if next_active_key is not None:
                    duplicate = session.scalar(
                        select(TenantTaskSavedView).where(
                            TenantTaskSavedView.tenant_id == tenant,
                            TenantTaskSavedView.active_view_key == next_active_key,
                            TenantTaskSavedView.id != identifier,
                        )
                    )
                    if duplicate is not None:
                        raise EnterpriseTaskOperationsConflict(
                            "an active task view with this name already exists"
                        )
                view.name = next_name
                view.normalized_name = normalized_name
                view.status = next_status
                view.active_view_key = next_active_key
                view.revision = expected + 1
                view.filters_json = next_filters
                view.filter_digest = canonical_task_digest("task-view-filters", next_filters)
                view.updated_at = moment
                view.updated_by = actor
                if next_status == "archived":
                    view.archived_at = moment
                    view.archived_by = actor
                else:
                    view.archived_at = None
                    view.archived_by = None
                session.flush()
                response = _mutation_outcome(
                    state="applied",
                    operation=operation_name,
                    resource_id=identifier,
                    revision=int(view.revision),
                    retryable=False,
                )
                return _complete(session, reservation, response, identifier)


__all__ = [
    "ACTION_ADAPTER_REGISTRY",
    "SOURCE_ADAPTER_ORDER",
    "SOURCE_ADAPTER_REGISTRY",
    "ServiceResult",
    "EnterpriseTaskOperationsError",
    "EnterpriseTaskOperationsUnavailable",
    "EnterpriseTaskOperationsInvalid",
    "EnterpriseTaskOperationsNotFound",
    "EnterpriseTaskOperationsConflict",
    "EnterpriseTaskOperationsBlocked",
    "TaskOperationsServiceError",
    "TaskOperationsUnavailable",
    "TaskOperationsInvalid",
    "TaskOperationsNotFound",
    "TaskOperationsConflict",
    "TaskOperationsBlocked",
    "get_task_summary",
    "list_tasks",
    "get_task",
    "list_task_events",
    "list_saved_task_views",
    "list_reconciliation_runs",
    "preview_task_reconciliation",
    "reconcile_enterprise_tasks",
    "request_task_retry",
    "request_task_cancel",
    "acknowledge_task_attention",
    "create_saved_task_view",
    "update_saved_task_view",
]
