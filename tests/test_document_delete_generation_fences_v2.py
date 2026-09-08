from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from core import catalog
from core.chunk_catalog import ChunkCatalog
from core.document_deletion import DocumentDeletionRepository
from core.index_operations import IndexOperationQueue
from core.ingest_ledger import IngestLedger
from core.knowledge_governance import AuditContext
from core.source_sync_ledger import SourceSyncLedger
from indexing import state_machine
from indexing.reindex import reindex_document
from models.orm import (
    ChunkHead,
    DataSourceRecord,
    Dataset,
    Document,
    DocumentIngestAttempt,
    IndexOperation,
)
from models.schemas import Chunk
from server.chunk_operations import ChunkAuthorityIncomplete, update_document_chunk_artifacts


def _configure_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core.catalog_schema import upgrade_catalog

    url = f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}"
    upgrade_catalog(url)
    catalog.reset_engine()
    monkeypatch.setattr(catalog, "_resolve_db_url", lambda: (url, None))
    monkeypatch.setattr(catalog, "_catalog_schema_mode", lambda: "verify")
    catalog.ensure_tenant("tenant-1", "Tenant")
    catalog.ensure_dataset("tenant-1", "dataset-1", "KB")
    engine = catalog.get_engine()
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-1",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Document",
                file_path=str(tmp_path / "document.md"),
            )
        )
        session.commit()
    return engine, {"id": "doc-1"}


def _set_generations(engine, *, document_generation: int, dataset_generation: int) -> None:
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        dataset = session.get(Dataset, "dataset-1")
        assert document is not None and dataset is not None
        document.mutation_generation = document_generation
        dataset.mutation_generation = dataset_generation
        session.commit()


def _request_delete(engine, expected_generation: int) -> None:
    DocumentDeletionRepository(engine).request_delete(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        expected_generation=expected_generation,
        idempotency_key=f"delete-doc-1-{expected_generation}",
        actor=AuditContext.system("system:test", f"req-delete-{expected_generation}"),
        reason="generation fence test",
        origin="operator",
    )


class FakeChunk:
    def __init__(self, chunk_id: str = "chunk-1") -> None:
        self.chunk_id = chunk_id
        self.doc_id = "doc-1"
        self.text = "body"
        self.text_hash = "hash-body"
        self.parent_chunk_id = None
        self.metadata = {"chunk_index": 0}
        self.tenant_id = "tenant-1"
        self.dataset_id = "dataset-1"
        self.document_revision = 0
        self.content_revision = 0


class IngestPipeline:
    graph_enabled = False
    graph_builder = None

    def __init__(self, *, before_return=None) -> None:
        self.before_return = before_return
        self.parse_calls = 0
        self.write_calls = 0

    def parse_and_chunk(self, *_args, progress=None, **_kwargs):
        self.parse_calls += 1
        if progress is not None:
            progress("parsing", 1.0, "parsed")
            progress("splitting", 1.0, "split")
        if self.before_return is not None:
            self.before_return()
        return [FakeChunk()]

    def embed_and_insert(self, chunks, *_args, progress=None, **_kwargs):
        self.write_calls += 1
        if progress is not None:
            progress("indexing", 1.0, "indexed")
        return SimpleNamespace(chunk_count=len(chunks), graph=None)

    def ingest_meta(self, _result):
        return {}


class ReindexPipeline:
    def __init__(self, *, before_return=None) -> None:
        self.before_return = before_return
        self.parse_calls = 0
        self.write_calls = 0
        self.milvus = SimpleNamespace(
            query_chunks_by_doc=lambda *_args, **_kwargs: [],
            delete_by_ids=lambda _ids: 0,
        )

    def parse_and_chunk(self, *_args, **_kwargs):
        self.parse_calls += 1
        if self.before_return is not None:
            self.before_return()
        return [FakeChunk()]

    def embed_and_insert(self, *_args, **_kwargs):
        self.write_calls += 1
        return SimpleNamespace(chunk_count=1)


