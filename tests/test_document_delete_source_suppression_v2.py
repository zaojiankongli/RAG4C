from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core import catalog
from core.document_deletion import DocumentDeletionRepository
from core.knowledge_governance import AuditContext
from core.source_sync_ledger import SourceSyncLedger, SourceSyncLedgerConflict
from models.orm import (
    DataSourceRecord,
    Dataset,
    Document,
    DocumentDeleteBatch,
    DocumentDeleteOperation,
    DocumentIngestAttempt,
    IndexOperation,
    SourceDocumentState,
    SourceSyncRun,
    Tenant,
)
from sources.base import DocumentSource, FetchedDocument, content_sha256
from sources.runner import SourceSpec, SourceSyncer, make_doc_id


def _database(tmp_path: Path):
    # 目录库用 session 级模板库（tests/_catalog_template.py）：建一次已迁移到
    # head 的 SQLite、各用例拷文件，实测 4.0ms/次 vs 重跑迁移 13.9s（3432x）。
    # 本文件不需要 BASELINE（不验证「从 baseline 升到 head」的过程本身），
    # 所以拷模板与重跑迁移等价。
    from _catalog_template import head_db_url

    url = head_db_url(tmp_path / "catalog.db")
    engine = catalog.create_engine(url) if hasattr(catalog, "create_engine") else None
    if engine is None:
        from sqlalchemy import create_engine

        engine = create_engine(url)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant"))
        session.add(Dataset(id="dataset-1", tenant_id="tenant-1", name="KB"))
        session.commit()
    return engine


def _spec() -> SourceSpec:
    return SourceSpec(
        name="docs-source",
        type="fake",
        dataset_id="dataset-1",
        tenant_id="tenant-1",
    )


def _fetched(tmp_path: Path, name: str, body: str) -> FetchedDocument:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return FetchedDocument(
        uri=f"fake://{name}",
        rel_path=name,
        local_path=path,
        content_hash=content_sha256(path.read_bytes()),
    )


class StaticSource(DocumentSource):
    name = "fake"

    def __init__(self, documents, *, before_yield=None):
        self.documents = list(documents)
        self.before_yield = before_yield

    def fetch(self, _workdir: Path):
        if self.before_yield is not None:
            self.before_yield()
        yield from self.documents

    def describe(self) -> str:
        return "fake"


class FakePipeline:
    def __init__(self, *, chunk_count: int = 0) -> None:
        self.chunk_count = chunk_count
        self.added: list[str] = []
        self.deleted: list[str] = []

    def ensure_collection(self) -> None:
        pass

    def add_file(self, path: str, **_kwargs):
        self.added.append(path)
        return SimpleNamespace(chunk_count=self.chunk_count)

    def delete_document(self, doc_id: str, unregister: bool = True):
        self.deleted.append(doc_id)
        return 1


class DurableChunk:
    def __init__(self, doc_id: str) -> None:
        self.chunk_id = f"{doc_id}-chunk-0"
        self.doc_id = doc_id
        self.text = "source body"
        self.text_hash = "source-hash"
        self.parent_chunk_id = None
        self.metadata = {"chunk_index": 0}
        self.tenant_id = "tenant-1"
        self.dataset_id = "dataset-1"
        self.document_revision = 0
        self.content_revision = 0


class DurablePipeline(FakePipeline):
    graph_enabled = False
    graph_builder = None

    def __init__(self, *, before_return=None) -> None:
        super().__init__()
        self.before_return = before_return

    def parse_and_chunk(self, _path: str, *, doc_id: str, progress=None, **_kwargs):
        if progress is not None:
            progress("parsing", 1.0, "parsed")
            progress("splitting", 1.0, "split")
        if self.before_return is not None:
            self.before_return()
        return [DurableChunk(doc_id)]

    def ingest_meta(self, _result):
        return {}


def _seed_source_document(engine, ledger: SourceSyncLedger, tmp_path: Path, *, state: str):
    source = ledger.ensure_source(_spec())
    doc_id = make_doc_id(_spec().name, "source.md")
    with Session(engine) as session:
        session.add(
            Document(
                id=doc_id,
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="source.md",
                source_id=source.id,
                external_id="source.md",
                source_uri="fake://source.md",
                status="completed",
                chunk_count=0,
            )
        )
        session.add(
            SourceDocumentState(
                id=f"state-{state}",
                source_id=source.id,
                doc_id=doc_id,
                external_id="source.md",
                source_uri="fake://source.md",
                content_hash="old-hash",
                chunk_count=0,
                last_run_id="",
                state=state,
                document_generation=0,
            )
        )
        session.commit()
    return source, doc_id, _fetched(tmp_path, "source.md", "new content")


