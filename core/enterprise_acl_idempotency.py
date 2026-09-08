"""Durable, tenant/actor-scoped idempotency primitives for ACL mutations.

The service intentionally has no HTTP concerns.  It operates inside the caller's
SQLAlchemy transaction so a grant change, its audit event, and the completed
replay response either commit together or disappear together.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
import threading
from collections.abc import Iterator, Mapping
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.orm import DatasetAclMutationRequest

_MAX_CANONICAL_BYTES = 64 * 1024
_KEY_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")
_SENSITIVE_KEY = re.compile(
    r"(?i)(bearer\s+|authorization\s*:|cookie\s*:|password\s*[=:]|"
    r"api[-_ ]?key\s*[=:]|mysql(?:\+\w+)?://|eyJ[A-Za-z0-9_-]+\.|"
    r"sk_(?:live|test)_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{20,}|"
    r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,}|hf_[A-Za-z0-9]{20,}|"
    r"sk-[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9_-]{20,})"
)


class EnterpriseAclIdempotencyError(RuntimeError):
    """Base class for stable idempotency errors."""


class EnterpriseAclIdempotencyConflict(EnterpriseAclIdempotencyError):
    """The same tenant/actor/key was used for a different request hash."""


class EnterpriseAclIdempotencyInProgress(EnterpriseAclIdempotencyError):
    """A prior request with this key is still pending."""


class EnterpriseAclIdempotencyValidationError(ValueError):
    """The key or request payload is invalid or too large."""


@dataclass(frozen=True)
class IdempotencyReplay:
    response: dict[str, Any]
    http_status: int
    resource_id: str | None


@dataclass(frozen=True)
class IdempotencyReservation:
    record: DatasetAclMutationRequest
    replay: IdempotencyReplay | None = None


@dataclass
class _LockEntry:
    lock: threading.RLock
    users: int = 0


# Process locks are contention optimizations only.  Durable uniqueness and the
# database transaction remain the source of truth across workers/processes.
_key_locks: dict[tuple[str, str, str], _LockEntry] = {}
_key_locks_guard = threading.Lock()
_engine_locks: dict[int, _LockEntry] = {}
_engine_locks_guard = threading.Lock()


def normalize_idempotency_key(value: Any) -> str:
    if not isinstance(value, str):
        raise EnterpriseAclIdempotencyValidationError("Idempotency-Key must be a string")
    key = value.strip()
    if not key or len(key) > 128 or not _KEY_RE.fullmatch(key):
        raise EnterpriseAclIdempotencyValidationError(
            "Idempotency-Key must contain between 1 and 128 safe characters"
        )
    if _SENSITIVE_KEY.search(key):
        raise EnterpriseAclIdempotencyValidationError(
            "Idempotency-Key contains credential-like material"
        )
    return key


def _json_safe(value: Any, *, path: str = "$") -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise EnterpriseAclIdempotencyValidationError(
                f"request payload contains a non-finite number at {path}"
            )
        return value
    if isinstance(value, Mapping):
        projected: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise EnterpriseAclIdempotencyValidationError(
                    f"request payload contains a non-string key at {path}"
                )
            key = raw_key
            if not key:
                raise EnterpriseAclIdempotencyValidationError(
                    f"request payload contains an empty key at {path}"
                )
            if key in projected:
                raise EnterpriseAclIdempotencyValidationError(
                    f"request payload contains a duplicate key at {path}.{key}"
                )
            projected[key] = _json_safe(raw_value, path=f"{path}.{key}")
        return {key: projected[key] for key in sorted(projected)}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise EnterpriseAclIdempotencyValidationError(f"request payload is not JSON-safe at {path}")


def canonical_json(value: Any) -> str:
    projected = _json_safe(value)
    try:
        encoded = json.dumps(
            projected,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EnterpriseAclIdempotencyValidationError(
            "request payload cannot be canonicalized"
        ) from exc
    if len(encoded.encode("utf-8")) > _MAX_CANONICAL_BYTES:
        raise EnterpriseAclIdempotencyValidationError(
            "request payload exceeds the idempotency hash limit"
        )
    return encoded


def canonical_request_hash(payload: Any) -> str:
    """Return a deterministic SHA-256 digest without persisting payload text."""

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def idempotency_key_digest(tenant_id: str, actor_id: str, value: Any) -> str:
    """Derive a domain-separated, irreversible lookup digest for persistence.

    This removes plaintext operator keys from the catalog.  It is deliberately
    described as a digest rather than absolute secret protection: predictable,
    low-entropy keys remain guessable and should not contain credentials.
    """

    key = normalize_idempotency_key(value)
    fields = [str(tenant_id).strip(), str(actor_id).strip(), key]
    if not fields[0] or not fields[1]:
        raise EnterpriseAclIdempotencyValidationError("idempotency scope is required")
    digest = hashlib.sha256()
    digest.update(b"rag4c:dataset-acl:idempotency-key:v1\x00")
    for field in fields:
        encoded = field.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _safe_response(response: Mapping[str, Any]) -> dict[str, Any]:
    """Project only the response fields ACL callers are allowed to replay."""

    def project(value: Any, *, depth: int = 0) -> Any:
        if depth > 5:
            raise EnterpriseAclIdempotencyValidationError("response is too deeply nested")
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        if isinstance(value, Mapping):
            return {
                str(key): project(item, depth=depth + 1)
                for key, item in value.items()
                if str(key)
                not in {
                    "authorization_header",
                    "access_token",
                    "api_key",
                    "cookie",
                    "database_url",
                    "raw_token",
                    "token",
                    "token_hash",
                }
            }
        if isinstance(value, (list, tuple)):
            return [project(item, depth=depth + 1) for item in value]
        raise EnterpriseAclIdempotencyValidationError("response is not JSON-safe")

    projected = project(dict(response))
    serialized = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
    if _SENSITIVE_KEY.search(serialized):
        raise EnterpriseAclIdempotencyValidationError("response contains credential-like material")
    return projected


@contextmanager
def idempotency_key_lock(tenant_id: str, actor_id: str, idempotency_key: str) -> Iterator[None]:
    identity = (str(tenant_id), str(actor_id), str(idempotency_key))
    with _key_locks_guard:
        entry = _key_locks.get(identity)
        if entry is None:
            entry = _LockEntry(threading.RLock())
            _key_locks[identity] = entry
        entry.users += 1
    entry.lock.acquire()
    try:
        yield
    finally:
        entry.lock.release()
        with _key_locks_guard:
            entry.users -= 1
            if entry.users == 0 and _key_locks.get(identity) is entry:
                _key_locks.pop(identity, None)


@contextmanager
def engine_serialization_lock(engine: Any) -> Iterator[None]:
    """Serialize SQLite/StaticPool callers that share one DB-API connection.

    MySQL/PostgreSQL engines provide independent transactional connections and
    rely on Dataset row locks.  Temporary SQLite harnesses commonly use
    ``StaticPool``; concurrent sessions on its single connection can otherwise
    interleave inspection/rollback and make a valid schema look unavailable.
    """

    dialect = str(getattr(getattr(engine, "dialect", None), "name", ""))
    pool_name = type(getattr(engine, "pool", None)).__name__
    if dialect != "sqlite" and pool_name != "StaticPool":
        yield
        return
    catalog_lock = None
    if dialect == "sqlite":
        try:
            from core import catalog as catalog_module

            catalog_lock = getattr(catalog_module, "_catalog_write_lock", None)
        except Exception:
            catalog_lock = None
    if catalog_lock is not None:
        catalog_lock.acquire()
    identity = id(engine)
    with _engine_locks_guard:
        entry = _engine_locks.get(identity)
        if entry is None:
            entry = _LockEntry(threading.RLock())
            _engine_locks[identity] = entry
        entry.users += 1
    entry.lock.acquire()
    try:
        yield
    finally:
        entry.lock.release()
        with _engine_locks_guard:
            entry.users -= 1
            if entry.users == 0 and _engine_locks.get(identity) is entry:
                _engine_locks.pop(identity, None)
        if catalog_lock is not None:
            catalog_lock.release()


def reserve_idempotency(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    operation: str,
    idempotency_key: str,
    request_hash: str,
) -> IdempotencyReservation:
    """Reserve/replay one key inside the caller's open transaction."""

    key = normalize_idempotency_key(idempotency_key)
    tenant_id = str(tenant_id).strip()
    actor_id = str(actor_id).strip()
    dataset_id = str(dataset_id).strip()
    operation = str(operation).strip()
    if not tenant_id or not actor_id or not dataset_id or not operation:
        raise EnterpriseAclIdempotencyValidationError("idempotency scope is required")
    if not re.fullmatch(r"[0-9a-f]{64}", str(request_hash)):
        raise EnterpriseAclIdempotencyValidationError("request_hash must be SHA-256")
    lookup_key = idempotency_key_digest(tenant_id, actor_id, key)

    record = session.scalar(
        select(DatasetAclMutationRequest)
        .where(
            DatasetAclMutationRequest.tenant_id == tenant_id,
            DatasetAclMutationRequest.actor_id == actor_id,
            DatasetAclMutationRequest.idempotency_key == lookup_key,
        )
        .with_for_update()
    )
    if record is not None:
        if str(record.dataset_id) != dataset_id or str(record.operation) != operation:
            raise EnterpriseAclIdempotencyConflict(
                "idempotency key was already used for a different resource"
            )
        if str(record.request_hash) != request_hash:
            raise EnterpriseAclIdempotencyConflict(
                "idempotency key was already used with a different request"
            )
        if str(record.status) == "completed" and record.response_json is not None:
            response = dict(record.response_json)
            return IdempotencyReservation(
                record=record,
                replay=IdempotencyReplay(
                    response=response,
                    http_status=int(record.http_status or 200),
                    resource_id=(str(record.resource_id) if record.resource_id else None),
                ),
            )
        if str(record.status) == "pending":
            raise EnterpriseAclIdempotencyInProgress("idempotency request is already in progress")
        # A failed row is not a successful replay.  Reusing its key with the
        # same hash is safe only after deleting the incomplete reservation in
        # this transaction; a failed row should normally be rolled back.
        session.delete(record)
        session.flush()

    record = DatasetAclMutationRequest(
        id=f"dataset-acl-idem-{uuid.uuid4().hex}",
        tenant_id=tenant_id,
        actor_id=actor_id,
        dataset_id=dataset_id,
        operation=operation,
        idempotency_key=lookup_key,
        request_hash=request_hash,
        status="pending",
        created_at=datetime.utcnow(),
    )
    try:
        with session.begin_nested():
            session.add(record)
            session.flush()
    except IntegrityError:
        existing = session.scalar(
            select(DatasetAclMutationRequest)
            .where(
                DatasetAclMutationRequest.tenant_id == tenant_id,
                DatasetAclMutationRequest.actor_id == actor_id,
                DatasetAclMutationRequest.idempotency_key == lookup_key,
            )
            .with_for_update()
        )
        if existing is None:
            raise
        if str(existing.dataset_id) != dataset_id or str(existing.operation) != operation:
            raise EnterpriseAclIdempotencyConflict(
                "idempotency key was already used for a different resource"
            )
        if str(existing.request_hash) != request_hash:
            raise EnterpriseAclIdempotencyConflict(
                "idempotency key was already used with a different request"
            )
        if str(existing.status) == "completed" and existing.response_json is not None:
            response = existing.response_json
            if isinstance(response, str):
                response = json.loads(response)
            return IdempotencyReservation(
                record=existing,
                replay=IdempotencyReplay(
                    response=dict(response),
                    http_status=int(existing.http_status or 200),
                    resource_id=(str(existing.resource_id) if existing.resource_id else None),
                ),
            )
        raise EnterpriseAclIdempotencyInProgress("idempotency request is already in progress")
    return IdempotencyReservation(record=record)


def complete_idempotency(
    session: Session,
    reservation: IdempotencyReservation,
    *,
    response: Mapping[str, Any],
    http_status: int,
    resource_id: str | None = None,
) -> dict[str, Any]:
    projected = _safe_response(response)
    record = reservation.record
    record.status = "completed"
    record.response_json = projected
    record.http_status = int(http_status)
    record.resource_id = str(resource_id) if resource_id is not None else None
    record.completed_at = datetime.utcnow()
    session.flush()
    return projected


__all__ = [
    "EnterpriseAclIdempotencyConflict",
    "EnterpriseAclIdempotencyError",
    "EnterpriseAclIdempotencyInProgress",
    "EnterpriseAclIdempotencyValidationError",
    "IdempotencyReplay",
    "IdempotencyReservation",
    "canonical_json",
    "canonical_request_hash",
    "complete_idempotency",
    "engine_serialization_lock",
    "idempotency_key_digest",
    "idempotency_key_lock",
    "normalize_idempotency_key",
    "reserve_idempotency",
]
