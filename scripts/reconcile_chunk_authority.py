"""Reconcile authoritative chunk heads with the Milvus projection.

The command is report-only by default. Repair mode only enqueues durable work;
it never mutates Milvus directly. CLI summaries use stable pseudonyms while raw
identifiers are retained only in the durable repair payload needed by workers.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from core.chunk_catalog import ChunkCatalog
from core.index_operations import IndexOperationQueue
from models.orm import (
    ChunkHead,
    Dataset,
    Document,
    DocumentIngestAttempt,
    IndexOperation,
)
from scripts.rollout_security import (
    REPORT_SECRET_ENV as _REPORT_SECRET_ENV,
    decode_canonical_base64url_json,
    validate_rollout_cursor_fields,
    validate_rollout_secret,
    validate_sha256_hmac,
)
from scripts.rollout_snapshot import (
    DEFAULT_ROLLOUT_BATCH_SIZE,
    capture_document_snapshot,
    estimate_rollout_cost,
    rollout_snapshot_session,
)

_CURSOR_VERSION = 5
_DOMAIN = "rag4c/knowledgeops/reconcile/v1"
_ACTIVE_OPERATION_STATES = frozenset({"pending", "retry", "claimed"})
_CURSOR_FIELDS = (
    "v",
    "dataset_generation",
    "snapshot_started_at",
    "snapshot_count",
    "snapshot_fingerprint",
    "upper_created_at",
    "upper_document_id",
    "last_created_at",
    "last_document_id",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _secret_bytes(report_secret: str) -> bytes:
    return validate_rollout_secret(report_secret)


def _hmac_hex(report_secret: str, *, domain: str, value: Any) -> str:
    message = domain.encode("utf-8") + b"\0" + _canonical(value)
    return hmac.new(_secret_bytes(report_secret), message, hashlib.sha256).hexdigest()


def _pseudonym(value: str, *, report_secret: str, kind: str) -> str:
    digest = _hmac_hex(report_secret, domain=f"{_DOMAIN}/report/{kind}", value=value)
    return f"ref-{digest[:20]}"


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds")


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid reconcile cursor timestamp") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _dataset_generation(dataset: Dataset, *, tenant_id: str, report_secret: str) -> str:
    return _hmac_hex(
        report_secret,
        domain=f"{_DOMAIN}/cursor/dataset-generation",
        value={
            "tenant_id": tenant_id,
            "dataset_id": dataset.id,
            "created_at": _datetime_text(dataset.created_at),
            "status": dataset.status,
        },
    )


@dataclass(frozen=True)
class _ScanState:
    dataset_generation: str
    snapshot_started_at: str
    snapshot_count: int
    snapshot_fingerprint: str
    upper_created_at: str
    upper_document_id: str
    last_created_at: str = ""
    last_document_id: str = ""

    def unsigned_payload(self) -> dict[str, Any]:
        return {
            "v": _CURSOR_VERSION,
            "dataset_generation": self.dataset_generation,
            "snapshot_started_at": self.snapshot_started_at,
            "snapshot_count": self.snapshot_count,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "upper_created_at": self.upper_created_at,
            "upper_document_id": self.upper_document_id,
            "last_created_at": self.last_created_at,
            "last_document_id": self.last_document_id,
        }


def _encode_cursor(state: _ScanState, *, report_secret: str) -> str:
    unsigned = state.unsigned_payload()
    payload = {
        **unsigned,
        "signature": _hmac_hex(report_secret, domain=f"{_DOMAIN}/cursor/signature", value=unsigned),
    }
    return base64.urlsafe_b64encode(_canonical(payload)).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, *, report_secret: str) -> _ScanState | None:
    _secret_bytes(report_secret)
    if not cursor:
        return None
    error_message = "invalid reconcile cursor"
    payload = decode_canonical_base64url_json(cursor, error_message=error_message)
    if set(payload) != {*_CURSOR_FIELDS, "signature"}:
        raise ValueError(error_message)
    unsigned = {key: payload[key] for key in _CURSOR_FIELDS}
    expected_signature = _hmac_hex(
        report_secret, domain=f"{_DOMAIN}/cursor/signature", value=unsigned
    )
    authentication_error = "reconcile cursor is not authenticated"
    validate_rollout_cursor_fields(
        unsigned,
        expected_version=_CURSOR_VERSION,
        error_message=authentication_error,
    )
    signature = validate_sha256_hmac(payload["signature"], error_message=authentication_error)
    if not hmac.compare_digest(signature, expected_signature):
        raise ValueError(authentication_error)
    state = _ScanState(
        dataset_generation=unsigned["dataset_generation"],
        snapshot_started_at=unsigned["snapshot_started_at"],
        snapshot_count=unsigned["snapshot_count"],
        snapshot_fingerprint=unsigned["snapshot_fingerprint"],
        upper_created_at=unsigned["upper_created_at"],
        upper_document_id=unsigned["upper_document_id"],
        last_created_at=unsigned["last_created_at"],
        last_document_id=unsigned["last_document_id"],
    )
    _parse_datetime(state.snapshot_started_at)
    if state.upper_created_at:
        _parse_datetime(state.upper_created_at)
    if state.last_created_at:
        _parse_datetime(state.last_created_at)
    return state


def _key_after(created_at: datetime, document_id: str) -> Any:
    return or_(
        Document.created_at > created_at,
        and_(Document.created_at == created_at, Document.id > document_id),
    )


def _key_at_or_before(created_at: datetime, document_id: str) -> Any:
    return or_(
        Document.created_at < created_at,
        and_(Document.created_at == created_at, Document.id <= document_id),
    )


def _scan_state(
    session: Session,
    *,
    tenant_id: str,
    dataset_id: str,
    cursor: str,
    report_secret: str,
) -> _ScanState:
    dataset = session.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id)
    )
    if dataset is None:
        raise ValueError("dataset does not exist in requested scope")
    current_generation = _dataset_generation(
        dataset, tenant_id=tenant_id, report_secret=report_secret
    )
    current_snapshot = capture_document_snapshot(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
    )
    resumed = _decode_cursor(cursor, report_secret=report_secret)
    if resumed is None:
        upper = session.scalar(
            select(Document)
            .where(
                Document.tenant_id == tenant_id,
                Document.dataset_id == dataset_id,
            )
            .order_by(Document.created_at.desc(), Document.id.desc())
            .limit(1)
        )
        return _ScanState(
            dataset_generation=current_generation,
            snapshot_started_at=_datetime_text(datetime.now(timezone.utc).replace(tzinfo=None)),
            snapshot_count=current_snapshot.count,
            snapshot_fingerprint=current_snapshot.fingerprint,
            upper_created_at=_datetime_text(upper.created_at) if upper else "",
            upper_document_id=upper.id if upper else "",
        )
    if resumed.dataset_generation != current_generation:
        raise ValueError("dataset generation changed during rollout scan")
    if resumed.snapshot_count != current_snapshot.count or not hmac.compare_digest(
        resumed.snapshot_fingerprint,
        current_snapshot.fingerprint,
    ):
        raise ValueError("rollout scan snapshot assumptions were violated")
    return resumed


def _assert_scan_state_current(
    chunk_catalog: ChunkCatalog,
    *,
    state: _ScanState,
    tenant_id: str,
    dataset_id: str,
    report_secret: str,
) -> None:
    """Re-read snapshot and dataset generation in a fresh transaction."""
    with Session(chunk_catalog.engine, expire_on_commit=False) as session:
        dataset = session.scalar(
            select(Dataset).where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id)
        )
        if dataset is None:
            raise ValueError("dataset does not exist in requested scope")
        if state.dataset_generation != _dataset_generation(
            dataset, tenant_id=tenant_id, report_secret=report_secret
        ):
            raise ValueError("dataset generation changed during rollout scan")
        current = capture_document_snapshot(session, tenant_id=tenant_id, dataset_id=dataset_id)
    if state.snapshot_count != current.count or not hmac.compare_digest(
        state.snapshot_fingerprint, current.fingerprint
    ):
        raise ValueError("rollout scan snapshot assumptions were violated")


def _context_header(metadata: dict[str, Any]) -> str:
    for key in ("context_header", "context"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _projection_role(
    chunk: Any,
    *,
    referenced_parent_ids: set[str],
) -> str:
    metadata = dict(chunk.metadata or {})
    explicit = str(metadata.get("chunk_role") or "").lower()
    if explicit in {"parent", "child", "flat"}:
        return explicit
    if str(chunk.chunk_id) in referenced_parent_ids:
        return "parent"
    if getattr(chunk, "parent_chunk_id", None):
        return "child"
    return "flat"


@dataclass(frozen=True)
class ReconcileReport:
    tenant_id: str
    dataset_id: str
    mode: str
    documents_scanned: int
    authoritative_heads: int
    projection_chunks: int
    missing_ids: tuple[str, ...]
    stale_ids: tuple[str, ...]
    orphaned_ids: tuple[str, ...]
    stale_reasons: dict[str, tuple[str, ...]]
    repair_blocked_document_ids: tuple[str, ...]
    enqueued: int
    snapshot_count: int
    batch_size: int
    manifest_hash: str
    post_snapshot_excluded: int
    next_cursor: str
    complete: bool

    @property
    def has_drift(self) -> bool:
        return bool(
            self.missing_ids
            or self.stale_ids
            or self.orphaned_ids
            or self.repair_blocked_document_ids
        )

    def to_summary(self, report_secret: str) -> dict[str, Any]:
        _secret_bytes(report_secret)

        def refs(values: tuple[str, ...]) -> list[str]:
            return [
                _pseudonym(value, report_secret=report_secret, kind="chunk") for value in values
            ]

        cost = estimate_rollout_cost(document_count=self.snapshot_count, batch_size=self.batch_size)
        return {
            "tenant_id": _pseudonym(self.tenant_id, report_secret=report_secret, kind="tenant"),
            "dataset_id": _pseudonym(self.dataset_id, report_secret=report_secret, kind="dataset"),
            "mode": self.mode,
            "documents_scanned": self.documents_scanned,
            "authoritative_heads": self.authoritative_heads,
            "projection_chunks": self.projection_chunks,
            "missing_ids": refs(self.missing_ids),
            "stale_ids": refs(self.stale_ids),
            "orphaned_ids": refs(self.orphaned_ids),
            "stale_reasons": {
                _pseudonym(key, report_secret=report_secret, kind="chunk"): list(value)
                for key, value in sorted(self.stale_reasons.items())
            },
            "repair_blocked_document_ids": [
                _pseudonym(value, report_secret=report_secret, kind="document")
                for value in self.repair_blocked_document_ids
            ],
            "enqueued": self.enqueued,
            "manifest_ref": "ref-"
            + _hmac_hex(
                report_secret,
                domain=f"{_DOMAIN}/report/manifest-ref",
                value=self.manifest_hash,
            ),
            "snapshot_validation": {
                "estimated_batches": cost.batch_count,
                "fingerprint_scans": cost.fingerprint_scan_count,
                "estimated_rows_scanned": cost.estimated_rows_scanned,
                "warning": cost.warning,
            },
            "post_snapshot_excluded": self.post_snapshot_excluded,
            "resume_cursor_ref": (
                _hmac_hex(
                    report_secret,
                    domain=f"{_DOMAIN}/report/resume-cursor-ref",
                    value=self.next_cursor,
                )[:20]
                if self.next_cursor
                else ""
            ),
            "complete": self.complete,
            "has_drift": self.has_drift,
        }


def _document_drift(
    document: Document,
    heads: list[ChunkHead],
    projected_chunks: list[Any],
) -> tuple[list[str], list[str], list[str], dict[str, tuple[str, ...]], dict[str, Any]]:
    desired = {
        head.id: head for head in heads if bool(head.enabled) and head.chunk_role != "parent"
    }
    projected = {str(chunk.chunk_id): chunk for chunk in projected_chunks}
    referenced_parent_ids = {
        str(chunk.parent_chunk_id)
        for chunk in projected_chunks
        if getattr(chunk, "parent_chunk_id", None)
    }
    missing = sorted(set(desired) - set(projected))
    orphaned = sorted(set(projected) - set(desired))
    stale_reasons: dict[str, tuple[str, ...]] = {}
    for chunk_id in sorted(set(desired) & set(projected)):
        head = desired[chunk_id]
        chunk = projected[chunk_id]
        reasons: list[str] = []
        if int(chunk.document_revision or 0) != int(head.document_revision or 0):
            reasons.append("document_revision")
        if int(chunk.content_revision or 0) != int(head.content_revision or 0):
            reasons.append("content_revision")
        projection_text_hash = hashlib.sha256(str(chunk.text).encode("utf-8")).hexdigest()
        if str(chunk.text_hash or "") != str(
            head.content_hash or ""
        ) or projection_text_hash != str(head.content_hash or ""):
            reasons.append("hash")
        if chunk.tenant_id and chunk.tenant_id != document.tenant_id:
            reasons.append("tenant_id")
        if chunk.dataset_id and chunk.dataset_id != document.dataset_id:
            reasons.append("dataset_id")
        if getattr(chunk, "parent_chunk_id", None) != head.parent_chunk_id:
            reasons.append("parent_chunk_id")
        projection_role = _projection_role(chunk, referenced_parent_ids=referenced_parent_ids)
        if projection_role != head.chunk_role:
            reasons.append("chunk_role")
        if _context_header(dict(chunk.metadata or {})) != str(head.context_header or ""):
            reasons.append("context_header")
        if reasons:
            stale_reasons[chunk_id] = tuple(reasons)
    stale = sorted(stale_reasons)
    manifest = {
        "document_id": document.id,
        "document_target_revision": int(document.desired_index_revision or 0),
        "heads": [
            {
                "chunk_id": head.id,
                "parent_chunk_id": head.parent_chunk_id or "",
                "document_revision": int(head.document_revision or 0),
                "content_revision": int(head.content_revision or 0),
                "content_hash": str(head.content_hash or ""),
                "enabled": bool(head.enabled),
                "chunk_role": head.chunk_role,
                "context_hash": hashlib.sha256(
                    str(head.context_header or "").encode("utf-8")
                ).hexdigest(),
            }
            for head in heads
        ],
        "projection": [
            {
                "chunk_id": str(chunk.chunk_id),
                "parent_chunk_id": str(chunk.parent_chunk_id)
                if getattr(chunk, "parent_chunk_id", None)
                else "",
                "document_revision": int(chunk.document_revision or 0),
                "content_revision": int(chunk.content_revision or 0),
                "stored_hash": str(chunk.text_hash or ""),
                "computed_hash": hashlib.sha256(str(chunk.text).encode("utf-8")).hexdigest(),
                "tenant_id": str(chunk.tenant_id or ""),
                "dataset_id": str(chunk.dataset_id or ""),
                "chunk_role": _projection_role(chunk, referenced_parent_ids=referenced_parent_ids),
                "context_hash": hashlib.sha256(
                    _context_header(dict(chunk.metadata or {})).encode("utf-8")
                ).hexdigest(),
            }
            for chunk in sorted(projected_chunks, key=lambda item: str(item.chunk_id))
        ],
        "missing_ids": missing,
        "stale_ids": stale,
        "orphaned_ids": orphaned,
        "stale_reasons": {key: list(value) for key, value in sorted(stale_reasons.items())},
    }
    return missing, stale, orphaned, stale_reasons, manifest


@dataclass(frozen=True)
class _RepairPlan:
    document_id: str
    tenant_id: str
    dataset_id: str
    content_revision: int
    desired_index_revision: int
    created_at: datetime
    updated_at: datetime
    status: str
    current_attempt_id: str | None
    document_hash: str
    payload: dict[str, Any]


def _repair_base_key(plan: _RepairPlan) -> str:
    return (
        f"knowledgeops:reconcile:{plan.document_id}:"
        f"{plan.desired_index_revision}:{plan.document_hash}"
    )


def _existing_repair_operations(session: Session, plan: _RepairPlan) -> list[IndexOperation]:
    base_key = _repair_base_key(plan)
    return list(
        session.scalars(
            select(IndexOperation)
            .where(
                IndexOperation.document_id == plan.document_id,
                IndexOperation.target_store == "milvus_chunks",
                IndexOperation.operation == "reconcile",
                IndexOperation.dedup_key.like(f"{base_key}%"),
            )
            .order_by(IndexOperation.created_at, IndexOperation.id)
        )
    )


def _create_repair_attempt_in_session(
    session: Session,
    *,
    document: Document,
    plan: _RepairPlan,
) -> str:
    attempt_no = (
        int(
            session.scalar(
                select(func.max(DocumentIngestAttempt.attempt_no)).where(
                    DocumentIngestAttempt.document_id == plan.document_id
                )
            )
            or 0
        )
        + 1
    )
    attempt = DocumentIngestAttempt(
        id=f"attempt-{uuid.uuid4().hex[:16]}",
        tenant_id=plan.tenant_id,
        dataset_id=plan.dataset_id,
        document_id=plan.document_id,
        attempt_no=attempt_no,
        input_content_hash=plan.document_hash,
        input_revision=plan.desired_index_revision,
        state="running",
        worker_id="knowledgeops-reconcile",
    )
    session.add(attempt)
    session.flush()
    document.current_attempt_id = attempt.id
    return attempt.id


def _repair_document_matches_plan(document: Document, plan: _RepairPlan) -> bool:
    return bool(
        document.tenant_id == plan.tenant_id
        and document.dataset_id == plan.dataset_id
        and int(document.content_revision or 0) == plan.content_revision
        and int(document.desired_index_revision or 0) == plan.desired_index_revision
        and document.created_at == plan.created_at
        and document.updated_at == plan.updated_at
        and str(document.status or "") == plan.status
        and document.current_attempt_id == plan.current_attempt_id
    )


def _enqueue_repair_plans(queue: IndexOperationQueue, plans: list[_RepairPlan]) -> int:
    """CAS all planned documents, then bind attempts and operations atomically."""
    if not plans:
        return 0
    with Session(queue.engine, expire_on_commit=False) as session:
        if session.bind is not None and session.bind.dialect.name == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        locked_documents: dict[str, Document] = {}
        try:
            for plan in sorted(plans, key=lambda item: item.document_id):
                document = session.scalar(
                    select(Document)
                    .where(
                        Document.id == plan.document_id,
                        Document.tenant_id == plan.tenant_id,
                        Document.dataset_id == plan.dataset_id,
                    )
                    .with_for_update()
                )
                if document is None or not _repair_document_matches_plan(document, plan):
                    raise ValueError("repair plan became stale before enqueue")
                locked_documents[plan.document_id] = document

            enqueued = 0
            for plan in plans:
                existing = _existing_repair_operations(session, plan)
                if any(item.status in _ACTIVE_OPERATION_STATES for item in existing):
                    continue
                document = locked_documents[plan.document_id]
                attempt_id = document.current_attempt_id
                if not attempt_id:
                    attempt_id = _create_repair_attempt_in_session(
                        session, document=document, plan=plan
                    )
                generation = len(existing) + 1
                base_key = _repair_base_key(plan)
                dedup_key = base_key if generation == 1 else f"{base_key}:generation:{generation}"
                queue.enqueue_operation(
                    tenant_id=plan.tenant_id,
                    dataset_id=plan.dataset_id,
                    document_id=plan.document_id,
                    attempt_id=attempt_id,
                    target_store="milvus_chunks",
                    operation="reconcile",
                    dedup_key=dedup_key,
                    target_revision=plan.desired_index_revision,
                    payload=plan.payload,
                    session=session,
                )
                enqueued += 1
            session.commit()
            return enqueued
        except Exception:
            session.rollback()
            raise


def reconcile_chunk_authority(
    chunk_catalog: ChunkCatalog,
    milvus: Any,
    queue: IndexOperationQueue | None,
    *,
    tenant_id: str,
    dataset_id: str,
    report_secret: str,
    cursor: str = "",
    batch_size: int = DEFAULT_ROLLOUT_BATCH_SIZE,
    repair: bool = False,
) -> ReconcileReport:
    """Compare a stable snapshot and optionally enqueue a complete repair plan."""
    if not tenant_id or not dataset_id:
        raise ValueError("tenant_id and dataset_id are required")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if repair and queue is None:
        raise ValueError("repair mode requires an IndexOperationQueue")
    if repair and cursor:
        raise ValueError("repair mode does not accept a resume cursor")

    with rollout_snapshot_session(chunk_catalog.engine) as session:
        state = _scan_state(
            session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            cursor=cursor,
            report_secret=report_secret,
        )
        filters: list[Any] = [
            Document.tenant_id == tenant_id,
            Document.dataset_id == dataset_id,
        ]
        if state.last_created_at:
            filters.append(
                _key_after(_parse_datetime(state.last_created_at), state.last_document_id)
            )
        if state.upper_created_at:
            filters.append(
                _key_at_or_before(
                    _parse_datetime(state.upper_created_at),
                    state.upper_document_id,
                )
            )
        else:
            filters.append(Document.id == "__empty_snapshot__")
        document_query = select(Document).where(*filters).order_by(Document.created_at, Document.id)
        if not repair:
            document_query = document_query.limit(batch_size + 1)
        selected = list(session.scalars(document_query))
        documents = selected if repair else selected[:batch_size]
        has_more = False if repair else len(selected) > batch_size

        missing_all: list[str] = []
        stale_all: list[str] = []
        orphaned_all: list[str] = []
        reasons_all: dict[str, tuple[str, ...]] = {}
        blocked: list[str] = []
        manifests: list[dict[str, Any]] = []
        repair_plans: list[_RepairPlan] = []
        authoritative_count = projection_count = 0

        for document in documents:
            heads = list(
                session.scalars(
                    select(ChunkHead)
                    .where(
                        ChunkHead.document_id == document.id,
                        ChunkHead.tenant_id == tenant_id,
                        ChunkHead.dataset_id == dataset_id,
                    )
                    .order_by(ChunkHead.id)
                )
            )
            projected = sorted(
                milvus.query_chunks_by_doc(document.id, tenant_id=document.tenant_id),
                key=lambda item: str(item.chunk_id),
            )
            authoritative_count += len(heads)
            projection_count += len(projected)
            missing, stale, orphaned, stale_reasons, manifest = _document_drift(
                document, heads, projected
            )
            manifests.append(manifest)
            missing_all.extend(missing)
            stale_all.extend(stale)
            orphaned_all.extend(orphaned)
            reasons_all.update(stale_reasons)
            if repair and (missing or stale or orphaned):
                document_hash = _digest(manifest)
                repair_plans.append(
                    _RepairPlan(
                        document_id=document.id,
                        tenant_id=document.tenant_id,
                        dataset_id=document.dataset_id,
                        content_revision=int(document.content_revision or 0),
                        desired_index_revision=int(document.desired_index_revision or 0),
                        created_at=document.created_at,
                        updated_at=document.updated_at,
                        status=str(document.status or ""),
                        current_attempt_id=document.current_attempt_id,
                        document_hash=document_hash,
                        payload={
                            "missing_ids": missing,
                            "orphaned_ids": orphaned,
                            "stale_ids": stale,
                            "stale_reasons": {
                                key: list(value) for key, value in stale_reasons.items()
                            },
                            "manifest_hash": document_hash,
                        },
                    )
                )

    _assert_scan_state_current(
        chunk_catalog,
        state=state,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        report_secret=report_secret,
    )

    enqueued = 0
    if repair:
        assert queue is not None
        enqueued = _enqueue_repair_plans(queue, repair_plans)

    manifest_hash = _digest(manifests)
    if len(manifests) == 1:
        manifest_hash = _digest(manifests[0])
    next_cursor = ""
    if has_more and documents:
        next_cursor = _encode_cursor(
            replace(
                state,
                last_created_at=_datetime_text(documents[-1].created_at),
                last_document_id=documents[-1].id,
            ),
            report_secret=report_secret,
        )
    effective_batch_size = max(state.snapshot_count, 1) if repair else batch_size
    return ReconcileReport(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        mode="repair-enqueue" if repair else "report-only",
        documents_scanned=len(documents),
        authoritative_heads=authoritative_count,
        projection_chunks=projection_count,
        missing_ids=tuple(sorted(missing_all)),
        stale_ids=tuple(sorted(stale_all)),
        orphaned_ids=tuple(sorted(orphaned_all)),
        stale_reasons={key: reasons_all[key] for key in sorted(reasons_all)},
        repair_blocked_document_ids=tuple(sorted(blocked)),
        enqueued=enqueued,
        snapshot_count=state.snapshot_count,
        batch_size=effective_batch_size,
        manifest_hash=manifest_hash,
        post_snapshot_excluded=0,
        next_cursor=next_cursor,
        complete=not has_more,
    )


def _runtime_dependencies() -> tuple[Any, Any, Any]:
    from config.settings import get_settings
    from core import catalog
    from core.milvus_client import RagMilvusClient

    settings = get_settings()
    return catalog.get_engine(), RagMilvusClient(settings.milvus), settings


def _runtime_safety_error(engine: Any, settings: Any, *, mutation: bool) -> str:
    from core.catalog_schema import CatalogSchemaError, verify_catalog_schema

    try:
        verify_catalog_schema(engine)
    except CatalogSchemaError as exc:
        return str(exc)
    catalog_settings = settings.catalog
    if str(catalog_settings.schema_mode) != "verify":
        return "catalog schema_mode must be verify"
    if mutation and str(catalog_settings.chunk_authority_mode) != "shadow":
        return "chunk authority mutation requires shadow rollout mode"
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--cursor", default="")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_ROLLOUT_BATCH_SIZE)
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--fail-on-drift", action="store_true")
    parser.add_argument("--emit-sensitive-resume-cursor", action="store_true")
    args = parser.parse_args(argv)

    if args.repair and args.cursor:
        print(
            json.dumps(
                {"status": "unsafe", "error": "repair mode does not accept a resume cursor"},
                sort_keys=True,
            )
        )
        return 2

    report_secret = os.getenv(_REPORT_SECRET_ENV, "")
    if not report_secret.strip():
        print(
            json.dumps(
                {"status": "unsafe", "error": f"{_REPORT_SECRET_ENV} is required"},
                sort_keys=True,
            )
        )
        return 2
    try:
        _secret_bytes(report_secret)
    except ValueError as exc:
        print(json.dumps({"status": "unsafe", "error": str(exc)}, sort_keys=True))
        return 2
    engine, milvus, settings = _runtime_dependencies()
    safety_error = _runtime_safety_error(engine, settings, mutation=args.repair)
    if safety_error:
        print(json.dumps({"status": "unsafe", "error": safety_error}, sort_keys=True))
        return 2
    report = reconcile_chunk_authority(
        ChunkCatalog(engine),
        milvus,
        IndexOperationQueue(engine),
        tenant_id=args.tenant_id,
        dataset_id=args.dataset_id,
        report_secret=report_secret,
        cursor=args.cursor,
        batch_size=args.batch_size,
        repair=args.repair,
    )
    print(json.dumps(report.to_summary(report_secret), ensure_ascii=False, sort_keys=True))
    if args.emit_sensitive_resume_cursor and report.next_cursor:
        print(
            json.dumps({"sensitive_resume_cursor": report.next_cursor}, sort_keys=True),
            file=sys.stderr,
        )
    return 2 if args.fail_on_drift and report.has_drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