def test_source_ingest_queues_generation_fenced_projection_without_direct_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _database(tmp_path)
    ledger = SourceSyncLedger(engine)
    source = ledger.ensure_source(_spec())
    with Session(engine) as session:
        dataset = session.get(Dataset, "dataset-1")
        assert dataset is not None
        dataset.mutation_generation = 6
        source_row = session.get(DataSourceRecord, source.id)
        assert source_row is not None
        source_row.mutation_generation = 4
        session.commit()
    document = _fetched(tmp_path, "source.md", "content")
    pipeline = DurablePipeline()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    monkeypatch.setattr(
        "sources.runner.create_source",
        lambda *_args, **_kwargs: StaticSource([document]),
    )

    report = SourceSyncer(
        pipeline,
        tmp_path / "cache",
        ledger=ledger,
        state_mode="database",
    ).sync(_spec())

    assert report.ingested == 1
    assert pipeline.added == []
    with Session(engine) as session:
        operation = session.scalar(
            select(IndexOperation).where(IndexOperation.operation == "upsert")
        )
        assert operation is not None
        assert operation.document_generation == 0
        assert operation.payload["dataset_generation"] == 6
        assert operation.payload["source_generation"] == 4
    engine.dispose()


@pytest.mark.parametrize("state", ["operator_suppressed", "delete_pending"])
def test_suppressed_source_identity_is_preserved_and_never_reingested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    engine = _database(tmp_path)
    ledger = SourceSyncLedger(engine)
    source, doc_id, document = _seed_source_document(engine, ledger, tmp_path, state=state)
    pipeline = FakePipeline()
    monkeypatch.setattr("sources.runner.create_source", lambda *_args, **_kwargs: StaticSource([document]))

    report = SourceSyncer(
        pipeline,
        tmp_path / "cache",
        ledger=ledger,
        state_mode="database",
    ).sync(_spec(), force=True)

    assert report.ingested == 0
    assert report.skipped == 1
    assert pipeline.added == []
    assert pipeline.deleted == []
    loaded = ledger.load_state(source.id)[doc_id]
    assert loaded["state"] == state
    with Session(engine) as session:
        item = session.scalar(select(SourceSyncRun).order_by(SourceSyncRun.created_at.desc()))
        assert item is not None
        persisted = session.scalar(
            select(SourceDocumentState).where(SourceDocumentState.doc_id == doc_id)
        )
        assert persisted is not None and persisted.state == state
    engine.dispose()


def test_source_generation_change_supersedes_old_run_before_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _database(tmp_path)
    ledger = SourceSyncLedger(engine)
    source = ledger.ensure_source(_spec())
    document = _fetched(tmp_path, "source.md", "content")

    def bump_generation() -> None:
        ledger.bump_source_generation(source.id, reason="test config change")

    pipeline = FakePipeline()
    monkeypatch.setattr(
        "sources.runner.create_source",
        lambda *_args, **_kwargs: StaticSource([document], before_yield=bump_generation),
    )

    report = SourceSyncer(
        pipeline,
        tmp_path / "cache",
        ledger=ledger,
        state_mode="database",
    ).sync(_spec())

    assert pipeline.added == []
    assert report.fetch_error == "source sync generation changed"
    run = ledger.list_runs(source.id)[0]
    assert run.status == "superseded"
    engine.dispose()


def test_source_generation_change_supersedes_queued_writer_and_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _database(tmp_path)
    ledger = SourceSyncLedger(engine)
    source = ledger.ensure_source(_spec())
    document = _fetched(tmp_path, "source.md", "content")
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    monkeypatch.setattr(
        "sources.runner.create_source",
        lambda *_args, **_kwargs: StaticSource([document]),
    )
    report = SourceSyncer(
        DurablePipeline(),
        tmp_path / "cache",
        ledger=ledger,
        state_mode="database",
    ).sync(_spec())
    assert report.ingested == 1

    ledger.bump_source_generation(source.id, reason="source configuration changed")

    with Session(engine) as session:
        operation = session.scalar(
            select(IndexOperation).where(IndexOperation.operation == "upsert")
        )
        assert operation is not None and operation.status == "superseded"
        attempt = session.get(DocumentIngestAttempt, operation.attempt_id)
        assert attempt is not None and attempt.state == "superseded"
    engine.dispose()


