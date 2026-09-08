"""Stage 26 knowledge-serving reliability service.

This module is the durable boundary around the pure authority in
``core.enterprise_knowledge_serving``.  It reflects only the six Stage26
Catalog tables and reads known source-domain ORM tables through an explicit,
fixed adapter registry.  The public API intentionally exposes reads, profile
and policy control, and zero-write preview; snapshot recording is an internal
service operation used only by controlled tests/workers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import base64
import json
import re
from typing import Any

from sqlalchemy import MetaData, Table, and_, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.enterprise_acl_idempotency import engine_serialization_lock, idempotency_key_lock
from core.enterprise_tenant_idempotency import (
    TenantMutationIdempotencyConflict,
    TenantMutationIdempotencyInProgress,
    TenantMutationIdempotencyValidationError,
    complete_tenant_mutation,
    reserve_tenant_mutation,
    tenant_request_hash,
)
from core.enterprise_knowledge_serving import (
    SERVING_EVENT_TYPES,
    SERVING_STAGE_CODES,
    KnowledgeServingAuthorityError,
    canonical_knowledge_serving_evidence_link,
    canonical_knowledge_serving_event,
    canonical_knowledge_serving_policy,
    canonical_knowledge_serving_snapshot,
    canonical_knowledge_serving_stage_fact,
    canonical_safe_mapping,
)
from models.orm import (
    ChunkHead,
    DataSourceRecord,
    Dataset,
    DatasetReleaseManifest,
    DatasetReleaseQualityCertification,
    Document,
    DocumentIngestAttempt,
    IndexOperation,
    Tenant,
    TenantMember,
    TenantWorkspace,
)

UTC = timezone.utc
SERVING_TABLE_NAMES: tuple[str, ...] = (
    "tenant_knowledge_serving_profiles",
    "tenant_knowledge_serving_policy_revisions",
    "tenant_knowledge_serving_snapshots",
    "tenant_knowledge_serving_stage_facts",
    "tenant_knowledge_serving_evidence_links",
    "tenant_knowledge_serving_events",
)
SERVING_ADAPTER_ORDER: tuple[str, ...] = SERVING_STAGE_CODES

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50


class KnowledgeServingServiceError(RuntimeError):
    """Base error exposed by the Stage26 service boundary."""

    code = "knowledge_serving_service_error"
    status = 500

    def __init__(self, message: str = "Knowledge Serving service failed") -> None:
        super().__init__(message)
        self.message = message


class KnowledgeServingUnavailable(KnowledgeServingServiceError):
    code = "knowledge_serving_unavailable"
    status = 503


class KnowledgeServingInvalid(KnowledgeServingServiceError):
    code = "knowledge_serving_invalid"
    status = 422


class KnowledgeServingNotFound(KnowledgeServingServiceError):
    code = "knowledge_serving_not_found"
    status = 404


class KnowledgeServingConflict(KnowledgeServingServiceError):
    code = "knowledge_serving_conflict"
    status = 409


class KnowledgeServingForbidden(KnowledgeServingServiceError):
    code = "knowledge_serving_forbidden"
    status = 403


ServingServiceError = KnowledgeServingServiceError
ServingUnavailable = KnowledgeServingUnavailable
ServingInvalid = KnowledgeServingInvalid
ServingNotFound = KnowledgeServingNotFound
ServingConflict = KnowledgeServingConflict
ServingForbidden = KnowledgeServingForbidden


@dataclass(frozen=True)
class ServiceResult:
    body: dict[str, Any]
    status: int = 200


@dataclass(frozen=True)
class ServingTables:
    profiles: Table
    policies: Table
    snapshots: Table
    stage_facts: Table
    evidence_links: Table
    events: Table

    def by_name(self) -> dict[str, Table]:
        return {
            SERVING_TABLE_NAMES[0]: self.profiles,
            SERVING_TABLE_NAMES[1]: self.policies,
            SERVING_TABLE_NAMES[2]: self.snapshots,
            SERVING_TABLE_NAMES[3]: self.stage_facts,
            SERVING_TABLE_NAMES[4]: self.evidence_links,
            SERVING_TABLE_NAMES[5]: self.events,
        }


def _now(value: Any = None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise KnowledgeServingInvalid("now must be ISO-8601") from exc
        return _now(parsed)
    raise KnowledgeServingInvalid("now must be a datetime or ISO-8601 string")


def _iso(value: Any) -> str:
    moment = _now(value)
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _id(value: Any, field: str, *, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise KnowledgeServingInvalid(f"{field} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or _ID_RE.fullmatch(normalized) is None:
        raise KnowledgeServingInvalid(f"{field} is not a safe identifier")
    return normalized


def _optional_id(value: Any, field: str, *, maximum: int = 128) -> str | None:
    if value is None or value == "":
        return None
    return _id(value, field, maximum=maximum)


def _code(value: Any, field: str) -> str:
    if not isinstance(value, str) or _CODE_RE.fullmatch(value.strip()) is None:
        raise KnowledgeServingInvalid(f"{field} is not a safe code")
    return value.strip()


def _digest(value: Any, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise KnowledgeServingInvalid(f"{field} must be a lowercase SHA-256 digest")
    return value


def _safe_reason(value: Any) -> str:
    try:
        from core.enterprise_knowledge_serving import _safe_text  # type: ignore[attr-defined]

        return _safe_text(value, "reason", maximum=512)
    except ImportError:
        raise KnowledgeServingInvalid("reason is invalid")
    except KnowledgeServingAuthorityError as exc:
        raise KnowledgeServingInvalid(str(exc)) from exc


def _stable_id(prefix: str, *parts: Any, maximum: int = 64) -> str:
    digest = sha256()
    digest.update(f"rag4c:knowledge-serving:{prefix}:v1\x00".encode())
    for part in parts:
        data = str(part).encode("utf-8")
        digest.update(len(data).to_bytes(4, "big"))
        digest.update(data)
    result = f"{prefix}-{digest.hexdigest()}"
    return result[:maximum]


def _canonical_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _tables(session: Session) -> ServingTables:
    try:
        metadata = MetaData()
        reflected = {
            name: Table(name, metadata, autoload_with=session.connection())
            for name in SERVING_TABLE_NAMES
        }
    except SQLAlchemyError as exc:
        raise KnowledgeServingUnavailable(
            "Stage26 knowledge-serving catalog tables are unavailable"
        ) from exc
    return ServingTables(
        profiles=reflected[SERVING_TABLE_NAMES[0]],
        policies=reflected[SERVING_TABLE_NAMES[1]],
        snapshots=reflected[SERVING_TABLE_NAMES[2]],
        stage_facts=reflected[SERVING_TABLE_NAMES[3]],
        evidence_links=reflected[SERVING_TABLE_NAMES[4]],
        events=reflected[SERVING_TABLE_NAMES[5]],
    )


def _row(row: Any) -> dict[str, Any]:
    if row is None:
        raise KnowledgeServingNotFound("knowledge-serving resource was not found")
    mapping = row._mapping if hasattr(row, "_mapping") else row
    return dict(mapping)


def _json(value: Any, *, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError) as exc:
            raise KnowledgeServingUnavailable("stored JSON evidence is invalid") from exc
    return value


def _wire_row(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(item, datetime):
            result[key] = _iso(item)
        elif key.endswith("_json"):
            result[key] = _json(item, default={})
        else:
            result[key] = item
    return result


def _require_member(
    session: Session, tenant_id: str, actor_id: str, *, manager: bool = False
) -> TenantMember:
    tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None or str(tenant.status).casefold() != "active":
        raise KnowledgeServingForbidden("tenant is not active")
    member = session.scalar(
        select(TenantMember).where(
            TenantMember.tenant_id == tenant_id,
            TenantMember.account_id == actor_id,
            TenantMember.status == "active",
        )
    )
    if member is None:
        raise KnowledgeServingForbidden("actor is not an active tenant member")
    if manager and str(member.role).casefold() not in {"owner", "admin", "manager"}:
        raise KnowledgeServingForbidden("manager permission is required")
    return member


def _require_dataset(session: Session, tenant_id: str, dataset_id: str) -> Dataset:
    dataset = session.scalar(
        select(Dataset).where(Dataset.tenant_id == tenant_id, Dataset.id == dataset_id)
    )
    if dataset is None:
        raise KnowledgeServingNotFound("knowledge base was not found in this tenant")
    return dataset


def _profile_row(
    session: Session,
    tables: ServingTables,
    tenant_id: str,
    dataset_id: str,
    profile_id: str | None = None,
) -> dict[str, Any]:
    statement = select(tables.profiles).where(
        tables.profiles.c.tenant_id == tenant_id,
        tables.profiles.c.dataset_id == dataset_id,
    )
    if profile_id is not None:
        statement = statement.where(tables.profiles.c.id == profile_id)
    result = session.execute(statement.order_by(tables.profiles.c.id)).mappings().first()
    if result is None:
        raise KnowledgeServingNotFound("serving profile was not found in this tenant")
    return dict(result)


def _policy_row(
    session: Session, tables: ServingTables, tenant_id: str, profile_id: str, policy_id: str
) -> dict[str, Any]:
    result = (
        session.execute(
            select(tables.policies).where(
                tables.policies.c.tenant_id == tenant_id,
                tables.policies.c.profile_id == profile_id,
                tables.policies.c.id == policy_id,
            )
        )
        .mappings()
        .first()
    )
    if result is None:
        raise KnowledgeServingNotFound("serving policy revision was not found in this tenant")
    return dict(result)


def _profile_wire(value: Mapping[str, Any]) -> dict[str, Any]:
    item = _wire_row(value)
    try:
        from core.enterprise_knowledge_serving import canonical_serving_profile

        return canonical_serving_profile(item)
    except KnowledgeServingAuthorityError as exc:
        raise KnowledgeServingUnavailable(f"stored serving profile is invalid: {exc}") from exc


def _policy_wire(value: Mapping[str, Any]) -> dict[str, Any]:
    item = _wire_row(value)
    try:
        return canonical_knowledge_serving_policy(item)
    except KnowledgeServingAuthorityError as exc:
        raise KnowledgeServingUnavailable(f"stored serving policy is invalid: {exc}") from exc


def _stage_wire(value: Mapping[str, Any]) -> dict[str, Any]:
    item = _wire_row(value)
    try:
        return canonical_knowledge_serving_stage_fact(item)
    except KnowledgeServingAuthorityError as exc:
        raise KnowledgeServingUnavailable(f"stored serving stage fact is invalid: {exc}") from exc


def _evidence_wire(value: Mapping[str, Any]) -> dict[str, Any]:
    item = _wire_row(value)
    try:
        return canonical_knowledge_serving_evidence_link(item)
    except KnowledgeServingAuthorityError as exc:
        raise KnowledgeServingUnavailable(f"stored serving evidence is invalid: {exc}") from exc


def _event_wire(value: Mapping[str, Any]) -> dict[str, Any]:
    item = _wire_row(value)
    item["safe_snapshot_json"] = _json(item.get("safe_snapshot_json"), default={})
    try:
        event = canonical_knowledge_serving_event(item)
    except KnowledgeServingAuthorityError as exc:
        raise KnowledgeServingUnavailable(f"stored serving event is invalid: {exc}") from exc
    event["safe_snapshot"] = event.pop("safe_snapshot_json")
    return event


def _snapshot_wire(
    value: Mapping[str, Any],
    facts: Iterable[Mapping[str, Any]],
    evidence: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    item = _wire_row(value)
    fact_items = list(facts)
    evidence_items = list(evidence)
    try:
        return canonical_knowledge_serving_snapshot(
            item, stage_facts=fact_items, evidence_links=evidence_items
        )
    except KnowledgeServingAuthorityError as exc:
        raise KnowledgeServingUnavailable(f"stored serving snapshot is invalid: {exc}") from exc


def _page_cursor(value: str | None, *, tenant_id: str, dataset_id: str) -> tuple[str, str] | None:
    if not value:
        return None
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii") + b"=" * (-len(value) % 4))
        body = json.loads(decoded.decode("utf-8"))
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise KnowledgeServingInvalid("cursor is invalid") from exc
    if (
        not isinstance(body, Mapping)
        or body.get("tenant_id") != tenant_id
        or body.get("dataset_id") != dataset_id
    ):
        raise KnowledgeServingInvalid("cursor scope is invalid")
    return _id(body.get("as_of"), "cursor.as_of", maximum=64), _id(body.get("id"), "cursor.id")


def _next_cursor(*, tenant_id: str, dataset_id: str, as_of: Any, resource_id: str) -> str:
    payload = {
        "tenant_id": tenant_id,
        "dataset_id": dataset_id,
        "as_of": _iso(as_of),
        "id": resource_id,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def _mutation_request(
    *, operation: str, tenant_id: str, dataset_id: str, body: Mapping[str, Any]
) -> str:
    try:
        return tenant_request_hash(
            operation=operation,
            path_identity={"tenant_id": tenant_id, "dataset_id": dataset_id},
            body=body,
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise KnowledgeServingInvalid(str(exc)) from exc


def _reserve(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    idempotency_key: str,
    request_hash: str,
    operation: str,
    resource_type: str,
) -> Any:
    try:
        return reserve_tenant_mutation(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            raw_idempotency_key=idempotency_key,
            request_hash=request_hash,
            operation=operation,
            resource_type=resource_type,
        )
    except TenantMutationIdempotencyConflict as exc:
        raise KnowledgeServingConflict(str(exc)) from exc
    except TenantMutationIdempotencyInProgress as exc:
        raise KnowledgeServingConflict(str(exc)) from exc
    except TenantMutationIdempotencyValidationError as exc:
        raise KnowledgeServingInvalid(str(exc)) from exc


def _complete(
    session: Session,
    reservation: Any,
    response: Mapping[str, Any],
    *,
    status: int,
    resource_id: str | None,
) -> ServiceResult:
    try:
        body = complete_tenant_mutation(
            session,
            reservation,
            response_for_replay=response,
            http_status=status,
            resource_id=resource_id,
        )
    except TenantMutationIdempotencyValidationError as exc:
        raise KnowledgeServingInvalid(str(exc)) from exc
    except TenantMutationIdempotencyInProgress as exc:
        raise KnowledgeServingConflict(str(exc)) from exc
    return ServiceResult(dict(body), status)


def _age_seconds(value: Any, now: datetime) -> int | None:
    if value is None:
        return None
    moment = _now(value)
    return max(0, int((now - moment).total_seconds()))


def _adapter_evidence(
    *, kind: str, resource_id: str, revision: int | None, digest: str | None, label: str
) -> dict[str, Any]:
    route = {
        "source": "knowledge_sources",
        "source_sync_run": "knowledge_sources",
        "document": "knowledge_documents",
        "ingest_attempt": "knowledge_documents",
        "chunk_head": "knowledge_documents",
        "index_operation": "knowledge_indexing",
        "release": "knowledge_base_releases",
        "certification": "release_quality",
        "task": "enterprise_tasks",
    }[kind]
    return {
        "evidence_kind": kind,
        "resource_id": resource_id,
        "resource_revision": revision,
        "resource_digest": digest,
        "route_code": route,
        "safe_label": label,
    }


def _base_stage(
    code: str,
    *,
    state: str,
    item_count: int,
    ready_count: int | None = None,
    warning_count: int | None = None,
    pending_count: int | None = None,
    error_count: int = 0,
    lag_seconds: int = 0,
    expected_revision: int | None = None,
    observed_revision: int | None = None,
    expected_digest: str | None = None,
    observed_digest: str | None = None,
    safe_error_code: str | None = None,
    safe_error: str | None = None,
    evidence: Iterable[Mapping[str, Any]] = (),
    **extra: Any,
) -> dict[str, Any]:
    normalized_items = max(0, int(item_count))
    normalized_errors = max(0, int(error_count))
    result: dict[str, Any] = {
        "stage_code": code,
        "state": state,
        "item_count": normalized_items,
        "ready_count": max(
            0,
            int(
                ready_count
                if ready_count is not None
                else (normalized_items - normalized_errors if state == "ready" else 0)
            ),
        ),
        "warning_count": max(
            0, int(warning_count if warning_count is not None else normalized_errors)
        ),
        "pending_count": max(0, int(pending_count if pending_count is not None else 0)),
        "error_count": normalized_errors,
        "lag_seconds": max(0, int(lag_seconds)),
        "expected_revision": expected_revision,
        "observed_revision": observed_revision,
        "expected_digest": expected_digest,
        "observed_digest": observed_digest,
        "safe_error_code": safe_error_code,
        "safe_error": safe_error,
        "evidence": [dict(item) for item in evidence],
    }
    result.update(extra)
    return result


def _source_adapter(
    *, session: Session, tenant_id: str, dataset_id: str, policy: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    sources = list(
        session.scalars(
            select(DataSourceRecord).where(
                DataSourceRecord.tenant_id == tenant_id, DataSourceRecord.dataset_id == dataset_id
            )
        )
    )
    active = [
        source
        for source in sources
        if str(source.status).casefold() not in {"archived", "deleted", "disabled"}
    ]
    stale = [
        source
        for source in active
        if _age_seconds(source.last_sync_at, now) is None
        or _age_seconds(source.last_sync_at, now) > int(policy["max_source_staleness_seconds"])
    ]
    evidence = [
        _adapter_evidence(
            kind="source",
            resource_id=str(source.id),
            revision=int(source.mutation_generation or 0)
            if source.mutation_generation is not None
            else None,
            digest=str(source.config_fingerprint) if source.config_fingerprint else None,
            label=str(source.name or "Knowledge source")[:256],
        )
        for source in active[:32]
    ]
    if not active:
        return _base_stage(
            "source",
            state="missing",
            item_count=0,
            ready_count=0,
            warning_count=0,
            pending_count=0,
            safe_error_code="source_missing",
            safe_error="No active source is configured",
            evidence=evidence,
            source_count=0,
            ready_source_count=0,
            stale_source_count=0,
        )
    state = "lagging" if stale else "ready"
    return _base_stage(
        "source",
        state=state,
        item_count=len(active),
        ready_count=len(active) - len(stale),
        warning_count=len(stale),
        pending_count=0,
        error_count=len(stale),
        lag_seconds=max((_age_seconds(source.last_sync_at, now) or 0) for source in stale)
        if stale
        else 0,
        safe_error_code="source_stale" if stale else None,
        safe_error="One or more sources have stale observations" if stale else None,
        evidence=evidence,
        source_count=len(active),
        ready_source_count=len(active) - len(stale),
        stale_source_count=len(stale),
    )


def _parse_adapter(
    *, session: Session, tenant_id: str, dataset_id: str, policy: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    documents = list(
        session.scalars(
            select(Document).where(
                Document.tenant_id == tenant_id, Document.dataset_id == dataset_id
            )
        )
    )
    active = [
        document
        for document in documents
        if str(document.lifecycle_state).casefold() not in {"deleted", "recycled"}
    ]
    failed = [
        document for document in active if str(document.status).casefold() in {"error", "failed"}
    ]
    pending = [
        document
        for document in active
        if str(document.status).casefold() not in {"completed", "indexed"}
    ]
    attempts = list(
        session.scalars(
            select(DocumentIngestAttempt)
            .where(
                DocumentIngestAttempt.tenant_id == tenant_id,
                DocumentIngestAttempt.dataset_id == dataset_id,
            )
            .order_by(DocumentIngestAttempt.created_at.desc())
        )
    )
    latest_attempts: dict[str, Any] = {}
    for attempt in attempts:
        latest_attempts.setdefault(str(attempt.document_id), attempt)
    evidence = [
        _adapter_evidence(
            kind="document",
            resource_id=str(document.id),
            revision=int(document.content_revision or 0),
            digest=str(document.file_hash) if document.file_hash else None,
            label=str(document.name or "Document")[:256],
        )
        for document in active[:32]
    ]
    if not active:
        return _base_stage(
            "parse",
            state="missing",
            item_count=0,
            safe_error_code="document_missing",
            safe_error="No active document is available",
            evidence=evidence,
            active_document_count=0,
            failed_document_count=0,
        )
    stale_lag = (
        max((_age_seconds(document.updated_at, now) or 0) for document in pending) if pending else 0
    )
    threshold = int(policy["max_parse_lag_seconds"])
    if failed and len(failed) > int(policy["max_failed_document_count"]):
        state, error_code, error_text = (
            "blocked",
            "parse_failed",
            "One or more documents failed parsing",
        )
    elif pending and stale_lag > threshold:
        state, error_code, error_text = (
            "lagging",
            "parse_lagging",
            "Document parsing is behind the configured threshold",
        )
    else:
        state, error_code, error_text = "ready", None, None
    return _base_stage(
        "parse",
        state=state,
        item_count=len(active),
        ready_count=max(0, len(active) - len(failed) - len(pending)),
        warning_count=len(failed),
        pending_count=len(pending),
        error_count=len(failed),
        lag_seconds=stale_lag,
        safe_error_code=error_code,
        safe_error=error_text,
        evidence=evidence,
        active_document_count=len(active),
        failed_document_count=len(failed),
        latest_attempt_count=len(latest_attempts),
    )


def _chunk_adapter(
    *, session: Session, tenant_id: str, dataset_id: str, policy: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    del policy, now
    chunks = list(
        session.scalars(
            select(ChunkHead).where(
                ChunkHead.tenant_id == tenant_id,
                ChunkHead.dataset_id == dataset_id,
                ChunkHead.enabled.is_(True),
            )
        )
    )
    errors = [
        chunk for chunk in chunks if str(chunk.index_status).casefold() in {"error", "failed"}
    ]
    evidence = [
        _adapter_evidence(
            kind="chunk_head",
            resource_id=str(chunk.id),
            revision=int(chunk.content_revision or 0),
            digest=str(chunk.content_hash) if chunk.content_hash else None,
            label=f"Chunk {chunk.id}"[:256],
        )
        for chunk in chunks[:32]
    ]
    if not chunks:
        state, error_code, error_text = (
            "missing",
            "chunk_missing",
            "No enabled chunks are available",
        )
    elif errors:
        state, error_code, error_text = (
            "blocked",
            "chunk_invalid",
            "One or more chunks are not usable",
        )
    else:
        state, error_code, error_text = "ready", None, None
    return _base_stage(
        "chunk",
        state=state,
        item_count=len(chunks),
        ready_count=max(0, len(chunks) - len(errors)),
        warning_count=len(errors),
        pending_count=0,
        error_count=len(errors),
        safe_error_code=error_code,
        safe_error=error_text,
        evidence=evidence,
    )


def _index_adapter(
    *, session: Session, tenant_id: str, dataset_id: str, policy: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    del now
    operations = list(
        session.scalars(
            select(IndexOperation).where(
                IndexOperation.tenant_id == tenant_id, IndexOperation.dataset_id == dataset_id
            )
        )
    )
    pending = [
        operation
        for operation in operations
        if str(operation.status).casefold() in {"pending", "running", "queued", "claimed"}
    ]
    failed = [
        operation
        for operation in operations
        if str(operation.status).casefold() in {"error", "failed", "dead_letter"}
    ]
    documents = list(
        session.scalars(
            select(Document).where(
                Document.tenant_id == tenant_id, Document.dataset_id == dataset_id
            )
        )
    )
    drift = [
        document
        for document in documents
        if int(document.desired_index_revision or 0) > int(document.indexed_revision or 0)
    ]
    evidence = [
        _adapter_evidence(
            kind="index_operation",
            resource_id=str(operation.id),
            revision=int(operation.target_revision or 0),
            digest=None,
            label=f"Index operation {operation.id}"[:256],
        )
        for operation in (pending + failed)[:32]
    ]
    count = len(operations) + len(drift)
    if not operations and not documents:
        state, error_code, error_text = (
            "missing",
            "index_missing",
            "No index authority is available",
        )
    elif failed:
        state, error_code, error_text = (
            "blocked",
            "index_failed",
            "One or more index operations failed",
        )
    elif len(pending) > int(policy["max_pending_index_count"]):
        state, error_code, error_text = (
            "blocked",
            "index_backlog",
            "The index backlog exceeds policy",
        )
    elif pending or drift:
        state, error_code, error_text = (
            "lagging",
            "index_lagging",
            "Index projection is behind the document authority",
        )
    else:
        state, error_code, error_text = "ready", None, None
    return _base_stage(
        "index",
        state=state,
        item_count=count,
        ready_count=max(0, count - len(failed) - len(pending) - len(drift)),
        warning_count=len(failed) + len(drift),
        pending_count=len(pending) + len(drift),
        error_count=len(failed),
        lag_seconds=len(pending),
        safe_error_code=error_code,
        safe_error=error_text,
        evidence=evidence,
        pending_index_count=len(pending) + len(drift),
    )


def _serve_adapter(
    *, session: Session, tenant_id: str, dataset_id: str, policy: Mapping[str, Any], now: datetime
) -> dict[str, Any]:
    del now
    dataset = session.scalar(
        select(Dataset).where(Dataset.tenant_id == tenant_id, Dataset.id == dataset_id)
    )
    if dataset is None:
        return _base_stage(
            "serve",
            state="unavailable",
            item_count=0,
            safe_error_code="dataset_missing",
            safe_error="Knowledge base is unavailable",
            evidence=[],
        )
    release_id = str(dataset.serving_release_id) if dataset.serving_release_id else None
    release = (
        session.scalar(
            select(DatasetReleaseManifest).where(
                DatasetReleaseManifest.tenant_id == tenant_id,
                DatasetReleaseManifest.dataset_id == dataset_id,
                DatasetReleaseManifest.id == release_id,
            )
        )
        if release_id
        else None
    )
    certification = None
    if release_id:
        certification = session.scalar(
            select(DatasetReleaseQualityCertification)
            .where(
                DatasetReleaseQualityCertification.tenant_id == tenant_id,
                DatasetReleaseQualityCertification.dataset_id == dataset_id,
                DatasetReleaseQualityCertification.release_id == release_id,
            )
            .order_by(DatasetReleaseQualityCertification.created_at.desc())
        )
    evidence: list[dict[str, Any]] = []
    if release is not None:
        evidence.append(
            _adapter_evidence(
                kind="release",
                resource_id=str(release.id),
                revision=int(release.release_number),
                digest=str(release.manifest_digest),
                label=f"Release {release.release_number}",
            )
        )
    if certification is not None:
        evidence.append(
            _adapter_evidence(
                kind="certification",
                resource_id=str(certification.id),
                revision=int(certification.policy_revision),
                digest=str(certification.certification_digest),
                label="Quality certification",
            )
        )
    expected = int(dataset.serving_generation or 0)
    observed = int(release.serving_generation or 0) if release is not None else 0
    if release is None and bool(policy["require_current_release"]):
        return _base_stage(
            "serve",
            state="blocked",
            item_count=0,
            expected_revision=expected,
            observed_revision=observed,
            safe_error_code="release_missing",
            safe_error="No current serving release is bound",
            evidence=evidence,
            current_release_id=None,
            current_certification_id=None,
            expected_serving_generation=expected,
            observed_serving_generation=observed,
        )
    if release is not None and release.readiness_state != "ready":
        return _base_stage(
            "serve",
            state="blocked",
            item_count=1,
            expected_revision=expected,
            observed_revision=observed,
            safe_error_code="release_not_ready",
            safe_error="Current serving release is not ready",
            evidence=evidence,
            current_release_id=str(release.id),
            current_certification_id=str(certification.id) if certification else None,
            expected_serving_generation=expected,
            observed_serving_generation=observed,
        )
    if bool(policy["require_passing_certification"]) and (
        certification is None
        or str(certification.status).casefold() not in {"passed", "certified", "valid", "active"}
    ):
        return _base_stage(
            "serve",
            state="blocked",
            item_count=1,
            expected_revision=expected,
            observed_revision=observed,
            safe_error_code="certification_missing",
            safe_error="Serving certification is not passing",
            evidence=evidence,
            current_release_id=str(release.id) if release else None,
            current_certification_id=str(certification.id) if certification else None,
            expected_serving_generation=expected,
            observed_serving_generation=observed,
        )
    if release is not None and observed < expected:
        return _base_stage(
            "serve",
            state="lagging",
            item_count=1,
            lag_seconds=expected - observed,
            expected_revision=expected,
            observed_revision=observed,
            safe_error_code="serving_generation_lag",
            safe_error="Serving projection is behind the dataset generation",
            evidence=evidence,
            current_release_id=str(release.id),
            current_certification_id=str(certification.id) if certification else None,
            expected_serving_generation=expected,
            observed_serving_generation=observed,
        )
    return _base_stage(
        "serve",
        state="ready",
        item_count=1,
        expected_revision=expected,
        observed_revision=observed,
        evidence=evidence,
        current_release_id=str(release.id) if release else None,
        current_certification_id=str(certification.id) if certification else None,
        expected_serving_generation=expected,
        observed_serving_generation=observed,
    )


SERVING_ADAPTER_REGISTRY: dict[str, Callable[..., Mapping[str, Any]]] = {
    "source": _source_adapter,
    "parse": _parse_adapter,
    "chunk": _chunk_adapter,
    "index": _index_adapter,
    "serve": _serve_adapter,
}


def _adapter_stage(
    code: str,
    raw: Mapping[str, Any],
    *,
    tenant_id: str,
    profile_id: str,
    snapshot_id: str,
    now: datetime,
) -> dict[str, Any]:
    stage = raw.get("stage", raw)
    if not isinstance(stage, Mapping):
        raise KnowledgeServingUnavailable(f"{code} adapter returned an invalid stage")
    values = {
        "id": _stable_id("stage", tenant_id, profile_id, snapshot_id, code),
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "snapshot_id": snapshot_id,
        "stage_code": code,
        "sequence": SERVING_STAGE_CODES.index(code) + 1,
        "state": stage.get("state", "unavailable"),
        "item_count": stage.get("item_count", 0),
        "ready_count": stage.get("ready_count", 0),
        "warning_count": stage.get("warning_count", 0),
        "pending_count": stage.get("pending_count", 0),
        "error_count": stage.get("error_count", 0),
        "lag_seconds": stage.get("lag_seconds", 0),
        "expected_revision": stage.get("expected_revision"),
        "observed_revision": stage.get("observed_revision"),
        "expected_digest": stage.get("expected_digest"),
        "observed_digest": stage.get("observed_digest"),
        "safe_error_code": stage.get("safe_error_code"),
        "safe_error": stage.get("safe_error"),
        "observed_at": _iso(now),
    }
    return canonical_knowledge_serving_stage_fact(values)


def _adapter_evidence_links(
    code: str,
    raw: Mapping[str, Any],
    *,
    tenant_id: str,
    profile_id: str,
    snapshot_id: str,
    stage_fact_id: str,
    now: datetime,
) -> list[dict[str, Any]]:
    del code
    raw_items = raw.get("evidence", [])
    if raw_items is None:
        return []
    if not isinstance(raw_items, Iterable) or isinstance(raw_items, (str, bytes, Mapping)):
        raise KnowledgeServingUnavailable("serving adapter evidence is invalid")
    links: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, Mapping):
            raise KnowledgeServingUnavailable("serving adapter evidence item is invalid")
        payload = {
            "id": _stable_id("evidence", tenant_id, profile_id, snapshot_id, stage_fact_id, index),
            "tenant_id": tenant_id,
            "profile_id": profile_id,
            "snapshot_id": snapshot_id,
            "stage_fact_id": stage_fact_id,
            "evidence_kind": item.get("evidence_kind"),
            "resource_id": item.get("resource_id"),
            "resource_revision": item.get("resource_revision"),
            "resource_digest": item.get("resource_digest"),
            "route_code": item.get("route_code"),
            "safe_label": item.get("safe_label"),
            "created_at": _iso(now),
        }
        links.append(canonical_knowledge_serving_evidence_link(payload))
    return links


def _collect_observation(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    profile_id: str,
    policy: Mapping[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read known authorities through five fixed adapters and canonicalize them."""

    raw_by_code: dict[str, Mapping[str, Any]] = {}
    fingerprints: dict[str, Any] = {}
    for code in SERVING_ADAPTER_ORDER:
        adapter = SERVING_ADAPTER_REGISTRY.get(code)
        if not callable(adapter):
            raise KnowledgeServingUnavailable(f"serving adapter registry is missing {code}")
        try:
            raw = adapter(
                session=session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                policy=dict(policy),
                now=now,
            )
        except KnowledgeServingServiceError:
            raise
        except (SQLAlchemyError, KnowledgeServingAuthorityError, KeyError, TypeError, ValueError):
            raw = {
                "state": "unavailable",
                "item_count": 0,
                "error_count": 0,
                "lag_seconds": 0,
                "safe_error_code": "adapter_unavailable",
                "safe_error": f"{code} authority is unavailable",
                "evidence": [],
            }
        if not isinstance(raw, Mapping):
            raise KnowledgeServingUnavailable(f"{code} adapter returned an invalid object")
        raw_by_code[code] = dict(raw)
        stage = raw.get("stage", raw)
        if not isinstance(stage, Mapping):
            stage = {}
        fingerprints[code] = {
            "state": stage.get("state", "unavailable"),
            "item_count": stage.get("item_count", 0),
            "error_count": stage.get("error_count", 0),
            "lag_seconds": stage.get("lag_seconds", 0),
            "expected_revision": stage.get("expected_revision"),
            "observed_revision": stage.get("observed_revision"),
            "expected_digest": stage.get("expected_digest"),
            "observed_digest": stage.get("observed_digest"),
            "safe_error_code": stage.get("safe_error_code"),
            "evidence": [
                {
                    "evidence_kind": item.get("evidence_kind"),
                    "resource_id": item.get("resource_id"),
                    "resource_revision": item.get("resource_revision"),
                    "resource_digest": item.get("resource_digest"),
                }
                for item in raw.get("evidence", [])
                if isinstance(item, Mapping)
            ],
        }
    fingerprint = _canonical_payload(fingerprints)
    observation_key = _stable_id(
        "observation",
        tenant_id,
        profile_id,
        policy["policy_digest"],
        fingerprint,
        _iso(now),
        maximum=192,
    )
    snapshot_id = _stable_id("snapshot", tenant_id, profile_id, observation_key)
    facts: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    for code in SERVING_STAGE_CODES:
        fact = _adapter_stage(
            code,
            raw_by_code[code],
            tenant_id=tenant_id,
            profile_id=profile_id,
            snapshot_id=snapshot_id,
            now=now,
        )
        facts.append(fact)
        evidence.extend(
            _adapter_evidence_links(
                code,
                raw_by_code[code],
                tenant_id=tenant_id,
                profile_id=profile_id,
                snapshot_id=snapshot_id,
                stage_fact_id=str(fact["id"]),
                now=now,
            )
        )
    facts_by_code = {fact["stage_code"]: fact for fact in facts}
    source_fact = facts_by_code["source"]
    parse_fact = facts_by_code["parse"]
    index_fact = facts_by_code["index"]
    serve_fact = facts_by_code["serve"]
    serve_raw = raw_by_code["serve"].get("stage", raw_by_code["serve"])
    serve_raw = serve_raw if isinstance(serve_raw, Mapping) else {}
    snapshot_values = {
        "id": snapshot_id,
        "tenant_id": tenant_id,
        "profile_id": profile_id,
        "policy_revision_id": policy["id"],
        "observation_key": observation_key,
        "state": None,
        "source_count": source_fact["item_count"],
        "ready_source_count": source_fact["ready_count"],
        "stale_source_count": source_fact["warning_count"],
        "active_document_count": parse_fact["item_count"],
        "failed_document_count": parse_fact["warning_count"],
        "pending_index_count": index_fact["pending_count"],
        "expected_serving_generation": serve_fact["expected_revision"] or 0,
        "observed_serving_generation": serve_fact["observed_revision"] or 0,
        "current_release_id": serve_raw.get("current_release_id"),
        "current_certification_id": serve_raw.get("current_certification_id"),
        "stage_count": None,
        "ready_stage_count": None,
        "blocked_stage_count": None,
        "snapshot_digest": None,
        "as_of": _iso(now),
        "created_at": _iso(now),
        "created_by": policy.get("created_by") or "system:serving",
    }
    snapshot = canonical_knowledge_serving_snapshot(
        snapshot_values, stage_facts=facts, evidence_links=evidence
    )
    return snapshot, facts, evidence


