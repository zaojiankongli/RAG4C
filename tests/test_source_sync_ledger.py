from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from core import catalog
from core.knowledge_governance import AuditContext
from models.orm import DataSourceRecord, Dataset, SourceSyncRun, Tenant
from sources.base import DocumentSource, FetchedDocument, SourceError, content_sha256
from sources.runner import SourceSpec, SourceSyncer


def ledger_module() -> ModuleType:
    try:
        return importlib.import_module("core.source_sync_ledger")
    except ModuleNotFoundError:
        pytest.fail("core.source_sync_ledger is missing")


def create_ledger(tmp_path: Path):
    from core.catalog_schema import upgrade_catalog

    url = f"sqlite:///{(tmp_path / 'catalog.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant"))
        session.add(Dataset(id="dataset-1", tenant_id="tenant-1", name="KB"))
        session.commit()
    return engine, ledger_module().SourceSyncLedger(engine)


def spec() -> SourceSpec:
    return SourceSpec(
        name="docs-source",
        type="fake",
        dataset_id="dataset-1",
        tenant_id="tenant-1",
        params={
            "repo": "owner/docs",
            "api_key": "top" + "-secret",
            "nested": {"token": "nested-secret", "safe": "kept"},
        },
        metadata={"project": "Docs"},
    )


def test_source_run_items_cursor_and_config_are_durable_and_sanitized(
    tmp_path: Path,
) -> None:
    engine, ledger = create_ledger(tmp_path)
    source = ledger.ensure_source(spec())

    assert "top-secret" not in str(source.effective_config)
    assert "nested-secret" not in str(source.effective_config)
    assert source.effective_config["params"]["repo"] == "owner/docs"
    assert source.effective_config["params"]["nested"]["safe"] == "kept"

    run = ledger.start_run(
        source.id,
        trigger="manual",
        force_full=False,
        dry_run=False,
        cursor_before={"etag": "before"},
    )
    first = ledger.record_item(
        run.id,
        external_id="guide.md",
        doc_id="doc-guide",
        source_uri="fake://guide.md",
        content_hash="hash-1",
        action="upsert",
        result="completed",
        chunk_count=3,
    )
    second = ledger.record_item(
        run.id,
        external_id="guide.md",
        doc_id="doc-guide",
        source_uri="fake://guide.md",
        content_hash="hash-1",
        action="upsert",
        result="completed",
        chunk_count=3,
    )
    finished = ledger.finish_run(
        run.id,
        status="completed",
        cursor_after={"etag": "after"},
        counts={"fetched": 1, "ingested": 1, "chunks": 3},
    )

    assert second.id == first.id
    assert finished.status == "completed"
    assert ledger.get_source(source.id).last_cursor == {"etag": "after"}
    assert len(ledger.list_items(run.id)) == 1
    engine.dispose()


class StaticSource(DocumentSource):
    name = "fake"

    def __init__(self, documents: list[FetchedDocument], *, fail_after: bool = False):
        self.documents = documents
        self.fail_after = fail_after

    def fetch(self, workdir: Path):
        yield from self.documents
        if self.fail_after:
            raise SourceError("connection reset")

    def describe(self) -> str:
        return "test source"


class FakePipeline:
    graph_enabled = False
    graph_builder = None

    def __init__(self):
        self.deleted: list[str] = []

    def ensure_collection(self) -> None:
        pass

    def add_file(self, _path: str, **kwargs):
        return SimpleNamespace(chunk_count=2)

    def parse_and_chunk(self, _path: str, *, doc_id: str, progress=None, **_kwargs):
        if progress is not None:
            progress("parsing", 1.0, "parsed")
            progress("splitting", 1.0, "split")
        return [
            SimpleNamespace(
                chunk_id=f"{doc_id}-chunk-{index}",
                doc_id=doc_id,
                text=f"body-{index}",
                text_hash=f"hash-{index}",
                parent_chunk_id=None,
                metadata={"chunk_index": index},
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                document_revision=0,
                content_revision=0,
            )
            for index in range(2)
        ]

    def ingest_meta(self, _result):
        return {}

    def delete_document(self, doc_id: str, unregister: bool = True):
        self.deleted.append(doc_id)
        return 1


def fetched(tmp_path: Path, name: str, body: str) -> FetchedDocument:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    data = path.read_bytes()
    return FetchedDocument(
        uri=f"fake://{name}",
        rel_path=name,
        local_path=path,
        content_hash=content_sha256(data),
    )