def test_source_generation_change_during_parse_prevents_stale_projection_enqueue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _database(tmp_path)
    ledger = SourceSyncLedger(engine)
    source = ledger.ensure_source(_spec())
    document = _fetched(tmp_path, "source.md", "content")
    pipeline = DurablePipeline(
        before_return=lambda: ledger.bump_source_generation(
            source.id, reason="source changed during parse"
        )
    )
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    monkeypatch.setattr(
        "sources.runner.create_source",
        lambda *_args, **_kwargs: StaticSource([document]),
    )

    report = SourceSyncer(
        pipeline,
        tmp_path / "cache",
        ledger=ledger,
        state_mode="database",
    ).sync(_spec())

    doc_id = make_doc_id(_spec().name, "source.md")
    assert report.fetch_error == "source sync generation changed"
    with Session(engine) as session:
        stale_upserts = list(
            session.scalars(
                select(IndexOperation).where(IndexOperation.operation == "upsert")
            )
        )
        persisted = session.get(Document, doc_id)
        assert stale_upserts == []
        assert persisted is not None and persisted.lifecycle_state == "delete_requested"
    engine.dispose()


def test_generation_change_after_source_registration_queues_cleanup_not_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _database(tmp_path)
    ledger = SourceSyncLedger(engine)
    document = _fetched(tmp_path, "source.md", "content")
    pipeline = DurablePipeline()
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    monkeypatch.setattr(
        "sources.runner.create_source",
        lambda *_args, **_kwargs: StaticSource([document]),
    )
    syncer = SourceSyncer(
        pipeline,
        tmp_path / "cache",
        ledger=ledger,
        state_mode="database",
    )
    original_register = syncer._register

    def register_then_reset(*args, **kwargs):
        result = original_register(*args, **kwargs)
        with Session(engine) as session:
            dataset = session.get(Dataset, "dataset-1")
            assert dataset is not None
            dataset.mutation_generation = int(dataset.mutation_generation or 0) + 1
            session.commit()
        return result

    monkeypatch.setattr(syncer, "_register", register_then_reset)

    report = syncer.sync(_spec())

    doc_id = make_doc_id(_spec().name, "source.md")
    assert report.fetch_error == "source sync generation changed"
    assert pipeline.added == []
    with Session(engine) as session:
        persisted = session.get(Document, doc_id)
        operation = session.scalar(
            select(DocumentDeleteOperation).where(
                DocumentDeleteOperation.document_id == doc_id
            )
        )
        assert persisted is not None
        assert persisted.lifecycle_state == "delete_requested"
        assert persisted.retrieval_enabled is False
        assert operation is not None and operation.origin == "source_sync"
    engine.dispose()


def test_dataset_generation_change_supersedes_old_source_run_before_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _database(tmp_path)
    ledger = SourceSyncLedger(engine)
    source = ledger.ensure_source(_spec())
    document = _fetched(tmp_path, "source.md", "content")

    def bump_generation() -> None:
        with Session(engine) as session:
            dataset = session.get(Dataset, "dataset-1")
            assert dataset is not None
            dataset.mutation_generation = int(dataset.mutation_generation or 0) + 1
            session.commit()

    pipeline = FakePipeline()
    monkeypatch.setattr(
        "sources.runner.create_source",
        lambda *_args, **_kwargs: StaticSource([document], before_yield=bump_generation),
    )

    report = SourceSyncer(
        pipeline,
        tmp_path / "cache",
        ledger=ledger,
        state_mode="database",
    ).sync(_spec())

    assert pipeline.added == []
    assert report.fetch_error == "source sync generation changed"
    run = ledger.list_runs(source.id)[0]
    assert run.status == "superseded"
    engine.dispose()


def test_upstream_delete_creates_durable_delete_and_keeps_source_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _database(tmp_path)
    ledger = SourceSyncLedger(engine)
    source, doc_id, _document = _seed_source_document(
        engine, ledger, tmp_path, state="active"
    )
    pipeline = FakePipeline()
    monkeypatch.setattr("sources.runner.create_source", lambda *_args, **_kwargs: StaticSource([]))

    report = SourceSyncer(
        pipeline,
        tmp_path / "cache",
        ledger=ledger,
        state_mode="database",
    ).sync(_spec())

    assert report.removed == 0
    assert report.pending_deletes == 1
    assert pipeline.deleted == []
    loaded = ledger.load_state(source.id)[doc_id]
    assert loaded["state"] == "delete_pending"
    assert loaded["delete_operation_id"]
    with Session(engine) as session:
        operation = session.scalar(
            select(DocumentDeleteOperation).where(
                DocumentDeleteOperation.document_id == doc_id
            )
        )
        assert operation is not None
        assert operation.origin == "source_sync"
        document = session.get(Document, doc_id)
        assert document is not None
        assert document.lifecycle_state == "delete_requested"
        assert document.retrieval_enabled is False
    engine.dispose()