def _insert_row(session: Session, table: Table, values: Mapping[str, Any]) -> None:
    filtered = {key: value for key, value in values.items() if key in table.c}
    session.execute(table.insert().values(**filtered))


def _event_sequence(
    session: Session, table: Table, *, tenant_id: str, profile_id: str, stream_key: str
) -> tuple[int, str | None]:
    row = session.execute(
        select(table.c.sequence, table.c.event_digest)
        .where(
            table.c.tenant_id == tenant_id,
            table.c.profile_id == profile_id,
            table.c.stream_key == stream_key,
        )
        .order_by(table.c.sequence.desc())
        .limit(1)
    ).first()
    if row is None:
        return 1, None
    return int(row[0]) + 1, str(row[1])


def _append_event(
    session: Session,
    table: Table,
    *,
    tenant_id: str,
    profile_id: str,
    snapshot_id: str | None,
    event_type: str,
    actor_id: str,
    request_id: str,
    safe_snapshot: Mapping[str, Any],
    occurred_at: datetime,
) -> dict[str, Any]:
    stream_key = f"profile:{profile_id}"
    sequence, previous = _event_sequence(
        session, table, tenant_id=tenant_id, profile_id=profile_id, stream_key=stream_key
    )
    event = canonical_knowledge_serving_event(
        {
            "id": _stable_id(
                "event", tenant_id, profile_id, stream_key, sequence, event_type, request_id
            ),
            "tenant_id": tenant_id,
            "profile_id": profile_id,
            "snapshot_id": snapshot_id,
            "stream_key": stream_key,
            "sequence": sequence,
            "event_type": event_type,
            "previous_event_digest": previous,
            "actor_id": actor_id,
            "request_id": request_id,
            "safe_snapshot_json": dict(safe_snapshot),
            "occurred_at": _iso(occurred_at),
        }
    )
    _insert_row(
        session,
        table,
        {**event, "safe_snapshot_json": event["safe_snapshot_json"], "occurred_at": occurred_at},
    )
    return event


