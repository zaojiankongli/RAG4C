from __future__ import annotations

import importlib
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from core.index_operations import IndexOperationConflict, IndexOperationQueue
from core.ingest_ledger import IngestLedger
from models.orm import Dataset, Document, DocumentIngestAttempt, Tenant


def worker_module() -> ModuleType:
    try:
        return importlib.import_module("indexing.index_worker")
    except ModuleNotFoundError:
        pytest.fail("indexing.index_worker is missing")


def create_worker_state(tmp_path: Path):
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
                content_revision=2,
                desired_index_revision=2,
            )
        )
        session.commit()
    attempt = IngestLedger(engine).start_attempt(
        tenant_id="tenant-1", dataset_id="dataset-1", document_id="doc-1"
    )
    queue = IndexOperationQueue(engine)
    return engine, queue, attempt


def enqueue(queue, attempt, *, revision: int):
    return queue.enqueue_operation(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        attempt_id=attempt.id,
        target_store="milvus_chunks",
        operation="upsert",
        dedup_key=f"worker:doc-1:{revision}",
        target_revision=revision,
        payload={"chunk_ids": ["chunk-1"]},
    )


def test_worker_retries_idempotent_handler_then_completes(tmp_path: Path) -> None:
    engine, queue, attempt = create_worker_state(tmp_path)
    operation = enqueue(queue, attempt, revision=2)
    calls: list[str] = []

    def flaky(item):
        calls.append(item.id)
        if len(calls) == 1:
            raise RuntimeError("temporary projection failure")

    worker = worker_module().IndexOperationWorker(
        queue, handlers={"milvus_chunks": flaky}, worker_id="worker-a"
    )
    now = datetime(2026, 8, 24, 10, 0, 0)

    first = worker.run_once(now=now)
    second = worker.run_once(now=now + timedelta(seconds=5))

    assert first.retried == 1
    assert second.succeeded == 1
    assert calls == [operation.id, operation.id]
    assert queue.get_operation(operation.id).status == "succeeded"
    engine.dispose()


def test_stale_operation_cannot_advance_document_projection_revision(tmp_path: Path) -> None:
    engine, queue, attempt = create_worker_state(tmp_path)
    stale = enqueue(queue, attempt, revision=1)
    current = enqueue(queue, attempt, revision=2)
    worker = worker_module().IndexOperationWorker(
        queue, handlers={"milvus_chunks": lambda item: None}, worker_id="worker-a"
    )
    now = datetime(2026, 8, 24, 10, 0, 0)

    worker.run_once(limit=1, now=now)
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        assert document.indexed_revision == 0
    assert queue.get_operation(stale.id).status == "succeeded"

    worker.run_once(limit=1, now=now)
    with Session(engine) as session:
        document = session.get(Document, "doc-1")
        assert document is not None
        assert document.indexed_revision == 2
    assert queue.get_operation(current.id).status == "succeeded"
    engine.dispose()


def test_stale_projection_operation_is_superseded_without_retry(tmp_path: Path) -> None:
    from indexing.projection_handlers import StaleProjectionOperation

    engine, queue, attempt = create_worker_state(tmp_path)
    operation = enqueue(queue, attempt, revision=2)

    def stale(_item):
        raise StaleProjectionOperation("stale chunk fence")

    worker = worker_module().IndexOperationWorker(
        queue, handlers={"milvus_chunks": stale}, worker_id="worker-a"
    )

    result = worker.run_once(limit=1)

    assert result.retried == 0
    assert result.dead == 0
    assert result.superseded == 1
    assert queue.get_operation(operation.id).status == "superseded"
    engine.dispose()


def test_rapid_edit_supersedes_old_ops_and_closes_old_attempt(tmp_path: Path) -> None:
    from indexing.projection_handlers import StaleProjectionOperation

    engine, queue, first_attempt = create_worker_state(tmp_path)
    first_vector = enqueue(queue, first_attempt, revision=2)
    first_graph = queue.enqueue_operation(
        tenant_id="tenant-1", dataset_id="dataset-1", document_id="doc-1",
        attempt_id=first_attempt.id, target_store="graph_projection", operation="upsert",
        dedup_key="worker:doc-1:2:graph", target_revision=2,
        payload={"chunk_ids": ["chunk-1"]},
    )
    second_attempt = IngestLedger(engine).start_attempt(
        tenant_id="tenant-1", dataset_id="dataset-1", document_id="doc-1"
    )
    current = queue.enqueue_operation(
        tenant_id="tenant-1", dataset_id="dataset-1", document_id="doc-1",
        attempt_id=second_attempt.id, target_store="milvus_chunks", operation="upsert",
        dedup_key="worker:doc-1:2:current", target_revision=2,
        payload={"chunk_ids": ["chunk-1"]},
    )

    def handler(item):
        if item.attempt_id == first_attempt.id:
            raise StaleProjectionOperation("rapid edit superseded")

    worker = worker_module().IndexOperationWorker(
        queue, handlers={"milvus_chunks": handler, "graph_projection": handler},
        worker_id="worker-a",
    )

    worker.run_once(limit=1)
    assert IngestLedger(engine).list_attempts("doc-1")[0].state == "running"
    worker.run_once(limit=1)
    attempts = IngestLedger(engine).list_attempts("doc-1")
    assert attempts[0].state == "superseded"
    assert attempts[0].finished_at is not None
    worker.run_once(limit=1)
    assert queue.get_operation(first_vector.id).status == "superseded"
    assert queue.get_operation(first_graph.id).status == "superseded"
    assert queue.get_operation(current.id).status == "succeeded"
    engine.dispose()


