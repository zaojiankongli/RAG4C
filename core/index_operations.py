"""Durable desired-state operations for external index projections."""
from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator

from sqlalchemy import Engine, and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.catalog import sanitize_error_message
from models.orm import (
    Document,
    DocumentIngestAttempt,
    IndexDeadLetter,
    IndexOperation,
)

_ACTIVE_STATUSES = frozenset({"pending", "retry", "claimed"})

_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCK_USERS: dict[str, int] = {}


@contextmanager
def process_keyed_lock(key: str) -> Iterator[None]:
    """Serialize one key across threads and independently loaded modules."""
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())
        _PROCESS_LOCK_USERS[key] = _PROCESS_LOCK_USERS.get(key, 0) + 1
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _PROCESS_LOCKS_GUARD:
            remaining = _PROCESS_LOCK_USERS[key] - 1
            if remaining:
                _PROCESS_LOCK_USERS[key] = remaining
            else:
                _PROCESS_LOCK_USERS.pop(key, None)
                if _PROCESS_LOCKS.get(key) is lock:
                    _PROCESS_LOCKS.pop(key, None)


class IndexOperationConflict(RuntimeError):
    """Raised when an operation cannot safely transition."""


class IndexOperationQueue:
    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"

    def _enqueue_in_session(
        self,
        session: Session,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        attempt_id: str,
        target_store: str,
        operation: str,
        dedup_key: str,
        target_revision: int,
        payload: dict[str, Any] | None,
        max_retries: int,
        initial_status: str,
    ) -> IndexOperation:
        existing = session.scalar(
            select(IndexOperation).where(IndexOperation.dedup_key == dedup_key)
        )
        if existing is not None:
            return existing
        item = IndexOperation(
            id=self._new_id("index-op"),
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document_id,
            attempt_id=attempt_id,
            target_store=target_store,
            operation=operation,
            dedup_key=dedup_key[:255],
            target_revision=max(0, int(target_revision)),
            payload=payload or {},
            status=initial_status,
            max_retries=max(1, int(max_retries)),
        )
        session.add(item)
        session.flush()
        return item

    def enqueue_operation(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        attempt_id: str,
        target_store: str,
        operation: str,
        dedup_key: str,
        target_revision: int,
        payload: dict[str, Any] | None = None,
        max_retries: int = 3,
        initial_status: str = "pending",
        session: Session | None = None,
    ) -> IndexOperation:
        kwargs = dict(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document_id,
            attempt_id=attempt_id,
            target_store=target_store,
            operation=operation,
            dedup_key=dedup_key,
            target_revision=target_revision,
            payload=payload,
            max_retries=max_retries,
            initial_status=initial_status,
        )
        if session is not None:
            return self._enqueue_in_session(session, **kwargs)
        with Session(self.engine, expire_on_commit=False) as owned:
            try:
                item = self._enqueue_in_session(owned, **kwargs)
                owned.commit()
                return item
            except IntegrityError:
                owned.rollback()
                existing = owned.scalar(
                    select(IndexOperation).where(IndexOperation.dedup_key == dedup_key)
                )
                if existing is None:
                    raise
                return existing

    def start_chunk_mutation_attempt(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        input_revision: int,
        initial_status: str,
        session: Session,
    ) -> DocumentIngestAttempt:
        document = session.scalar(
            select(Document).where(Document.id == document_id).with_for_update()
        )
        if document is None:
            raise IndexOperationConflict("document does not exist")
        if document.tenant_id != tenant_id or document.dataset_id != dataset_id:
            raise IndexOperationConflict("document scope mismatch")
        attempt_no = int(
            session.scalar(
                select(func.max(DocumentIngestAttempt.attempt_no)).where(
                    DocumentIngestAttempt.document_id == document_id
                )
            )
            or 0
        ) + 1
        completed = initial_status == "shadow"
        attempt = DocumentIngestAttempt(
            id=self._new_id("attempt"),
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document_id,
            attempt_no=attempt_no,
            input_revision=max(0, int(input_revision)),
            state="completed" if completed else "running",
            worker_id="manual-chunk-mutation",
            finished_at=datetime.utcnow() if completed else None,
        )
        session.add(attempt)
        document.current_attempt_id = attempt.id
        session.flush()
        return attempt

    def enqueue_chunk_projection_operations(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        attempt_id: str,
        target_revision: int,
        chunk_id: str,
        expected_content_revision: int,
        mutation: str,
        include_graph: bool,
        initial_status: str = "pending",
        session: Session | None = None,
    ) -> tuple[IndexOperation, ...]:
        """Record revision-fenced desired projection work for one chunk mutation."""
        if mutation not in {"edit", "delete"}:
            raise ValueError("chunk mutation must be edit or delete")
        operation = "upsert" if mutation == "edit" else "delete"
        stores = ["milvus_chunks"]
        if include_graph:
            stores.append("graph_projection")
        payload = {
            "chunk_id": chunk_id,
            "chunk_ids": [chunk_id],
            "expected_content_revision": max(0, int(expected_content_revision)),
            "mutation": mutation,
        }
        return tuple(
            self.enqueue_operation(
                tenant_id=tenant_id,
                dataset_id=dataset_id,
                document_id=document_id,
                attempt_id=attempt_id,
                target_store=target_store,
                operation=operation,
                dedup_key=(
                    f"chunk-mutation:{document_id}:{chunk_id}:"
                    f"{expected_content_revision}:{target_store}:{operation}"
                ),
                target_revision=target_revision,
                payload=payload,
                initial_status=initial_status,
                session=session,
            )
            for target_store in stores
        )

    def list_operations(self) -> list[IndexOperation]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(
                session.scalars(
                    select(IndexOperation).order_by(IndexOperation.created_at, IndexOperation.id)
                )
            )

    def count_operations(self) -> int:
        with Session(self.engine) as session:
            return int(session.scalar(select(func.count(IndexOperation.id))) or 0)

    def get_operation(self, operation_id: str) -> IndexOperation:
        with Session(self.engine, expire_on_commit=False) as session:
            item = session.get(IndexOperation, operation_id)
            if item is None:
                raise IndexOperationConflict("operation does not exist")
            return item

    def claim_operations(
        self,
        worker_id: str,
        *,
        limit: int,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> list[IndexOperation]:
        now = now or datetime.utcnow()
        lease_until = now + timedelta(seconds=max(1, lease_seconds))
        with Session(self.engine, expire_on_commit=False) as session:
            eligible = or_(
                and_(
                    IndexOperation.status.in_(("pending", "retry")),
                    or_(
                        IndexOperation.next_retry_at.is_(None),
                        IndexOperation.next_retry_at <= now,
                    ),
                ),
                and_(
                    IndexOperation.status == "claimed",
                    IndexOperation.lease_until.is_not(None),
                    IndexOperation.lease_until <= now,
                ),
            )
            statement = (
                select(IndexOperation)
                .where(eligible)
                .order_by(IndexOperation.created_at, IndexOperation.id)
                .limit(max(0, limit))
            )
            if session.get_bind().dialect.name in {"mysql", "mariadb", "postgresql"}:
                statement = statement.with_for_update(skip_locked=True)
            items = list(session.scalars(statement))
            for item in items:
                item.status = "claimed"
                item.claimed_by = worker_id
                item.lease_until = lease_until
            session.commit()
            return items

    def renew_lease(
        self,
        operation_id: str,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> IndexOperation:
        now = now or datetime.utcnow()
        lease_until = now + timedelta(seconds=max(1, lease_seconds))
        with Session(self.engine, expire_on_commit=False) as session:
            result = session.execute(
                update(IndexOperation)
                .where(
                    IndexOperation.id == operation_id,
                    IndexOperation.status == "claimed",
                    IndexOperation.claimed_by == worker_id,
                )
                .values(lease_until=lease_until)
            )
            if result.rowcount != 1:
                session.rollback()
                raise IndexOperationConflict("operation is not claimed by this worker")
            session.commit()
            item = session.get(IndexOperation, operation_id)
            if item is None:
                raise IndexOperationConflict("operation does not exist")
            return item

    def complete_operation(
        self, operation_id: str, *, worker_id: str, now: datetime | None = None
    ) -> IndexOperation:
        now = now or datetime.utcnow()
        with Session(self.engine, expire_on_commit=False) as session:
            item = session.get(IndexOperation, operation_id)
            if item is None or item.status != "claimed" or item.claimed_by != worker_id:
                raise IndexOperationConflict("operation is not claimed by this worker")
            item.status = "succeeded"
            item.finished_at = now
            item.lease_until = None
            session.commit()
            return item

    def supersede_operation(
        self,
        operation_id: str,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> IndexOperation:
        now = now or datetime.utcnow()
        with Session(self.engine, expire_on_commit=False) as session:
            item = session.get(IndexOperation, operation_id)
            if item is None or item.status != "claimed" or item.claimed_by != worker_id:
                raise IndexOperationConflict("operation is not claimed by this worker")
            item.status = "superseded"
            item.claimed_by = ""
            item.lease_until = None
            item.finished_at = now
            session.commit()
            return item

    def supersede_operation_and_close_attempt_if_idle(
        self,
        operation_id: str,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> IndexOperation:
        """Atomically supersede an operation and close its idle attempt."""
        now = now or datetime.utcnow()
        with Session(self.engine, expire_on_commit=False) as session:
            with session.begin():
                item = session.get(IndexOperation, operation_id, with_for_update=True)
                if item is None or item.status != "claimed" or item.claimed_by != worker_id:
                    raise IndexOperationConflict("operation is not claimed by this worker")
                attempt = session.get(
                    DocumentIngestAttempt, item.attempt_id, with_for_update=True
                )
                item.status = "superseded"
                item.claimed_by = ""
                item.lease_until = None
                item.finished_at = now
                session.flush()
                active_count = int(
                    session.scalar(
                        select(func.count(IndexOperation.id)).where(
                            IndexOperation.attempt_id == item.attempt_id,
                            IndexOperation.status.in_(tuple(_ACTIVE_STATUSES)),
                        )
                    )
                    or 0
                )
                if (
                    active_count == 0
                    and attempt is not None
                    and attempt.state
                    not in {"completed", "failed", "cancelled", "superseded"}
                ):
                    attempt.state = "superseded"
                    attempt.finished_at = now
            return item

    def retry_operation(
        self,
        operation_id: str,
        *,
        worker_id: str,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
        base_delay_seconds: int = 5,
    ) -> IndexOperation:
        now = now or datetime.utcnow()
        with Session(self.engine, expire_on_commit=False) as session:
            item = session.get(IndexOperation, operation_id)
            if item is None or item.status != "claimed" or item.claimed_by != worker_id:
                raise IndexOperationConflict("operation is not claimed by this worker")
            item.retry_count += 1
            item.last_error_code = error_code[:64]
            item.last_error = sanitize_error_message(error_message)[:2000]
            item.claimed_by = ""
            item.lease_until = None
            if item.retry_count >= item.max_retries:
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
            else:
                item.status = "retry"
                delay = max(1, base_delay_seconds) * (2 ** (item.retry_count - 1))
                item.next_retry_at = now + timedelta(seconds=delay)
            session.commit()
            return item

    def list_dead_letters(self) -> list[IndexDeadLetter]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(
                session.scalars(
                    select(IndexDeadLetter).order_by(IndexDeadLetter.failed_at)
                )
            )

    def requeue_dead_letter(
        self, dead_letter_id: str, *, operator_note: str = ""
    ) -> IndexOperation:
        with Session(self.engine, expire_on_commit=False) as session:
            letter = session.get(IndexDeadLetter, dead_letter_id)
            if letter is None:
                raise IndexOperationConflict("dead letter does not exist")
            if letter.requeued_to_operation_id:
                existing = session.get(IndexOperation, letter.requeued_to_operation_id)
                if existing is None:
                    raise IndexOperationConflict("requeued operation is missing")
                return existing
            item = IndexOperation(
                id=self._new_id("index-op"),
                tenant_id=letter.tenant_id,
                dataset_id=letter.dataset_id,
                document_id=letter.document_id,
                attempt_id=letter.attempt_id,
                target_store=letter.target_store,
                operation=letter.operation,
                dedup_key=f"{letter.dedup_key}:requeue:{uuid.uuid4().hex[:8]}",
                target_revision=letter.target_revision,
                payload=letter.payload or {},
                status="pending",
                max_retries=max(1, letter.retry_count),
            )
            session.add(item)
            session.flush()
            letter.requeued_to_operation_id = item.id
            letter.operator_note = operator_note[:512]
            session.commit()
            return item

    def supersede_stale_operations(
        self, document_id: str, *, current_revision: int
    ) -> int:
        with Session(self.engine) as session:
            result = session.execute(
                update(IndexOperation)
                .where(
                    IndexOperation.document_id == document_id,
                    IndexOperation.target_revision < current_revision,
                    IndexOperation.status.in_(tuple(_ACTIVE_STATUSES)),
                )
                .values(
                    status="superseded",
                    claimed_by="",
                    lease_until=None,
                    finished_at=datetime.utcnow(),
                )
            )
            session.commit()
            return int(result.rowcount or 0)