def _mutation_result(
    *,
    operation: str,
    resource_id: str,
    revision: int,
    message: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "state": "applied",
        "operation": operation,
        "resource_id": resource_id,
        "revision": revision,
        "message": message,
        "retryable": False,
        **extra,
    }


def create_serving_profile(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    name: str,
    reason: str,
    idempotency_key: str,
    workspace_id: str | None = None,
    request_id: str | None = None,
    now: Any = None,
) -> ServiceResult:
    """Create the one operator-owned profile for a Tenant/Dataset pair."""

    tenant = _id(tenant_id, "tenant_id", maximum=64)
    actor = _id(actor_id, "actor_id", maximum=64)
    dataset = _id(dataset_id, "dataset_id", maximum=64)
    safe_name = _safe_reason(name) if len(str(name or "")) <= 512 else str(name)
    safe_reason = _safe_reason(reason)
    workspace = _optional_id(workspace_id, "workspace_id", maximum=128)
    operation = "create_serving_profile"
    request_body = {"name": safe_name, "workspace_id": workspace, "reason": safe_reason}
    request_hash = _mutation_request(
        operation=operation, tenant_id=tenant, dataset_id=dataset, body=request_body
    )
    with engine_serialization_lock(engine), idempotency_key_lock(tenant, actor, idempotency_key):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                _require_member(session, tenant, actor, manager=True)
                _require_dataset(session, tenant, dataset)
                if workspace is not None:
                    workspace_row = session.scalar(
                        select(TenantWorkspace).where(
                            TenantWorkspace.tenant_id == tenant,
                            TenantWorkspace.id == workspace,
                            TenantWorkspace.status == "active",
                        )
                    )
                    if workspace_row is None:
                        raise KnowledgeServingNotFound("workspace was not found in this tenant")
                tables = _tables(session)
                reservation = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    operation=operation,
                    resource_type="knowledge_serving_profile",
                )
                if reservation.replay is not None:
                    return ServiceResult(
                        dict(reservation.replay.response), reservation.replay.http_status
                    )
                existing = (
                    session.execute(
                        select(tables.profiles).where(
                            tables.profiles.c.tenant_id == tenant,
                            tables.profiles.c.dataset_id == dataset,
                        )
                    )
                    .mappings()
                    .first()
                )
                if existing is not None:
                    raise KnowledgeServingConflict(
                        "a serving profile already exists for this knowledge base"
                    )
                moment = _now(now)
                profile_id = _stable_id("serving-profile", tenant, dataset)
                values = {
                    "id": profile_id,
                    "tenant_id": tenant,
                    "workspace_id": workspace,
                    "dataset_id": dataset,
                    "name": safe_name,
                    "normalized_name": safe_name.casefold(),
                    "status": "draft",
                    "active_profile_key": None,
                    "revision": 1,
                    "current_policy_revision_id": None,
                    "current_snapshot_id": None,
                    "created_at": moment,
                    "created_by": actor,
                    "updated_at": moment,
                    "updated_by": actor,
                    "archived_at": None,
                    "archived_by": None,
                }
                _insert_row(session, tables.profiles, values)
                _append_event(
                    session,
                    tables.events,
                    tenant_id=tenant,
                    profile_id=profile_id,
                    snapshot_id=None,
                    event_type="profile_created",
                    actor_id=actor,
                    request_id=request_id or f"{operation}:{profile_id}",
                    safe_snapshot={"status": "draft", "revision": 1},
                    occurred_at=moment,
                )
                profile = _profile_wire(values)
                response = _mutation_result(
                    operation=operation, resource_id=profile_id, revision=1, profile=profile
                )
                return _complete(session, reservation, response, status=201, resource_id=profile_id)