def test_stale_operation_and_attempt_close_roll_back_in_one_transaction(tmp_path: Path) -> None:
    from indexing.projection_handlers import StaleProjectionOperation

    engine, queue, attempt = create_worker_state(tmp_path)
    operation = enqueue(queue, attempt, revision=2)

    def stale(_item):
        raise StaleProjectionOperation("stale chunk fence")

    def fail_attempt_close(session: Session) -> None:
        if session.get_bind() is not engine:
            return
        if any(
            isinstance(item, DocumentIngestAttempt) and item.state == "superseded"
            for item in session.dirty
        ):
            raise RuntimeError("injected commit failure")

    worker = worker_module().IndexOperationWorker(
        queue, handlers={"milvus_chunks": stale}, worker_id="worker-a"
    )
    event.listen(Session, "before_commit", fail_attempt_close)
    try:
        with pytest.raises(RuntimeError, match="injected commit failure"):
            worker.run_once(limit=1)
    finally:
        event.remove(Session, "before_commit", fail_attempt_close)

    assert queue.get_operation(operation.id).status == "claimed"
    refreshed_attempt = IngestLedger(engine).list_attempts("doc-1")[0]
    assert refreshed_attempt.state == "running"
    assert refreshed_attempt.finished_at is None
    engine.dispose()



def test_worker_heartbeats_long_handler_so_expired_operation_is_not_reclaimed(
    tmp_path: Path,
) -> None:
    engine, queue, attempt = create_worker_state(tmp_path)
    operation = enqueue(queue, attempt, revision=2)
    handler_started = threading.Event()
    release_handler = threading.Event()
    calls: list[str] = []
    first_results = []
    first_errors: list[Exception] = []

    def slow_handler(_item):
        calls.append(threading.current_thread().name)
        handler_started.set()
        assert release_handler.wait(timeout=5)

    first = worker_module().IndexOperationWorker(
        queue,
        handlers={"milvus_chunks": slow_handler},
        worker_id="worker-a",
        lease_seconds=1,
    )
    second = worker_module().IndexOperationWorker(
        queue,
        handlers={"milvus_chunks": slow_handler},
        worker_id="worker-b",
        lease_seconds=1,
    )

    def run_first() -> None:
        try:
            first_results.append(first.run_once(limit=1))
        except Exception as exc:  # pragma: no cover - asserted below
            first_errors.append(exc)

    thread = threading.Thread(target=run_first, name="worker-a-thread")
    thread.start()
    assert handler_started.wait(timeout=3)
    time.sleep(1.2)

    second_result = second.run_once(limit=1)
    release_handler.set()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert first_errors == []
    assert second_result.claimed == 0
    assert len(calls) == 1
    assert first_results[0].succeeded == 1
    assert queue.get_operation(operation.id).status == "succeeded"
    engine.dispose()


@pytest.mark.parametrize("outcome", ["complete", "retry", "supersede"])
def test_worker_treats_lease_ownership_transition_conflict_as_taken_over(
    tmp_path: Path,
    outcome: str,
) -> None:
    from indexing.projection_handlers import StaleProjectionOperation

    engine, queue, attempt = create_worker_state(tmp_path)
    operation = enqueue(queue, attempt, revision=2)

    def transfer_ownership() -> None:
        with Session(engine) as session:
            item = session.get(type(operation), operation.id)
            assert item is not None
            item.claimed_by = "worker-b"
            session.commit()

    def handler(_item):
        transfer_ownership()
        if outcome == "retry":
            raise RuntimeError("handler failed after lease takeover")
        if outcome == "supersede":
            raise StaleProjectionOperation("stale after lease takeover")

    worker = worker_module().IndexOperationWorker(
        queue,
        handlers={"milvus_chunks": handler},
        worker_id="worker-a",
        lease_seconds=30,
    )

    try:
        result = worker.run_once(limit=1)
    except IndexOperationConflict as exc:  # pragma: no cover - regression signal
        pytest.fail(f"lease takeover escaped run_once: {exc}")

    refreshed = queue.get_operation(operation.id)
    assert refreshed.status == "claimed"
    assert refreshed.claimed_by == "worker-b"
    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.retried == 0
    assert result.dead == 0
    assert result.superseded == 0
    engine.dispose()



