"""Consume durable index operations with retry and revision fencing."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core import cache_epoch
from core.document_deletion import (
    DocumentDeletionConflict,
    DocumentDeletionRepository,
)
from core.index_operations import IndexOperationConflict, IndexOperationQueue
from core.ingest_ledger import IngestLedger, IngestLedgerConflict
from indexing.projection_handlers import StaleProjectionOperation
from models.orm import (
    ChunkHead,
    Dataset,
    Document,
    DocumentIngestAttempt,
    IndexOperation,
    Tenant,
)

OperationHandler = Callable[[IndexOperation], None]


@dataclass(frozen=True)
class WorkerRunResult:
    claimed: int = 0
    succeeded: int = 0
    retried: int = 0
    dead: int = 0
    superseded: int = 0


@dataclass(frozen=True)
class _LeaseHeartbeat:
    stop: threading.Event
    lease_lost: threading.Event

    def mark_transition_committed(self) -> None:
        """Stop renewing after ownership has been durably released."""
        self.stop.set()


class IndexOperationWorker:
    def __init__(
        self,
        queue: IndexOperationQueue,
        *,
        handlers: dict[str, OperationHandler],
        worker_id: str,
        lease_seconds: int = 30,
    ):
        self.queue = queue
        self.handlers = dict(handlers)
        self.worker_id = worker_id
        self.lease_seconds = max(1, lease_seconds)
        self.deletion_repository = DocumentDeletionRepository(queue.engine)

    @contextmanager
    def _lease_heartbeat(self, operation: IndexOperation) -> Iterator[_LeaseHeartbeat]:
        # Fence ownership synchronously before any external side effect. A claimed
        # row may already have expired between claim_operations() and this point.
        self.queue.renew_lease(
            operation.id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        stop = threading.Event()
        lease_lost = threading.Event()
        interval = max(0.05, self.lease_seconds / 3.0)

        def renew_until_stopped() -> None:
            while not stop.wait(interval):
                try:
                    self.queue.renew_lease(
                        operation.id,
                        worker_id=self.worker_id,
                        lease_seconds=self.lease_seconds,
                    )
                except Exception:  # noqa: BLE001 - claim is no longer safe to finalize
                    lease_lost.set()
                    return

        thread = threading.Thread(
            target=renew_until_stopped,
            name=f"index-lease-{self.worker_id}-{operation.id}",
            daemon=True,
        )
        heartbeat = _LeaseHeartbeat(stop=stop, lease_lost=lease_lost)
        thread.start()
        try:
            yield heartbeat
        finally:
            stop.set()
            thread.join(timeout=max(1.0, interval + 0.5))

    def _advance_projection_revision(self, operation: IndexOperation) -> None:
        with Session(self.queue.engine) as session:
            document = session.get(Document, operation.document_id)
            if document is None:
                return
            if (
                operation.target_store == "milvus_chunks"
                and document.desired_index_revision == operation.target_revision
                and document.indexed_revision < operation.target_revision
            ):
                document.indexed_revision = operation.target_revision
            elif (
                operation.target_store.startswith("graph_")
                and document.content_revision == operation.target_revision
                and document.graph_revision < operation.target_revision
            ):
                document.graph_revision = operation.target_revision
            session.commit()


    def _advance_attempt_lifecycle(self, operation: IndexOperation) -> str | None:
        ledger = IngestLedger(self.queue.engine)
        with Session(self.queue.engine, expire_on_commit=False) as session:
            attempt = session.get(DocumentIngestAttempt, operation.attempt_id)
            if attempt is None:
                return
            active_count = int(
                session.scalar(
                    select(func.count(IndexOperation.id)).where(
                        IndexOperation.attempt_id == operation.attempt_id,
                        IndexOperation.status.in_(("pending", "retry", "claimed")),
                    )
                )
                or 0
            )
            dead_count = int(
                session.scalar(
                    select(func.count(IndexOperation.id)).where(
                        IndexOperation.attempt_id == operation.attempt_id,
                        IndexOperation.status == "dead",
                    )
                )
                or 0
            )
            graph_pending = int(
                session.scalar(
                    select(func.count(IndexOperation.id)).where(
                        IndexOperation.attempt_id == operation.attempt_id,
                        IndexOperation.target_store == "graph_projection",
                        IndexOperation.status.in_(("pending", "retry", "claimed")),
                    )
                )
                or 0
            )
            attempt_state = attempt.state
        try:
            if operation.target_store == "milvus_chunks" and attempt_state == "running":
                ledger.set_attempt_state(operation.attempt_id, "primary_ready")
                attempt_state = "primary_ready"
            if active_count and graph_pending and attempt_state == "primary_ready":
                ledger.set_attempt_state(operation.attempt_id, "finalizing")
                return
        except IngestLedgerConflict:
            return
        if active_count or dead_count:
            return
        with Session(self.queue.engine) as session:
            attempt = session.get(DocumentIngestAttempt, operation.attempt_id)
            document = session.get(Document, operation.document_id)
            if attempt is None or document is None:
                return
            if document.current_attempt_id != attempt.id:
                return
            if document.desired_index_revision != document.indexed_revision:
                return
            graph_operations = int(
                session.scalar(
                    select(func.count(IndexOperation.id)).where(
                        IndexOperation.attempt_id == attempt.id,
                        IndexOperation.target_store == "graph_projection",
                    )
                )
                or 0
            )
            if graph_operations and document.graph_revision != document.content_revision:
                return
            chunk_count = int(
                session.scalar(
                    select(func.count(ChunkHead.id)).where(
                        ChunkHead.document_id == document.id,
                        ChunkHead.document_revision == document.content_revision,
                        ChunkHead.enabled.is_(True),
                    )
                )
                or 0
            )
            previous_count = int(document.chunk_count or 0)
            delta = chunk_count - previous_count
            document.status = "completed"
            document.status_detail = "持久化投影完成"
            document.progress = 1.0
            document.chunk_count = chunk_count
            parser_meta = dict(document.parser_meta or {})
            parser_meta["chunk_count"] = chunk_count
            document.parser_meta = parser_meta
            document.file_hash = attempt.input_content_hash or document.file_hash
            tenant = session.get(Tenant, document.tenant_id)
            dataset = session.get(Dataset, document.dataset_id)
            if tenant is not None:
                tenant.chunk_count = max(0, int(tenant.chunk_count or 0) + delta)
            if dataset is not None:
                dataset.chunk_count = max(0, int(dataset.chunk_count or 0) + delta)
            tenant_id = document.tenant_id
            session.commit()
        try:
            ledger.finish_attempt(operation.attempt_id, "completed")
        except IngestLedgerConflict:
            pass
        return tenant_id


    @staticmethod
    def _is_durable_delete(operation: IndexOperation) -> bool:
        return bool(operation.delete_operation_id) and operation.operation in {
            "delete_document",
            "finalize_document_delete",
        }

    def run_once(
        self,
        *,
        limit: int = 1,
        now: datetime | None = None,
    ) -> WorkerRunResult:
        # A worker must never pre-claim work it cannot heartbeat yet. Keep the
        # parameter for API compatibility, but active execution is one-at-a-time.
        claim_limit = 1 if limit > 0 else 0
        operations = self.queue.claim_operations(
            self.worker_id,
            limit=claim_limit,
            lease_seconds=self.lease_seconds,
            now=now,
        )
        succeeded = 0
        retried = 0
        dead = 0
        superseded = 0
        for operation in operations:
            handler = self.handlers.get(operation.target_store)
            try:
                with self._lease_heartbeat(operation) as heartbeat:
                    try:
                        if operation.operation == "finalize_document_delete":
                            self.deletion_repository.finalize_document_delete(
                                operation.id,
                                worker_id=self.worker_id,
                                now=now,
                            )
                            heartbeat.mark_transition_committed()
                            succeeded += 1
                            continue
                        if handler is None:
                            raise RuntimeError(
                                f"no index operation handler for {operation.target_store}"
                            )
                        handler(operation)
                        if heartbeat.lease_lost.is_set():
                            continue
                        if self._is_durable_delete(operation):
                            self.deletion_repository.complete_projection_operation(
                                operation.id,
                                worker_id=self.worker_id,
                                now=now,
                            )
                            heartbeat.mark_transition_committed()
                            succeeded += 1
                            continue
                        if not getattr(
                            operation, "_projection_revision_advanced", False
                        ):
                            self._advance_projection_revision(operation)
                        if heartbeat.lease_lost.is_set():
                            continue
                        self.queue.complete_operation(
                            operation.id, worker_id=self.worker_id, now=now
                        )
                        heartbeat.mark_transition_committed()
                        finalized_tenant = self._advance_attempt_lifecycle(operation)
                        if finalized_tenant:
                            cache_epoch.bump(
                                finalized_tenant,
                                reason=f"切片投影完成 {operation.document_id}",
                            )
                        succeeded += 1
                    except StaleProjectionOperation:
                        if heartbeat.lease_lost.is_set():
                            continue
                        try:
                            self.queue.supersede_operation_and_close_attempt_if_idle(
                                operation.id, worker_id=self.worker_id, now=now
                            )
                        except IndexOperationConflict:
                            continue
                        heartbeat.mark_transition_committed()
                        superseded += 1
                    except IndexOperationConflict:
                        # The lease expired or another worker took ownership. The
                        # current worker must not mutate the operation again.
                        continue
                    except Exception as exc:  # noqa: BLE001 - durable retry boundary
                        if heartbeat.lease_lost.is_set():
                            continue
                        try:
                            if self._is_durable_delete(operation):
                                updated = self.deletion_repository.retry_delete_operation(
                                    operation.id,
                                    worker_id=self.worker_id,
                                    error_code=type(exc).__name__[:64],
                                    error_message=str(exc),
                                    now=now,
                                )
                            else:
                                updated = self.queue.retry_operation(
                                    operation.id,
                                    worker_id=self.worker_id,
                                    error_code=type(exc).__name__[:64],
                                    error_message=str(exc),
                                    now=now,
                                )
                        except (IndexOperationConflict, DocumentDeletionConflict):
                            continue
                        heartbeat.mark_transition_committed()
                        if updated.status == "dead":
                            dead += 1
                        else:
                            retried += 1
            except IndexOperationConflict:
                # The synchronous pre-handler ownership fence failed. No handler
                # or external projection side effect has run for this operation.
                continue
        return WorkerRunResult(
            claimed=len(operations),
            succeeded=succeeded,
            retried=retried,
            dead=dead,
            superseded=superseded,
        )
