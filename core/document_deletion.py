"""Durable document-delete request authority without external side effects.

This module owns only the short relational transaction that establishes a deletion
fence and records desired projection work. Milvus, graph, finalization, retries,
and physical purge are deliberately handled by later worker slices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import json
import time
import uuid
from typing import Iterable, Sequence

from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from core.catalog import sanitize_error_message
from core.document_delete_targets import (
    DocumentDeleteTargetPolicy,
    document_delete_projection_targets,
)
from core.knowledge_content import ContentConflict, ContentNotFound
from core.knowledge_governance import AuditContext
from models.orm import (
    ChunkHead,
    DataSourceRecord,
    Dataset,
    Document,
    DocumentDeleteBatch,
    DocumentDeleteOperation,
    DocumentIngestAttempt,
    IndexDeadLetter,
    IndexOperation,
    KnowledgeAuditEvent,
    SourceDocumentState,
    SourceSyncRun,
    Tenant,
)

_ALLOWED_ORIGINS = frozenset({"operator", "source_sync", "dataset_reset", "retention"})
_DELETE_LIFECYCLE_STATES = frozenset({"delete_requested", "deleting", "delete_failed", "deleted"})
_ACTIVE_OPERATION_STATUSES = ("pending", "retry", "claimed")
_PROJECTION_WRITER_OPERATIONS = ("upsert", "reconcile")
_TERMINAL_ATTEMPT_STATES = ("completed", "failed", "cancelled", "superseded")


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class DeleteBatchItemRequest:
    document_id: str
    expected_generation: int


@dataclass(frozen=True)
class ChunkManifest:
    count: int
    hash: str
    chunk_ids: tuple[str, ...]


class DocumentDeletionConflict(RuntimeError):
    """Raised when durable delete state no longer permits an operation."""


@dataclass(frozen=True)
class DeleteProjectionPlan:
    operation_id: str
    delete_operation_id: str
    tenant_id: str
    dataset_id: str
    document_id: str
    document_generation: int
    target_store: str
    manifest: ChunkManifest


@dataclass(frozen=True)
class DeleteOperationProjection:
    id: str
    batch_id: str | None
    request_index: int
    requested_document_id: str
    document_id: str | None
    expected_generation: int | None
    delete_generation: int | None
    attempt_id: str | None
    origin: str
    status: str
    result_code: str
    result_message: str
    chunk_manifest_count: int
    chunk_manifest_hash: str
    quota_chunk_count: int
    required_store_count: int
    completed_store_count: int
    failed_store_count: int
    requested_by: str
    request_id: str
    reason: str
    started_at: datetime | None
    finalized_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class DeleteBatchProjection:
    id: str
    idempotency_key: str
    request_hash: str
    actor_id: str
    request_id: str
    reason: str
    status: str
    requested_count: int
    accepted_count: int
    completed_count: int
    failed_count: int
    rejected_count: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    items: tuple[DeleteOperationProjection, ...]


@dataclass(frozen=True)
class DatasetResetProjection:
    source_id: str
    tenant_id: str
    dataset_id: str
    source_generation: int
    dataset_generation: int
    document_count: int
    batch_ids: tuple[str, ...]
    request_id: str


@dataclass(frozen=True)
class _PreparedItem:
    request_index: int
    requested_document_id: str
    expected_generation: int
    operation_id: str
    document_id: str | None
    delete_generation: int | None
    manifest: ChunkManifest
    quota_chunk_count: int
    status: str
    result_code: str
    result_message: str
    previous_lifecycle: str = ""
    previous_retrieval_enabled: bool = False


class DocumentDeletionRepository:
    """Establish durable delete desired state in one short database transaction."""

    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"

    @staticmethod
    def _clean(value: str, limit: int, field: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError(f"{field} is required")
        if len(cleaned) > limit:
            raise ValueError(f"{field} exceeds {limit} characters")
        if any(ord(character) < 32 for character in cleaned):
            raise ValueError(f"{field} contains control characters")
        return cleaned

    @staticmethod
    def _optional_text(value: str, limit: int, field: str) -> str:
        cleaned = str(value or "").strip()
        if len(cleaned) > limit:
            raise ValueError(f"{field} exceeds {limit} characters")
        if any(ord(character) < 32 for character in cleaned):
            raise ValueError(f"{field} contains control characters")
        return cleaned

    @staticmethod
    def _generation(value: int) -> int:
        if type(value) is not int or value < 0:
            raise ValueError("expected_generation must be a non-negative integer")
        return value

    @classmethod
    def _normalize_items(
        cls, items: Sequence[DeleteBatchItemRequest]
    ) -> tuple[DeleteBatchItemRequest, ...]:
        if not items:
            raise ValueError("at least one document is required")
        if len(items) > 100:
            raise ValueError("batch delete accepts at most 100 items")
        result: list[DeleteBatchItemRequest] = []
        seen: set[str] = set()
        for item in items:
            document_id = cls._clean(item.document_id, 128, "document_id")
            generation = cls._generation(item.expected_generation)
            if document_id in seen:
                continue
            seen.add(document_id)
            result.append(DeleteBatchItemRequest(document_id, generation))
        return tuple(result)

    @staticmethod
    def _request_hash(
        *,
        tenant_id: str,
        dataset_id: str,
        items: Sequence[DeleteBatchItemRequest],
        reason: str,
        origin: str,
    ) -> str:
        payload = {
            "dataset_id": dataset_id,
            "items": [
                {
                    "document_id": item.document_id,
                    "expected_generation": item.expected_generation,
                }
                for item in items
            ],
            "origin": origin,
            "reason": reason,
            "tenant_id": tenant_id,
        }
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def compute_chunk_manifest(
        self,
        session: Session,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
    ) -> ChunkManifest:
        rows = list(
            session.scalars(
                select(ChunkHead)
                .where(
                    ChunkHead.tenant_id == tenant_id,
                    ChunkHead.dataset_id == dataset_id,
                    ChunkHead.document_id == document_id,
                )
                .order_by(ChunkHead.id)
            )
        )
        facts = [
            {
                "chunk_id": row.id,
                "chunk_role": row.chunk_role,
                "content_revision": int(row.content_revision),
                "document_revision": int(row.document_revision),
                "enabled": bool(row.enabled),
            }
            for row in rows
        ]
        canonical = json.dumps(
            facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return ChunkManifest(
            count=len(rows),
            hash=hashlib.sha256(canonical).hexdigest(),
            chunk_ids=tuple(row.id for row in rows),
        )

    @staticmethod
    def _operation_projection(row: DocumentDeleteOperation) -> DeleteOperationProjection:
        return DeleteOperationProjection(
            id=row.id,
            batch_id=row.batch_id,
            request_index=row.request_index,
            requested_document_id=row.requested_document_id,
            document_id=row.document_id,
            expected_generation=row.expected_generation,
            delete_generation=row.delete_generation,
            attempt_id=row.attempt_id,
            origin=row.origin,
            status=row.status,
            result_code=row.result_code,
            result_message=row.result_message,
            chunk_manifest_count=row.chunk_manifest_count,
            chunk_manifest_hash=row.chunk_manifest_hash,
            quota_chunk_count=row.quota_chunk_count,
            required_store_count=row.required_store_count,
            completed_store_count=row.completed_store_count,
            failed_store_count=row.failed_store_count,
            requested_by=row.requested_by,
            request_id=row.request_id,
            reason=row.reason,
            started_at=row.started_at,
            finalized_at=row.finalized_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @classmethod
    def _batch_projection(
        cls,
        batch: DocumentDeleteBatch,
        items: Iterable[DocumentDeleteOperation],
    ) -> DeleteBatchProjection:
        return DeleteBatchProjection(
            id=batch.id,
            idempotency_key=batch.idempotency_key,
            request_hash=batch.request_hash,
            actor_id=batch.actor_id,
            request_id=batch.request_id,
            reason=batch.reason,
            status=batch.status,
            requested_count=batch.requested_count,
            accepted_count=batch.accepted_count,
            completed_count=batch.completed_count,
            failed_count=batch.failed_count,
            rejected_count=batch.rejected_count,
            created_at=batch.created_at,
            updated_at=batch.updated_at,
            finished_at=batch.finished_at,
            items=tuple(
                cls._operation_projection(item)
                for item in sorted(items, key=lambda item: item.request_index)
            ),
        )

    def _load_batch_projection(
        self,
        session: Session,
        *,
        tenant_id: str,
        dataset_id: str,
        batch_id: str,
    ) -> DeleteBatchProjection:
        batch = session.scalar(
            select(DocumentDeleteBatch).where(
                DocumentDeleteBatch.id == batch_id,
                DocumentDeleteBatch.tenant_id == tenant_id,
                DocumentDeleteBatch.dataset_id == dataset_id,
            )
        )
        if batch is None:
            raise ContentNotFound("document delete batch does not exist in dataset scope")
        items = list(
            session.scalars(
                select(DocumentDeleteOperation)
                .where(DocumentDeleteOperation.batch_id == batch.id)
                .order_by(DocumentDeleteOperation.request_index)
            )
        )
        return self._batch_projection(batch, items)

    def get_operation(
        self, tenant_id: str, dataset_id: str, operation_id: str
    ) -> DeleteOperationProjection:
        with Session(self.engine, expire_on_commit=False) as session:
            row = session.scalar(
                select(DocumentDeleteOperation).where(
                    DocumentDeleteOperation.id == operation_id,
                    DocumentDeleteOperation.tenant_id == tenant_id,
                    DocumentDeleteOperation.dataset_id == dataset_id,
                )
            )
            if row is None:
                raise ContentNotFound("document delete operation does not exist in dataset scope")
            return self._operation_projection(row)

    def get_batch(self, tenant_id: str, dataset_id: str, batch_id: str) -> DeleteBatchProjection:
        with Session(self.engine, expire_on_commit=False) as session:
            return self._load_batch_projection(
                session,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                batch_id=batch_id,
            )

    def _before_document_cas(
        self,
        _session: Session,
        _document: Document,
        _expected_generation: int,
    ) -> None:
        """Fault-injection hook used by concurrency tests."""

    def _add_index_operation(self, session: Session, operation: IndexOperation) -> None:
        session.add(operation)
        session.flush()

    def _add_audit_event(self, session: Session, event: KnowledgeAuditEvent) -> None:
        session.add(event)
        session.flush()

    @staticmethod
    def _source_suppression_error(
        session: Session,
        *,
        document: Document,
        tenant_id: str,
        dataset_id: str,
        origin: str,
    ) -> str | None:
        source_id = str(document.source_id or "").strip()
        if not source_id:
            return None
        source = session.scalar(
            select(DataSourceRecord).where(
                DataSourceRecord.id == source_id,
                DataSourceRecord.tenant_id == tenant_id,
                DataSourceRecord.dataset_id == dataset_id,
            )
        )
        if source is None:
            return f"{origin} source identity does not belong to the document dataset"
        existing = session.scalar(
            select(SourceDocumentState.id).where(
                SourceDocumentState.source_id == source_id,
                SourceDocumentState.doc_id == document.id,
            )
        )
        if existing is not None:
            return None
        external_id = str(document.external_id or "").strip()
        source_uri = str(document.source_uri or "").strip()
        if not external_id and not source_uri:
            return f"{origin} source identity requires external_id or source_uri"
        return None

    def _upsert_source_suppression(
        self,
        session: Session,
        *,
        document: Document,
        delete_operation_id: str,
        delete_generation: int,
        origin: str,
        now: datetime,
    ) -> None:
        source_id = str(document.source_id or "").strip()
        if not source_id:
            return
        state = session.scalar(
            select(SourceDocumentState)
            .where(
                SourceDocumentState.source_id == source_id,
                SourceDocumentState.doc_id == document.id,
            )
            .with_for_update()
        )
        state_value = "operator_suppressed" if origin == "operator" else "delete_pending"
        if state is None:
            external_id = str(document.external_id or "").strip()
            source_uri = str(document.source_uri or "").strip()
            stable_external_id = external_id or source_uri
            if not stable_external_id:
                raise ContentConflict(
                    f"{origin} source identity requires external_id or source_uri"
                )
            state = SourceDocumentState(
                id=self._new_id("source-state"),
                source_id=source_id,
                doc_id=document.id,
                external_id=stable_external_id,
                source_uri=source_uri,
                content_hash=str(document.file_hash or "")[:64],
                chunk_count=max(0, int(document.chunk_count)),
                last_run_id="",
                state=state_value,
                document_generation=delete_generation,
                delete_operation_id=delete_operation_id,
                suppressed_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(state)
        else:
            state.state = state_value
            state.document_generation = delete_generation
            state.delete_operation_id = delete_operation_id
            state.suppressed_at = now
            state.updated_at = now
        session.flush()

    @staticmethod
    def _supersede_projection_writers(
        session: Session,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        delete_generation: int,
        now: datetime,
    ) -> int:
        rows = list(
            session.execute(
                select(IndexOperation.id, IndexOperation.attempt_id)
                .where(
                    IndexOperation.tenant_id == tenant_id,
                    IndexOperation.dataset_id == dataset_id,
                    IndexOperation.document_id == document_id,
                    IndexOperation.operation.in_(_PROJECTION_WRITER_OPERATIONS),
                    IndexOperation.status.in_(_ACTIVE_OPERATION_STATUSES),
                    IndexOperation.document_generation < delete_generation,
                )
                .order_by(IndexOperation.id)
                .with_for_update()
            )
        )
        if not rows:
            return 0
        operation_ids = [row.id for row in rows]
        attempt_ids = sorted({row.attempt_id for row in rows})
        session.execute(
            update(IndexOperation)
            .where(IndexOperation.id.in_(operation_ids))
            .values(
                status="superseded",
                claimed_by="",
                lease_until=None,
                finished_at=now,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        for attempt_id in attempt_ids:
            remaining = int(
                session.scalar(
                    select(func.count())
                    .select_from(IndexOperation)
                    .where(
                        IndexOperation.attempt_id == attempt_id,
                        IndexOperation.status.in_(_ACTIVE_OPERATION_STATUSES),
                    )
                )
                or 0
            )
            if remaining == 0:
                session.execute(
                    update(DocumentIngestAttempt)
                    .where(
                        DocumentIngestAttempt.id == attempt_id,
                        DocumentIngestAttempt.state.notin_(_TERMINAL_ATTEMPT_STATES),
                    )
                    .values(state="superseded", finished_at=now, updated_at=now)
                    .execution_options(synchronize_session=False)
                )
        return len(operation_ids)

    def _replay_if_present(
        self,
        session: Session,
        *,
        tenant_id: str,
        dataset_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> DeleteBatchProjection | None:
        existing = session.scalar(
            select(DocumentDeleteBatch).where(
                DocumentDeleteBatch.tenant_id == tenant_id,
                DocumentDeleteBatch.dataset_id == dataset_id,
                DocumentDeleteBatch.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise ContentConflict("idempotency key was already used for a different request body")
        return self._load_batch_projection(
            session,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            batch_id=existing.id,
        )

    def _recover_idempotent_replay(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> DeleteBatchProjection | None:
        for attempt in range(5):
            with Session(self.engine, expire_on_commit=False) as recovery:
                replay = self._replay_if_present(
                    recovery,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    return replay
            if attempt < 4:
                time.sleep(0.05)
        return None

    def request_delete(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        expected_generation: int,
        idempotency_key: str,
        actor: AuditContext,
        reason: str,
        origin: str,
    ) -> DeleteOperationProjection:
        batch = self.request_batch(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            items=[DeleteBatchItemRequest(document_id, expected_generation)],
            idempotency_key=idempotency_key,
            actor=actor,
            reason=reason,
            origin=origin,
            _raise_rejected=True,
        )
        item = batch.items[0]
        if item.status != "rejected":
            return item
        if item.result_code == "not_found":
            raise ContentNotFound("document does not exist in dataset scope")
        raise ContentConflict(item.result_message or item.result_code)

    def request_batch(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        items: Sequence[DeleteBatchItemRequest],
        idempotency_key: str,
        actor: AuditContext,
        reason: str,
        origin: str,
        _raise_rejected: bool = False,
    ) -> DeleteBatchProjection:
        tenant_id = self._clean(tenant_id, 64, "tenant_id")
        dataset_id = self._clean(dataset_id, 64, "dataset_id")
        idempotency_key = self._clean(idempotency_key, 128, "idempotency_key")
        actor_id = self._clean(actor.actor_id, 64, "actor_id")
        request_id = self._clean(actor.request_id, 128, "request_id")
        reason = self._optional_text(reason, 512, "reason")
        origin = self._clean(origin, 24, "origin").casefold()
        if origin not in _ALLOWED_ORIGINS:
            raise ValueError("invalid document delete origin")
        normalized_items = self._normalize_items(items)
        request_hash = self._request_hash(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            items=normalized_items,
            reason=reason,
            origin=origin,
        )
        now = _utc_now()
        batch_id = self._new_id("delete-batch")

        try:
            with Session(self.engine, expire_on_commit=False) as session:
                replay = self._replay_if_present(
                    session,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    return replay
                delete_targets = document_delete_projection_targets()

                dataset = session.scalar(
                    select(Dataset)
                    .where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id)
                    .with_for_update()
                )
                if dataset is None:
                    raise ContentNotFound("dataset does not exist in tenant scope")
                if dataset.status != "active":
                    raise ContentConflict("dataset is not active")

                requested_ids = sorted(item.document_id for item in normalized_items)
                documents = {
                    row.id: row
                    for row in session.scalars(
                        select(Document)
                        .where(
                            Document.tenant_id == tenant_id,
                            Document.dataset_id == dataset_id,
                            Document.id.in_(requested_ids),
                        )
                        .order_by(Document.id)
                        .with_for_update()
                    )
                }

                prepared: list[_PreparedItem] = []
                accepted_count = 0
                for request_index, item in enumerate(normalized_items):
                    document = documents.get(item.document_id)
                    if document is None:
                        prepared.append(
                            _PreparedItem(
                                request_index=request_index,
                                requested_document_id=item.document_id,
                                expected_generation=item.expected_generation,
                                operation_id=self._new_id("doc-delete"),
                                document_id=None,
                                delete_generation=None,
                                manifest=ChunkManifest(0, "", ()),
                                quota_chunk_count=0,
                                status="rejected",
                                result_code="not_found",
                                result_message="document does not exist in dataset scope",
                            )
                        )
                        continue
                    if int(document.mutation_generation) != item.expected_generation:
                        prepared.append(
                            _PreparedItem(
                                request_index=request_index,
                                requested_document_id=item.document_id,
                                expected_generation=item.expected_generation,
                                operation_id=self._new_id("doc-delete"),
                                document_id=document.id,
                                delete_generation=None,
                                manifest=ChunkManifest(0, "", ()),
                                quota_chunk_count=0,
                                status="rejected",
                                result_code="document_generation_conflict",
                                result_message=(
                                    "document generation conflict: "
                                    f"expected {item.expected_generation}, current "
                                    f"{document.mutation_generation}"
                                ),
                            )
                        )
                        continue
                    if (
                        document.active_delete_operation_id
                        or document.lifecycle_state in _DELETE_LIFECYCLE_STATES
                    ):
                        prepared.append(
                            _PreparedItem(
                                request_index=request_index,
                                requested_document_id=item.document_id,
                                expected_generation=item.expected_generation,
                                operation_id=self._new_id("doc-delete"),
                                document_id=document.id,
                                delete_generation=None,
                                manifest=ChunkManifest(0, "", ()),
                                quota_chunk_count=0,
                                status="rejected",
                                result_code="delete_already_active",
                                result_message=(
                                    "document already has an active delete operation"
                                    + (
                                        f": {document.active_delete_operation_id}"
                                        if document.active_delete_operation_id
                                        else ""
                                    )
                                ),
                            )
                        )
                        continue
                    if document.lifecycle_state not in {"active", "expired"}:
                        prepared.append(
                            _PreparedItem(
                                request_index=request_index,
                                requested_document_id=item.document_id,
                                expected_generation=item.expected_generation,
                                operation_id=self._new_id("doc-delete"),
                                document_id=document.id,
                                delete_generation=None,
                                manifest=ChunkManifest(0, "", ()),
                                quota_chunk_count=0,
                                status="rejected",
                                result_code="document_lifecycle_conflict",
                                result_message="document lifecycle does not allow deletion",
                            )
                        )
                        continue

                    source_error = self._source_suppression_error(
                        session,
                        document=document,
                        tenant_id=tenant_id,
                        dataset_id=dataset_id,
                        origin=origin,
                    )
                    if source_error:
                        prepared.append(
                            _PreparedItem(
                                request_index=request_index,
                                requested_document_id=item.document_id,
                                expected_generation=item.expected_generation,
                                operation_id=self._new_id("doc-delete"),
                                document_id=document.id,
                                delete_generation=None,
                                manifest=ChunkManifest(0, "", ()),
                                quota_chunk_count=0,
                                status="rejected",
                                result_code="source_identity_incomplete",
                                result_message=source_error,
                            )
                        )
                        continue

                    manifest = self.compute_chunk_manifest(
                        session, tenant_id, dataset_id, document.id
                    )
                    current_enabled = int(
                        session.scalar(
                            select(func.count())
                            .select_from(ChunkHead)
                            .where(
                                ChunkHead.tenant_id == tenant_id,
                                ChunkHead.dataset_id == dataset_id,
                                ChunkHead.document_id == document.id,
                                ChunkHead.document_revision == document.content_revision,
                                ChunkHead.enabled.is_(True),
                            )
                        )
                        or 0
                    )
                    if current_enabled != int(document.chunk_count):
                        prepared.append(
                            _PreparedItem(
                                request_index=request_index,
                                requested_document_id=item.document_id,
                                expected_generation=item.expected_generation,
                                operation_id=self._new_id("doc-delete"),
                                document_id=document.id,
                                delete_generation=None,
                                manifest=manifest,
                                quota_chunk_count=int(document.chunk_count),
                                status="rejected",
                                result_code="chunk_authority_incomplete",
                                result_message="chunk authority incomplete for document deletion",
                            )
                        )
                        continue

                    next_generation = item.expected_generation + 1
                    operation_id = self._new_id("doc-delete")
                    self._before_document_cas(session, document, item.expected_generation)
                    result = session.execute(
                        update(Document)
                        .where(
                            Document.id == document.id,
                            Document.tenant_id == tenant_id,
                            Document.dataset_id == dataset_id,
                            Document.mutation_generation == item.expected_generation,
                            Document.active_delete_operation_id.is_(None),
                            Document.lifecycle_state.in_(("active", "expired")),
                        )
                        .values(
                            mutation_generation=next_generation,
                            lifecycle_state="delete_requested",
                            retrieval_enabled=False,
                            deletion_requested_at=now,
                            updated_at=now,
                        )
                        .execution_options(synchronize_session=False)
                    )
                    if int(result.rowcount or 0) != 1:
                        prepared.append(
                            _PreparedItem(
                                request_index=request_index,
                                requested_document_id=item.document_id,
                                expected_generation=item.expected_generation,
                                operation_id=operation_id,
                                document_id=document.id,
                                delete_generation=None,
                                manifest=manifest,
                                quota_chunk_count=int(document.chunk_count),
                                status="rejected",
                                result_code="document_generation_conflict",
                                result_message="document changed while deletion was requested",
                            )
                        )
                        continue
                    accepted_count += 1
                    prepared.append(
                        _PreparedItem(
                            request_index=request_index,
                            requested_document_id=item.document_id,
                            expected_generation=item.expected_generation,
                            operation_id=operation_id,
                            document_id=document.id,
                            delete_generation=next_generation,
                            manifest=manifest,
                            quota_chunk_count=int(document.chunk_count),
                            status="queued",
                            result_code="",
                            result_message="",
                            previous_lifecycle=document.lifecycle_state,
                            previous_retrieval_enabled=bool(document.retrieval_enabled),
                        )
                    )

                if _raise_rejected and prepared[0].status == "rejected":
                    rejected = prepared[0]
                    session.rollback()
                    if rejected.result_code == "not_found":
                        raise ContentNotFound("document does not exist in dataset scope")
                    if rejected.result_code == "document_generation_conflict":
                        replay = self._recover_idempotent_replay(
                            tenant_id=tenant_id,
                            dataset_id=dataset_id,
                            idempotency_key=idempotency_key,
                            request_hash=request_hash,
                        )
                        if replay is not None:
                            return replay
                    raise ContentConflict(rejected.result_message or rejected.result_code)

                batch = DocumentDeleteBatch(
                    id=batch_id,
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    actor_id=actor_id,
                    request_id=request_id,
                    reason=reason,
                    status="running" if accepted_count else "failed",
                    requested_count=len(normalized_items),
                    accepted_count=accepted_count,
                    completed_count=0,
                    failed_count=0,
                    rejected_count=len(normalized_items) - accepted_count,
                    created_at=now,
                    updated_at=now,
                    finished_at=now if accepted_count == 0 else None,
                )
                session.add(batch)
                session.flush()

                if accepted_count:
                    session.execute(
                        update(Dataset)
                        .where(Dataset.id == dataset_id, Dataset.tenant_id == tenant_id)
                        .values(
                            serving_generation=Dataset.serving_generation + accepted_count,
                            updated_at=now,
                        )
                        .execution_options(synchronize_session=False)
                    )

                item_rows: list[DocumentDeleteOperation] = []
                for prepared_item in prepared:
                    item_row = DocumentDeleteOperation(
                        id=prepared_item.operation_id,
                        batch_id=batch.id,
                        request_index=prepared_item.request_index,
                        tenant_id=tenant_id,
                        dataset_id=dataset_id,
                        requested_document_id=prepared_item.requested_document_id,
                        document_id=prepared_item.document_id,
                        expected_generation=prepared_item.expected_generation,
                        delete_generation=prepared_item.delete_generation,
                        attempt_id=None,
                        origin=origin,
                        status=prepared_item.status,
                        result_code=prepared_item.result_code,
                        result_message=prepared_item.result_message,
                        chunk_manifest_count=prepared_item.manifest.count,
                        chunk_manifest_hash=prepared_item.manifest.hash,
                        quota_chunk_count=prepared_item.quota_chunk_count,
                        required_store_count=(
                            len(delete_targets) if prepared_item.status == "queued" else 0
                        ),
                        completed_store_count=0,
                        failed_store_count=0,
                        requested_by=actor_id,
                        request_id=request_id,
                        reason=reason,
                        started_at=now if prepared_item.status == "queued" else None,
                        finalized_at=None,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(item_row)
                    session.flush()
                    item_rows.append(item_row)
                    if prepared_item.status != "queued":
                        continue

                    document_id = prepared_item.document_id
                    assert document_id is not None
                    assert prepared_item.delete_generation is not None
                    attempt_no = (
                        int(
                            session.scalar(
                                select(func.max(DocumentIngestAttempt.attempt_no)).where(
                                    DocumentIngestAttempt.document_id == document_id
                                )
                            )
                            or 0
                        )
                        + 1
                    )
                    attempt = DocumentIngestAttempt(
                        id=self._new_id("attempt"),
                        tenant_id=tenant_id,
                        dataset_id=dataset_id,
                        document_id=document_id,
                        attempt_no=attempt_no,
                        input_revision=prepared_item.delete_generation,
                        attempt_kind="document_delete",
                        document_generation=prepared_item.delete_generation,
                        state="running",
                        worker_id="durable-document-delete",
                        started_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(attempt)
                    session.flush()
                    item_row.attempt_id = attempt.id
                    document_link = session.execute(
                        update(Document)
                        .where(
                            Document.id == document_id,
                            Document.tenant_id == tenant_id,
                            Document.dataset_id == dataset_id,
                            Document.mutation_generation == prepared_item.delete_generation,
                            Document.active_delete_operation_id.is_(None),
                        )
                        .values(
                            active_delete_operation_id=item_row.id,
                            current_attempt_id=attempt.id,
                            updated_at=now,
                        )
                        .execution_options(synchronize_session=False)
                    )
                    if int(document_link.rowcount or 0) != 1:
                        raise ContentConflict(
                            "document delete fence changed before operation linkage"
                        )
                    document = documents[document_id]
                    self._upsert_source_suppression(
                        session,
                        document=document,
                        delete_operation_id=item_row.id,
                        delete_generation=prepared_item.delete_generation,
                        origin=origin,
                        now=now,
                    )
                    self._supersede_projection_writers(
                        session,
                        tenant_id=tenant_id,
                        dataset_id=dataset_id,
                        document_id=document_id,
                        delete_generation=prepared_item.delete_generation,
                        now=now,
                    )
                    for target_store in delete_targets:
                        child = IndexOperation(
                            id=self._new_id("index-op"),
                            tenant_id=tenant_id,
                            dataset_id=dataset_id,
                            document_id=document_id,
                            attempt_id=attempt.id,
                            target_store=target_store,
                            operation="delete_document",
                            dedup_key=(
                                f"document-delete:{item_row.id}:"
                                f"{prepared_item.delete_generation}:{target_store}"
                            ),
                            target_revision=prepared_item.delete_generation,
                            document_generation=prepared_item.delete_generation,
                            delete_operation_id=item_row.id,
                            payload={
                                "delete_operation_id": item_row.id,
                                "document_generation": prepared_item.delete_generation,
                                "chunk_manifest_count": prepared_item.manifest.count,
                                "chunk_manifest_hash": prepared_item.manifest.hash,
                            },
                            status="pending",
                            max_retries=3,
                            created_at=now,
                            updated_at=now,
                        )
                        self._add_index_operation(session, child)
                    audit_event = KnowledgeAuditEvent(
                        id=self._new_id("audit"),
                        tenant_id=tenant_id,
                        dataset_id=dataset_id,
                        actor_id=actor_id,
                        action="document.delete_requested",
                        resource_type="document_delete_operation",
                        resource_id=item_row.id,
                        before_snapshot={
                            "document_id": document_id,
                            "mutation_generation": prepared_item.expected_generation,
                            "lifecycle_state": prepared_item.previous_lifecycle,
                            "retrieval_enabled": prepared_item.previous_retrieval_enabled,
                        },
                        after_snapshot={
                            "document_id": document_id,
                            "mutation_generation": prepared_item.delete_generation,
                            "lifecycle_state": "delete_requested",
                            "retrieval_enabled": False,
                            "chunk_manifest_count": prepared_item.manifest.count,
                            "chunk_manifest_hash": prepared_item.manifest.hash,
                        },
                        request_id=request_id,
                        request_ip=str(actor.request_ip or "")[:64],
                        occurred_at=now,
                    )
                    self._add_audit_event(session, audit_event)

                session.flush()
                session.commit()
                return self._batch_projection(batch, item_rows)
        except OperationalError as exc:
            if "locked" in str(exc).casefold():
                replay = self._recover_idempotent_replay(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                )
                if replay is not None:
                    return replay
                raise ContentConflict("document changed while deletion was requested") from exc
            raise
        except IntegrityError as exc:
            replay = self._recover_idempotent_replay(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
            if replay is not None:
                return replay
            raise ContentConflict("document delete request conflicts with current state") from exc


    @staticmethod
    def _lock_dataset_reset_scope(
        session: Session,
        source_id: str,
    ) -> tuple[Dataset, DataSourceRecord, list[SourceSyncRun], list[IndexOperation]]:
        scope = session.execute(
            select(
                DataSourceRecord.tenant_id,
                DataSourceRecord.dataset_id,
            ).where(DataSourceRecord.id == source_id)
        ).one_or_none()
        if scope is None:
            raise ContentNotFound("source does not exist")
        dataset = session.scalar(
            select(Dataset)
            .where(
                Dataset.id == str(scope.dataset_id),
                Dataset.tenant_id == str(scope.tenant_id),
            )
            .with_for_update()
        )
        source = session.scalar(
            select(DataSourceRecord)
            .where(
                DataSourceRecord.id == source_id,
                DataSourceRecord.dataset_id == str(scope.dataset_id),
                DataSourceRecord.tenant_id == str(scope.tenant_id),
            )
            .with_for_update()
        )
        if dataset is None or source is None:
            raise ContentNotFound("dataset or source no longer exists")
        runs = list(
            session.scalars(
                select(SourceSyncRun)
                .where(
                    SourceSyncRun.source_id == source.id,
                    SourceSyncRun.status == "running",
                )
                .order_by(SourceSyncRun.id)
                .with_for_update()
            )
        )
        operations = list(
            session.scalars(
                select(IndexOperation)
                .join(Document, Document.id == IndexOperation.document_id)
                .where(
                    Document.source_id == source.id,
                    IndexOperation.status.in_(_ACTIVE_OPERATION_STATUSES),
                )
                .order_by(IndexOperation.id)
                .with_for_update()
            )
        )
        return dataset, source, runs, operations

    def request_dataset_reset(
        self,
        *,
        source_id: str,
        actor: AuditContext,
        reason: str,
        idempotency_prefix: str,
    ) -> DatasetResetProjection:
        """Fence one source/dataset and enqueue every document in atomic <=100 batches."""
        source_id = self._clean(source_id, 64, "source_id")
        actor_id = self._clean(actor.actor_id, 64, "actor_id")
        request_id = self._clean(actor.request_id, 128, "request_id")
        reason = self._optional_text(reason, 512, "reason")
        prefix = self._clean(idempotency_prefix, 88, "idempotency_prefix")
        now = _utc_now()

        with self.engine.connect() as connection:
            outer = connection.begin()
            control = Session(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="rollback_only",
            )
            try:
                dataset, source, affected_runs, _affected_operations = (
                    self._lock_dataset_reset_scope(control, source_id)
                )
                if source.status != "active" or dataset.status != "active":
                    raise ContentConflict("source or dataset is not active")

                existing_batches = list(
                    control.scalars(
                        select(DocumentDeleteBatch)
                        .where(
                            DocumentDeleteBatch.tenant_id == source.tenant_id,
                            DocumentDeleteBatch.dataset_id == source.dataset_id,
                            DocumentDeleteBatch.request_id == request_id,
                            DocumentDeleteBatch.idempotency_key.like(
                                f"{prefix}:%"
                            ),
                        )
                        .order_by(DocumentDeleteBatch.created_at, DocumentDeleteBatch.id)
                    )
                )
                if existing_batches:
                    result = DatasetResetProjection(
                        source_id=source.id,
                        tenant_id=source.tenant_id,
                        dataset_id=source.dataset_id,
                        source_generation=int(source.mutation_generation or 0),
                        dataset_generation=int(dataset.mutation_generation or 0),
                        document_count=sum(
                            int(batch.requested_count or 0) for batch in existing_batches
                        ),
                        batch_ids=tuple(batch.id for batch in existing_batches),
                        request_id=request_id,
                    )
                    control.close()
                    outer.commit()
                    return result

                documents = list(
                    control.scalars(
                        select(Document)
                        .where(
                            Document.tenant_id == source.tenant_id,
                            Document.dataset_id == source.dataset_id,
                            Document.lifecycle_state.in_(("active", "expired")),
                        )
                        .order_by(Document.id)
                        .with_for_update()
                    )
                )
                source.mutation_generation = int(source.mutation_generation or 0) + 1
                dataset.mutation_generation = int(dataset.mutation_generation or 0) + 1
                dataset.updated_at = now
                for affected_run in affected_runs:
                    affected_run.status = "superseded"
                    affected_run.finished_at = now
                control.flush()

                nested = DocumentDeletionRepository(connection)
                batches: list[DeleteBatchProjection] = []
                for batch_index, offset in enumerate(range(0, len(documents), 100)):
                    group = documents[offset : offset + 100]
                    batch = nested.request_batch(
                        tenant_id=source.tenant_id,
                        dataset_id=source.dataset_id,
                        items=[
                            DeleteBatchItemRequest(
                                document_id=document.id,
                                expected_generation=int(
                                    document.mutation_generation or 0
                                ),
                            )
                            for document in group
                        ],
                        idempotency_key=f"{prefix}:{batch_index:04d}",
                        actor=AuditContext(
                            actor_id=actor_id,
                            request_id=request_id,
                            request_ip=actor.request_ip,
                        ),
                        reason=reason,
                        origin="dataset_reset",
                    )
                    if batch.accepted_count != batch.requested_count:
                        raise ContentConflict(
                            "dataset reset could not fence every document"
                        )
                    batches.append(batch)

                result = DatasetResetProjection(
                    source_id=source.id,
                    tenant_id=source.tenant_id,
                    dataset_id=source.dataset_id,
                    source_generation=int(source.mutation_generation),
                    dataset_generation=int(dataset.mutation_generation),
                    document_count=len(documents),
                    batch_ids=tuple(batch.id for batch in batches),
                    request_id=request_id,
                )
                control.close()
                outer.commit()
                return result
            except Exception:
                control.close()
                if outer.is_active:
                    outer.rollback()
                raise


    @staticmethod
    def _validate_claimed_operation(
        item: IndexOperation,
        *,
        worker_id: str,
    ) -> None:
        if item.status != "claimed" or item.claimed_by != worker_id:
            raise DocumentDeletionConflict("delete operation is not claimed by this worker")

    def _delete_context(
        self,
        session: Session,
        *,
        operation_id: str,
        worker_id: str | None = None,
        lock: bool = False,
    ) -> tuple[IndexOperation, DocumentDeleteOperation, Document, DocumentIngestAttempt]:
        item = session.get(IndexOperation, operation_id, with_for_update=lock)
        if item is None or not item.delete_operation_id:
            raise DocumentDeletionConflict("index operation is not a durable delete operation")
        if worker_id is not None:
            self._validate_claimed_operation(item, worker_id=worker_id)
        parent = session.get(
            DocumentDeleteOperation, item.delete_operation_id, with_for_update=lock
        )
        if parent is None or parent.document_id is None or parent.attempt_id is None:
            raise DocumentDeletionConflict("document delete parent operation is missing")
        document = session.get(Document, parent.document_id, with_for_update=lock)
        attempt = session.get(DocumentIngestAttempt, parent.attempt_id, with_for_update=lock)
        if document is None or attempt is None:
            raise DocumentDeletionConflict("document delete authority is incomplete")
        if (
            item.tenant_id != parent.tenant_id
            or item.dataset_id != parent.dataset_id
            or item.document_id != parent.document_id
            or item.attempt_id != parent.attempt_id
            or item.document_generation != parent.delete_generation
            or document.tenant_id != parent.tenant_id
            or document.dataset_id != parent.dataset_id
            or document.mutation_generation != parent.delete_generation
            or document.active_delete_operation_id != parent.id
            or document.lifecycle_state
            not in {"delete_requested", "deleting", "delete_failed"}
            or document.retrieval_enabled
        ):
            raise DocumentDeletionConflict("document delete generation fence is stale")
        return item, parent, document, attempt

    def projection_plan(self, operation: IndexOperation) -> DeleteProjectionPlan:
        """Return a validated, detached plan before any external store call."""
        if operation.operation != "delete_document":
            raise DocumentDeletionConflict("operation is not a document projection delete")
        if operation.target_store not in document_delete_projection_targets():
            raise DocumentDeletionConflict("unsupported document delete target store")
        with Session(self.engine) as session:
            item, parent, document, _attempt = self._delete_context(
                session,
                operation_id=operation.id,
                worker_id=operation.claimed_by,
            )
            if parent.status not in {"queued", "projecting"}:
                raise DocumentDeletionConflict("document delete parent is not projecting")
            manifest = self.compute_chunk_manifest(
                session, parent.tenant_id, parent.dataset_id, document.id
            )
            if item.target_store == "graph_projection" and (
                manifest.count != parent.chunk_manifest_count
                or manifest.hash != parent.chunk_manifest_hash
            ):
                raise DocumentDeletionConflict("document delete chunk manifest drifted")
            return DeleteProjectionPlan(
                operation_id=item.id,
                delete_operation_id=parent.id,
                tenant_id=parent.tenant_id,
                dataset_id=parent.dataset_id,
                document_id=document.id,
                document_generation=int(parent.delete_generation or 0),
                target_store=item.target_store,
                manifest=manifest,
            )

    def _enqueue_delete_finalizer(
        self,
        session: Session,
        *,
        parent: DocumentDeleteOperation,
        now: datetime,
    ) -> IndexOperation:
        dedup_key = (
            f"document-delete:{parent.id}:{parent.delete_generation}:catalog-finalize"
        )
        existing = session.scalar(
            select(IndexOperation).where(IndexOperation.dedup_key == dedup_key)
        )
        if existing is not None:
            return existing
        item = IndexOperation(
            id=self._new_id("index-op"),
            tenant_id=parent.tenant_id,
            dataset_id=parent.dataset_id,
            document_id=str(parent.document_id),
            attempt_id=str(parent.attempt_id),
            target_store="catalog_finalize",
            operation="finalize_document_delete",
            dedup_key=dedup_key,
            target_revision=int(parent.delete_generation or 0),
            document_generation=int(parent.delete_generation or 0),
            delete_operation_id=parent.id,
            payload={
                "delete_operation_id": parent.id,
                "document_generation": int(parent.delete_generation or 0),
            },
            status="pending",
            max_retries=3,
            created_at=now,
            updated_at=now,
        )
        self._add_index_operation(session, item)
        return item

    def _complete_projection_in_session(
        self,
        session: Session,
        *,
        operation_id: str,
        worker_id: str,
        now: datetime,
    ) -> DeleteOperationProjection:
        item, parent, document, attempt = self._delete_context(
            session,
            operation_id=operation_id,
            worker_id=worker_id,
            lock=True,
        )
        if (
            item.operation != "delete_document"
            or item.target_store not in document_delete_projection_targets()
        ):
            raise DocumentDeletionConflict("operation is not a projection delete child")
        if parent.status not in {"queued", "projecting"}:
            raise DocumentDeletionConflict("document delete parent is no longer projecting")
        item.status = "succeeded"
        item.claimed_by = ""
        item.lease_until = None
        item.finished_at = now
        item.updated_at = now
        if document.lifecycle_state == "delete_requested":
            document.lifecycle_state = "deleting"
        session.flush()
        completed = int(
            session.scalar(
                select(func.count(IndexOperation.id)).where(
                    IndexOperation.delete_operation_id == parent.id,
                    IndexOperation.operation == "delete_document",
                    IndexOperation.status == "succeeded",
                )
            )
            or 0
        )
        parent.completed_store_count = completed
        parent.failed_store_count = int(
            session.scalar(
                select(func.count(IndexOperation.id)).where(
                    IndexOperation.delete_operation_id == parent.id,
                    IndexOperation.operation == "delete_document",
                    IndexOperation.status == "dead",
                )
            )
            or 0
        )
        parent.updated_at = now
        if completed >= parent.required_store_count:
            parent.status = "finalizing"
            attempt.state = "finalizing"
            attempt.updated_at = now
            self._enqueue_delete_finalizer(session, parent=parent, now=now)
        else:
            parent.status = "projecting"
            attempt.state = "running"
            attempt.updated_at = now
        session.flush()
        return self._operation_projection(parent)

    def complete_projection_operation(
        self,
        operation_id: str,
        *,
        worker_id: str,
        now: datetime | None = None,
        session: Session | None = None,
    ) -> DeleteOperationProjection:
        """Atomically complete one child and enqueue the finalizer when last."""
        now = now or _utc_now()
        if session is not None:
            return self._complete_projection_in_session(
                session, operation_id=operation_id, worker_id=worker_id, now=now
            )
        with Session(self.engine, expire_on_commit=False) as owned:
            with owned.begin():
                result = self._complete_projection_in_session(
                    owned, operation_id=operation_id, worker_id=worker_id, now=now
                )
            return result

    @staticmethod
    def _refresh_batch_state(
        session: Session,
        batch: DocumentDeleteBatch | None,
        *,
        now: datetime,
    ) -> None:
        if batch is None:
            return
        completed = int(
            session.scalar(
                select(func.count(DocumentDeleteOperation.id)).where(
                    DocumentDeleteOperation.batch_id == batch.id,
                    DocumentDeleteOperation.status == "completed",
                )
            )
            or 0
        )
        failed = int(
            session.scalar(
                select(func.count(DocumentDeleteOperation.id)).where(
                    DocumentDeleteOperation.batch_id == batch.id,
                    DocumentDeleteOperation.status == "failed",
                )
            )
            or 0
        )
        batch.completed_count = completed
        batch.failed_count = failed
        if completed + failed >= batch.accepted_count:
            if failed == 0:
                batch.status = "completed"
            elif completed == 0:
                batch.status = "failed"
            else:
                batch.status = "partially_failed"
            batch.finished_at = now
        else:
            batch.status = "running"
            batch.finished_at = None
        batch.updated_at = now

    def retry_delete_operation(
        self,
        operation_id: str,
        *,
        worker_id: str,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
        base_delay_seconds: int = 5,
    ) -> IndexOperation:
        """Retry a delete operation; atomically fail its parent on dead-letter."""
        now = now or _utc_now()
        with Session(self.engine, expire_on_commit=False) as session:
            with session.begin():
                item, parent, document, attempt = self._delete_context(
                    session,
                    operation_id=operation_id,
                    worker_id=worker_id,
                    lock=True,
                )
                item.retry_count += 1
                item.last_error_code = str(error_code or "")[:64]
                item.last_error = sanitize_error_message(error_message)[:2000]
                item.claimed_by = ""
                item.lease_until = None
                item.updated_at = now
                if item.retry_count < item.max_retries:
                    item.status = "retry"
                    delay = max(1, base_delay_seconds) * (2 ** (item.retry_count - 1))
                    item.next_retry_at = now + timedelta(seconds=delay)
                    return item

                item.status = "dead"
                item.finished_at = now
                session.add(
                    IndexDeadLetter(
                        id=self._new_id("dead-letter"),
                        operation_id=item.id,
                        tenant_id=item.tenant_id,
                        dataset_id=item.dataset_id,
                        document_id=item.document_id,
                        attempt_id=item.attempt_id,
                        dedup_key=item.dedup_key,
                        target_store=item.target_store,
                        operation=item.operation,
                        target_revision=item.target_revision,
                        payload=item.payload or {},
                        retry_count=item.retry_count,
                        last_error_code=item.last_error_code,
                        last_error=item.last_error,
                        failed_at=now,
                    )
                )
                was_failed = parent.status == "failed"
                parent.status = "failed"
                parent.result_code = item.last_error_code or "projection_failed"
                parent.result_message = item.last_error
                parent.failed_store_count = int(
                    session.scalar(
                        select(func.count(IndexOperation.id)).where(
                            IndexOperation.delete_operation_id == parent.id,
                            IndexOperation.operation == "delete_document",
                            IndexOperation.status == "dead",
                        )
                    )
                    or 0
                )
                parent.failed_store_count = min(
                    parent.required_store_count, parent.failed_store_count
                )
                parent.updated_at = now
                session.execute(
                    update(IndexOperation)
                    .where(
                        IndexOperation.delete_operation_id == parent.id,
                        IndexOperation.id != item.id,
                        IndexOperation.status.in_(_ACTIVE_OPERATION_STATUSES),
                    )
                    .values(
                        status="superseded",
                        claimed_by="",
                        lease_until=None,
                        finished_at=now,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
                attempt.state = "failed"
                attempt.error_code = item.last_error_code
                attempt.error_message = item.last_error
                attempt.finished_at = now
                attempt.updated_at = now
                document.lifecycle_state = "delete_failed"
                document.retrieval_enabled = False
                document.updated_at = now
                batch = (
                    session.get(DocumentDeleteBatch, parent.batch_id, with_for_update=True)
                    if parent.batch_id
                    else None
                )
                session.flush()
                if not was_failed:
                    self._refresh_batch_state(session, batch, now=now)
                return item

    def _release_usage(
        self,
        *,
        tenant: Tenant,
        dataset: Dataset,
        parent: DocumentDeleteOperation,
        document: Document,
        now: datetime,
    ) -> None:
        if document.usage_released_at is not None:
            return
        chunks = max(0, int(parent.quota_chunk_count or 0))
        tenant.doc_count = max(0, int(tenant.doc_count or 0) - 1)
        tenant.chunk_count = max(0, int(tenant.chunk_count or 0) - chunks)
        dataset.doc_count = max(0, int(dataset.doc_count or 0) - 1)
        dataset.chunk_count = max(0, int(dataset.chunk_count or 0) - chunks)
        document.usage_released_at = now

    def _finalize_in_session(
        self,
        session: Session,
        *,
        operation_id: str,
        worker_id: str,
        now: datetime,
    ) -> DeleteOperationProjection:
        item = session.get(IndexOperation, operation_id, with_for_update=True)
        if item is None or not item.delete_operation_id:
            raise DocumentDeletionConflict("delete finalizer operation does not exist")
        parent = session.get(
            DocumentDeleteOperation, item.delete_operation_id, with_for_update=True
        )
        if parent is None:
            raise DocumentDeletionConflict("document delete parent operation is missing")
        if item.status == "succeeded" and parent.status == "completed":
            return self._operation_projection(parent)
        self._validate_claimed_operation(item, worker_id=worker_id)
        if item.operation != "finalize_document_delete" or item.target_store != "catalog_finalize":
            raise DocumentDeletionConflict("operation is not a catalog delete finalizer")
        document = session.get(Document, parent.document_id, with_for_update=True)
        attempt = session.get(DocumentIngestAttempt, parent.attempt_id, with_for_update=True)
        dataset = session.get(Dataset, parent.dataset_id, with_for_update=True)
        tenant = session.get(Tenant, parent.tenant_id, with_for_update=True)
        if any(value is None for value in (document, attempt, dataset, tenant)):
            raise DocumentDeletionConflict("document delete finalizer authority is incomplete")
        if (
            parent.status != "finalizing"
            or parent.delete_generation != item.document_generation
            or document.mutation_generation != parent.delete_generation
            or document.active_delete_operation_id != parent.id
            or document.lifecycle_state not in {"delete_requested", "deleting", "delete_failed"}
            or document.retrieval_enabled
        ):
            raise DocumentDeletionConflict("document delete finalizer generation fence is stale")
        completed_children = int(
            session.scalar(
                select(func.count(IndexOperation.id)).where(
                    IndexOperation.delete_operation_id == parent.id,
                    IndexOperation.operation == "delete_document",
                    IndexOperation.status == "succeeded",
                )
            )
            or 0
        )
        if completed_children != parent.required_store_count:
            raise DocumentDeletionConflict("document delete projections are incomplete")
        active_writers = int(
            session.scalar(
                select(func.count(IndexOperation.id)).where(
                    IndexOperation.document_id == document.id,
                    IndexOperation.operation.in_(_PROJECTION_WRITER_OPERATIONS),
                    IndexOperation.status.in_(_ACTIVE_OPERATION_STATUSES),
                )
            )
            or 0
        )
        if active_writers:
            raise DocumentDeletionConflict("document still has active projection writers")

        session.execute(
            update(ChunkHead)
            .where(
                ChunkHead.tenant_id == parent.tenant_id,
                ChunkHead.dataset_id == parent.dataset_id,
                ChunkHead.document_id == document.id,
            )
            .values(enabled=False, index_status="deleted", updated_at=now)
        )
        before = {
            "document_id": document.id,
            "mutation_generation": int(document.mutation_generation),
            "lifecycle_state": document.lifecycle_state,
            "chunk_count": int(document.chunk_count or 0),
        }
        self._release_usage(
            tenant=tenant,
            dataset=dataset,
            parent=parent,
            document=document,
            now=now,
        )
        document.lifecycle_state = "deleted"
        document.retrieval_enabled = False
        document.chunk_count = 0
        document.deleted_at = document.deleted_at or now
        document.active_delete_operation_id = None
        document.updated_at = now
        attempt.state = "completed"
        attempt.finished_at = attempt.finished_at or now
        attempt.pending_subtasks = 0
        attempt.updated_at = now
        parent.status = "completed"
        parent.completed_store_count = parent.required_store_count
        parent.failed_store_count = 0
        parent.result_code = ""
        parent.result_message = ""
        parent.finalized_at = parent.finalized_at or now
        parent.updated_at = now
        item.status = "succeeded"
        item.claimed_by = ""
        item.lease_until = None
        item.finished_at = item.finished_at or now
        item.updated_at = now
        batch = (
            session.get(DocumentDeleteBatch, parent.batch_id, with_for_update=True)
            if parent.batch_id
            else None
        )
        session.flush()
        self._refresh_batch_state(session, batch, now=now)
        self._add_audit_event(
            session,
            KnowledgeAuditEvent(
                id=self._new_id("audit"),
                tenant_id=parent.tenant_id,
                dataset_id=parent.dataset_id,
                actor_id=parent.requested_by,
                action="document.deleted",
                resource_type="document_delete_operation",
                resource_id=parent.id,
                before_snapshot=before,
                after_snapshot={
                    "document_id": document.id,
                    "mutation_generation": int(document.mutation_generation),
                    "lifecycle_state": "deleted",
                    "retrieval_enabled": False,
                    "chunk_count": 0,
                    "usage_released_at": now.isoformat(timespec="microseconds"),
                },
                request_id=parent.request_id,
                request_ip="",
                occurred_at=now,
            ),
        )
        session.flush()
        return self._operation_projection(parent)

    def finalize_document_delete(
        self,
        operation_id: str,
        *,
        worker_id: str,
        now: datetime | None = None,
        session: Session | None = None,
    ) -> DeleteOperationProjection:
        """Atomically finalize relational deletion after both stores are absent."""
        now = now or _utc_now()
        if session is not None:
            return self._finalize_in_session(
                session, operation_id=operation_id, worker_id=worker_id, now=now
            )
        with Session(self.engine, expire_on_commit=False) as owned:
            with owned.begin():
                result = self._finalize_in_session(
                    owned, operation_id=operation_id, worker_id=worker_id, now=now
                )
            return result


__all__ = [
    "ChunkManifest",
    "DeleteBatchItemRequest",
    "DatasetResetProjection",
    "DocumentDeleteTargetPolicy",
    "DeleteProjectionPlan",
    "DeleteBatchProjection",
    "DeleteOperationProjection",
    "DocumentDeletionConflict",
    "DocumentDeletionRepository",
    "document_delete_projection_targets",
]