def test_run_once_claims_only_one_operation_while_another_worker_progresses(
    tmp_path: Path,
) -> None:
    engine, queue, attempt = create_worker_state(tmp_path)
    first_operation = enqueue(queue, attempt, revision=2)
    second_operation = queue.enqueue_operation(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        document_id="doc-1",
        attempt_id=attempt.id,
        target_store="milvus_chunks",
        operation="upsert",
        dedup_key="worker:doc-1:2:second",
        target_revision=2,
        payload={"chunk_ids": ["chunk-2"]},
    )
    with Session(engine) as session:
        first_row = session.get(type(first_operation), first_operation.id)
        second_row = session.get(type(second_operation), second_operation.id)
        assert first_row is not None and second_row is not None
        first_row.created_at = datetime(2026, 8, 24, 10, 0, 0)
        second_row.created_at = datetime(2026, 8, 24, 10, 0, 1)
        session.commit()

    first_started = threading.Event()
    release_first = threading.Event()
    calls: list[str] = []
    first_results = []
    first_errors: list[Exception] = []

    def handler(item) -> None:
        calls.append(item.id)
        if item.id == first_operation.id:
            first_started.set()
            assert release_first.wait(timeout=5)

    first_worker = worker_module().IndexOperationWorker(
        queue,
        handlers={"milvus_chunks": handler},
        worker_id="worker-a",
        lease_seconds=1,
    )
    second_worker = worker_module().IndexOperationWorker(
        queue,
        handlers={"milvus_chunks": handler},
        worker_id="worker-b",
        lease_seconds=1,
    )

    def run_first() -> None:
        try:
            first_results.append(first_worker.run_once(limit=8))
        except Exception as exc:  # pragma: no cover - asserted below
            first_errors.append(exc)

    thread = threading.Thread(target=run_first, name="worker-a-prefetch")
    thread.start()
    assert first_started.wait(timeout=3)
    time.sleep(1.2)

    second_result = second_worker.run_once(limit=8)
    release_first.set()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert first_errors == []
    assert first_results[0].claimed == 1
    assert first_results[0].succeeded == 1
    assert second_result.claimed == 1
    assert second_result.succeeded == 1
    assert calls.count(first_operation.id) == 1
    assert calls.count(second_operation.id) == 1
    assert queue.get_operation(first_operation.id).status == "succeeded"
    assert queue.get_operation(second_operation.id).status == "succeeded"
    engine.dispose()


def test_heartbeat_remains_active_while_projection_revision_advance_is_slow(
    tmp_path: Path,
) -> None:
    engine, queue, attempt = create_worker_state(tmp_path)
    operation = enqueue(queue, attempt, revision=2)
    advance_started = threading.Event()
    release_advance = threading.Event()
    calls: list[str] = []
    first_results = []
    first_errors: list[Exception] = []

    def handler(item) -> None:
        calls.append(item.id)

    worker_class = worker_module().IndexOperationWorker

    class SlowAdvanceWorker(worker_class):
        def _advance_projection_revision(self, item) -> None:
            advance_started.set()
            assert release_advance.wait(timeout=5)
            super()._advance_projection_revision(item)

    first_worker = SlowAdvanceWorker(
        queue,
        handlers={"milvus_chunks": handler},
        worker_id="worker-a",
        lease_seconds=1,
    )
    second_worker = worker_class(
        queue,
        handlers={"milvus_chunks": handler},
        worker_id="worker-b",
        lease_seconds=1,
    )

    def run_first() -> None:
        try:
            first_results.append(first_worker.run_once(limit=1))
        except Exception as exc:  # pragma: no cover - asserted below
            first_errors.append(exc)

    thread = threading.Thread(target=run_first, name="worker-a-slow-advance")
    thread.start()
    assert advance_started.wait(timeout=3)
    time.sleep(1.2)

    second_result = second_worker.run_once(limit=1)
    release_advance.set()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert first_errors == []
    assert second_result.claimed == 0
    assert calls == [operation.id]
    assert first_results[0].succeeded == 1
    assert queue.get_operation(operation.id).status == "succeeded"
    engine.dispose()


def test_sync_ownership_fence_failure_never_invokes_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, queue, attempt = create_worker_state(tmp_path)
    operation = enqueue(queue, attempt, revision=2)
    calls: list[str] = []

    def reject_renewal(*_args, **_kwargs):
        raise IndexOperationConflict("operation ownership was transferred")

    monkeypatch.setattr(queue, "renew_lease", reject_renewal)
    worker = worker_module().IndexOperationWorker(
        queue,
        handlers={"milvus_chunks": lambda item: calls.append(item.id)},
        worker_id="worker-a",
        lease_seconds=30,
    )

    result = worker.run_once(limit=1)

    assert result.claimed == 1
    assert result.succeeded == 0
    assert result.retried == 0
    assert result.superseded == 0
    assert calls == []
    refreshed = queue.get_operation(operation.id)
    assert refreshed.status == "claimed"
    assert refreshed.claimed_by == "worker-a"
    engine.dispose()