def test_dual_mode_preserves_completed_progress_and_suppresses_deletes_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, ledger = create_ledger(tmp_path)
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    pipeline = FakePipeline()
    docs = [fetched(tmp_path, "one.md", "one"), fetched(tmp_path, "two.md", "two")]
    sources = [StaticSource(docs), StaticSource([docs[0]], fail_after=True)]
    monkeypatch.setattr("sources.runner.create_source", lambda *_args, **_kwargs: sources.pop(0))
    syncer = SourceSyncer(
        pipeline,
        tmp_path / "cache",
        ledger=ledger,
        state_mode="dual",
    )

    first = syncer.sync(spec())
    second = syncer.sync(spec())

    source = ledger.find_source("tenant-1", "docs-source")
    assert source is not None
    database_state = ledger.load_state(source.id)
    json_state = syncer._load_json_state(spec())
    assert first.ingested == 2
    assert second.fetch_error
    assert second.removed == 0
    assert pipeline.deleted == []
    assert set(database_state) == set(json_state)
    assert len(database_state) == 2
    runs = ledger.list_runs(source.id)
    assert [run.status for run in runs] == ["completed", "incomplete"]
    engine.dispose()


def test_database_mode_resumes_without_json_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, ledger = create_ledger(tmp_path)
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)
    pipeline = FakePipeline()
    document = fetched(tmp_path, "one.md", "one")
    monkeypatch.setattr(
        "sources.runner.create_source",
        lambda *_args, **_kwargs: StaticSource([document]),
    )
    dual = SourceSyncer(pipeline, tmp_path / "cache", ledger=ledger, state_mode="dual")
    dual.sync(spec())
    dual._state_path(spec()).unlink()

    database_only = SourceSyncer(
        pipeline, tmp_path / "cache", ledger=ledger, state_mode="database"
    )
    report = database_only.sync(spec())

    assert report.skipped == 1
    assert report.ingested == 0
    engine.dispose()


def test_ingest_source_cli_builds_configured_database_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _ = create_ledger(tmp_path)
    from scripts import ingest_source

    settings = SimpleNamespace(
        sources=SimpleNamespace(state_mode="dual"),
    )
    monkeypatch.setattr(catalog, "get_engine", lambda: engine)

    syncer = ingest_source._build_syncer(
        settings, FakePipeline(), tmp_path / "cache"
    )

    assert syncer.state_mode == "dual"
    assert isinstance(syncer.ledger, ledger_module().SourceSyncLedger)
    engine.dispose()



def test_finish_run_cas_cannot_revive_superseded_or_stale_generation(tmp_path: Path) -> None:
    engine, ledger = create_ledger(tmp_path)
    source = ledger.ensure_source(spec())
    superseded = ledger.start_run(
        source.id,
        trigger="manual",
        force_full=False,
        dry_run=False,
    )
    stale = ledger.start_run(
        source.id,
        trigger="manual",
        force_full=False,
        dry_run=False,
    )
    with Session(engine) as session:
        session.get(SourceSyncRun, superseded.id).status = "superseded"
        session.get(DataSourceRecord, source.id).mutation_generation = 1
        session.commit()

    finished_superseded = ledger.finish_run(
        superseded.id,
        status="completed",
        cursor_after={"etag": "must-not-commit"},
        counts={"fetched": 9},
    )
    finished_stale = ledger.finish_run(
        stale.id,
        status="completed",
        cursor_after={"etag": "must-not-commit"},
        counts={"fetched": 9},
    )

    assert finished_superseded.status == "superseded"
    assert finished_superseded.fetched == 0
    assert finished_stale.status == "superseded"
    assert finished_stale.fetched == 0
    with Session(engine) as session:
        persisted_source = session.scalar(select(DataSourceRecord).where(DataSourceRecord.id == source.id))
        assert persisted_source.last_cursor == {}
    engine.dispose()



