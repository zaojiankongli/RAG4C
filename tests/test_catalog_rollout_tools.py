from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.catalog_schema import HEAD_REVISION
from core.chunk_catalog import ChunkCatalog
from core.index_operations import IndexOperationQueue
from core.ingest_ledger import IngestLedger
from models.orm import Dataset, Document, Tenant
from models.schemas import Chunk


class FakeMilvus:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = list(chunks)

    def query_chunks_by_doc(self, doc_id: str, tenant_id: str = ""):
        return [chunk for chunk in self.chunks if chunk.doc_id == doc_id]


def chunk(chunk_id: str, *, revision: int = 0) -> Chunk:
    now = datetime(2026, 8, 24, tzinfo=timezone.utc)
    return Chunk(
        chunk_id=chunk_id,
        doc_id="doc-1",
        text=f"content {chunk_id}",
        text_hash=f"hash-{chunk_id}",
        created_at=now,
        updated_at=now,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_revision=revision,
        content_revision=0,
        metadata={"chunk_index": 0 if chunk_id == "chunk-1" else 1},
    )


def create_state(tmp_path: Path):
    from core.catalog_schema import upgrade_catalog

    url = f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant"))
        session.add(Dataset(id="dataset-1", tenant_id="tenant-1", name="KB"))
        session.add(
            Document(
                id="doc-1",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document",
                content_revision=1,
                desired_index_revision=1,
                indexed_revision=0,
            )
        )
        session.commit()
    attempt = IngestLedger(engine).start_attempt(
        tenant_id="tenant-1", dataset_id="dataset-1", document_id="doc-1"
    )
    return engine, attempt


def test_backfill_is_dry_run_by_default_and_idempotent_when_applied(tmp_path: Path) -> None:
    engine, _ = create_state(tmp_path)
    from scripts.backfill_chunk_catalog import backfill_document

    catalog = ChunkCatalog(engine)
    milvus = FakeMilvus([chunk("chunk-1"), chunk("chunk-2")])

    preview = backfill_document(
        catalog,
        milvus,
        document_id="doc-1",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_revision=1,
    )
    applied = backfill_document(
        catalog,
        milvus,
        document_id="doc-1",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_revision=1,
        apply=True,
    )
    repeated = backfill_document(
        catalog,
        milvus,
        document_id="doc-1",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_revision=1,
        apply=True,
    )

    assert preview.would_write == 2
    assert preview.applied == 0
    assert applied.applied == 2
    assert repeated.applied == 0
    assert [item.id for item in catalog.list_projection_candidates("doc-1", document_revision=1)] == [
        "chunk-1",
        "chunk-2",
    ]
    engine.dispose()


def test_reconcile_report_only_requires_explicit_enqueue_flag(tmp_path: Path) -> None:
    engine, attempt = create_state(tmp_path)
    from scripts.reconcile_catalog_indexes import reconcile_document

    catalog = ChunkCatalog(engine)
    catalog.upsert_head(
        chunk_id="chunk-1",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        parent_chunk_id=None,
        chunk_index=0,
        chunk_role="flat",
        document_revision=1,
        source_content="content",
        content="content",
    )
    milvus = FakeMilvus([chunk("orphan")])
    queue = IndexOperationQueue(engine)

    preview = reconcile_document(
        catalog,
        milvus,
        queue,
        document_id="doc-1",
        attempt_id=attempt.id,
        enqueue_repairs=False,
    )
    applied = reconcile_document(
        catalog,
        milvus,
        queue,
        document_id="doc-1",
        attempt_id=attempt.id,
        enqueue_repairs=True,
    )

    assert preview.missing_ids == ("chunk-1",)
    assert preview.orphan_ids == ("orphan",)
    assert preview.enqueued == 0
    assert queue.count_operations() == 1
    assert applied.enqueued == 1
    engine.dispose()


def test_backfill_supports_resume_cursor_limit_and_hash_mismatch_report(
    tmp_path: Path,
) -> None:
    engine, _ = create_state(tmp_path)
    from scripts.backfill_chunk_catalog import backfill_document

    catalog = ChunkCatalog(engine)
    milvus = FakeMilvus([chunk("chunk-1"), chunk("chunk-2")])

    report = backfill_document(
        catalog,
        milvus,
        document_id="doc-1",
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_revision=1,
        after_chunk_id="chunk-1",
        limit=1,
    )

    assert report.would_write == 1
    assert report.next_cursor == "chunk-2"
    assert report.hash_mismatch_ids == ("chunk-2",)
    engine.dispose()


def test_catalog_rollback_snapshot_is_explicit_and_excludes_content(
    tmp_path: Path,
) -> None:
    engine, _ = create_state(tmp_path)
    from scripts.export_catalog_rollback_snapshot import export_catalog_snapshot

    output = tmp_path / "snapshot.json"
    result = export_catalog_snapshot(engine, output)
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert result == output
    assert payload["tables"]["documents"]["row_count"] == 1
    assert payload["schema"]["revision"] == HEAD_REVISION
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "content " not in serialized
    assert "rows" not in payload["tables"]["documents"]
    assert "values" not in payload["tables"]["documents"]
    engine.dispose()