def create_serving_policy_revision(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    expected_profile_revision: int,
    expected_policy_digest: str | None,
    max_source_staleness_seconds: int,
    max_parse_lag_seconds: int,
    max_index_lag_seconds: int,
    max_failed_document_count: int,
    max_pending_index_count: int,
    require_current_release: bool,
    require_passing_certification: bool,
    reason: str,
    idempotency_key: str,
    request_id: str | None = None,
    now: Any = None,
) -> ServiceResult:
    """Append one immutable policy revision without activating it."""

    tenant = _id(tenant_id, "tenant_id", maximum=64)
    actor = _id(actor_id, "actor_id", maximum=64)
    dataset = _id(dataset_id, "dataset_id", maximum=64)
    expected = expected_profile_revision
    if type(expected) is not int or expected < 1 or expected > 9_007_199_254_740_991:
        raise KnowledgeServingInvalid("expected_profile_revision is required")
    policy_fence = _digest(expected_policy_digest, "expected_policy_digest", optional=True)
    safe_reason = _safe_reason(reason)
    operation = "create_serving_policy_revision"
    request_body = {
        "expected_profile_revision": expected,
        "expected_policy_digest": policy_fence,
        "max_source_staleness_seconds": max_source_staleness_seconds,
        "max_parse_lag_seconds": max_parse_lag_seconds,
        "max_index_lag_seconds": max_index_lag_seconds,
        "max_failed_document_count": max_failed_document_count,
        "max_pending_index_count": max_pending_index_count,
        "require_current_release": require_current_release,
        "require_passing_certification": require_passing_certification,
        "reason": safe_reason,
    }
    request_hash = _mutation_request(
        operation=operation, tenant_id=tenant, dataset_id=dataset, body=request_body
    )
    with engine_serialization_lock(engine), idempotency_key_lock(tenant, actor, idempotency_key):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                _require_member(session, tenant, actor, manager=True)
                _require_dataset(session, tenant, dataset)
                tables = _tables(session)
                reservation = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    operation=operation,
                    resource_type="knowledge_serving_policy_revision",
                )
                if reservation.replay is not None:
                    return ServiceResult(
                        dict(reservation.replay.response), reservation.replay.http_status
                    )
                profile_row = _profile_row(session, tables, tenant, dataset)
                profile = str(profile_row["id"])
                if int(profile_row["revision"]) != expected:
                    raise KnowledgeServingConflict("serving profile revision fence is stale")
                current_id = profile_row.get("current_policy_revision_id")
                if current_id is None:
                    if policy_fence is not None:
                        raise KnowledgeServingConflict(
                            "expected_policy_digest must be null before the first policy"
                        )
                else:
                    if policy_fence is None:
                        raise KnowledgeServingConflict(
                            "expected_policy_digest is required for the current policy"
                        )
                    current = _policy_row(session, tables, tenant, profile, str(current_id))
                    if str(current["policy_digest"]) != policy_fence:
                        raise KnowledgeServingConflict("serving policy digest fence is stale")
                next_revision = (
                    int(
                        session.scalar(
                            select(func.max(tables.policies.c.revision)).where(
                                tables.policies.c.tenant_id == tenant,
                                tables.policies.c.profile_id == profile,
                            )
                        )
                        or 0
                    )
                    + 1
                )
                moment = _now(now)
                policy_id = _stable_id("serving-policy", tenant, profile, next_revision)
                values = {
                    "id": policy_id,
                    "tenant_id": tenant,
                    "profile_id": profile,
                    "revision": next_revision,
                    "max_source_staleness_seconds": max_source_staleness_seconds,
                    "max_parse_lag_seconds": max_parse_lag_seconds,
                    "max_index_lag_seconds": max_index_lag_seconds,
                    "max_failed_document_count": max_failed_document_count,
                    "max_pending_index_count": max_pending_index_count,
                    "require_current_release": require_current_release,
                    "require_passing_certification": require_passing_certification,
                    "policy_digest": None,
                    "created_at": moment,
                    "created_by": actor,
                }
                policy_wire = _policy_wire(values)
                values["policy_digest"] = policy_wire["policy_digest"]
                _insert_row(session, tables.policies, values)
                update_result = session.execute(
                    tables.profiles.update()
                    .where(
                        tables.profiles.c.tenant_id == tenant,
                        tables.profiles.c.id == profile,
                        tables.profiles.c.revision == expected,
                    )
                    .values(revision=expected + 1, updated_at=moment, updated_by=actor)
                )
                if update_result.rowcount != 1:
                    raise KnowledgeServingConflict("serving profile revision changed concurrently")
                profile_row = dict(profile_row)
                profile_row.update(
                    {"revision": expected + 1, "updated_at": moment, "updated_by": actor}
                )
                profile_wire = _profile_wire(profile_row)
                _append_event(
                    session,
                    tables.events,
                    tenant_id=tenant,
                    profile_id=profile,
                    snapshot_id=None,
                    event_type="policy_revision_created",
                    actor_id=actor,
                    request_id=request_id or f"{operation}:{policy_id}",
                    safe_snapshot={
                        "policy_revision": next_revision,
                        "policy_digest": policy_wire["policy_digest"],
                    },
                    occurred_at=moment,
                )
                response = _mutation_result(
                    operation=operation,
                    resource_id=policy_id,
                    revision=expected + 1,
                    policy=policy_wire,
                    profile=profile_wire,
                )
                return _complete(session, reservation, response, status=201, resource_id=policy_id)