def test_active_ingest_carries_document_and_dataset_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, document = _configure_catalog(tmp_path, monkeypatch)
    _set_generations(engine, document_generation=4, dataset_generation=7)
    file_path = tmp_path / "document.md"
    file_path.write_text("hello", encoding="utf-8")

    result = state_machine.DocumentIngestJob(
        IngestPipeline(),
        document["id"],
        dataset_id="dataset-1",
        ledger=IngestLedger(engine),
        operation_queue=IndexOperationQueue(engine),
        chunk_catalog=ChunkCatalog(engine),
        ledger_mode="active",
    ).run(str(file_path))

    assert result["status"] == "indexing"
    with Session(engine) as session:
        attempt = session.scalar(select(DocumentIngestAttempt))
        operation = session.scalar(
            select(IndexOperation).where(IndexOperation.operation == "upsert")
        )
        assert attempt is not None and operation is not None
        assert attempt.document_generation == 4
        assert attempt.attempt_kind == "ingest"
        assert operation.document_generation == 4
        assert operation.payload["document_generation"] == 4
        assert operation.payload["dataset_generation"] == 7
    catalog.reset_engine()


def test_ingest_deleted_after_read_is_superseded_before_authority_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, document = _configure_catalog(tmp_path, monkeypatch)
    file_path = tmp_path / "document.md"
    file_path.write_text("hello", encoding="utf-8")
    pipeline = IngestPipeline(before_return=lambda: _request_delete(engine, 0))

    result = state_machine.DocumentIngestJob(
        pipeline,
        document["id"],
        dataset_id="dataset-1",
        ledger=IngestLedger(engine),
        operation_queue=IndexOperationQueue(engine),
        chunk_catalog=ChunkCatalog(engine),
        ledger_mode="active",
    ).run(str(file_path))

    assert result["status"] == "superseded"
    assert pipeline.write_calls == 0
    with Session(engine) as session:
        assert list(
            session.scalars(select(ChunkHead).where(ChunkHead.document_id == "doc-1"))
        ) == []
    assert all(
        operation.operation == "delete_document"
        for operation in IndexOperationQueue(engine).list_operations()
    )
    with Session(engine) as session:
        attempt = session.scalar(
            select(DocumentIngestAttempt).where(
                DocumentIngestAttempt.attempt_kind == "ingest"
            )
        )
        assert attempt is not None
        assert attempt.state == "superseded"
    catalog.reset_engine()


def test_ingest_dataset_generation_change_is_superseded_before_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, document = _configure_catalog(tmp_path, monkeypatch)
    file_path = tmp_path / "document.md"
    file_path.write_text("hello", encoding="utf-8")

    def bump_dataset_generation() -> None:
        with Session(engine) as session:
            dataset = session.get(Dataset, "dataset-1")
            assert dataset is not None
            dataset.mutation_generation = int(dataset.mutation_generation or 0) + 1
            session.commit()

    pipeline = IngestPipeline(before_return=bump_dataset_generation)
    result = state_machine.DocumentIngestJob(
        pipeline,
        document["id"],
        dataset_id="dataset-1",
        ledger=IngestLedger(engine),
        operation_queue=IndexOperationQueue(engine),
        chunk_catalog=ChunkCatalog(engine),
        ledger_mode="active",
    ).run(str(file_path))

    assert result["status"] == "superseded"
    assert pipeline.write_calls == 0
    assert IndexOperationQueue(engine).list_operations() == []
    catalog.reset_engine()


def test_manual_reindex_rejects_deleting_document_with_409_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _document = _configure_catalog(tmp_path, monkeypatch)
    file_path = tmp_path / "document.md"
    file_path.write_text("hello", encoding="utf-8")
    _request_delete(engine, 0)
    pipeline = ReindexPipeline()

    conflict_type = getattr(state_machine, "DocumentWriteSuperseded", RuntimeError)
    with pytest.raises(conflict_type) as exc_info:
        reindex_document(pipeline, "doc-1", str(file_path), force=True)

    assert getattr(exc_info.value, "status_code", None) == 409
    assert pipeline.parse_calls == 0
    assert pipeline.write_calls == 0
    catalog.reset_engine()


def test_reindex_deleted_after_parse_does_not_write_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _document = _configure_catalog(tmp_path, monkeypatch)
    file_path = tmp_path / "document.md"
    file_path.write_text("hello", encoding="utf-8")
    pipeline = ReindexPipeline(before_return=lambda: _request_delete(engine, 0))

    result = reindex_document(pipeline, "doc-1", str(file_path), force=True)

    assert result["status"] == "superseded"
    assert pipeline.write_calls == 0
    catalog.reset_engine()


