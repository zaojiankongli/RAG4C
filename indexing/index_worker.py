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
from core.providers import UnknownProviderError
from core.projection_attempt_lifecycle import (
    InvalidProjectionAttemptTarget,
    ProjectionAttemptLifecyclePolicy,
    ProjectionAttemptReadinessContext,
    invoke_projection_attempt_readiness,
    resolve_projection_attempt_lifecycle_policy,
)
from core.projection_revision_strategies import (
    InvalidProjectionRevisionTarget,
    ProjectionRevisionContext,
    invoke_projection_revision_strategy,
    resolve_projection_revision_strategy,
)
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


class MissingProjectionAttemptLifecyclePolicy(RuntimeError):
    """Raised before a projection handler runs when its target has no lifecycle policy."""


class ProjectionAttemptNotReady(RuntimeError):
    """Raised before an operation completes so its readiness can be retried durably."""


class MissingProjectionRevisionStrategy(RuntimeError):
    """Raised before a projection handler runs when its store has no lifecycle policy."""


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
        self._attempt_lifecycle_policy_snapshots: dict[
            str, dict[str, ProjectionAttemptLifecyclePolicy]
        ] = {}
        self._attempt_lifecycle_policy_snapshots_lock = threading.RLock()

    def _pin_attempt_lifecycle_policies(
        self, operation: IndexOperation
    ) -> dict[str, ProjectionAttemptLifecyclePolicy]:
        with Session(self.queue.engine) as session:
            target_stores = set(
                session.scalars(
                    select(IndexOperation.target_store)
                    .where(IndexOperation.attempt_id == operation.attempt_id)
                    .distinct()
                )
            )
        target_stores.add(operation.target_store)
        with self._attempt_lifecycle_policy_snapshots_lock:
            pinned = self._attempt_lifecycle_policy_snapshots.get(operation.attempt_id)
            if pinned is None:
                pinned = {
                    target_store: resolve_projection_attempt_lifecycle_policy(target_store)
                    for target_store in target_stores
                }
                self._attempt_lifecycle_policy_snapshots[operation.attempt_id] = pinned
            else:
                unpinned_targets = target_stores.difference(pinned)
                additions = {
                    target_store: resolve_projection_attempt_lifecycle_policy(target_store)
                    for target_store in unpinned_targets
                }
                pinned.update(additions)
            return dict(pinned)

    def _forget_attempt_lifecycle_policies(self, attempt_id: str) -> None:
        with self._attempt_lifecycle_policy_snapshots_lock:
            self._attempt_lifecycle_policy_snapshots.pop(attempt_id, None)

    def _forget_attempt_lifecycle_policies_if_terminal(self, attempt_id: str) -> None:
        with Session(self.queue.engine) as session:
            attempt = session.get(DocumentIngestAttempt, attempt_id)
            if attempt is not None and attempt.state not in {
                "completed",
                "failed",
                "cancelled",
                "superseded",
            }:
                return
        self._forget_attempt_lifecycle_policies(attempt_id)

    def _ensure_projection_attempt_policy_ready(self, operation: IndexOperation) -> None:
        policies = getattr(
            operation, "_resolved_projection_attempt_lifecycle_policies", {}
        )
        policy = policies.get(operation.target_store)
        if policy is None:
            raise MissingProjectionAttemptLifecyclePolicy(
                f"no attempt lifecycle policy for projection target {operation.target_store!r}"
            )
        with Session(self.queue.engine) as session:
            document = session.get(Document, operation.document_id)
            if document is None:
                return
            context = ProjectionAttemptReadinessContext(
                attempt_id=operation.attempt_id,
                target_store=operation.target_store,
                target_revision=int(operation.target_revision or 0),
                document_id=document.id,
                content_revision=int(document.content_revision or 0),
                desired_index_revision=int(document.desired_index_revision or 0),
                indexed_revision=int(document.indexed_revision or 0),
                graph_revision=int(document.graph_revision or 0),
            )
        if not invoke_projection_attempt_readiness(policy, context):
            raise ProjectionAttemptNotReady(
                f"projection target {operation.target_store!r} is not ready"
            )

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
        strategy = getattr(operation, "_resolved_projection_revision_strategy", None)
        if strategy is None:
            strategy = resolve_projection_revision_strategy(operation.target_store)
        invoke_projection_revision_strategy(
            strategy, ProjectionRevisionContext(self.queue.engine, operation)
        )


    def _advance_attempt_lifecycle(self, operation: IndexOperation) -> str | None:
        ledger = IngestLedger(self.queue.engine)
        pinned_policies = getattr(
            operation, "_resolved_projection_attempt_lifecycle_policies", {}
        )
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
            active_target_stores = set(
                session.scalars(
                    select(IndexOperation.target_store)
                    .where(
                        IndexOperation.attempt_id == operation.attempt_id,
                        IndexOperation.status.in_(("pending", "retry", "claimed")),
                    )
                    .distinct()
                )
            )
            attempt_state = attempt.state
        try:
            operation_policy = pinned_policies.get(operation.target_store)
            if operation_policy is not None and operation_policy.role == "primary":
                if attempt_state == "running":
                    ledger.set_attempt_state(operation.attempt_id, "primary_ready")
                    attempt_state = "primary_ready"
            if (
                active_count
                and attempt_state == "primary_ready"
                and any(
                    (policy := pinned_policies.get(target_store)) is not None
                    and policy.blocks_finalization
                    for target_store in active_target_stores
                )
            ):
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
                if attempt.state in {"completed", "failed", "cancelled", "superseded"}:
                    self._forget_attempt_lifecycle_policies(attempt.id)
                return
            if document.desired_index_revision != document.indexed_revision:
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
        self._forget_attempt_lifecycle_policies(operation.attempt_id)
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
                        if not self._is_durable_delete(operation):
                            try:
                                revision_strategy = resolve_projection_revision_strategy(
                                    operation.target_store
                                )
                            except (UnknownProviderError, InvalidProjectionRevisionTarget) as exc:
                                raise MissingProjectionRevisionStrategy(
                                    f"no revision strategy for projection target {operation.target_store!r}"
                                ) from exc
                            setattr(
                                operation,
                                "_resolved_projection_revision_strategy",
                                revision_strategy,
                            )
                            try:
                                attempt_policies = self._pin_attempt_lifecycle_policies(
                                    operation
                                )
                            except (
                                UnknownProviderError,
                                InvalidProjectionAttemptTarget,
                            ) as exc:
                                raise MissingProjectionAttemptLifecyclePolicy(
                                    "no attempt lifecycle policy for projection target "
                                    f"{operation.target_store!r}"
                                ) from exc
                            setattr(
                                operation,
                                "_resolved_projection_attempt_lifecycle_policies",
                                attempt_policies,
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
                        self._ensure_projection_attempt_policy_ready(operation)
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
                        self._forget_attempt_lifecycle_policies_if_terminal(
                            operation.attempt_id
                        )
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