def activate_serving_policy(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    policy_revision_id: str,
    expected_profile_revision: int,
    expected_policy_digest: str,
    reason: str,
    idempotency_key: str,
    request_id: str | None = None,
    now: Any = None,
) -> ServiceResult:
    """Activate a policy with profile revision and policy digest fences."""

    tenant = _id(tenant_id, "tenant_id", maximum=64)
    actor = _id(actor_id, "actor_id", maximum=64)
    dataset = _id(dataset_id, "dataset_id", maximum=64)
    policy_id = _id(policy_revision_id, "policy_revision_id")
    expected = expected_profile_revision
    if type(expected) is not int or expected < 1 or expected > 9_007_199_254_740_991:
        raise KnowledgeServingInvalid("expected_profile_revision is required")
    policy_fence = _digest(expected_policy_digest, "expected_policy_digest")
    safe_reason = _safe_reason(reason)
    operation = "activate_serving_policy"
    request_body = {
        "policy_revision_id": policy_id,
        "expected_profile_revision": expected,
        "expected_policy_digest": policy_fence,
        "reason": safe_reason,
    }
    request_hash = _mutation_request(
        operation=operation, tenant_id=tenant, dataset_id=dataset, body=request_body
    )
    with engine_serialization_lock(engine), idempotency_key_lock(tenant, actor, idempotency_key):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                _require_member(session, tenant, actor, manager=True)
                _require_dataset(session, tenant, dataset)
                tables = _tables(session)
                reservation = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    operation=operation,
                    resource_type="knowledge_serving_profile",
                )
                if reservation.replay is not None:
                    return ServiceResult(
                        dict(reservation.replay.response), reservation.replay.http_status
                    )
                profile_row = _profile_row(session, tables, tenant, dataset)
                profile = str(profile_row["id"])
                if int(profile_row["revision"]) != expected:
                    raise KnowledgeServingConflict("serving profile revision fence is stale")
                policy_row = _policy_row(session, tables, tenant, profile, policy_id)
                if str(policy_row["policy_digest"]) != policy_fence:
                    raise KnowledgeServingConflict("serving policy digest fence is stale")
                moment = _now(now)
                updated = session.execute(
                    tables.profiles.update()
                    .where(
                        tables.profiles.c.tenant_id == tenant,
                        tables.profiles.c.id == profile,
                        tables.profiles.c.revision == expected,
                    )
                    .values(
                        status="active",
                        active_profile_key=dataset,
                        current_policy_revision_id=policy_id,
                        revision=expected + 1,
                        updated_at=moment,
                        updated_by=actor,
                    )
                )
                if updated.rowcount != 1:
                    raise KnowledgeServingConflict("serving profile changed concurrently")
                profile_row.update(
                    {
                        "status": "active",
                        "active_profile_key": dataset,
                        "current_policy_revision_id": policy_id,
                        "revision": expected + 1,
                        "updated_at": moment,
                        "updated_by": actor,
                    }
                )
                profile_wire = _profile_wire(profile_row)
                policy_wire = _policy_wire(policy_row)
                _append_event(
                    session,
                    tables.events,
                    tenant_id=tenant,
                    profile_id=profile,
                    snapshot_id=None,
                    event_type="policy_activated",
                    actor_id=actor,
                    request_id=request_id or f"{operation}:{profile}",
                    safe_snapshot={
                        "policy_revision_id": policy_id,
                        "policy_digest": policy_wire["policy_digest"],
                    },
                    occurred_at=moment,
                )
                response = _mutation_result(
                    operation=operation,
                    resource_id=profile,
                    revision=expected + 1,
                    profile=profile_wire,
                    policy=policy_wire,
                )
                return _complete(session, reservation, response, status=200, resource_id=profile)