def test_execution_claim_is_leased_and_only_owner_can_complete(tmp_path: Path) -> None:
    engine, ledger = create_ledger(tmp_path)
    source = ledger.ensure_source(spec())
    run = ledger.request_run(
        "tenant-1",
        "dataset-1",
        source.id,
        trigger="manual",
        force_full=False,
        dry_run=False,
        audit=AuditContext.system("operator", "execution-request"),
        idempotency_key="Execution-Key-Exact-0001",
        request_hash="a" * 64,
    ).run
    assert run.execution_state == "pending"

    now = datetime.utcnow()
    claimed = ledger.claim_execution(
        run.id,
        owner="worker-a",
        now=now,
        lease_until=now + timedelta(seconds=30),
    )
    duplicate = ledger.claim_execution(
        run.id,
        owner="worker-b",
        now=now,
        lease_until=now + timedelta(seconds=30),
    )
    assert claimed is not None
    assert duplicate is None
    assert claimed.execution_state == "executing"
    assert claimed.execution_attempts == 1

    completed = ledger.finish_run(
        run.id,
        status="completed",
        cursor_after={},
        counts={},
        execution_owner="worker-a",
    )
    assert completed.execution_state == "completed"
    assert ledger.claim_execution(
        run.id,
        owner="worker-b",
        now=now + timedelta(seconds=31),
        lease_until=now + timedelta(seconds=61),
    ) is None
    engine.dispose()


def test_expired_or_failed_execution_is_reconciler_claimable(tmp_path: Path) -> None:
    engine, ledger = create_ledger(tmp_path)
    source = ledger.ensure_source(spec())
    run = ledger.request_run(
        "tenant-1",
        "dataset-1",
        source.id,
        trigger="manual",
        force_full=False,
        dry_run=False,
        audit=AuditContext.system("operator", "execution-reconcile"),
        idempotency_key="Execution-Reconcile-0001",
        request_hash="b" * 64,
    ).run
    now = datetime.utcnow()
    ledger.claim_execution(
        run.id,
        owner="dead-worker",
        now=now,
        lease_until=now + timedelta(seconds=1),
    )
    reclaimed = ledger.claim_execution(
        run.id,
        owner="reconciler",
        now=now + timedelta(seconds=2),
        lease_until=now + timedelta(seconds=32),
    )
    assert reclaimed is not None
    assert reclaimed.execution_attempts == 2
    failed = ledger.mark_execution_failed(
        run.id,
        owner="reconciler",
        error="queue offline",
        now=now + timedelta(seconds=3),
    )
    assert failed.execution_state == "failed"
    assert failed.execution_next_attempt_at is not None
    assert ledger.claim_execution(
        run.id,
        owner="reconciler-2",
        now=now + timedelta(seconds=4),
        lease_until=now + timedelta(seconds=34),
    ) is None
    due = failed.execution_next_attempt_at + timedelta(microseconds=1)
    retry = ledger.claim_execution(
        run.id,
        owner="reconciler-2",
        now=due,
        lease_until=due + timedelta(seconds=30),
    )
    assert retry is not None
    assert retry.execution_attempts == 3
    engine.dispose()



def test_owner_fenced_item_upsert_rejects_old_worker_and_replaces_failed_result(
    tmp_path: Path,
) -> None:
    engine, ledger = create_ledger(tmp_path)
    source = ledger.ensure_source(spec())
    run = ledger.request_run(
        "tenant-1",
        "dataset-1",
        source.id,
        trigger="manual",
        force_full=False,
        dry_run=False,
        audit=AuditContext.system("operator", "owner-fenced-item"),
        idempotency_key="Owner-Fenced-Item-0001",
        request_hash="c" * 64,
    ).run
    now = datetime.utcnow()
    ledger.claim_execution(
        run.id,
        owner="old-owner",
        now=now,
        lease_until=now + timedelta(seconds=1),
    )
    ledger.claim_execution(
        run.id,
        owner="new-owner",
        now=now + timedelta(seconds=2),
        lease_until=now + timedelta(seconds=30),
    )
    with pytest.raises(Exception, match="ownership"):
        ledger.record_item(
            run.id,
            external_id="guide.md",
            doc_id="doc-guide",
            source_uri="file:///guide.md",
            content_hash="hash",
            action="upsert",
            result="failed",
            execution_owner="old-owner",
            now=now + timedelta(seconds=3),
        )
    failed = ledger.record_item(
        run.id,
        external_id="guide.md",
        doc_id="doc-guide",
        source_uri="file:///guide.md",
        content_hash="hash",
        action="upsert",
        result="failed",
        error_message="first failure",
        execution_owner="new-owner",
        now=now + timedelta(seconds=3),
    )
    recovered = ledger.record_item(
        run.id,
        external_id="guide.md",
        doc_id="doc-guide",
        source_uri="file:///guide.md",
        content_hash="hash-2",
        action="upsert",
        result="completed",
        chunk_count=4,
        execution_owner="new-owner",
        now=now + timedelta(seconds=4),
    )
    assert recovered.id == failed.id
    assert recovered.result == "completed"
    assert recovered.error_message == ""
    assert recovered.chunk_count == 4
    engine.dispose()



