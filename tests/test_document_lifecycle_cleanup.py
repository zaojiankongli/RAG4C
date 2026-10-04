from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core import catalog
from core.chunk_catalog import ChunkCatalog
from core.index_operations import IndexOperationQueue
from core.ingest_ledger import IngestLedger
from models.orm import (
    ChunkHead,
    ChunkRevision,
    Dataset,
    Document,
    DocumentIngestAttempt,
    DocumentIngestSpan,
    IndexDeadLetter,
    IndexOperation,
    Tenant,
)


def test_remove_document_deletes_all_owned_ledger_and_chunk_rows(
    tmp_path: Path, monkeypatch
) -> None:
    # 目录库用 session 级模板库（tests/_catalog_template.py）：建一次已迁移到
    # head 的 SQLite、各用例拷文件，实测 4.0ms/次 vs 重跑迁移 13.9s（3432x）。
    # 本文件不需要 BASELINE（不验证「从 baseline 升到 head」的过程本身），
    # 所以拷模板与重跑迁移等价。
    from _catalog_template import head_db_url

    url = head_db_url(tmp_path / "catalog.db")
    catalog.reset_engine()
    monkeypatch.setattr(catalog, "_resolve_db_url", lambda: (url, None))
    monkeypatch.setattr(catalog, "_catalog_schema_mode", lambda: "verify")
    engine = catalog.get_engine()
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant"))
        session.add(Dataset(id="dataset-1", tenant_id="tenant-1", name="KB"))
        session.add(
            Document(
                id="doc-1",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document",
            )
        )
        session.commit()
    ledger = IngestLedger(engine)
    attempt = ledger.start_attempt(
        tenant_id="tenant-1", dataset_id="dataset-1", document_id="doc-1"
    )
    ledger.start_span(attempt.id, span_id="parse", name="parse", kind="stage")
    chunk_catalog = ChunkCatalog(engine)
    chunk_catalog.upsert_head(
        chunk_id="chunk-1",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        parent_chunk_id=None,
        chunk_index=0,
        chunk_role="flat",
        document_revision=1,
        source_content="old",
        content="old",
    )
    chunk_catalog.edit_chunk(
        "chunk-1", expected_revision=0, content="new", editor_id="user-1"
    )
    queue = IndexOperationQueue(engine)
    operation = queue.enqueue_operation(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        attempt_id=attempt.id,
        target_store="milvus_chunks",
        operation="upsert",
        dedup_key="cleanup-test",
        target_revision=1,
        max_retries=1,
    )
    queue.claim_operations("worker", limit=1, lease_seconds=30)
    queue.retry_operation(
        operation.id,
        worker_id="worker",
        error_code="FAIL",
        error_message="fail",
    )

    removed = catalog.remove_document("doc-1")

    assert removed is not None
    with Session(engine) as session:
        for model in (
            IndexDeadLetter,
            IndexOperation,
            DocumentIngestSpan,
            DocumentIngestAttempt,
            ChunkRevision,
            ChunkHead,
        ):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        assert session.get(Document, "doc-1") is None
    catalog.reset_engine()
