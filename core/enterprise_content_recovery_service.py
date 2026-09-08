"""Tenant-safe document recycle, recovery, legal hold and purge-request authority."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.enterprise_access_control import evaluate_dataset_permissions
from core.enterprise_acl_idempotency import engine_serialization_lock, idempotency_key_lock
from core.enterprise_approval_control import cancel_approval_request, create_approval_request
from core.enterprise_content_recovery import (
    canonical_purge_request_digest,
    canonical_recovery_event,
    canonical_recycle_key,
    canonical_recycle_snapshot,
    project_recovery_route,
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
from core.knowledge_permissions import KNOWLEDGE_DELETE
from models.orm import (
    Account,
    Dataset,
    Document,
    TenantApprovalPolicy,
    TenantApprovalRequest,
    TenantContentRetentionPolicy,
    TenantDocumentLegalHold,
    TenantDocumentPurgeRequest,
    TenantDocumentRecoveryEvent,
    TenantDocumentRecycleEntry,
    TenantMember,
)

UTC = timezone.utc
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ACTIVE_ENTRY_STATUSES = {"recycled", "restoring", "purge_requested"}


class ContentRecoveryServiceError(RuntimeError):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class ContentRecoveryUnavailable(ContentRecoveryServiceError):
    def __init__(self, message: str = "Content Recovery authority is unavailable") -> None:
        super().__init__("enterprise_content_recovery_unavailable", message, 503)


class ContentRecoveryInvalid(ContentRecoveryServiceError):
    def __init__(self, message: str) -> None:
        super().__init__("enterprise_content_recovery_invalid", message, 422)


class ContentRecoveryNotFound(ContentRecoveryServiceError):
    def __init__(self, message: str) -> None:
        super().__init__("enterprise_content_recovery_not_found", message, 404)


class ContentRecoveryConflict(ContentRecoveryServiceError):
    def __init__(self, message: str) -> None:
        super().__init__("enterprise_content_recovery_conflict", message, 409)


class ContentRecoveryBlocked(ContentRecoveryServiceError):
    def __init__(self, message: str) -> None:
        super().__init__("enterprise_content_recovery_blocked", message, 409)


@dataclass(frozen=True)
class ServiceResult:
    body: dict[str, Any]
    status: int = 200


def _id(value: Any, field: str, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise ContentRecoveryInvalid(f"{field} is invalid")
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > maximum
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", normalized)
    ):
        raise ContentRecoveryInvalid(f"{field} is invalid")
    return normalized


def _reason(value: Any) -> str:
    if not isinstance(value, str):
        raise ContentRecoveryInvalid("reason is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > 512 or any(ord(char) < 32 for char in normalized):
        raise ContentRecoveryInvalid("reason is invalid")
    if re.search(
        r"(?i)(?:token|ticket|password|secret|credential|authorization|bearer)(?:\s|[:=])|(?:[a-z][a-z0-9+.-]{1,31}://)",
        normalized,
    ):
        raise ContentRecoveryInvalid("reason contains unsafe material")
    return normalized


def _exact(value: Any, field: str, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ContentRecoveryInvalid(f"{field} is invalid")
    return value


def _now(value: Any = None) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(tzinfo=None)
    if not isinstance(value, datetime):
        raise ContentRecoveryInvalid("now is invalid")
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _stable(prefix: str, *parts: str) -> str:
    return prefix + sha256("\0".join(parts).encode()).hexdigest()[: 64 - len(prefix)]


def _scope_actor(session: Session, tenant_id: str, actor_id: str) -> tuple[TenantMember, Account]:
    member = session.scalar(
        select(TenantMember).where(
            TenantMember.tenant_id == tenant_id,
            TenantMember.account_id == actor_id,
            TenantMember.status == "active",
        )
    )
    account = session.get(Account, actor_id)
    if member is None or account is None:
        raise ContentRecoveryUnavailable("active Tenant actor is unavailable")
    return member, account


def _require_mutator(member: TenantMember) -> None:
    if str(member.role) not in {"owner", "admin", "editor"}:
        raise ContentRecoveryBlocked("actor cannot manage document recovery")


def _authorize_dataset(
    engine: Any, session: Session, member: TenantMember, dataset_id: str, *, lock: bool = False
) -> Dataset:
    decision = evaluate_dataset_permissions(
        engine,
        str(member.tenant_id),
        str(member.account_id),
        str(member.role),
        dataset_id,
        session=session,
        lock_for_update=lock,
    )
    if KNOWLEDGE_DELETE not in decision.effective_permissions:
        raise ContentRecoveryBlocked("Dataset delete permission is required for recovery mutation")
    dataset = session.scalar(
        select(Dataset).where(Dataset.tenant_id == member.tenant_id, Dataset.id == dataset_id)
    )
    if dataset is None or str(dataset.status) != "active":
        raise ContentRecoveryUnavailable("active Dataset authority is unavailable")
    return dataset


def _policy(
    session: Session, tenant_id: str, *, lock: bool = False
) -> TenantContentRetentionPolicy:
    statement = select(TenantContentRetentionPolicy).where(
        TenantContentRetentionPolicy.tenant_id == tenant_id
    )
    if lock:
        statement = statement.with_for_update()
    policy = session.scalar(statement)
    if policy is None or str(policy.status) != "active":
        raise ContentRecoveryUnavailable("active content retention policy is unavailable")
    return policy


def _entry_projection(session: Session, entry: TenantDocumentRecycleEntry) -> dict[str, Any]:
    try:
        canonical_recycle_snapshot(
            dict(entry.safe_snapshot_json or {}), snapshot_digest=str(entry.snapshot_digest)
        )
    except Exception as exc:
        raise ContentRecoveryUnavailable("canonical recycle snapshot is unavailable") from exc
    document = session.get(Document, str(entry.document_id))
    dataset = session.get(Dataset, str(entry.dataset_id))
    hold_count = int(
        session.scalar(
            select(func.count())
            .select_from(TenantDocumentLegalHold)
            .where(
                TenantDocumentLegalHold.tenant_id == entry.tenant_id,
                TenantDocumentLegalHold.recycle_entry_id == entry.id,
                TenantDocumentLegalHold.status == "active",
            )
        )
        or 0
    )
    return {
        "id": str(entry.id),
        "tenant_id": str(entry.tenant_id),
        "dataset_id": str(entry.dataset_id),
        "document_id": str(entry.document_id),
        "recycle_generation": int(entry.recycle_generation),
        "active_recycle_key": entry.active_recycle_key,
        "status": str(entry.status),
        "revision": int(entry.revision),
        "document_mutation_generation": int(entry.document_mutation_generation),
        "original_lifecycle_state": str(entry.original_lifecycle_state),
        "original_retrieval_enabled": bool(entry.original_retrieval_enabled),
        "retention_days_snapshot": int(entry.retention_days_snapshot),
        "recycled_at": _iso(entry.recycled_at),
        "recycled_by": str(entry.recycled_by),
        "purge_eligible_at": _iso(entry.purge_eligible_at),
        "restored_at": _iso(entry.restored_at),
        "restored_by": entry.restored_by,
        "purge_requested_at": _iso(entry.purge_requested_at),
        "purged_at": _iso(entry.purged_at),
        "purged_by": entry.purged_by,
        "safe_snapshot_json": dict(entry.safe_snapshot_json or {}),
        "snapshot_digest": str(entry.snapshot_digest),
        "created_at": _iso(entry.created_at),
        "updated_at": _iso(entry.updated_at),
        "dataset_label": str(dataset.name) if dataset is not None else str(entry.dataset_id),
        "document_label": str(document.name) if document is not None else str(entry.document_id),
        "current_retrieval_enabled": bool(document.retrieval_enabled)
        if document is not None
        else False,
        "active_hold_count": hold_count,
    }


def _policy_projection(policy: TenantContentRetentionPolicy) -> dict[str, Any]:
    return {
        "id": str(policy.id),
        "tenant_id": str(policy.tenant_id),
        "status": str(policy.status),
        "retention_days": int(policy.retention_days),
        "auto_purge_enabled": bool(policy.auto_purge_enabled),
        "purge_requires_approval": bool(policy.purge_requires_approval),
        "revision": int(policy.revision),
        "created_at": _iso(policy.created_at),
        "created_by": str(policy.created_by),
        "updated_at": _iso(policy.updated_at),
        "updated_by": str(policy.updated_by),
    }


def _hold_projection(row: TenantDocumentLegalHold) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "dataset_id": str(row.dataset_id),
        "document_id": str(row.document_id),
        "recycle_entry_id": str(row.recycle_entry_id),
        "status": str(row.status),
        "active_hold_key": row.active_hold_key,
        "revision": int(row.revision),
        "reason_code": str(row.reason_code),
        "safe_reason": str(row.safe_reason),
        "held_at": _iso(row.held_at),
        "held_by": str(row.held_by),
        "released_at": _iso(row.released_at),
        "released_by": row.released_by,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _event_projection(row: TenantDocumentRecoveryEvent) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "dataset_id": str(row.dataset_id),
        "document_id": str(row.document_id),
        "recycle_entry_id": str(row.recycle_entry_id),
        "sequence": int(row.sequence),
        "event_type": str(row.event_type),
        "previous_event_digest": row.previous_event_digest,
        "event_digest": str(row.event_digest),
        "actor_id": str(row.actor_id),
        "request_id": str(row.request_id),
        "safe_snapshot_json": dict(row.safe_snapshot_json or {}),
        "occurred_at": _iso(row.occurred_at),
    }


def _purge_projection(row: TenantDocumentPurgeRequest) -> dict[str, Any]:
    route = None
    if row.approval_request_id:
        projected = project_recovery_route(
            route_code="enterprise_approval",
            params={"tenant_id": row.tenant_id, "approval_request_id": row.approval_request_id},
        )
        request_id = projected["target_route_params_json"]["approval_request_id"]
        route = {
            "target_route_code": "enterprise_approval",
            "target_route_params_json": {"approval_request_id": request_id},
        }
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "dataset_id": str(row.dataset_id),
        "document_id": str(row.document_id),
        "recycle_entry_id": str(row.recycle_entry_id),
        "status": str(row.status),
        "revision": int(row.revision),
        "expected_entry_revision": int(row.expected_entry_revision),
        "request_digest": str(row.request_digest),
        "idempotency_key_digest": str(row.idempotency_key_digest),
        "approval_request_id": row.approval_request_id,
        "route": route,
        "retention_snapshot_json": dict(row.retention_snapshot_json or {}),
        "legal_hold_count_snapshot": int(row.legal_hold_count_snapshot),
        "requested_at": _iso(row.requested_at),
        "requested_by": str(row.requested_by),
        "approved_at": _iso(row.approved_at),
        "cancelled_at": _iso(row.cancelled_at),
        "cancelled_by": row.cancelled_by,
        "expires_at": _iso(row.expires_at),
        "executed_at": _iso(row.executed_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _mutation_response(
    state: str,
    operation: str,
    resource_id: str | None,
    *,
    revision: int | None = None,
    approval_request_id: str | None = None,
) -> dict[str, Any]:
    route = None
    if approval_request_id:
        route = {
            "target_route_code": "enterprise_approval",
            "target_route_params_json": {"approval_request_id": approval_request_id},
        }
    return {
        "state": state,
        "operation": operation,
        "resource_id": resource_id,
        "approval_request_id": approval_request_id,
        "route": route,
        "revision": revision,
        "message": None,
        "retryable": False,
    }


def _reserve(
    session: Session,
    *,
    tenant: str,
    actor: str,
    key: str,
    operation: str,
    resource_type: str,
    path: Mapping[str, Any],
    payload: Mapping[str, Any],
):
    try:
        reservation = reserve_tenant_mutation(
            session,
            tenant_id=tenant,
            actor_id=actor,
            raw_idempotency_key=key,
            request_hash=tenant_request_hash(operation=operation, path_identity=path, body=payload),
            operation=operation,
            resource_type=resource_type,
        )
    except TenantMutationIdempotencyConflict as exc:
        raise ContentRecoveryConflict(
            "idempotency key was used for a different recovery request"
        ) from exc
    except TenantMutationIdempotencyInProgress as exc:
        raise ContentRecoveryConflict("recovery request is already in progress") from exc
    except TenantMutationIdempotencyValidationError as exc:
        raise ContentRecoveryInvalid(str(exc)) from exc
    if reservation.replay is not None:
        return reservation, ServiceResult(
            reservation.replay.response, reservation.replay.http_status
        )
    return reservation, None


def _complete(
    session: Session, reservation: Any, response: dict[str, Any], resource_id: str | None
) -> ServiceResult:
    complete_tenant_mutation(
        session, reservation, response_for_replay=response, http_status=200, resource_id=resource_id
    )
    return ServiceResult(response)


def _append_event(
    session: Session,
    entry: TenantDocumentRecycleEntry,
    *,
    event_type: str,
    actor: str,
    request_id: str,
    now: datetime,
    snapshot: Mapping[str, Any],
) -> TenantDocumentRecoveryEvent:
    latest = session.scalar(
        select(TenantDocumentRecoveryEvent)
        .where(
            TenantDocumentRecoveryEvent.tenant_id == entry.tenant_id,
            TenantDocumentRecoveryEvent.recycle_entry_id == entry.id,
        )
        .order_by(TenantDocumentRecoveryEvent.sequence.desc())
        .limit(1)
        .with_for_update()
    )
    sequence = 1 if latest is None else int(latest.sequence) + 1
    previous = None if latest is None else str(latest.event_digest)
    canonical = canonical_recovery_event(
        tenant_id=str(entry.tenant_id),
        recycle_entry_id=str(entry.id),
        dataset_id=str(entry.dataset_id),
        document_id=str(entry.document_id),
        recycle_generation=int(entry.recycle_generation),
        sequence=sequence,
        event_type=event_type,
        previous_event_digest=previous,
        actor_id=actor,
        request_id=request_id,
        safe_snapshot=dict(snapshot),
        occurred_at=now,
    )
    event = TenantDocumentRecoveryEvent(
        id=_stable("recovery-event-", canonical["event_digest"]),
        tenant_id=str(entry.tenant_id),
        dataset_id=str(entry.dataset_id),
        document_id=str(entry.document_id),
        recycle_entry_id=str(entry.id),
        sequence=sequence,
        event_type=event_type,
        previous_event_digest=previous,
        event_digest=canonical["event_digest"],
        actor_id=actor,
        request_id=request_id,
        safe_snapshot_json=dict(canonical["safe_snapshot"]),
        occurred_at=now,
    )
    session.add(event)
    session.flush()
    return event


def get_retention_policy(engine: Any, *, tenant_id: str, actor_id: str) -> ServiceResult:
    tenant, actor = _id(tenant_id, "tenant_id", 64), _id(actor_id, "actor_id", 64)
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        return ServiceResult(_policy_projection(_policy(session, tenant)))


def get_recovery_summary(
    engine: Any, *, tenant_id: str, actor_id: str, now: Any = None
) -> ServiceResult:
    tenant, actor, timestamp = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _now(now),
    )
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        _policy(session, tenant)
        recycled = int(
            session.scalar(
                select(func.count())
                .select_from(TenantDocumentRecycleEntry)
                .where(
                    TenantDocumentRecycleEntry.tenant_id == tenant,
                    TenantDocumentRecycleEntry.status.in_(tuple(_ACTIVE_ENTRY_STATUSES)),
                )
            )
            or 0
        )
        expiring = int(
            session.scalar(
                select(func.count())
                .select_from(TenantDocumentRecycleEntry)
                .where(
                    TenantDocumentRecycleEntry.tenant_id == tenant,
                    TenantDocumentRecycleEntry.status == "recycled",
                    TenantDocumentRecycleEntry.purge_eligible_at <= timestamp + timedelta(days=7),
                )
            )
            or 0
        )
        held = int(
            session.scalar(
                select(func.count())
                .select_from(TenantDocumentLegalHold)
                .where(
                    TenantDocumentLegalHold.tenant_id == tenant,
                    TenantDocumentLegalHold.status == "active",
                )
            )
            or 0
        )
        pending = int(
            session.scalar(
                select(func.count())
                .select_from(TenantDocumentPurgeRequest)
                .where(
                    TenantDocumentPurgeRequest.tenant_id == tenant,
                    TenantDocumentPurgeRequest.status == "pending_approval",
                )
            )
            or 0
        )
        return ServiceResult(
            {
                "tenant_id": tenant,
                "state": "ready",
                "recycled_count": recycled,
                "expiring_count": expiring,
                "held_count": held,
                "pending_purge_count": pending,
                "as_of": _iso(timestamp),
                "reason_code": None,
            }
        )


def list_recycle_entries(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    cursor: str | None = None,
    limit: int = 50,
    status: str | None = None,
    dataset_id: str | None = None,
    now: Any = None,
) -> ServiceResult:
    del cursor, now
    tenant, actor, size = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _exact(limit, "limit", 1, 200),
    )
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        statement = select(TenantDocumentRecycleEntry).where(
            TenantDocumentRecycleEntry.tenant_id == tenant
        )
        if status:
            statement = statement.where(TenantDocumentRecycleEntry.status == status)
        if dataset_id:
            statement = statement.where(
                TenantDocumentRecycleEntry.dataset_id == _id(dataset_id, "dataset_id", 64)
            )
        rows = list(
            session.scalars(
                statement.order_by(
                    TenantDocumentRecycleEntry.recycled_at.desc(), TenantDocumentRecycleEntry.id
                ).limit(size)
            )
        )
        return ServiceResult(
            {
                "items": [_entry_projection(session, row) for row in rows],
                "next_cursor": None,
                "invalid_item_count": 0,
            }
        )


def get_recycle_entry(
    engine: Any, *, tenant_id: str, actor_id: str, entry_id: str
) -> ServiceResult:
    tenant, actor, identifier = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _id(entry_id, "entry_id", 64),
    )
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        entry = session.scalar(
            select(TenantDocumentRecycleEntry).where(
                TenantDocumentRecycleEntry.tenant_id == tenant,
                TenantDocumentRecycleEntry.id == identifier,
            )
        )
        if entry is None:
            raise ContentRecoveryNotFound("recycle entry was not found")
        events = list(
            session.scalars(
                select(TenantDocumentRecoveryEvent)
                .where(
                    TenantDocumentRecoveryEvent.tenant_id == tenant,
                    TenantDocumentRecoveryEvent.recycle_entry_id == identifier,
                )
                .order_by(TenantDocumentRecoveryEvent.sequence)
            )
        )
        if not events:
            raise ContentRecoveryUnavailable("recovery event stream is unavailable")
        previous = None
        projected = []
        for index, event in enumerate(events, 1):
            canonical_recovery_event(
                tenant_id=event.tenant_id,
                recycle_entry_id=event.recycle_entry_id,
                dataset_id=event.dataset_id,
                document_id=event.document_id,
                recycle_generation=entry.recycle_generation,
                sequence=event.sequence,
                event_type=event.event_type,
                previous_event_digest=event.previous_event_digest,
                event_digest=event.event_digest,
                actor_id=event.actor_id,
                request_id=event.request_id,
                safe_snapshot=event.safe_snapshot_json,
                occurred_at=event.occurred_at,
            )
            if int(event.sequence) != index or event.previous_event_digest != previous:
                raise ContentRecoveryUnavailable("recovery event chain is unavailable")
            previous = str(event.event_digest)
            projected.append(_event_projection(event))
        return ServiceResult({"entry": _entry_projection(session, entry), "events": projected})


def list_legal_holds(engine: Any, *, tenant_id: str, actor_id: str, entry_id: str) -> ServiceResult:
    tenant, actor, identifier = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _id(entry_id, "entry_id", 64),
    )
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        rows = list(
            session.scalars(
                select(TenantDocumentLegalHold)
                .where(
                    TenantDocumentLegalHold.tenant_id == tenant,
                    TenantDocumentLegalHold.recycle_entry_id == identifier,
                )
                .order_by(TenantDocumentLegalHold.held_at.desc())
            )
        )
        return ServiceResult(
            {
                "items": [_hold_projection(row) for row in rows],
                "next_cursor": None,
                "invalid_item_count": 0,
            }
        )


def list_purge_requests(
    engine: Any, *, tenant_id: str, actor_id: str, entry_id: str
) -> ServiceResult:
    tenant, actor, identifier = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _id(entry_id, "entry_id", 64),
    )
    with Session(engine) as session:
        _scope_actor(session, tenant, actor)
        rows = list(
            session.scalars(
                select(TenantDocumentPurgeRequest)
                .where(
                    TenantDocumentPurgeRequest.tenant_id == tenant,
                    TenantDocumentPurgeRequest.recycle_entry_id == identifier,
                )
                .order_by(TenantDocumentPurgeRequest.requested_at.desc())
            )
        )
        return ServiceResult(
            {
                "items": [_purge_projection(row) for row in rows],
                "next_cursor": None,
                "invalid_item_count": 0,
            }
        )


def recycle_document(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    account_id: str | None = None,
    dataset_id: str | None = None,
    document_id: str,
    expected_mutation_generation: int,
    reason: str,
    idempotency_key: str,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip
    tenant, actor = _id(tenant_id, "tenant_id", 64), _id(actor_id, "actor_id", 64)
    account = actor if account_id is None else _id(account_id, "account_id", 64)
    if account != actor:
        raise ContentRecoveryInvalid("account_id must match actor")
    dataset, document_id_value = (
        _id(dataset_id, "dataset_id", 64),
        _id(document_id, "document_id", 64),
    )
    expected, safe_reason, timestamp = (
        _exact(expected_mutation_generation, "expected_mutation_generation"),
        _reason(reason),
        _now(now),
    )
    operation = "recycle_document"
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant, actor, idempotency_key))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        stack.enter_context(session.begin())
        member, _ = _scope_actor(session, tenant, actor)
        _require_mutator(member)
        policy = _policy(session, tenant, lock=True)
        reservation, replay = _reserve(
            session,
            tenant=tenant,
            actor=actor,
            key=idempotency_key,
            operation=operation,
            resource_type="tenant_document_recycle_entry",
            path={"tenant_id": tenant, "dataset_id": dataset, "document_id": document_id_value},
            payload={"expected_mutation_generation": expected, "reason": safe_reason},
        )
        if replay is not None:
            return replay
        document_query = select(Document).where(
            Document.tenant_id == tenant, Document.id == document_id_value
        )
        if dataset:
            document_query = document_query.where(Document.dataset_id == dataset)
        document = session.scalar(document_query.with_for_update())
        if document is None:
            raise ContentRecoveryNotFound("document was not found")
        dataset = str(document.dataset_id)
        dataset_row = _authorize_dataset(engine, session, member, dataset, lock=True)
        existing = session.scalar(
            select(TenantDocumentRecycleEntry)
            .where(
                TenantDocumentRecycleEntry.tenant_id == tenant,
                TenantDocumentRecycleEntry.active_recycle_key
                == canonical_recycle_key(dataset, document_id_value),
            )
            .with_for_update()
        )
        if existing is not None:
            response = _mutation_response(
                "coalesced", operation, str(existing.id), revision=int(existing.revision)
            )
            return _complete(session, reservation, response, str(existing.id))
        if int(document.mutation_generation) != expected:
            raise ContentRecoveryConflict("document mutation generation fence rejected")
        if (
            str(document.lifecycle_state) not in {"active", "expired"}
            or document.active_delete_operation_id is not None
        ):
            raise ContentRecoveryConflict("document is not eligible for recycle")
        generation = (
            int(
                session.scalar(
                    select(func.max(TenantDocumentRecycleEntry.recycle_generation)).where(
                        TenantDocumentRecycleEntry.tenant_id == tenant,
                        TenantDocumentRecycleEntry.dataset_id == dataset,
                        TenantDocumentRecycleEntry.document_id == document_id_value,
                    )
                )
                or 0
            )
            + 1
        )
        entry_id = _stable("recycle-entry-", tenant, dataset, document_id_value, str(generation))
        purge_at = timestamp + timedelta(days=int(policy.retention_days))
        snapshot = canonical_recycle_snapshot(
            tenant_id=tenant,
            recycle_entry_id=entry_id,
            dataset_id=dataset,
            document_id=document_id_value,
            recycle_generation=generation,
            status="recycled",
            revision=1,
            document_mutation_generation=expected,
            document_revision=max(1, int(document.content_revision)),
            document_version_id=str(document.current_version_id or "version-unassigned"),
            original_lifecycle_state=str(document.lifecycle_state),
            original_retrieval_enabled=bool(document.retrieval_enabled),
            retention_days_snapshot=int(policy.retention_days),
            recycled_at=timestamp,
            recycled_by=actor,
            purge_eligible_at=purge_at,
            request_id=_id(request_id or f"recycle-{entry_id}", "request_id", 128),
            reason_code="operator_recycle",
        )
        entry = TenantDocumentRecycleEntry(
            id=entry_id,
            tenant_id=tenant,
            dataset_id=dataset,
            document_id=document_id_value,
            recycle_generation=generation,
            active_recycle_key=canonical_recycle_key(dataset, document_id_value),
            status="recycled",
            revision=1,
            document_mutation_generation=expected,
            original_lifecycle_state=str(document.lifecycle_state),
            original_retrieval_enabled=bool(document.retrieval_enabled),
            retention_days_snapshot=int(policy.retention_days),
            recycled_at=timestamp,
            recycled_by=actor,
            purge_eligible_at=purge_at,
            restored_at=None,
            restored_by=None,
            purge_requested_at=None,
            purged_at=None,
            purged_by=None,
            safe_snapshot_json={
                key: value for key, value in snapshot.items() if key != "snapshot_digest"
            },
            snapshot_digest=str(snapshot["snapshot_digest"]),
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(entry)
        document.lifecycle_state = "recycled"
        document.retrieval_enabled = False
        document.mutation_generation = expected + 1
        document.updated_at = timestamp
        dataset_row.serving_generation = int(dataset_row.serving_generation) + 1
        session.flush()
        event_request_id = _id(request_id or f"recycle-{entry_id}", "request_id", 128)
        _append_event(
            session,
            entry,
            event_type="recycled",
            actor=actor,
            request_id=event_request_id,
            now=timestamp,
            snapshot={
                "status": "recycled",
                "revision": 1,
                "document_mutation_generation": expected + 1,
                "retention_days_snapshot": int(policy.retention_days),
            },
        )
        response = _mutation_response("applied", operation, entry_id, revision=1)
        return _complete(session, reservation, response, entry_id)


def restore_document(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    account_id: str | None = None,
    entry_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip
    tenant, actor, identifier = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _id(entry_id, "entry_id", 64),
    )
    if account_id is not None and _id(account_id, "account_id", 64) != actor:
        raise ContentRecoveryInvalid("account_id must match actor")
    expected, safe_reason, timestamp, operation = (
        _exact(expected_revision, "expected_revision", 1),
        _reason(reason),
        _now(now),
        "restore_document",
    )
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant, actor, idempotency_key))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        stack.enter_context(session.begin())
        member, _ = _scope_actor(session, tenant, actor)
        _require_mutator(member)
        reservation, replay = _reserve(
            session,
            tenant=tenant,
            actor=actor,
            key=idempotency_key,
            operation=operation,
            resource_type="tenant_document_recycle_entry",
            path={"tenant_id": tenant, "entry_id": identifier},
            payload={"expected_revision": expected, "reason": safe_reason},
        )
        if replay is not None:
            return replay
        entry = session.scalar(
            select(TenantDocumentRecycleEntry)
            .where(
                TenantDocumentRecycleEntry.tenant_id == tenant,
                TenantDocumentRecycleEntry.id == identifier,
            )
            .with_for_update()
        )
        if entry is None:
            raise ContentRecoveryNotFound("recycle entry was not found")
        _authorize_dataset(engine, session, member, str(entry.dataset_id), lock=True)
        if int(entry.revision) != expected or str(entry.status) != "recycled":
            raise ContentRecoveryConflict("recycle entry revision or lifecycle rejected restore")
        document = session.scalar(
            select(Document)
            .where(
                Document.tenant_id == tenant,
                Document.dataset_id == entry.dataset_id,
                Document.id == entry.document_id,
            )
            .with_for_update()
        )
        dataset_row = _authorize_dataset(engine, session, member, str(entry.dataset_id), lock=True)
        stored_snapshot = dict(entry.safe_snapshot_json or {})
        if (
            document is None
            or str(document.lifecycle_state) != "recycled"
            or int(document.mutation_generation) != int(entry.document_mutation_generation) + 1
            or int(document.content_revision) != int(stored_snapshot.get("document_revision") or 0)
            or str(document.current_version_id or "version-unassigned")
            != str(stored_snapshot.get("document_version_id") or "")
        ):
            raise ContentRecoveryConflict("document version authority changed after recycle")
        document.lifecycle_state = str(entry.original_lifecycle_state)
        document.retrieval_enabled = bool(entry.original_retrieval_enabled)
        document.mutation_generation = int(document.mutation_generation) + 1
        document.updated_at = timestamp
        dataset_row.serving_generation = int(dataset_row.serving_generation) + 1
        entry.status = "restored"
        entry.active_recycle_key = None
        entry.revision = expected + 1
        entry.restored_at = timestamp
        entry.restored_by = actor
        entry.updated_at = timestamp
        session.flush()
        _append_event(
            session,
            entry,
            event_type="restored",
            actor=actor,
            request_id=_id(request_id or f"restore-{identifier}", "request_id", 128),
            now=timestamp,
            snapshot={
                "status": "restored",
                "revision": int(entry.revision),
                "document_mutation_generation": int(document.mutation_generation),
            },
        )
        response = _mutation_response(
            "applied", operation, identifier, revision=int(entry.revision)
        )
        return _complete(session, reservation, response, identifier)


def apply_legal_hold(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    account_id: str | None = None,
    entry_id: str,
    expected_revision: int,
    reason_code: str,
    reason: str,
    idempotency_key: str,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip
    tenant, actor, identifier = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _id(entry_id, "entry_id", 64),
    )
    if account_id is not None and _id(account_id, "account_id", 64) != actor:
        raise ContentRecoveryInvalid("account_id must match actor")
    expected, code, safe_reason, timestamp, operation = (
        _exact(expected_revision, "expected_revision", 1),
        _id(reason_code, "reason_code", 64),
        _reason(reason),
        _now(now),
        "apply_legal_hold",
    )
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant, actor, idempotency_key))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        stack.enter_context(session.begin())
        member, _ = _scope_actor(session, tenant, actor)
        _require_mutator(member)
        reservation, replay = _reserve(
            session,
            tenant=tenant,
            actor=actor,
            key=idempotency_key,
            operation=operation,
            resource_type="tenant_document_legal_hold",
            path={"tenant_id": tenant, "entry_id": identifier},
            payload={"expected_revision": expected, "reason_code": code, "reason": safe_reason},
        )
        if replay is not None:
            return replay
        entry = session.scalar(
            select(TenantDocumentRecycleEntry)
            .where(
                TenantDocumentRecycleEntry.tenant_id == tenant,
                TenantDocumentRecycleEntry.id == identifier,
            )
            .with_for_update()
        )
        if entry is None:
            raise ContentRecoveryNotFound("recycle entry was not found")
        _authorize_dataset(engine, session, member, str(entry.dataset_id), lock=False)
        if int(entry.revision) != expected or str(entry.status) != "recycled":
            raise ContentRecoveryConflict("entry cannot receive legal hold")
        active_key = f"{identifier}:{code}"
        existing = session.scalar(
            select(TenantDocumentLegalHold)
            .where(
                TenantDocumentLegalHold.tenant_id == tenant,
                TenantDocumentLegalHold.active_hold_key == active_key,
            )
            .with_for_update()
        )
        if existing is not None:
            response = _mutation_response(
                "coalesced", operation, str(existing.id), revision=int(entry.revision)
            )
            return _complete(session, reservation, response, str(existing.id))
        hold_id = _stable("legal-hold-", tenant, identifier, code)
        hold = TenantDocumentLegalHold(
            id=hold_id,
            tenant_id=tenant,
            dataset_id=entry.dataset_id,
            document_id=entry.document_id,
            recycle_entry_id=identifier,
            status="active",
            active_hold_key=active_key,
            revision=1,
            reason_code=code,
            safe_reason=safe_reason,
            held_at=timestamp,
            held_by=actor,
            released_at=None,
            released_by=None,
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(hold)
        entry.revision = expected + 1
        entry.updated_at = timestamp
        session.flush()
        _append_event(
            session,
            entry,
            event_type="hold_applied",
            actor=actor,
            request_id=_id(request_id or f"hold-{hold_id}", "request_id", 128),
            now=timestamp,
            snapshot={
                "status": "recycled",
                "revision": int(entry.revision),
                "hold_id": hold_id,
                "reason_code": code,
            },
        )
        response = _mutation_response("applied", operation, hold_id, revision=int(entry.revision))
        return _complete(session, reservation, response, hold_id)


def release_legal_hold(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    account_id: str | None = None,
    entry_id: str,
    hold_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_ip
    tenant, actor, entry_identifier, hold_identifier = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _id(entry_id, "entry_id", 64),
        _id(hold_id, "hold_id", 64),
    )
    if account_id is not None and _id(account_id, "account_id", 64) != actor:
        raise ContentRecoveryInvalid("account_id must match actor")
    expected, safe_reason, timestamp, operation = (
        _exact(expected_revision, "expected_revision", 1),
        _reason(reason),
        _now(now),
        "release_legal_hold",
    )
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant, actor, idempotency_key))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        stack.enter_context(session.begin())
        member, _ = _scope_actor(session, tenant, actor)
        _require_mutator(member)
        reservation, replay = _reserve(
            session,
            tenant=tenant,
            actor=actor,
            key=idempotency_key,
            operation=operation,
            resource_type="tenant_document_legal_hold",
            path={"tenant_id": tenant, "entry_id": entry_identifier, "hold_id": hold_identifier},
            payload={"expected_revision": expected, "reason": safe_reason},
        )
        if replay is not None:
            return replay
        entry = session.scalar(
            select(TenantDocumentRecycleEntry)
            .where(
                TenantDocumentRecycleEntry.tenant_id == tenant,
                TenantDocumentRecycleEntry.id == entry_identifier,
            )
            .with_for_update()
        )
        hold = session.scalar(
            select(TenantDocumentLegalHold)
            .where(
                TenantDocumentLegalHold.tenant_id == tenant,
                TenantDocumentLegalHold.id == hold_identifier,
                TenantDocumentLegalHold.recycle_entry_id == entry_identifier,
            )
            .with_for_update()
        )
        if entry is None or hold is None:
            raise ContentRecoveryNotFound("recovery entry or hold was not found")
        _authorize_dataset(engine, session, member, str(entry.dataset_id), lock=True)
        if int(hold.revision) != expected or str(hold.status) != "active":
            raise ContentRecoveryConflict("legal hold revision fence rejected")
        if str(entry.status) != "recycled":
            raise ContentRecoveryConflict("entry cannot release legal hold")
        hold.status = "released"
        hold.active_hold_key = None
        hold.revision = expected + 1
        hold.released_at = timestamp
        hold.released_by = actor
        hold.updated_at = timestamp
        entry.revision = int(entry.revision) + 1
        entry.updated_at = timestamp
        session.flush()
        _append_event(
            session,
            entry,
            event_type="hold_released",
            actor=actor,
            request_id=_id(request_id or f"release-{hold_identifier}", "request_id", 128),
            now=timestamp,
            snapshot={
                "status": "recycled",
                "revision": int(entry.revision),
                "hold_id": hold_identifier,
            },
        )
        response = _mutation_response(
            "applied", operation, hold_identifier, revision=int(entry.revision)
        )
        return _complete(session, reservation, response, hold_identifier)


def request_document_purge(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    account_id: str | None = None,
    entry_id: str,
    expected_revision: int,
    approval_policy_id: str | None = None,
    reason: str,
    idempotency_key: str,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    tenant, actor, identifier = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _id(entry_id, "entry_id", 64),
    )
    policy_id = (
        _id(approval_policy_id, "approval_policy_id", 64) if approval_policy_id is not None else ""
    )
    if account_id is not None and _id(account_id, "account_id", 64) != actor:
        raise ContentRecoveryInvalid("account_id must match actor")
    expected, safe_reason, timestamp = (
        _exact(expected_revision, "expected_revision", 1),
        _reason(reason),
        _now(now),
    )
    with Session(engine) as session:
        member, _ = _scope_actor(session, tenant, actor)
        _require_mutator(member)
        entry = session.scalar(
            select(TenantDocumentRecycleEntry).where(
                TenantDocumentRecycleEntry.tenant_id == tenant,
                TenantDocumentRecycleEntry.id == identifier,
            )
        )
        if entry is None:
            raise ContentRecoveryNotFound("recycle entry was not found")
        if int(entry.revision) != expected or str(entry.status) != "recycled":
            raise ContentRecoveryConflict("entry revision or lifecycle rejected purge request")
        hold_count = int(
            session.scalar(
                select(func.count())
                .select_from(TenantDocumentLegalHold)
                .where(
                    TenantDocumentLegalHold.tenant_id == tenant,
                    TenantDocumentLegalHold.recycle_entry_id == identifier,
                    TenantDocumentLegalHold.status == "active",
                )
            )
            or 0
        )
        if hold_count:
            raise ContentRecoveryBlocked("active legal hold blocks purge request")
        if timestamp < entry.purge_eligible_at:
            raise ContentRecoveryBlocked("retention period has not elapsed")
        if not policy_id:
            policy_row = session.scalar(
                select(TenantApprovalPolicy)
                .where(
                    TenantApprovalPolicy.tenant_id == tenant,
                    TenantApprovalPolicy.action_type == "document_purge",
                    TenantApprovalPolicy.status == "active",
                )
                .order_by(TenantApprovalPolicy.updated_at.desc(), TenantApprovalPolicy.id)
            )
            if policy_row is None:
                raise ContentRecoveryUnavailable(
                    "active document purge approval policy is unavailable"
                )
            policy_id = str(policy_row.id)
        selected_policy = session.get(TenantApprovalPolicy, policy_id)
        if (
            selected_policy is None
            or str(selected_policy.status) != "active"
            or str(selected_policy.action_type) != "document_purge"
        ):
            raise ContentRecoveryBlocked("document_purge approval policy is required")
        snapshot = {
            "recycle_entry_id": identifier,
            "document_id": str(entry.document_id),
            "dataset_id": str(entry.dataset_id),
            "entry_revision": expected,
            "purge_eligible_at": _iso(entry.purge_eligible_at),
            "retention_days_snapshot": int(entry.retention_days_snapshot),
            "legal_hold_count": 0,
        }
    approval_key = f"document-purge-{sha256(idempotency_key.encode()).hexdigest()[:32]}"
    approval = create_approval_request(
        engine,
        tenant_id=tenant,
        actor_id=actor,
        policy_id=policy_id,
        resource_type="document_recycle_entry",
        resource_id=identifier,
        snapshot=snapshot,
        reason=safe_reason,
        idempotency_key=approval_key,
        request_id=_id(request_id or f"purge-{identifier}", "request_id", 128),
        request_ip=str(request_ip or "")[:64],
        now=timestamp,
    )
    approval_body = approval.body
    approval_request = approval_body.get("request", approval_body)
    approval_request_id = _id(approval_request.get("id"), "approval_request_id", 64)
    approval_request_revision = _exact(
        approval_request.get("revision"), "approval_request_revision", 1
    )
    expires_at_raw = approval_request.get("expires_at")
    expires_at = (
        datetime.fromisoformat(str(expires_at_raw).replace("Z", "+00:00"))
        .astimezone(UTC)
        .replace(tzinfo=None)
        if expires_at_raw
        else timestamp + timedelta(hours=2)
    )
    operation = "request_document_purge"
    try:
        with ExitStack() as stack:
            stack.enter_context(idempotency_key_lock(tenant, actor, idempotency_key))
            stack.enter_context(engine_serialization_lock(engine))
            session = stack.enter_context(Session(engine, expire_on_commit=False))
            stack.enter_context(session.begin())
            reservation, replay = _reserve(
                session,
                tenant=tenant,
                actor=actor,
                key=idempotency_key,
                operation=operation,
                resource_type="tenant_document_purge_request",
                path={"tenant_id": tenant, "entry_id": identifier},
                payload={
                    "expected_revision": expected,
                    "approval_policy_id": policy_id,
                    "reason": safe_reason,
                },
            )
            if replay is not None:
                return replay
            entry = session.scalar(
                select(TenantDocumentRecycleEntry)
                .where(
                    TenantDocumentRecycleEntry.tenant_id == tenant,
                    TenantDocumentRecycleEntry.id == identifier,
                )
                .with_for_update()
            )
            if entry is None or int(entry.revision) != expected or str(entry.status) != "recycled":
                raise ContentRecoveryConflict("entry changed while approval request was created")
            hold_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(TenantDocumentLegalHold)
                    .where(
                        TenantDocumentLegalHold.tenant_id == tenant,
                        TenantDocumentLegalHold.recycle_entry_id == identifier,
                        TenantDocumentLegalHold.status == "active",
                    )
                )
                or 0
            )
            if hold_count:
                raise ContentRecoveryBlocked("active legal hold blocks purge request")
            canonical_digest = canonical_purge_request_digest(
                tenant_id=tenant,
                recycle_entry_id=identifier,
                dataset_id=str(entry.dataset_id),
                document_id=str(entry.document_id),
                recycle_generation=int(entry.recycle_generation),
                entry_revision=expected,
                purge_eligible_at=entry.purge_eligible_at,
                retention_days_snapshot=int(entry.retention_days_snapshot),
                legal_hold_count=0,
                approval_request_id=approval_request_id,
                requested_at=timestamp,
                requested_by=actor,
                expires_at=expires_at,
                status="pending_approval",
                request_id=_id(request_id or f"purge-{identifier}", "request_id", 128),
                idempotency_key_digest=tenant_idempotency_key_digest(
                    tenant, actor, idempotency_key
                ),
                retention_snapshot={
                    "retention_days": int(entry.retention_days_snapshot),
                    "purge_eligible_at": _iso(entry.purge_eligible_at),
                },
            )
            purge_id = _stable("purge-request-", canonical_digest)
            row = TenantDocumentPurgeRequest(
                id=purge_id,
                tenant_id=tenant,
                dataset_id=entry.dataset_id,
                document_id=entry.document_id,
                recycle_entry_id=identifier,
                status="pending_approval",
                revision=1,
                expected_entry_revision=expected,
                request_digest=canonical_digest,
                idempotency_key_digest=tenant_idempotency_key_digest(
                    tenant, actor, idempotency_key
                ),
                approval_request_id=approval_request_id,
                retention_snapshot_json={
                    "retention_days": int(entry.retention_days_snapshot),
                    "purge_eligible_at": _iso(entry.purge_eligible_at),
                },
                legal_hold_count_snapshot=0,
                requested_at=timestamp,
                requested_by=actor,
                approved_at=None,
                cancelled_at=None,
                cancelled_by=None,
                expires_at=expires_at,
                executed_at=None,
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(row)
            entry.status = "purge_requested"
            entry.revision = expected + 1
            entry.purge_requested_at = timestamp
            entry.updated_at = timestamp
            session.flush()
            _append_event(
                session,
                entry,
                event_type="purge_requested",
                actor=actor,
                request_id=_id(request_id or f"purge-{identifier}", "request_id", 128),
                now=timestamp,
                snapshot={
                    "status": "purge_requested",
                    "revision": int(entry.revision),
                    "approval_request_id": approval_request_id,
                    "legal_hold_count": 0,
                },
            )
            response = _mutation_response(
                "applied",
                operation,
                purge_id,
                revision=int(entry.revision),
                approval_request_id=approval_request_id,
            )
            return _complete(session, reservation, response, purge_id)
    except Exception as persistence_error:
        try:
            cancel_approval_request(
                engine,
                tenant_id=tenant,
                actor_id=actor,
                request_id=approval_request_id,
                expected_revision=approval_request_revision,
                reason="Recovery purge request persistence compensation",
                idempotency_key=f"purge-compensate-{sha256(idempotency_key.encode()).hexdigest()[:32]}",
                request_id_header=_id(
                    request_id or f"purge-compensate-{identifier}", "request_id", 128
                ),
                request_ip=str(request_ip or "")[:64],
                now=timestamp,
            )
        except Exception as compensation_error:
            raise ContentRecoveryUnavailable(
                "purge approval compensation failed; operator reconciliation is required"
            ) from compensation_error
        raise persistence_error


def cancel_document_purge_request(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    account_id: str | None = None,
    entry_id: str,
    purge_request_id: str,
    expected_revision: int,
    reason: str,
    idempotency_key: str,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    tenant, actor, entry_identifier, purge_identifier = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _id(entry_id, "entry_id", 64),
        _id(purge_request_id, "purge_request_id", 64),
    )
    if account_id is not None and _id(account_id, "account_id", 64) != actor:
        raise ContentRecoveryInvalid("account_id must match actor")
    expected, safe_reason, timestamp, operation = (
        _exact(expected_revision, "expected_revision", 1),
        _reason(reason),
        _now(now),
        "cancel_document_purge_request",
    )
    with Session(engine) as preflight_session:
        linked = preflight_session.scalar(
            select(TenantDocumentPurgeRequest).where(
                TenantDocumentPurgeRequest.tenant_id == tenant,
                TenantDocumentPurgeRequest.id == purge_identifier,
                TenantDocumentPurgeRequest.recycle_entry_id == entry_identifier,
            )
        )
        if linked is None:
            raise ContentRecoveryNotFound("purge request was not found")
        approval_id = str(linked.approval_request_id or "")
        approval_row = (
            preflight_session.get(TenantApprovalRequest, approval_id) if approval_id else None
        )
    if approval_row is not None and str(approval_row.status) == "pending":
        cancel_approval_request(
            engine,
            tenant_id=tenant,
            actor_id=actor,
            request_id=approval_id,
            expected_revision=int(approval_row.revision),
            reason=safe_reason,
            idempotency_key=f"purge-cancel-approval-{sha256(idempotency_key.encode()).hexdigest()[:32]}",
            request_id_header=_id(request_id or f"cancel-{purge_identifier}", "request_id", 128),
            request_ip=str(request_ip or "")[:64],
            now=timestamp,
        )
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant, actor, idempotency_key))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        stack.enter_context(session.begin())
        member, _ = _scope_actor(session, tenant, actor)
        _require_mutator(member)
        reservation, replay = _reserve(
            session,
            tenant=tenant,
            actor=actor,
            key=idempotency_key,
            operation=operation,
            resource_type="tenant_document_purge_request",
            path={
                "tenant_id": tenant,
                "entry_id": entry_identifier,
                "purge_request_id": purge_identifier,
            },
            payload={"expected_revision": expected, "reason": safe_reason},
        )
        if replay is not None:
            return replay
        entry = session.scalar(
            select(TenantDocumentRecycleEntry)
            .where(
                TenantDocumentRecycleEntry.tenant_id == tenant,
                TenantDocumentRecycleEntry.id == entry_identifier,
            )
            .with_for_update()
        )
        row = session.scalar(
            select(TenantDocumentPurgeRequest)
            .where(
                TenantDocumentPurgeRequest.tenant_id == tenant,
                TenantDocumentPurgeRequest.id == purge_identifier,
                TenantDocumentPurgeRequest.recycle_entry_id == entry_identifier,
            )
            .with_for_update()
        )
        if entry is None or row is None:
            raise ContentRecoveryNotFound("purge request was not found")
        if int(row.revision) != expected or str(row.status) != "pending_approval":
            raise ContentRecoveryConflict("purge request revision fence rejected")
        row.status = "cancelled"
        row.revision = expected + 1
        row.cancelled_at = timestamp
        row.cancelled_by = actor
        row.updated_at = timestamp
        entry.status = "recycled"
        entry.revision = int(entry.revision) + 1
        entry.active_recycle_key = canonical_recycle_key(
            str(entry.dataset_id), str(entry.document_id)
        )
        entry.updated_at = timestamp
        session.flush()
        _append_event(
            session,
            entry,
            event_type="purge_cancelled",
            actor=actor,
            request_id=_id(request_id or f"cancel-{purge_identifier}", "request_id", 128),
            now=timestamp,
            snapshot={
                "status": "recycled",
                "revision": int(entry.revision),
                "purge_request_id": purge_identifier,
            },
        )
        response = _mutation_response(
            "applied", operation, purge_identifier, revision=int(entry.revision)
        )
        return _complete(session, reservation, response, purge_identifier)


def update_content_retention_policy(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    account_id: str | None = None,
    expected_revision: int,
    retention_days: int,
    auto_purge_enabled: bool,
    purge_requires_approval: bool,
    status: str,
    reason: str,
    idempotency_key: str,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    del request_id, request_ip
    tenant, actor = _id(tenant_id, "tenant_id", 64), _id(actor_id, "actor_id", 64)
    if account_id is not None and _id(account_id, "account_id", 64) != actor:
        raise ContentRecoveryInvalid("account_id must match actor")
    expected, days, safe_reason, timestamp, operation = (
        _exact(expected_revision, "expected_revision", 1),
        _exact(retention_days, "retention_days", 1, 3650),
        _reason(reason),
        _now(now),
        "update_content_retention_policy",
    )
    if (
        type(auto_purge_enabled) is not bool
        or type(purge_requires_approval) is not bool
        or status not in {"active", "paused"}
    ):
        raise ContentRecoveryInvalid("retention policy values are invalid")
    if auto_purge_enabled and not purge_requires_approval:
        raise ContentRecoveryBlocked("automatic purge requires approval")
    with ExitStack() as stack:
        stack.enter_context(idempotency_key_lock(tenant, actor, idempotency_key))
        stack.enter_context(engine_serialization_lock(engine))
        session = stack.enter_context(Session(engine, expire_on_commit=False))
        stack.enter_context(session.begin())
        member, _ = _scope_actor(session, tenant, actor)
        _require_mutator(member)
        reservation, replay = _reserve(
            session,
            tenant=tenant,
            actor=actor,
            key=idempotency_key,
            operation=operation,
            resource_type="tenant_content_retention_policy",
            path={"tenant_id": tenant},
            payload={
                "expected_revision": expected,
                "retention_days": days,
                "auto_purge_enabled": auto_purge_enabled,
                "purge_requires_approval": purge_requires_approval,
                "status": status,
                "reason": safe_reason,
            },
        )
        if replay is not None:
            return replay
        policy = session.scalar(
            select(TenantContentRetentionPolicy)
            .where(TenantContentRetentionPolicy.tenant_id == tenant)
            .with_for_update()
        )
        if policy is None or int(policy.revision) != expected:
            raise ContentRecoveryConflict("retention policy revision fence rejected")
        policy.retention_days = days
        policy.auto_purge_enabled = auto_purge_enabled
        policy.purge_requires_approval = purge_requires_approval
        policy.status = status
        policy.revision = expected + 1
        policy.updated_at = timestamp
        policy.updated_by = actor
        session.flush()
        response = _mutation_response(
            "applied", operation, str(policy.id), revision=int(policy.revision)
        )
        return _complete(session, reservation, response, str(policy.id))


def bulk_recycle_documents(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    account_id: str | None = None,
    dataset_id: str,
    items: Sequence[Mapping[str, Any]],
    reason: str,
    idempotency_key: str,
    request_id: str | None = None,
    request_ip: str = "",
    now: Any = None,
) -> ServiceResult:
    tenant, actor, dataset = (
        _id(tenant_id, "tenant_id", 64),
        _id(actor_id, "actor_id", 64),
        _id(dataset_id, "dataset_id", 64),
    )
    if account_id is not None and _id(account_id, "account_id", 64) != actor:
        raise ContentRecoveryInvalid("account_id must match actor")
    if (
        not isinstance(items, Sequence)
        or isinstance(items, (str, bytes))
        or not 1 <= len(items) <= 100
    ):
        raise ContentRecoveryInvalid("bulk recycle accepts between 1 and 100 documents")
    normalized: list[tuple[str, int]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise ContentRecoveryInvalid("bulk recycle item is invalid")
        document = _id(item.get("document_id"), "document_id", 64)
        if document in seen:
            raise ContentRecoveryInvalid("bulk recycle document IDs must be unique")
        seen.add(document)
        normalized.append(
            (
                document,
                _exact(
                    item.get("expected_mutation_generation"),
                    "expected_mutation_generation",
                ),
            )
        )
    safe_reason, timestamp = _reason(reason), _now(now)
    operation = "bulk_recycle_documents"
    request_payload = {
        "items": [
            {"document_id": document, "expected_mutation_generation": generation}
            for document, generation in normalized
        ],
        "reason": safe_reason,
    }
    with idempotency_key_lock(tenant, actor, idempotency_key), engine_serialization_lock(engine):
        with Session(engine) as session, session.begin():
            member, _ = _scope_actor(session, tenant, actor)
            _require_mutator(member)
            _authorize_dataset(engine, session, member, dataset, lock=False)
            reservation, replay = _reserve(
                session,
                tenant=tenant,
                actor=actor,
                key=idempotency_key,
                operation=operation,
                resource_type="tenant_document_recycle_batch",
                path={"tenant_id": tenant, "dataset_id": dataset},
                payload=request_payload,
            )
            if replay is not None:
                return replay

    outcomes: list[dict[str, Any]] = []
    for index, (document, generation) in enumerate(normalized):
        child_key = (
            "bulk-recycle-" + sha256((idempotency_key + ":" + str(index)).encode()).hexdigest()[:32]
        )
        try:
            child = recycle_document(
                engine,
                tenant_id=tenant,
                actor_id=actor,
                account_id=account_id,
                dataset_id=dataset,
                document_id=document,
                expected_mutation_generation=generation,
                reason=safe_reason,
                idempotency_key=child_key,
                request_id=request_id,
                request_ip=request_ip,
                now=timestamp,
            ).body
            outcomes.append({"document_id": document, **child})
        except ContentRecoveryServiceError as exc:
            outcomes.append(
                {
                    "document_id": document,
                    "state": "rejected",
                    "operation": "recycle_document",
                    "resource_id": None,
                    "approval_request_id": None,
                    "route": None,
                    "revision": None,
                    "message": exc.message,
                    "retryable": exc.status >= 500,
                }
            )

    applied = sum(item["state"] in {"applied", "coalesced", "replayed"} for item in outcomes)
    response = {
        "state": "applied" if applied == len(outcomes) else "coalesced",
        "operation": operation,
        "resource_id": None,
        "approval_request_id": None,
        "route": None,
        "revision": None,
        "message": None,
        "retryable": False,
        "requested_count": len(outcomes),
        "applied_count": applied,
        "rejected_count": len(outcomes) - applied,
        "items": outcomes,
    }
    with engine_serialization_lock(engine):
        with Session(engine) as session, session.begin():
            complete_tenant_mutation(
                session,
                reservation,
                response_for_replay=response,
                http_status=200,
                resource_id=None,
            )
    return ServiceResult(response)


__all__ = [
    "ServiceResult",
    "apply_legal_hold",
    "bulk_recycle_documents",
    "cancel_document_purge_request",
    "get_recovery_summary",
    "get_recycle_entry",
    "get_retention_policy",
    "list_legal_holds",
    "list_purge_requests",
    "list_recycle_entries",
    "recycle_document",
    "release_legal_hold",
    "request_document_purge",
    "restore_document",
    "update_content_retention_policy",
]
