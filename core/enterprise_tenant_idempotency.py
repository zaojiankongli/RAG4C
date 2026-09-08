"""Generic tenant-scoped mutation idempotency for enterprise control-plane writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Mapping
import uuid

from sqlalchemy import MetaData, Table, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.enterprise_acl_idempotency import (
    EnterpriseAclIdempotencyValidationError,
    canonical_request_hash,
    engine_serialization_lock,
    idempotency_key_lock,
    normalize_idempotency_key,
)


class TenantMutationIdempotencyError(RuntimeError):
    """Base class for generic tenant mutation idempotency failures."""


class TenantMutationIdempotencyConflict(TenantMutationIdempotencyError):
    """The same tenant/actor/key refers to a different semantic request."""


class TenantMutationIdempotencyInProgress(TenantMutationIdempotencyError):
    """A matching mutation reservation is not yet completed."""


class TenantMutationIdempotencyValidationError(ValueError):
    """The mutation key or canonical payload is invalid."""


@dataclass(frozen=True)
class TenantMutationReplay:
    response: dict[str, Any]
    http_status: int
    resource_id: str | None


@dataclass(frozen=True)
class TenantMutationReservation:
    record_id: str
    replay: TenantMutationReplay | None = None


def tenant_idempotency_key_digest(tenant_id: str, actor_id: str, raw_key: Any) -> str:
    """Return a domain-separated digest; raw operator keys are never persisted."""

    try:
        key = normalize_idempotency_key(raw_key)
    except EnterpriseAclIdempotencyValidationError as exc:
        raise TenantMutationIdempotencyValidationError(str(exc)) from exc
    tenant = str(tenant_id or "").strip()
    actor = str(actor_id or "").strip()
    if not tenant or not actor:
        raise TenantMutationIdempotencyValidationError("tenant and actor scope are required")
    digest = hashlib.sha256()
    digest.update(b"rag4c:tenant-control:idempotency-key:v1\x00")
    for value in (tenant, actor, key):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def tenant_request_hash(
    *,
    operation: str,
    path_identity: Mapping[str, str],
    body: Mapping[str, Any],
) -> str:
    try:
        return canonical_request_hash(
            {
                "operation": str(operation),
                "path": dict(path_identity),
                "body": dict(body),
            }
        )
    except EnterpriseAclIdempotencyValidationError as exc:
        raise TenantMutationIdempotencyValidationError(str(exc)) from exc


def _ledger_table(session: Session) -> Table:
    return Table(
        "tenant_control_mutation_requests",
        MetaData(),
        autoload_with=session.connection(),
    )


def _decoded_response(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise TenantMutationIdempotencyValidationError("stored tenant mutation response is invalid")
    return dict(value)


def reserve_tenant_mutation(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    raw_idempotency_key: str,
    request_hash: str,
    operation: str,
    resource_type: str,
) -> TenantMutationReservation:
    ledger = _ledger_table(session)
    lookup_key = tenant_idempotency_key_digest(tenant_id, actor_id, raw_idempotency_key)
    statement = (
        select(ledger)
        .where(
            ledger.c.tenant_id == tenant_id,
            ledger.c.actor_id == actor_id,
            ledger.c.idempotency_key == lookup_key,
        )
        .with_for_update()
    )
    row = session.execute(statement).mappings().one_or_none()
    if row is not None:
        if (
            str(row["request_hash"]) != request_hash
            or str(row["operation"]) != operation
            or str(row["resource_type"]) != resource_type
        ):
            raise TenantMutationIdempotencyConflict(
                "idempotency key was already used for a different request"
            )
        if str(row["status"]) == "completed" and row["response_json"] is not None:
            return TenantMutationReservation(
                record_id=str(row["id"]),
                replay=TenantMutationReplay(
                    response=_decoded_response(row["response_json"]),
                    http_status=int(row["http_status"] or 200),
                    resource_id=(
                        str(row["resource_id"]) if row["resource_id"] is not None else None
                    ),
                ),
            )
        raise TenantMutationIdempotencyInProgress("tenant mutation request is already in progress")

    record_id = f"tenant-mutation-{uuid.uuid4().hex}"
    values = {
        "id": record_id,
        "tenant_id": tenant_id,
        "actor_id": actor_id,
        "idempotency_key": lookup_key,
        "request_hash": request_hash,
        "operation": operation,
        "resource_type": resource_type,
        "resource_id": None,
        "status": "pending",
        "response_json": None,
        "http_status": None,
        "created_at": datetime.utcnow(),
        "completed_at": None,
    }
    try:
        with session.begin_nested():
            session.execute(ledger.insert().values(**values))
    except IntegrityError:
        row = session.execute(statement).mappings().one_or_none()
        if row is None:
            raise
        if (
            str(row["request_hash"]) != request_hash
            or str(row["operation"]) != operation
            or str(row["resource_type"]) != resource_type
        ):
            raise TenantMutationIdempotencyConflict(
                "idempotency key was already used for a different request"
            )
        if str(row["status"]) == "completed" and row["response_json"] is not None:
            return TenantMutationReservation(
                record_id=str(row["id"]),
                replay=TenantMutationReplay(
                    response=_decoded_response(row["response_json"]),
                    http_status=int(row["http_status"] or 200),
                    resource_id=(
                        str(row["resource_id"]) if row["resource_id"] is not None else None
                    ),
                ),
            )
        raise TenantMutationIdempotencyInProgress("tenant mutation request is already in progress")
    return TenantMutationReservation(record_id=record_id)


def complete_tenant_mutation(
    session: Session,
    reservation: TenantMutationReservation,
    *,
    response_for_replay: Mapping[str, Any],
    http_status: int,
    resource_id: str | None,
) -> dict[str, Any]:
    ledger = _ledger_table(session)
    replay = dict(response_for_replay)
    serialized = json.dumps(replay, ensure_ascii=False, separators=(",", ":"))
    lowered = serialized.casefold()
    if any(
        marker in lowered
        for marker in (
            "invite_token",
            "token_hash",
            '"authorization":',
            "bearer",
            "cookie",
            "raw_token",
        )
    ):
        raise TenantMutationIdempotencyValidationError(
            "tenant mutation replay payload contains sensitive material"
        )
    result = session.execute(
        update(ledger)
        .where(ledger.c.id == reservation.record_id, ledger.c.status == "pending")
        .values(
            status="completed",
            response_json=replay,
            http_status=int(http_status),
            resource_id=resource_id,
            completed_at=datetime.utcnow(),
        )
    )
    if result.rowcount != 1:
        raise TenantMutationIdempotencyInProgress(
            "tenant mutation reservation could not be completed"
        )
    return replay


__all__ = [
    "TenantMutationIdempotencyConflict",
    "TenantMutationIdempotencyError",
    "TenantMutationIdempotencyInProgress",
    "TenantMutationIdempotencyValidationError",
    "TenantMutationReplay",
    "TenantMutationReservation",
    "complete_tenant_mutation",
    "engine_serialization_lock",
    "idempotency_key_lock",
    "reserve_tenant_mutation",
    "tenant_idempotency_key_digest",
    "tenant_request_hash",
]