def record_knowledge_serving_snapshot(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    profile_id: str,
    policy_revision_id: str | None = None,
    expected_profile_revision: int | None = None,
    expected_revision: int | None = None,
    reason: str,
    idempotency_key: str,
    request_id: str | None = None,
    now: Any = None,
) -> ServiceResult:
    """Internal-only observation writer; never expose this as an HTTP route."""

    tenant = _id(tenant_id, "tenant_id", maximum=64)
    actor = _id(actor_id, "actor_id", maximum=64)
    dataset = _id(dataset_id, "dataset_id", maximum=64)
    profile = _id(profile_id, "profile_id")
    expected = (
        expected_profile_revision if expected_profile_revision is not None else expected_revision
    )
    if type(expected) is not int or expected < 1 or expected > 9_007_199_254_740_991:
        raise KnowledgeServingInvalid("expected_profile_revision is required")
    safe_reason = _safe_reason(reason)
    operation = "record_knowledge_serving_snapshot"
    request_body = {
        "profile_id": profile,
        "policy_revision_id": policy_revision_id,
        "expected_profile_revision": expected,
        "reason": safe_reason,
    }
    request_hash = _mutation_request(
        operation=operation, tenant_id=tenant, dataset_id=dataset, body=request_body
    )
    with engine_serialization_lock(engine), idempotency_key_lock(tenant, actor, idempotency_key):
        with Session(engine, expire_on_commit=False) as session:
            with session.begin():
                _require_member(session, tenant, actor, manager=True)
                _require_dataset(session, tenant, dataset)
                tables = _tables(session)
                reservation = _reserve(
                    session,
                    tenant_id=tenant,
                    actor_id=actor,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    operation=operation,
                    resource_type="knowledge_serving_snapshot",
                )
                if reservation.replay is not None:
                    return ServiceResult(
                        dict(reservation.replay.response), reservation.replay.http_status
                    )
                profile_row = _profile_row(session, tables, tenant, dataset, profile)
                if int(profile_row["revision"]) != expected:
                    raise KnowledgeServingConflict("serving profile revision fence is stale")
                current_policy_id = str(profile_row["current_policy_revision_id"] or "")
                selected_policy_id = (
                    _optional_id(policy_revision_id, "policy_revision_id") or current_policy_id
                )
                if not selected_policy_id:
                    raise KnowledgeServingConflict("serving profile has no current policy revision")
                policy_row = _policy_row(session, tables, tenant, profile, selected_policy_id)
                if current_policy_id and current_policy_id != selected_policy_id:
                    raise KnowledgeServingConflict(
                        "snapshot policy is not the current profile policy"
                    )
                moment = _now(now)
                policy_wire = _policy_wire(policy_row)
                snapshot, facts, evidence = _collect_observation(
                    session,
                    tenant_id=tenant,
                    dataset_id=dataset,
                    profile_id=profile,
                    policy=policy_wire,
                    now=moment,
                )
                existing = (
                    session.execute(
                        select(tables.snapshots).where(
                            tables.snapshots.c.tenant_id == tenant,
                            tables.snapshots.c.profile_id == profile,
                            tables.snapshots.c.observation_key == snapshot["observation_key"],
                        )
                    )
                    .mappings()
                    .first()
                )
                if existing is not None:
                    raise KnowledgeServingConflict("the observation key has already been recorded")
                _insert_row(
                    session, tables.snapshots, {**snapshot, "as_of": moment, "created_at": moment}
                )
                for fact in facts:
                    _insert_row(session, tables.stage_facts, {**fact, "observed_at": moment})
                for link in evidence:
                    _insert_row(session, tables.evidence_links, {**link, "created_at": moment})
                previous_snapshot = None
                if profile_row.get("current_snapshot_id"):
                    previous_snapshot = (
                        session.execute(
                            select(tables.snapshots).where(
                                tables.snapshots.c.tenant_id == tenant,
                                tables.snapshots.c.profile_id == profile,
                                tables.snapshots.c.id == profile_row["current_snapshot_id"],
                            )
                        )
                        .mappings()
                        .first()
                    )
                _append_event(
                    session,
                    tables.events,
                    tenant_id=tenant,
                    profile_id=profile,
                    snapshot_id=str(snapshot["id"]),
                    event_type="snapshot_recorded",
                    actor_id=actor,
                    request_id=request_id or f"{operation}:{snapshot['id']}",
                    safe_snapshot={
                        "state": snapshot["state"],
                        "snapshot_digest": snapshot["snapshot_digest"],
                        "observation_key": snapshot["observation_key"],
                    },
                    occurred_at=moment,
                )
                for fact in facts:
                    if fact["state"] == "lagging":
                        _append_event(
                            session,
                            tables.events,
                            tenant_id=tenant,
                            profile_id=profile,
                            snapshot_id=str(snapshot["id"]),
                            event_type="stage_degraded",
                            actor_id=actor,
                            request_id=request_id
                            or f"{operation}:{snapshot['id']}:{fact['stage_code']}",
                            safe_snapshot={
                                "stage_code": fact["stage_code"],
                                "state": fact["state"],
                                "stage_digest": fact["stage_digest"],
                            },
                            occurred_at=moment,
                        )
                    elif fact["state"] == "blocked":
                        _append_event(
                            session,
                            tables.events,
                            tenant_id=tenant,
                            profile_id=profile,
                            snapshot_id=str(snapshot["id"]),
                            event_type="stage_blocked",
                            actor_id=actor,
                            request_id=request_id
                            or f"{operation}:{snapshot['id']}:{fact['stage_code']}",
                            safe_snapshot={
                                "stage_code": fact["stage_code"],
                                "state": fact["state"],
                                "stage_digest": fact["stage_digest"],
                            },
                            occurred_at=moment,
                        )
                if (
                    previous_snapshot is not None
                    and str(previous_snapshot.get("state")) != "ready"
                    and snapshot["state"] == "ready"
                ):
                    _append_event(
                        session,
                        tables.events,
                        tenant_id=tenant,
                        profile_id=profile,
                        snapshot_id=str(snapshot["id"]),
                        event_type="service_recovered",
                        actor_id=actor,
                        request_id=request_id or f"{operation}:{snapshot['id']}:recovered",
                        safe_snapshot={
                            "previous_state": previous_snapshot.get("state"),
                            "state": "ready",
                        },
                        occurred_at=moment,
                    )
                updated = session.execute(
                    tables.profiles.update()
                    .where(
                        tables.profiles.c.tenant_id == tenant,
                        tables.profiles.c.id == profile,
                        tables.profiles.c.revision == expected,
                    )
                    .values(
                        current_snapshot_id=snapshot["id"],
                        revision=expected + 1,
                        updated_at=moment,
                        updated_by=actor,
                    )
                )
                if updated.rowcount != 1:
                    raise KnowledgeServingConflict(
                        "serving profile changed while recording snapshot"
                    )
                profile_row.update(
                    {
                        "current_snapshot_id": snapshot["id"],
                        "revision": expected + 1,
                        "updated_at": moment,
                        "updated_by": actor,
                    }
                )
                response = _mutation_result(
                    operation=operation,
                    resource_id=str(snapshot["id"]),
                    revision=expected + 1,
                    snapshot=snapshot,
                    stage_facts=facts,
                    evidence_links=evidence,
                    profile=_profile_wire(profile_row),
                )
                return _complete(
                    session, reservation, response, status=201, resource_id=str(snapshot["id"])
                )


def _preview_observation(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    profile_id: str,
    policy: Mapping[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    return _collect_observation(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        profile_id=profile_id,
        policy=policy,
        now=now,
    )


def preview_serving_profile(
    engine: Any,
    *,
    tenant_id: str,
    actor_id: str,
    dataset_id: str,
    profile_id: str,
    expected_profile_revision: int,
    expected_policy_digest: str | None,
    max_source_staleness_seconds: int,
    max_parse_lag_seconds: int,
    max_index_lag_seconds: int,
    max_failed_document_count: int,
    max_pending_index_count: int,
    require_current_release: bool,
    require_passing_certification: bool,
    reason: str,
    now: Any = None,
    request_id: str | None = None,
) -> ServiceResult:
    del request_id
    """Evaluate current read adapters without inserting any Stage26 row."""

    tenant = _id(tenant_id, "tenant_id", maximum=64)
    actor = _id(actor_id, "actor_id", maximum=64)
    dataset = _id(dataset_id, "dataset_id", maximum=64)
    profile = _id(profile_id, "profile_id")
    if (
        type(expected_profile_revision) is not int
        or expected_profile_revision < 1
        or expected_profile_revision > 9_007_199_254_740_991
    ):
        raise KnowledgeServingInvalid("expected_profile_revision is required")
    policy_fence = _digest(expected_policy_digest, "expected_policy_digest", optional=True)
    _safe_reason(reason)
    with Session(engine, expire_on_commit=False) as session:
        _require_member(session, tenant, actor, manager=False)
        _require_dataset(session, tenant, dataset)
        tables = _tables(session)
        profile_row = _profile_row(session, tables, tenant, dataset, profile)
        if int(profile_row["revision"]) != expected_profile_revision:
            raise KnowledgeServingConflict("serving profile revision fence is stale")
        current_policy_id = profile_row.get("current_policy_revision_id")
        current_policy = None
        if current_policy_id is None:
            if policy_fence is not None:
                raise KnowledgeServingConflict(
                    "expected_policy_digest must be null when no policy is active"
                )
            next_policy_revision = 1
        else:
            current_policy = _policy_wire(
                _policy_row(session, tables, tenant, profile, str(current_policy_id))
            )
            if policy_fence is None:
                raise KnowledgeServingConflict(
                    "expected_policy_digest is required for the current policy"
                )
            if current_policy["policy_digest"] != policy_fence:
                raise KnowledgeServingConflict("serving policy digest fence is stale")
            next_policy_revision = int(current_policy["revision"]) + 1
        moment = _now(now)
        transient_policy = {
            "id": _stable_id(
                "preview-policy",
                tenant,
                profile,
                next_policy_revision,
                max_source_staleness_seconds,
                max_parse_lag_seconds,
                max_index_lag_seconds,
                max_failed_document_count,
                max_pending_index_count,
                require_current_release,
                require_passing_certification,
            ),
            "tenant_id": tenant,
            "profile_id": profile,
            "revision": next_policy_revision,
            "max_source_staleness_seconds": max_source_staleness_seconds,
            "max_parse_lag_seconds": max_parse_lag_seconds,
            "max_index_lag_seconds": max_index_lag_seconds,
            "max_failed_document_count": max_failed_document_count,
            "max_pending_index_count": max_pending_index_count,
            "require_current_release": require_current_release,
            "require_passing_certification": require_passing_certification,
            "policy_digest": None,
            "created_at": _iso(moment),
            "created_by": actor,
        }
        try:
            policy = canonical_knowledge_serving_policy(transient_policy)
        except KnowledgeServingAuthorityError as exc:
            raise KnowledgeServingInvalid(f"transient serving policy is invalid: {exc}") from exc
        snapshot, facts, evidence = _preview_observation(
            session,
            tenant_id=tenant,
            dataset_id=dataset,
            profile_id=profile,
            policy=policy,
            now=moment,
        )
        blockers = [
            {
                "code": fact.get("safe_error_code") or f"{fact['stage_code']}_{fact['state']}",
                "stage_code": fact["stage_code"],
                "safe_message": fact.get("safe_error")
                or f"{fact['stage_code']} stage is {fact['state']}",
            }
            for fact in facts
            if fact["state"] != "ready"
        ]
        del evidence
        return ServiceResult(
            {
                "preview": True,
                "state": snapshot["state"],
                "policy_revision": int(policy["revision"]),
                "stage_facts": facts,
                "blockers": blockers,
            },
            200,
        )


def _profile_ids(
    session: Session, tables: ServingTables, *, tenant_id: str, dataset_id: str
) -> list[str]:
    return [
        str(value)
        for value in session.scalars(
            select(tables.profiles.c.id)
            .where(
                tables.profiles.c.tenant_id == tenant_id, tables.profiles.c.dataset_id == dataset_id
            )
            .order_by(tables.profiles.c.id)
        )
    ]


def _facts_for_snapshot(
    session: Session, tables: ServingTables, *, tenant_id: str, profile_id: str, snapshot_id: str
) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            select(tables.stage_facts)
            .where(
                tables.stage_facts.c.tenant_id == tenant_id,
                tables.stage_facts.c.profile_id == profile_id,
                tables.stage_facts.c.snapshot_id == snapshot_id,
            )
            .order_by(tables.stage_facts.c.sequence)
        )
        .mappings()
        .all()
    )
    return [_stage_wire(dict(row)) for row in rows]