def test_upstream_delete_does_not_report_fake_success_without_durable_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _database(tmp_path)
    ledger = SourceSyncLedger(engine)
    source = ledger.ensure_source(_spec())
    with Session(engine) as session:
        session.add(
            SourceDocumentState(
                id="state-missing",
                source_id=source.id,
                doc_id="missing-doc",
                external_id="missing.md",
                source_uri="fake://missing.md",
                content_hash="hash",
                chunk_count=0,
                last_run_id="",
                state="active",
                document_generation=0,
            )
        )
        session.commit()
    pipeline = FakePipeline()
    monkeypatch.setattr("sources.runner.create_source", lambda *_args, **_kwargs: StaticSource([]))

    report = SourceSyncer(
        pipeline,
        tmp_path / "cache",
        ledger=ledger,
        state_mode="database",
    ).sync(_spec())

    assert report.removed == 0
    assert pipeline.deleted == []
    assert "missing-doc" in ledger.load_state(source.id)
    assert report.failed
    engine.dispose()


def test_dataset_reset_generation_supersedes_claimed_non_source_writer(
    tmp_path: Path
) -> None:
    engine = _database(tmp_path)
    ledger = SourceSyncLedger(engine)
    source = ledger.ensure_source(_spec())
    with Session(engine) as session:
        session.add(
            Document(
                id="manual-doc",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="manual.md",
                status="indexing",
                chunk_count=0,
            )
        )
        attempt = DocumentIngestAttempt(
            id="manual-attempt",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            document_id="manual-doc",
            attempt_no=1,
            state="running",
            attempt_kind="ingest",
            document_generation=0,
        )
        session.add(attempt)
        session.add(
            IndexOperation(
                id="manual-op",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                document_id="manual-doc",
                attempt_id=attempt.id,
                target_store="milvus_chunks",
                operation="upsert",
                dedup_key="manual-reset-op",
                target_revision=1,
                document_generation=0,
                payload={"dataset_generation": 0},
                status="claimed",
                claimed_by="worker-old",
            )
        )
        session.commit()

    ledger.bump_reset_generations(source.id)

    with Session(engine) as session:
        operation = session.get(IndexOperation, "manual-op")
        attempt = session.get(DocumentIngestAttempt, "manual-attempt")
        assert operation is not None and operation.status == "superseded"
        assert operation.claimed_by == ""
        assert attempt is not None and attempt.state == "superseded"
    engine.dispose()


def test_reset_uses_durable_batch_and_fences_existing_source_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = _database(tmp_path)
    ledger = SourceSyncLedger(engine)
    source = ledger.ensure_source(_spec())
    with Session(engine) as session:
        session.add(
            Document(
                id="doc-reset",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="reset.md",
                status="completed",
                chunk_count=0,
            )
        )
        session.commit()
    old_run = ledger.start_run(
        source.id,
        trigger="manual",
        force_full=False,
        dry_run=False,
    )
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    monkeypatch.setattr(catalog, "purge_dataset", lambda *_args, **_kwargs: pytest.fail("purge_dataset must not run"))

    from scripts.ingest_source import _reset_dataset

    batch_ids = _reset_dataset(SimpleNamespace(), _spec(), tmp_path / "cache")

    assert len(batch_ids) == 1
    batch_id = batch_ids[0]
    assert batch_id.startswith("delete-batch-")
    output = capsys.readouterr().out
    assert batch_id in output
    with Session(engine) as session:
        dataset = session.get(Dataset, "dataset-1")
        refreshed_source = session.get(DataSourceRecord, source.id)
        run = session.get(SourceSyncRun, old_run.id)
        batch = session.get(DocumentDeleteBatch, batch_id)
        operation = session.scalar(
            select(DocumentDeleteOperation).where(
                DocumentDeleteOperation.batch_id == batch_id
            )
        )
        assert dataset is not None and dataset.mutation_generation == 1
        assert refreshed_source is not None and refreshed_source.mutation_generation == 1
        assert run is not None and run.status == "superseded"
        assert batch is not None
        assert operation is not None and operation.origin == "dataset_reset"
    with pytest.raises(SourceSyncLedgerConflict):
        ledger.assert_run_current(old_run.id)
    engine.dispose()


