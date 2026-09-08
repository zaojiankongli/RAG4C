"""Report catalog/Milvus projection drift and optionally enqueue repair."""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from core.chunk_catalog import ChunkCatalog
from core.index_operations import IndexOperationQueue


@dataclass(frozen=True)
class ReconcileReport:
    document_id: str
    missing_ids: tuple[str, ...]
    orphan_ids: tuple[str, ...]
    revision_mismatch_ids: tuple[str, ...]
    enqueued: int = 0


def reconcile_document(
    chunk_catalog: ChunkCatalog,
    milvus,
    queue: IndexOperationQueue,
    *,
    document_id: str,
    attempt_id: str,
    enqueue_repairs: bool = False,
) -> ReconcileReport:
    head = chunk_catalog.get_head
    del head  # make accidental single-row reads explicit; reconciliation is set-based below
    from sqlalchemy.orm import Session
    from models.orm import Document

    with Session(chunk_catalog.engine) as session:
        document = session.get(Document, document_id)
        if document is None:
            raise ValueError("document does not exist")
        revision = document.desired_index_revision
        tenant_id = document.tenant_id
        dataset_id = document.dataset_id
    candidates = chunk_catalog.list_projection_candidates(
        document_id, document_revision=revision
    )
    desired = {item.id: item for item in candidates}
    existing_chunks = milvus.query_chunks_by_doc(document_id, tenant_id=tenant_id)
    existing = {item.chunk_id: item for item in existing_chunks}
    missing = tuple(sorted(set(desired) - set(existing)))
    orphan = tuple(sorted(set(existing) - set(desired)))
    mismatched = tuple(
        sorted(
            chunk_id
            for chunk_id in set(desired) & set(existing)
            if existing[chunk_id].document_revision != desired[chunk_id].document_revision
            or existing[chunk_id].content_revision != desired[chunk_id].content_revision
        )
    )
    enqueued = 0
    if enqueue_repairs and (missing or orphan or mismatched):
        before = queue.count_operations()
        queue.enqueue_operation(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document_id,
            attempt_id=attempt_id,
            target_store="milvus_chunks",
            operation="reconcile",
            dedup_key=f"reconcile:{document_id}:{revision}:milvus",
            target_revision=revision,
            payload={
                "missing_ids": list(missing),
                "orphan_ids": list(orphan),
                "revision_mismatch_ids": list(mismatched),
            },
        )
        enqueued = max(0, queue.count_operations() - before)
    return ReconcileReport(document_id, missing, orphan, mismatched, enqueued)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--enqueue-repairs", action="store_true")
    args = parser.parse_args()

    from config.settings import get_settings
    from core import catalog
    from core.milvus_client import RagMilvusClient

    engine = catalog.get_engine()
    report = reconcile_document(
        ChunkCatalog(engine),
        RagMilvusClient(get_settings().milvus),
        IndexOperationQueue(engine),
        document_id=args.document_id,
        attempt_id=args.attempt_id,
        enqueue_repairs=args.enqueue_repairs,
    )
    print(report)
    if not args.enqueue_repairs and (
        report.missing_ids or report.orphan_ids or report.revision_mismatch_ids
    ):
        print("report-only; rerun with --enqueue-repairs after reviewing drift")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