def _evidence_for_snapshot(
    session: Session, tables: ServingTables, *, tenant_id: str, profile_id: str, snapshot_id: str
) -> list[dict[str, Any]]:
    rows = (
        session.execute(
            select(tables.evidence_links)
            .where(
                tables.evidence_links.c.tenant_id == tenant_id,
                tables.evidence_links.c.profile_id == profile_id,
                tables.evidence_links.c.snapshot_id == snapshot_id,
            )
            .order_by(tables.evidence_links.c.created_at, tables.evidence_links.c.id)
        )
        .mappings()
        .all()
    )
    return [_evidence_wire(dict(row)) for row in rows]


def _events_for_snapshot(
    session: Session,
    tables: ServingTables,
    *,
    tenant_id: str,
    profile_id: str,
    snapshot_id: str | None = None,
) -> list[dict[str, Any]]:
    statement = select(tables.events).where(
        tables.events.c.tenant_id == tenant_id, tables.events.c.profile_id == profile_id
    )
    if snapshot_id is not None:
        statement = statement.where(tables.events.c.snapshot_id == snapshot_id)
    rows = (
        session.execute(statement.order_by(tables.events.c.sequence, tables.events.c.id))
        .mappings()
        .all()
    )
    return [_event_wire(dict(row)) for row in rows]