def test_chunk_mutation_carries_generation_and_rejects_deleting_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _document = _configure_catalog(tmp_path, monkeypatch)
    _set_generations(engine, document_generation=3, dataset_generation=5)
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        document.status = "completed"
        document.content_revision = 1
        document.desired_index_revision = 1
        document.indexed_revision = 1
        document.chunk_count = 1
        session.commit()
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
        source_content="body",
        content="body",
    )
    queue = IndexOperationQueue(engine)
    pipeline = SimpleNamespace(
        graph_builder=None,
        milvus=SimpleNamespace(query_chunks_by_doc=lambda *_args, **_kwargs: []),
    )

    update_document_chunk_artifacts(
        "doc-1",
        "chunk-1",
        "edited",
        expected_revision=0,
        authority_mode="active",
        catalog_api=catalog,
        pipeline=pipeline,
        chunk_catalog=chunk_catalog,
        operation_queue=queue,
    )

    operations = [item for item in queue.list_operations() if item.operation == "upsert"]
    assert operations
    assert all(item.document_generation == 3 for item in operations)
    assert all(item.payload["dataset_generation"] == 5 for item in operations)

    _request_delete(engine, 3)
    with pytest.raises(ChunkAuthorityIncomplete):
        update_document_chunk_artifacts(
            "doc-1",
            "chunk-1",
            "must not write",
            expected_revision=1,
            authority_mode="active",
            catalog_api=catalog,
            pipeline=pipeline,
            chunk_catalog=chunk_catalog,
            operation_queue=queue,
        )
    assert chunk_catalog.get_head("chunk-1").content == "edited"
    catalog.reset_engine()


def test_attempt_creation_failure_rolls_back_pointer_and_running_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, document = _configure_catalog(tmp_path, monkeypatch)
    file_path = tmp_path / "document.md"
    file_path.write_text("hello", encoding="utf-8")
    ledger = IngestLedger(engine)
    with Session(engine) as session:
        previous = DocumentIngestAttempt(
            id="delete-attempt-existing",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="doc-1",
            attempt_no=1,
            state="completed",
            attempt_kind="document_delete",
            document_generation=0,
        )
        session.add(previous)
        row = session.get(Document, "doc-1")
        assert row is not None
        row.current_attempt_id = previous.id
        session.commit()

    start_in_session = getattr(ledger, "start_attempt_in_session", None)
    assert callable(start_in_session), "attempt creation must expose a session-composable API"

    def fail_after_attempt_flush(*args, **kwargs):
        attempt = start_in_session(*args, **kwargs)
        assert attempt.state == "running"
        raise RuntimeError("injected attempt transaction failure")

    monkeypatch.setattr(ledger, "start_attempt_in_session", fail_after_attempt_flush)
    result = state_machine.DocumentIngestJob(
        IngestPipeline(),
        document["id"],
        dataset_id="dataset-1",
        ledger=ledger,
        operation_queue=IndexOperationQueue(engine),
        chunk_catalog=ChunkCatalog(engine),
        ledger_mode="active",
    ).run(str(file_path))

    assert result["status"] == "error"
    with Session(engine) as session:
        row = session.get(Document, "doc-1")
        attempts = list(
            session.scalars(
                select(DocumentIngestAttempt).where(
                    DocumentIngestAttempt.document_id == "doc-1"
                )
            )
        )
        assert row is not None and row.current_attempt_id == "delete-attempt-existing"
        assert [item.id for item in attempts] == ["delete-attempt-existing"]
    catalog.reset_engine()


def test_reindex_only_enqueues_durable_generation_fenced_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _document = _configure_catalog(tmp_path, monkeypatch)
    _set_generations(engine, document_generation=2, dataset_generation=3)
    file_path = tmp_path / "document.md"
    file_path.write_text("changed", encoding="utf-8")
    pipeline = ReindexPipeline()
    pipeline.graph_enabled = False
    pipeline.graph_builder = None
    pipeline.ingest_meta = lambda _result: {}

    result = reindex_document(pipeline, "doc-1", str(file_path), force=True)

    assert result["status"] == "indexing", result
    assert result["queued"] is True
    assert pipeline.write_calls == 0
    operations = IndexOperationQueue(engine).list_operations()
    assert operations
    assert all(item.operation == "upsert" for item in operations)
    assert all(item.document_generation == 2 for item in operations)
    assert all(item.payload["dataset_generation"] == 3 for item in operations)
    catalog.reset_engine()