def test_database_time_prevents_process_clock_skew_reservation_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.source_sync_ledger as ledger_module_under_test

    engine, ledger = create_ledger(tmp_path)
    source = ledger.ensure_source(spec())
    run = ledger.request_run(
        "tenant-1", "dataset-1", source.id,
        trigger="manual", force_full=False, dry_run=False,
        audit=AuditContext.system("operator", "db-clock"),
        idempotency_key="Database-Clock-0001", request_hash="d" * 64,
    ).run
    assert ledger.reserve_run_dispatch(
        run.id, owner="replica-a", lease_seconds=30
    ) is True

    class FutureClock(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2099, 1, 1)

    monkeypatch.setattr(ledger_module_under_test, "datetime", FutureClock)
    assert ledger.reserve_run_dispatch(
        run.id, owner="replica-b", lease_seconds=30
    ) is False

    with Session(engine) as session:
        row = session.get(SourceSyncRun, run.id)
        row.reservation_lease_until = datetime(2000, 1, 1)
        session.commit()

    class PastClock(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(1970, 1, 1)

    monkeypatch.setattr(ledger_module_under_test, "datetime", PastClock)
    assert ledger.reserve_run_dispatch(
        run.id, owner="replica-b", lease_seconds=30
    ) is True
    engine.dispose()


def test_max_execution_attempt_sets_business_terminal_fields(tmp_path: Path) -> None:
    engine, ledger = create_ledger(tmp_path)
    source = ledger.ensure_source(spec())
    run = ledger.request_run(
        "tenant-1", "dataset-1", source.id,
        trigger="manual", force_full=False, dry_run=False,
        audit=AuditContext.system("operator", "max-attempt"),
        idempotency_key="Max-Attempt-0001", request_hash="e" * 64,
    ).run
    claimed = ledger.claim_execution(run.id, owner="owner", lease_seconds=30)
    assert claimed is not None
    failed = ledger.mark_execution_failed(
        run.id,
        owner="owner",
        error="token=top-secret terminal failure",
        max_attempts=1,
    )
    assert failed.status == "failed"
    assert failed.execution_state == "completed"
    assert failed.finished_at is not None
    assert failed.fetch_error
    assert "top-secret" not in failed.fetch_error
    assert failed.execution_next_attempt_at is None
    engine.dispose()



@pytest.mark.parametrize("operation", ["heartbeat", "finish"])
def test_lock_wait_crossing_expiry_cannot_renew_or_finalize(
    tmp_path: Path, operation: str
) -> None:
    import threading
    import time

    engine, ledger = create_ledger(tmp_path)
    source = ledger.ensure_source(spec())
    run = ledger.request_run(
        "tenant-1", "dataset-1", source.id,
        trigger="manual", force_full=False, dry_run=False,
        audit=AuditContext.system("operator", f"lock-wait-{operation}"),
        idempotency_key=f"Lock-Wait-{operation}-0001", request_hash="a" * 64,
    ).run
    claimed = ledger.claim_execution(run.id, owner="stale-owner", lease_seconds=30)
    assert claimed is not None

    locker = Session(engine)
    locker.connection().exec_driver_sql("BEGIN IMMEDIATE")
    locked = locker.get(SourceSyncRun, run.id)
    locked.execution_lease_until = datetime(2000, 1, 1)
    locker.flush()
    started = threading.Event()
    errors: list[Exception] = []

    def blocked_call() -> None:
        started.set()
        try:
            if operation == "heartbeat":
                ledger.heartbeat_execution(run.id, owner="stale-owner", lease_seconds=30)
            else:
                ledger.finish_run(
                    run.id,
                    status="completed",
                    cursor_after={},
                    counts={},
                    execution_owner="stale-owner",
                )
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=blocked_call)
    thread.start()
    assert started.wait(timeout=1)
    time.sleep(0.1)
    locker.commit()
    locker.close()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert errors and "ownership" in str(errors[0])
    persisted = ledger.get_run(run.id)
    assert persisted.status == "running"
    engine.dispose()
