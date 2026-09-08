"""Backfill authoritative chunk heads from an existing Milvus projection.

Dry-run is the default. Use --apply only after reviewing the report.
"""
from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass

from core.chunk_catalog import ChunkCatalog, ChunkRevisionConflict


@dataclass(frozen=True)
class BackfillReport:
    document_id: str
    scanned: int
    would_write: int
    applied: int
    skipped_existing: int
    next_cursor: str = ""
    hash_mismatch_ids: tuple[str, ...] = ()


def backfill_document(
    chunk_catalog: ChunkCatalog,
    milvus,
    *,
    document_id: str,
    tenant_id: str,
    dataset_id: str,
    document_revision: int,
    apply: bool = False,
    after_chunk_id: str = "",
    limit: int = 0,
) -> BackfillReport:
    chunks = sorted(
        milvus.query_chunks_by_doc(document_id, tenant_id=tenant_id),
        key=lambda item: item.chunk_id,
    )
    if after_chunk_id:
        chunks = [item for item in chunks if item.chunk_id > after_chunk_id]
    if limit > 0:
        chunks = chunks[:limit]
    would_write = 0
    applied = 0
    skipped = 0
    hash_mismatches: list[str] = []
    for fallback_index, chunk in enumerate(chunks):
        actual_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
        if chunk.text_hash != actual_hash:
            hash_mismatches.append(chunk.chunk_id)
        try:
            chunk_catalog.get_head(chunk.chunk_id)
        except ChunkRevisionConflict:
            pass
        else:
            skipped += 1
            continue
        would_write += 1
        if not apply:
            continue
        metadata = dict(chunk.metadata or {})
        is_parent = bool(metadata.get("is_parent"))
        role = "parent" if is_parent else "child" if chunk.parent_chunk_id else "flat"
        chunk_catalog.upsert_head(
            chunk_id=chunk.chunk_id,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            document_id=document_id,
            parent_chunk_id=chunk.parent_chunk_id,
            chunk_index=int(metadata.get("chunk_index", fallback_index)),
            chunk_role=role,
            document_revision=max(
                0, int(chunk.document_revision or document_revision)
            ),
            source_content=chunk.text,
            content=chunk.text,
            content_hash=chunk.text_hash,
            enabled=True,
            metadata=metadata,
        )
        applied += 1
    return BackfillReport(
        document_id=document_id,
        scanned=len(chunks),
        would_write=would_write,
        applied=applied,
        skipped_existing=skipped,
        next_cursor=chunks[-1].chunk_id if chunks else after_chunk_id,
        hash_mismatch_ids=tuple(hash_mismatches),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--document-revision", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--after-chunk-id", default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    from config.settings import get_settings
    from core import catalog
    from core.milvus_client import RagMilvusClient

    settings = get_settings()
    report = backfill_document(
        ChunkCatalog(catalog.get_engine()),
        RagMilvusClient(settings.milvus),
        document_id=args.document_id,
        tenant_id=args.tenant_id,
        dataset_id=args.dataset_id,
        document_revision=args.document_revision,
        apply=args.apply,
        after_chunk_id=args.after_chunk_id,
        limit=args.limit,
    )
    print(report)
    if not args.apply and report.would_write:
        print("dry-run only; rerun with --apply after reviewing scope")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