def test_shadow_chunk_write_stale_after_upsert_is_cleaned_under_generation_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _document = _configure_catalog(tmp_path, monkeypatch)
    with Session(engine) as session:
        row = session.get(Document, "doc-1")
        assert row is not None
        row.status = "completed"
        row.content_revision = 1
        row.desired_index_revision = 1
        row.indexed_revision = 1
        row.chunk_count = 1
        session.commit()
    legacy = Chunk(
        chunk_id="chunk-1",
        doc_id="doc-1",
        text="old body",
        text_hash="old-hash",
        created_at=datetime(2026, 8, 25, 8, 0),
        updated_at=datetime(2026, 8, 25, 8, 0),
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        metadata={"chunk_role": "flat"},
        document_revision=1,
        content_revision=0,
    )

    class RaceMilvus:
        def __init__(self) -> None:
            self.upserted: list[str] = []
            self.deleted: list[list[str]] = []
            self.injected = False

        def query_chunks_by_doc(self, *_args, **_kwargs):
            return [legacy]

        def upsert_chunks(self, chunks, _vectors):
            self.upserted.extend(chunk.chunk_id for chunk in chunks)
            if not self.injected:
                self.injected = True
                _request_delete(engine, 0)

        def delete_by_ids(self, ids):
            self.deleted.append(list(ids))
            return len(ids)

    milvus = RaceMilvus()
    pipeline = SimpleNamespace(
        milvus=milvus,
        embedder=SimpleNamespace(embed_texts=lambda _texts: [[0.1, 0.2]]),
        graph_builder=None,
        _embedding_text=lambda chunk: chunk.text,
    )
    chunk_catalog = ChunkCatalog(engine)

    with pytest.raises(ChunkAuthorityIncomplete):
        update_document_chunk_artifacts(
            "doc-1",
            "chunk-1",
            "new body",
            expected_revision=0,
            authority_mode="shadow",
            catalog_api=catalog,
            pipeline=pipeline,
            chunk_catalog=chunk_catalog,
            operation_queue=IndexOperationQueue(engine),
        )

    assert milvus.upserted == ["chunk-1"]
    assert ["chunk-1"] in milvus.deleted
    catalog.reset_engine()



def test_source_execution_owner_lost_during_parse_cannot_commit_chunk_or_index_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    engine, document = _configure_catalog(tmp_path, monkeypatch)
    file_path = tmp_path / "document.md"
    file_path.write_text("hello", encoding="utf-8")
    with Session(engine) as session:
        session.add(
            DataSourceRecord(
                id="source-1",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Source",
                source_type="local_dir",
            )
        )
        session.commit()
    ledger = SourceSyncLedger(engine)
    run = ledger.request_run(
        "tenant-1", "dataset-1", "source-1",
        trigger="manual", force_full=False, dry_run=False,
        audit=AuditContext.system("operator", "source-writer-fence"),
        idempotency_key="Source-Writer-Fence-0001", request_hash="f" * 64,
    ).run
    now = datetime.utcnow()
    ledger.claim_execution(
        run.id, owner="old-owner", now=now, lease_until=now + timedelta(seconds=1)
    )

    def reclaim() -> None:
        claimed = ledger.claim_execution(
            run.id,
            owner="new-owner",
            now=now + timedelta(seconds=2),
            lease_until=now + timedelta(seconds=30),
        )
        assert claimed is not None

    result = state_machine.DocumentIngestJob(
        IngestPipeline(before_return=reclaim),
        document["id"],
        dataset_id="dataset-1",
        ledger=IngestLedger(engine),
        operation_queue=IndexOperationQueue(engine),
        chunk_catalog=ChunkCatalog(engine),
        ledger_mode="active",
        attempt_kind="source_sync",
        source_id="source-1",
        source_generation=0,
        source_run_id=run.id,
        execution_owner="old-owner",
    ).run(str(file_path))

    assert result["status"] == "superseded"
    with Session(engine) as session:
        assert list(session.scalars(select(ChunkHead))) == []
        assert list(session.scalars(select(IndexOperation))) == []
    catalog.reset_engine()