def test_dataset_reset_partitions_more_than_one_hundred_documents_atomically(
    tmp_path: Path
) -> None:
    engine = _database(tmp_path)
    ledger = SourceSyncLedger(engine)
    source = ledger.ensure_source(_spec())
    with Session(engine) as session:
        session.add_all(
            Document(
                id=f"reset-doc-{index:03d}",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name=f"reset-{index:03d}.md",
                status="completed",
                chunk_count=0,
            )
            for index in range(205)
        )
        session.commit()

    result = DocumentDeletionRepository(engine).request_dataset_reset(
        source_id=source.id,
        actor=AuditContext.system("system:reset-test", "req-reset-205"),
        reason="reset more than one batch",
        idempotency_prefix="reset-205-documents",
    )

    assert len(result.batch_ids) == 3
    assert result.document_count == 205
    with Session(engine) as session:
        assert int(session.scalar(select(func.count(DocumentDeleteBatch.id))) or 0) == 3
        assert int(session.scalar(select(func.count(DocumentDeleteOperation.id))) or 0) == 205
        assert int(
            session.scalar(
                select(func.count(Document.id)).where(
                    Document.lifecycle_state == "delete_requested"
                )
            )
            or 0
        ) == 205
        dataset = session.get(Dataset, "dataset-1")
        source_row = session.get(DataSourceRecord, source.id)
        assert dataset is not None and dataset.mutation_generation == 1
        assert source_row is not None and source_row.mutation_generation == 1
    engine.dispose()


def test_dataset_reset_mid_batch_failure_rolls_back_every_batch_and_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _database(tmp_path)
    ledger = SourceSyncLedger(engine)
    source = ledger.ensure_source(_spec())
    with Session(engine) as session:
        session.add_all(
            Document(
                id=f"rollback-doc-{index:03d}",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name=f"rollback-{index:03d}.md",
                status="completed",
                chunk_count=0,
            )
            for index in range(105)
        )
        session.commit()

    original = DocumentDeletionRepository._add_index_operation
    calls = 0

    def fail_in_second_batch(self, session, operation):
        nonlocal calls
        calls += 1
        if calls == 202:
            raise RuntimeError("injected second-batch enqueue failure")
        return original(self, session, operation)

    monkeypatch.setattr(
        DocumentDeletionRepository, "_add_index_operation", fail_in_second_batch
    )
    with pytest.raises(RuntimeError, match="second-batch enqueue failure"):
        DocumentDeletionRepository(engine).request_dataset_reset(
            source_id=source.id,
            actor=AuditContext.system("system:reset-test", "req-reset-rollback"),
            reason="rollback every batch",
            idempotency_prefix="reset-rollback",
        )

    with Session(engine) as session:
        assert int(session.scalar(select(func.count(DocumentDeleteBatch.id))) or 0) == 0
        assert int(session.scalar(select(func.count(DocumentDeleteOperation.id))) or 0) == 0
        assert int(
            session.scalar(
                select(func.count(Document.id)).where(
                    Document.lifecycle_state != "active"
                )
            )
            or 0
        ) == 0
        dataset = session.get(Dataset, "dataset-1")
        source_row = session.get(DataSourceRecord, source.id)
        assert dataset is not None and dataset.mutation_generation == 0
        assert source_row is not None and source_row.mutation_generation == 0
    engine.dispose()



def test_dataset_reset_discovers_scope_then_locks_dataset_source_runs_operations() -> None:
    events: list[tuple[str, str]] = []
    dataset = SimpleNamespace(id="dataset-1", tenant_id="tenant-1")
    source = SimpleNamespace(id="source-1", dataset_id="dataset-1", tenant_id="tenant-1")

    class ScopeResult:
        @staticmethod
        def one_or_none():
            return SimpleNamespace(tenant_id="tenant-1", dataset_id="dataset-1")

    class FakeSession:
        def execute(self, statement):
            sql = str(statement)
            events.append(("discover", sql))
            return ScopeResult()

        def scalar(self, statement):
            sql = str(statement)
            if "FROM datasets" in sql:
                events.append(("dataset", sql))
                return dataset
            events.append(("source", sql))
            return source

        def scalars(self, statement):
            sql = str(statement)
            if "FROM source_sync_runs" in sql:
                events.append(("runs", sql))
            else:
                events.append(("operations", sql))
            return []

    locked = DocumentDeletionRepository._lock_dataset_reset_scope(
        FakeSession(), "source-1"
    )
    assert locked == (dataset, source, [], [])
    assert [name for name, _sql in events] == [
        "discover", "dataset", "source", "runs", "operations"
    ]
    assert "FOR UPDATE" not in events[0][1]
    assert all("FOR UPDATE" in sql for _name, sql in events[1:])
