"""Compare catalog desired revisions with external projection revisions."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from core.index_operations import IndexOperationQueue
from models.orm import Document


@dataclass(frozen=True)
class ReconcileResult:
    document_id: str
    enqueued: int = 0
    reason: str = ""


class IndexReconciler:
    def __init__(self, engine: Engine, queue: IndexOperationQueue):
        self.engine = engine
        self.queue = queue

    def scan_document(self, document_id: str) -> ReconcileResult:
        with Session(self.engine, expire_on_commit=False) as session:
            document = session.get(Document, document_id)
            if document is None:
                return ReconcileResult(document_id, reason="document_missing")
            if document.desired_index_revision <= document.indexed_revision:
                return ReconcileResult(document_id, reason="projection_current")
            if not document.current_attempt_id:
                return ReconcileResult(document_id, reason="attempt_missing")
            payload = {
                "document_id": document.id,
                "target_revision": document.desired_index_revision,
            }
            before = self.queue.count_operations()
            self.queue.enqueue_operation(
                tenant_id=document.tenant_id,
                dataset_id=document.dataset_id,
                document_id=document.id,
                attempt_id=document.current_attempt_id,
                target_store="milvus_chunks",
                operation="reconcile",
                dedup_key=(
                    f"reconcile:{document.id}:{document.desired_index_revision}:milvus"
                ),
                target_revision=document.desired_index_revision,
                payload=payload,
            )
            after = self.queue.count_operations()
            return ReconcileResult(
                document_id,
                enqueued=max(0, after - before),
                reason="projection_lag",
            )
