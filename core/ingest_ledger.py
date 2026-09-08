"""Durable document ingest attempts and execution spans."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.catalog import sanitize_error_message
from models.orm import Document, DocumentIngestAttempt, DocumentIngestSpan

_TERMINAL_ATTEMPT_STATES = frozenset({"completed", "failed", "cancelled", "superseded"})
_ALLOWED_ATTEMPT_TRANSITIONS = {
    "running": frozenset({"primary_ready", "finalizing", *_TERMINAL_ATTEMPT_STATES}),
    "primary_ready": frozenset({"finalizing", *_TERMINAL_ATTEMPT_STATES}),
    "finalizing": _TERMINAL_ATTEMPT_STATES,
}
_TERMINAL_SPAN_STATES = frozenset({"done", "failed", "skipped", "cancelled"})


class IngestLedgerConflict(RuntimeError):
    """Raised when an ingest ledger transition violates its durable contract."""


class IngestLedger:
    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:16]}"

    def start_attempt_in_session(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        input_content_hash: str = "",
        input_revision: int = 0,
        worker_id: str = "",
        attempt_kind: str = "ingest",
        document_generation: int | None = None,
        session: Session,
    ) -> DocumentIngestAttempt:
        """Create an attempt and move the document head inside the caller transaction."""
        document = session.scalar(
            select(Document).where(Document.id == document_id).with_for_update()
        )
        if document is None:
            raise IngestLedgerConflict("document does not exist")
        if document.tenant_id != tenant_id or document.dataset_id != dataset_id:
            raise IngestLedgerConflict("document scope mismatch")
        expected_generation = (
            int(document.mutation_generation or 0)
            if document_generation is None
            else document_generation
        )
        if type(expected_generation) is not int or expected_generation < 0:
            raise ValueError("document_generation must be a non-negative integer")
        if (
            document.lifecycle_state != "active"
            or not bool(document.retrieval_enabled)
            or bool(document.active_delete_operation_id)
            or int(document.mutation_generation or 0) != expected_generation
        ):
            raise IngestLedgerConflict("document writer permit is stale")
        attempt_no = int(
            session.scalar(
                select(func.max(DocumentIngestAttempt.attempt_no)).where(
                    DocumentIngestAttempt.document_id == document_id
                )
            )
            or 0
        ) + 1
        attempt = DocumentIngestAttempt(
            id=self._new_id("attempt"),
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document_id,
            attempt_no=attempt_no,
            input_content_hash=input_content_hash,
            input_revision=max(0, int(input_revision)),
            state="running",
            worker_id=worker_id,
            attempt_kind=str(attempt_kind or "ingest"),
            document_generation=expected_generation,
        )
        session.add(attempt)
        document.current_attempt_id = attempt.id
        session.flush()
        return attempt

    def start_attempt(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        document_id: str,
        input_content_hash: str = "",
        input_revision: int = 0,
        worker_id: str = "",
        attempt_kind: str = "ingest",
        document_generation: int | None = None,
    ) -> DocumentIngestAttempt:
        with Session(self.engine, expire_on_commit=False) as session:
            try:
                attempt = self.start_attempt_in_session(
                    tenant_id=tenant_id,
                    dataset_id=dataset_id,
                    document_id=document_id,
                    input_content_hash=input_content_hash,
                    input_revision=input_revision,
                    worker_id=worker_id,
                    attempt_kind=attempt_kind,
                    document_generation=document_generation,
                    session=session,
                )
                session.commit()
                return attempt
            except IntegrityError as exc:
                session.rollback()
                raise IngestLedgerConflict("attempt number conflict") from exc

    def list_attempts(self, document_id: str) -> list[DocumentIngestAttempt]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(
                session.scalars(
                    select(DocumentIngestAttempt)
                    .where(DocumentIngestAttempt.document_id == document_id)
                    .order_by(DocumentIngestAttempt.attempt_no)
                )
            )

    def list_spans(self, attempt_id: str) -> list[DocumentIngestSpan]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(
                session.scalars(
                    select(DocumentIngestSpan)
                    .where(DocumentIngestSpan.attempt_id == attempt_id)
                    .order_by(DocumentIngestSpan.created_at, DocumentIngestSpan.id)
                )
            )

    def set_attempt_state(
        self,
        attempt_id: str,
        state: str,
        *,
        error_code: str = "",
        error_message: str = "",
    ) -> DocumentIngestAttempt:
        with Session(self.engine, expire_on_commit=False) as session:
            attempt = session.get(DocumentIngestAttempt, attempt_id)
            if attempt is None:
                raise IngestLedgerConflict("attempt does not exist")
            if attempt.state in _TERMINAL_ATTEMPT_STATES:
                raise IngestLedgerConflict("terminal attempt cannot transition")
            allowed = _ALLOWED_ATTEMPT_TRANSITIONS.get(attempt.state, frozenset())
            if state != attempt.state and state not in allowed:
                raise IngestLedgerConflict(
                    f"invalid attempt transition: {attempt.state} -> {state}"
                )
            attempt.state = state
            if error_code:
                attempt.error_code = error_code[:64]
            if error_message:
                attempt.error_message = sanitize_error_message(error_message)[:2000]
            if state == "primary_ready" and attempt.primary_index_ready_at is None:
                attempt.primary_index_ready_at = datetime.utcnow()
            if state in _TERMINAL_ATTEMPT_STATES:
                attempt.finished_at = datetime.utcnow()
            session.commit()
            return attempt

    def finish_attempt(
        self,
        attempt_id: str,
        state: str,
        *,
        error_code: str = "",
        error_message: str = "",
    ) -> DocumentIngestAttempt:
        if state not in _TERMINAL_ATTEMPT_STATES:
            raise IngestLedgerConflict("finish_attempt requires a terminal state")
        return self.set_attempt_state(
            attempt_id,
            state,
            error_code=error_code,
            error_message=error_message,
        )

    def start_span(
        self,
        attempt_id: str,
        *,
        span_id: str,
        name: str,
        kind: str,
        parent_span_id: str | None = None,
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DocumentIngestSpan:
        with Session(self.engine, expire_on_commit=False) as session:
            if session.get(DocumentIngestAttempt, attempt_id) is None:
                raise IngestLedgerConflict("attempt does not exist")
            existing = session.scalar(
                select(DocumentIngestSpan).where(
                    DocumentIngestSpan.attempt_id == attempt_id,
                    DocumentIngestSpan.span_id == span_id,
                )
            )
            if existing is not None:
                raise IngestLedgerConflict("span already exists")
            span = DocumentIngestSpan(
                id=self._new_id("span"),
                attempt_id=attempt_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
                name=name,
                kind=kind,
                status="running",
                input=input or {},
                span_metadata=metadata or {},
            )
            session.add(span)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise IngestLedgerConflict("span already exists") from exc
            return span

    def finish_span(
        self,
        attempt_id: str,
        span_id: str,
        status: str,
        *,
        output: dict[str, Any] | None = None,
        error_code: str = "",
        error_message: str = "",
    ) -> DocumentIngestSpan:
        if status not in _TERMINAL_SPAN_STATES:
            raise IngestLedgerConflict("finish_span requires a terminal status")
        with Session(self.engine, expire_on_commit=False) as session:
            span = session.scalar(
                select(DocumentIngestSpan).where(
                    DocumentIngestSpan.attempt_id == attempt_id,
                    DocumentIngestSpan.span_id == span_id,
                )
            )
            if span is None:
                raise IngestLedgerConflict("span does not exist")
            if span.status in _TERMINAL_SPAN_STATES:
                raise IngestLedgerConflict("terminal span cannot transition")
            finished_at = datetime.utcnow()
            span.status = status
            span.output = output or {}
            span.error_code = error_code[:64]
            span.error_message = sanitize_error_message(error_message)[:2000]
            span.finished_at = finished_at
            span.duration_ms = max(
                0, int((finished_at - span.started_at).total_seconds() * 1000)
            )
            session.commit()
            return span