def get_serving_profile(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    profile_id: str | None = None,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> ServiceResult:
    del request_id
    tenant = _id(tenant_id, "tenant_id", maximum=64)
    dataset = _id(dataset_id, "dataset_id", maximum=64)
    profile = _optional_id(profile_id, "profile_id")
    with Session(engine, expire_on_commit=False) as session:
        if actor_id is not None:
            _require_member(session, tenant, _id(actor_id, "actor_id", maximum=64), manager=False)
        _require_dataset(session, tenant, dataset)
        tables = _tables(session)
        row = _profile_row(session, tables, tenant, dataset, profile)
        current_policy = None
        current_snapshot = None
        policy_id = row.get("current_policy_revision_id")
        if policy_id:
            current_policy = _policy_wire(
                _policy_row(session, tables, tenant, str(row["id"]), str(policy_id))
            )
        snapshot_id = row.get("current_snapshot_id")
        if snapshot_id:
            snapshot_row = (
                session.execute(
                    select(tables.snapshots).where(
                        tables.snapshots.c.tenant_id == tenant,
                        tables.snapshots.c.profile_id == str(row["id"]),
                        tables.snapshots.c.id == str(snapshot_id),
                    )
                )
                .mappings()
                .first()
            )
            if snapshot_row is not None:
                facts = _facts_for_snapshot(
                    session,
                    tables,
                    tenant_id=tenant,
                    profile_id=str(row["id"]),
                    snapshot_id=str(snapshot_id),
                )
                evidence = _evidence_for_snapshot(
                    session,
                    tables,
                    tenant_id=tenant,
                    profile_id=str(row["id"]),
                    snapshot_id=str(snapshot_id),
                )
                current_snapshot = _snapshot_wire(dict(snapshot_row), facts, evidence)
        return ServiceResult(
            {
                "profile": _profile_wire(row),
                "current_policy": current_policy,
                "current_snapshot": current_snapshot,
            }
        )


def get_serving_summary(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> ServiceResult:
    del request_id
    tenant = _id(tenant_id, "tenant_id", maximum=64)
    dataset = _id(dataset_id, "dataset_id", maximum=64)
    with Session(engine, expire_on_commit=False) as session:
        if actor_id is not None:
            _require_member(session, tenant, _id(actor_id, "actor_id", maximum=64), manager=False)
        _require_dataset(session, tenant, dataset)
        tables = _tables(session)
        profile_result = (
            session.execute(
                select(tables.profiles)
                .where(
                    tables.profiles.c.tenant_id == tenant, tables.profiles.c.dataset_id == dataset
                )
                .order_by(tables.profiles.c.id)
            )
            .mappings()
            .first()
        )
        empty = {
            "tenant_id": tenant,
            "dataset_id": dataset,
            "profile_id": None,
            "profile_name": None,
            "profile_status": None,
            "state": "unavailable",
            "as_of": None,
            "snapshot_id": None,
            "snapshot_digest": None,
            "policy_revision": None,
            "serving_generation": None,
            "source_count": 0,
            "ready_source_count": 0,
            "stale_source_count": 0,
            "active_document_count": 0,
            "failed_document_count": 0,
            "pending_index_count": 0,
            "stage_count": 0,
            "ready_stage_count": 0,
            "blocked_stage_count": 0,
            "current_release_id": None,
            "current_certification_id": None,
            "stage_facts": [],
            "reason_code": "profile_missing",
        }
        if profile_result is None:
            return ServiceResult(empty)
        profile_row = dict(profile_result)
        snapshot_id = profile_row.get("current_snapshot_id")
        if not snapshot_id:
            empty.update(
                {
                    "profile_id": str(profile_row["id"]),
                    "profile_name": str(profile_row["name"]),
                    "profile_status": str(profile_row["status"]),
                    "reason_code": "snapshot_missing",
                }
            )
            return ServiceResult(empty)
        snapshot_row = (
            session.execute(
                select(tables.snapshots).where(
                    tables.snapshots.c.tenant_id == tenant,
                    tables.snapshots.c.profile_id == str(profile_row["id"]),
                    tables.snapshots.c.id == str(snapshot_id),
                )
            )
            .mappings()
            .first()
        )
        if snapshot_row is None:
            empty.update(
                {
                    "profile_id": str(profile_row["id"]),
                    "profile_name": str(profile_row["name"]),
                    "profile_status": str(profile_row["status"]),
                    "snapshot_id": str(snapshot_id),
                    "reason_code": "snapshot_missing",
                }
            )
            return ServiceResult(empty)
        facts = _facts_for_snapshot(
            session,
            tables,
            tenant_id=tenant,
            profile_id=str(profile_row["id"]),
            snapshot_id=str(snapshot_id),
        )
        evidence = _evidence_for_snapshot(
            session,
            tables,
            tenant_id=tenant,
            profile_id=str(profile_row["id"]),
            snapshot_id=str(snapshot_id),
        )
        snapshot = _snapshot_wire(dict(snapshot_row), facts, evidence)
        policy = _policy_wire(
            _policy_row(
                session,
                tables,
                tenant,
                str(profile_row["id"]),
                str(snapshot["policy_revision_id"]),
            )
        )
        return ServiceResult(
            {
                "tenant_id": tenant,
                "dataset_id": dataset,
                "profile_id": str(profile_row["id"]),
                "profile_name": str(profile_row["name"]),
                "profile_status": str(profile_row["status"]),
                "state": snapshot["state"],
                "as_of": snapshot["as_of"],
                "snapshot_id": snapshot["id"],
                "snapshot_digest": snapshot["snapshot_digest"],
                "policy_revision": int(policy["revision"]),
                "serving_generation": snapshot["observed_serving_generation"],
                "source_count": snapshot["source_count"],
                "ready_source_count": snapshot["ready_source_count"],
                "stale_source_count": snapshot["stale_source_count"],
                "active_document_count": snapshot["active_document_count"],
                "failed_document_count": snapshot["failed_document_count"],
                "pending_index_count": snapshot["pending_index_count"],
                "stage_count": snapshot["stage_count"],
                "ready_stage_count": snapshot["ready_stage_count"],
                "blocked_stage_count": snapshot["blocked_stage_count"],
                "current_release_id": snapshot["current_release_id"],
                "current_certification_id": snapshot["current_certification_id"],
                "stage_facts": facts,
                "reason_code": None,
            }
        )


def list_serving_snapshots(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    state: str | None = None,
    profile_id: str | None = None,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> ServiceResult:
    del request_id
    tenant = _id(tenant_id, "tenant_id", maximum=64)
    dataset = _id(dataset_id, "dataset_id", maximum=64)
    if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
        raise KnowledgeServingInvalid("limit is invalid")
    if state is not None and state not in {"ready", "degraded", "blocked", "unavailable"}:
        raise KnowledgeServingInvalid("snapshot state is not allow-listed")
    with Session(engine, expire_on_commit=False) as session:
        if actor_id is not None:
            _require_member(session, tenant, _id(actor_id, "actor_id", maximum=64), manager=False)
        _require_dataset(session, tenant, dataset)
        tables = _tables(session)
        profile_ids = _profile_ids(session, tables, tenant_id=tenant, dataset_id=dataset)
        if profile_id is not None:
            requested = _id(profile_id, "profile_id")
            profile_ids = [requested] if requested in profile_ids else []
        if not profile_ids:
            return ServiceResult(
                {"items": [], "count": 0, "next_cursor": None, "invalid_item_count": 0}
            )
        statement = select(tables.snapshots).where(
            tables.snapshots.c.tenant_id == tenant, tables.snapshots.c.profile_id.in_(profile_ids)
        )
        if state is not None:
            statement = statement.where(tables.snapshots.c.state == state)
        decoded = _page_cursor(cursor, tenant_id=tenant, dataset_id=dataset)
        if decoded is not None:
            last_as_of, last_id = decoded
            statement = statement.where(
                (tables.snapshots.c.as_of < last_as_of)
                | and_(tables.snapshots.c.as_of == last_as_of, tables.snapshots.c.id < last_id)
            )
        rows = (
            session.execute(
                statement.order_by(
                    tables.snapshots.c.as_of.desc(), tables.snapshots.c.id.desc()
                ).limit(limit + 1)
            )
            .mappings()
            .all()
        )
        items: list[dict[str, Any]] = []
        for row in rows[:limit]:
            row_dict = dict(row)
            facts = _facts_for_snapshot(
                session,
                tables,
                tenant_id=tenant,
                profile_id=str(row_dict["profile_id"]),
                snapshot_id=str(row_dict["id"]),
            )
            evidence = _evidence_for_snapshot(
                session,
                tables,
                tenant_id=tenant,
                profile_id=str(row_dict["profile_id"]),
                snapshot_id=str(row_dict["id"]),
            )
            items.append(_snapshot_wire(row_dict, facts, evidence))
        next_value = None
        if len(rows) > limit and items:
            next_value = _next_cursor(
                tenant_id=tenant,
                dataset_id=dataset,
                as_of=rows[limit - 1]["as_of"],
                resource_id=str(rows[limit - 1]["id"]),
            )
        return ServiceResult(
            {
                "items": items,
                "count": len(items),
                "next_cursor": next_value,
                "invalid_item_count": 0,
            }
        )


def get_serving_snapshot(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    snapshot_id: str,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> ServiceResult:
    del request_id
    tenant = _id(tenant_id, "tenant_id", maximum=64)
    dataset = _id(dataset_id, "dataset_id", maximum=64)
    snapshot_key = _id(snapshot_id, "snapshot_id")
    with Session(engine, expire_on_commit=False) as session:
        if actor_id is not None:
            _require_member(session, tenant, _id(actor_id, "actor_id", maximum=64), manager=False)
        _require_dataset(session, tenant, dataset)
        tables = _tables(session)
        row = (
            session.execute(
                select(tables.snapshots).where(
                    tables.snapshots.c.tenant_id == tenant, tables.snapshots.c.id == snapshot_key
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise KnowledgeServingNotFound("serving snapshot was not found in this tenant")
        row_dict = dict(row)
        profile = _profile_row(session, tables, tenant, dataset, str(row_dict["profile_id"]))
        facts = _facts_for_snapshot(
            session,
            tables,
            tenant_id=tenant,
            profile_id=str(profile["id"]),
            snapshot_id=snapshot_key,
        )
        evidence = _evidence_for_snapshot(
            session,
            tables,
            tenant_id=tenant,
            profile_id=str(profile["id"]),
            snapshot_id=snapshot_key,
        )
        events = _events_for_snapshot(
            session,
            tables,
            tenant_id=tenant,
            profile_id=str(profile["id"]),
            snapshot_id=snapshot_key,
        )
        return ServiceResult(
            {
                "snapshot": _snapshot_wire(row_dict, facts, evidence),
                "stage_facts": facts,
                "evidence_links": evidence,
                "events": events,
            }
        )


def list_serving_stage_facts(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    snapshot_id: str | None = None,
    profile_id: str | None = None,
    stage_code: str | None = None,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> ServiceResult:
    del request_id
    tenant = _id(tenant_id, "tenant_id", maximum=64)
    dataset = _id(dataset_id, "dataset_id", maximum=64)
    with Session(engine, expire_on_commit=False) as session:
        if actor_id is not None:
            _require_member(session, tenant, _id(actor_id, "actor_id", maximum=64), manager=False)
        _require_dataset(session, tenant, dataset)
        tables = _tables(session)
        ids = _profile_ids(session, tables, tenant_id=tenant, dataset_id=dataset)
        if profile_id is not None:
            selected = _id(profile_id, "profile_id")
            ids = [selected] if selected in ids else []
        if not ids:
            return ServiceResult(
                {"items": [], "count": 0, "next_cursor": None, "invalid_item_count": 0}
            )
        statement = select(tables.stage_facts).where(
            tables.stage_facts.c.tenant_id == tenant, tables.stage_facts.c.profile_id.in_(ids)
        )
        if snapshot_id is not None:
            statement = statement.where(
                tables.stage_facts.c.snapshot_id == _id(snapshot_id, "snapshot_id")
            )
        if stage_code is not None:
            code = _code(stage_code, "stage_code")
            if code not in SERVING_STAGE_CODES:
                raise KnowledgeServingInvalid("stage_code is not allow-listed")
            statement = statement.where(tables.stage_facts.c.stage_code == code)
        rows = (
            session.execute(
                statement.order_by(
                    tables.stage_facts.c.observed_at.desc(),
                    tables.stage_facts.c.sequence,
                    tables.stage_facts.c.id,
                )
            )
            .mappings()
            .all()
        )
        return ServiceResult(
            {
                "items": [_stage_wire(dict(row)) for row in rows],
                "count": len(rows),
                "next_cursor": None,
                "invalid_item_count": 0,
            }
        )


def list_serving_events(
    engine: Any,
    *,
    tenant_id: str,
    dataset_id: str,
    snapshot_id: str | None = None,
    profile_id: str | None = None,
    event_type: str | None = None,
    cursor: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> ServiceResult:
    del request_id
    tenant = _id(tenant_id, "tenant_id", maximum=64)
    dataset = _id(dataset_id, "dataset_id", maximum=64)
    if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
        raise KnowledgeServingInvalid("limit is invalid")
    if event_type is not None and event_type not in SERVING_EVENT_TYPES:
        raise KnowledgeServingInvalid("event_type is not allow-listed")
    with Session(engine, expire_on_commit=False) as session:
        if actor_id is not None:
            _require_member(session, tenant, _id(actor_id, "actor_id", maximum=64), manager=False)
        _require_dataset(session, tenant, dataset)
        tables = _tables(session)
        ids = _profile_ids(session, tables, tenant_id=tenant, dataset_id=dataset)
        if profile_id is not None:
            selected = _id(profile_id, "profile_id")
            ids = [selected] if selected in ids else []
        if not ids:
            return ServiceResult(
                {"items": [], "count": 0, "next_cursor": None, "invalid_item_count": 0}
            )
        statement = select(tables.events).where(
            tables.events.c.tenant_id == tenant, tables.events.c.profile_id.in_(ids)
        )
        if snapshot_id is not None:
            statement = statement.where(
                tables.events.c.snapshot_id == _id(snapshot_id, "snapshot_id")
            )
        if event_type is not None:
            statement = statement.where(tables.events.c.event_type == event_type)
        decoded = _page_cursor(cursor, tenant_id=tenant, dataset_id=dataset)
        if decoded is not None:
            last_at, last_id = decoded
            statement = statement.where(
                (tables.events.c.occurred_at < last_at)
                | and_(tables.events.c.occurred_at == last_at, tables.events.c.id < last_id)
            )
        rows = (
            session.execute(
                statement.order_by(
                    tables.events.c.occurred_at.desc(), tables.events.c.id.desc()
                ).limit(limit + 1)
            )
            .mappings()
            .all()
        )
        items = [_event_wire(dict(row)) for row in rows[:limit]]
        next_value = None
        if len(rows) > limit and items:
            next_value = _next_cursor(
                tenant_id=tenant,
                dataset_id=dataset,
                as_of=rows[limit - 1]["occurred_at"],
                resource_id=str(rows[limit - 1]["id"]),
            )
        return ServiceResult(
            {
                "items": items,
                "count": len(items),
                "next_cursor": next_value,
                "invalid_item_count": 0,
            }
        )


# Vocabulary aliases keep the service discoverable for callers using either the
# design's long names or the shorter resource-oriented names.
create_knowledge_serving_profile = create_serving_profile
get_knowledge_serving_profile = get_serving_profile
create_knowledge_serving_policy_revision = create_serving_policy_revision
activate_knowledge_serving_policy = activate_serving_policy
get_knowledge_serving_summary = get_serving_summary
list_knowledge_serving_snapshots = list_serving_snapshots
get_knowledge_serving_snapshot = get_serving_snapshot
list_knowledge_serving_stage_facts = list_serving_stage_facts
list_knowledge_serving_events = list_serving_events
preview_knowledge_serving_profile = preview_serving_profile
record_knowledge_serving_snapshot_internal = record_knowledge_serving_snapshot

__all__ = [
    "KnowledgeServingConflict",
    "KnowledgeServingForbidden",
    "KnowledgeServingInvalid",
    "KnowledgeServingNotFound",
    "KnowledgeServingServiceError",
    "KnowledgeServingUnavailable",
    "SERVING_ADAPTER_ORDER",
    "SERVING_ADAPTER_REGISTRY",
    "SERVING_STAGE_CODES",
    "SERVING_TABLE_NAMES",
    "ServiceResult",
    "ServingConflict",
    "ServingForbidden",
    "ServingInvalid",
    "ServingNotFound",
    "ServingServiceError",
    "ServingTables",
    "ServingUnavailable",
    "activate_knowledge_serving_policy",
    "activate_serving_policy",
    "canonical_safe_mapping",
    "create_knowledge_serving_policy_revision",
    "create_knowledge_serving_profile",
    "create_serving_policy_revision",
    "create_serving_profile",
    "get_knowledge_serving_profile",
    "get_knowledge_serving_snapshot",
    "get_knowledge_serving_summary",
    "get_serving_profile",
    "get_serving_snapshot",
    "get_serving_summary",
    "list_knowledge_serving_events",
    "list_knowledge_serving_snapshots",
    "list_knowledge_serving_stage_facts",
    "list_serving_events",
    "list_serving_snapshots",
    "list_serving_stage_facts",
    "preview_knowledge_serving_profile",
    "preview_serving_profile",
    "record_knowledge_serving_snapshot",
    "record_knowledge_serving_snapshot_internal",
]
