from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from core.knowledge_governance import AuditContext
from core.source_sync_ledger import SourceSyncLedger, SourceSyncLedgerConflict
from models.orm import DataSourceRecord, Dataset, SourceSyncRun, Tenant
from server.source_dispatcher import SourceDispatchRuntime, execute_source_run
from sources.runner import SourceSpec


def _ledger(tmp_path: Path) -> tuple[Any, SourceSyncLedger, DataSourceRecord]:
    from core.catalog_schema import upgrade_catalog

    url = f"sqlite:///{(tmp_path / 'dispatch-runtime.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url, connect_args={"check_same_thread": False})
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant"))
        session.add(Dataset(id="dataset-1", tenant_id="tenant-1", name="Dataset"))
        source = DataSourceRecord(
            id="source-1",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            name="Source",
            source_type="local_dir",
            effective_config={
                "type": "local_dir",
                "dataset_id": "dataset-1",
                "params": {"path": str(tmp_path)},
                "metadata": {},
            },
        )
        session.add(source)
        session.commit()
    return engine, SourceSyncLedger(engine), source


def _run(ledger: SourceSyncLedger) -> SourceSyncRun:
    return ledger.request_run(
        "tenant-1",
        "dataset-1",
        "source-1",
        trigger="manual",
        force_full=False,
        dry_run=False,
        audit=AuditContext.system("operator", "request"),
        idempotency_key="Execution-Lease-Key-0001",
        request_hash="a" * 64,
    ).run


def _app(engine: Any, executor: Any) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            knowledge_auth_engine=engine,
            knowledge_source_sync_executor=executor,
            knowledge_auth_settings=SimpleNamespace(
                sources=SimpleNamespace(cache_dir=".rag4c_cache/sources")
            ),
        )
    )


def test_task_closure_claims_execution_only_when_it_starts(tmp_path: Path) -> None:
    engine, ledger, _ = _ledger(tmp_path)
    run = _run(ledger)
    called: list[str] = []
    app = _app(engine, lambda _spec, run_id: called.append(run_id))
    task = execute_source_run(app, run.id, lease_seconds=2, heartbeat_seconds=0.1)

    assert ledger.get_run_scoped("tenant-1", "dataset-1", "source-1", run.id).execution_state == (
        "pending"
    )
    task()
    persisted = ledger.get_run_scoped("tenant-1", "dataset-1", "source-1", run.id)
    assert called == [run.id]
    assert persisted.status == "completed"
    assert persisted.execution_state == "completed"
    assert persisted.execution_attempts == 1
    engine.dispose()


def test_process_death_during_execution_expires_and_is_reclaimable(tmp_path: Path) -> None:
    engine, ledger, _ = _ledger(tmp_path)
    run = _run(ledger)

    class ProcessDied(BaseException):
        pass

    app = _app(engine, lambda _spec, _run_id: (_ for _ in ()).throw(ProcessDied()))
    task = execute_source_run(app, run.id, lease_seconds=1, heartbeat_seconds=0.5)
    try:
        task()
    except ProcessDied:
        pass
    persisted = ledger.get_run_scoped("tenant-1", "dataset-1", "source-1", run.id)
    assert persisted.execution_state == "executing"
    owner = persisted.execution_owner
    assert owner

    reclaimed = ledger.claim_execution(
        run.id,
        owner="reconciler",
        now=persisted.execution_lease_until + timedelta(microseconds=1),
        lease_until=persisted.execution_lease_until + timedelta(seconds=2),
    )
    assert reclaimed is not None
    assert reclaimed.execution_owner == "reconciler"
    assert reclaimed.execution_attempts == 2
    engine.dispose()


def test_heartbeat_prevents_concurrent_reclaim_of_live_slow_worker(tmp_path: Path) -> None:
    engine, ledger, _ = _ledger(tmp_path)
    run = _run(ledger)
    entered = threading.Event()
    release = threading.Event()

    def slow(_spec: SourceSpec, _run_id: str) -> None:
        entered.set()
        release.wait(timeout=5)

    task = execute_source_run(_app(engine, slow), run.id, lease_seconds=1, heartbeat_seconds=0.1)
    thread = threading.Thread(target=task)
    thread.start()
    assert entered.wait(timeout=2)
    time.sleep(1.3)
    persisted = ledger.get_run_scoped("tenant-1", "dataset-1", "source-1", run.id)
    assert persisted.execution_state == "executing"
    assert persisted.execution_heartbeat_at is not None
    assert ledger.claim_execution(
        run.id,
        owner="duplicate",
        now=datetime.utcnow(),
        lease_until=datetime.utcnow() + timedelta(seconds=1),
    ) is None
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    engine.dispose()


def test_only_current_execution_owner_can_complete_or_fail(tmp_path: Path) -> None:
    engine, ledger, _ = _ledger(tmp_path)
    run = _run(ledger)
    now = datetime.utcnow()
    ledger.claim_execution(
        run.id,
        owner="owner-a",
        now=now,
        lease_until=now + timedelta(seconds=30),
    )

    try:
        ledger.finish_run(
            run.id,
            status="completed",
            cursor_after={},
            counts={},
            execution_owner="owner-b",
        )
    except SourceSyncLedgerConflict:
        pass
    else:
        raise AssertionError("non-owner completion must fail")
    try:
        ledger.mark_execution_failed(
            run.id,
            owner="owner-b",
            error="not owner",
            now=now,
        )
    except SourceSyncLedgerConflict:
        pass
    else:
        raise AssertionError("non-owner failure must fail")

    completed = ledger.finish_run(
        run.id,
        status="completed",
        cursor_after={},
        counts={},
        execution_owner="owner-a",
    )
    assert completed.execution_state == "completed"
    engine.dispose()


def test_periodic_runtime_recovers_unattended_pending_work(tmp_path: Path) -> None:
    engine, ledger, _ = _ledger(tmp_path)
    run = _run(ledger)
    called = threading.Event()
    app = _app(engine, lambda _spec, _run_id: called.set())
    runtime = SourceDispatchRuntime(
        app,
        poll_interval_seconds=0.05,
        lease_seconds=2,
        heartbeat_seconds=0.1,
        max_workers=1,
        batch_size=10,
    )
    runtime.start()
    try:
        assert called.wait(timeout=3)
        deadline = time.time() + 3
        while time.time() < deadline:
            persisted = ledger.get_run_scoped("tenant-1", "dataset-1", "source-1", run.id)
            if persisted.execution_state == "completed":
                break
            time.sleep(0.02)
        assert persisted.execution_state == "completed"
    finally:
        runtime.stop()
    engine.dispose()



def test_heartbeat_failure_signals_guard_before_lease_expiry_and_old_worker_cannot_finish(
    tmp_path: Path, monkeypatch
) -> None:
    engine, ledger, _ = _ledger(tmp_path)
    run = _run(ledger)
    entered = threading.Event()
    release = threading.Event()

    def slow(_spec: SourceSpec, _run_id: str) -> None:
        entered.set()
        release.wait(timeout=3)

    original_heartbeat = SourceSyncLedger.heartbeat_execution
    monkeypatch.setattr(
        SourceSyncLedger,
        "heartbeat_execution",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db transient")),
    )
    task = execute_source_run(_app(engine, slow), run.id, lease_seconds=0.5, heartbeat_seconds=0.1)
    thread = threading.Thread(target=task)
    thread.start()
    assert entered.wait(timeout=1)
    time.sleep(0.65)
    monkeypatch.setattr(SourceSyncLedger, "heartbeat_execution", original_heartbeat)
    persisted = ledger.get_run(run.id)
    reclaimed = ledger.claim_execution(
        run.id,
        owner="new-owner",
        now=datetime.utcnow(),
        lease_until=datetime.utcnow() + timedelta(seconds=1),
    )
    assert reclaimed is not None
    release.set()
    thread.join(timeout=3)
    persisted = ledger.get_run(run.id)
    assert persisted.status == "running"
    assert persisted.execution_owner == "new-owner"
    engine.dispose()


def test_failure_backoff_bounds_attempt_storm(tmp_path: Path) -> None:
    engine, ledger, _ = _ledger(tmp_path)
    run = _run(ledger)
    app = _app(engine, lambda _spec, _run_id: (_ for _ in ()).throw(RuntimeError("boom")))
    app.state.knowledge_auth_settings.sources.execution_retry_base_seconds = 0.2
    app.state.knowledge_auth_settings.sources.execution_retry_max_seconds = 1.0
    app.state.knowledge_auth_settings.sources.execution_retry_jitter_ratio = 0.1
    app.state.knowledge_auth_settings.sources.execution_max_attempts = 10
    runtime = SourceDispatchRuntime(
        app,
        poll_interval_seconds=0.01,
        lease_seconds=0.4,
        heartbeat_seconds=0.1,
        max_workers=1,
        batch_size=10,
        reservation_seconds=0.1,
        shutdown_grace_seconds=0.5,
    )
    runtime.start()
    try:
        time.sleep(0.5)
    finally:
        runtime.stop()
    persisted = ledger.get_run(run.id)
    assert persisted.execution_state == "failed"
    assert 1 <= persisted.execution_attempts <= 3
    assert persisted.execution_next_attempt_at > persisted.execution_finished_at
    engine.dispose()


def test_durable_reservation_prevents_cross_replica_duplicate_enqueue(tmp_path: Path) -> None:
    engine, ledger, _ = _ledger(tmp_path)
    run = _run(ledger)
    now = datetime.utcnow()
    first = ledger.reserve_dispatch_batch(
        owner="replica-a",
        now=now,
        lease_until=now + timedelta(seconds=1),
        limit=10,
    )
    second = ledger.reserve_dispatch_batch(
        owner="replica-b",
        now=now,
        lease_until=now + timedelta(seconds=1),
        limit=10,
    )
    assert first == [run.id]
    assert second == []
    recovered = ledger.reserve_dispatch_batch(
        owner="replica-b",
        now=now + timedelta(seconds=2),
        lease_until=now + timedelta(seconds=3),
        limit=10,
    )
    assert recovered == [run.id]
    engine.dispose()


def test_bounded_shutdown_returns_before_hung_work_and_stops_heartbeat(tmp_path: Path) -> None:
    engine, ledger, _ = _ledger(tmp_path)
    run = _run(ledger)
    entered = threading.Event()
    release = threading.Event()

    def hung(_spec: SourceSpec, _run_id: str) -> None:
        entered.set()
        release.wait(timeout=5)

    runtime = SourceDispatchRuntime(
        _app(engine, hung),
        poll_interval_seconds=0.01,
        lease_seconds=1,
        heartbeat_seconds=0.2,
        max_workers=1,
        batch_size=10,
        reservation_seconds=0.1,
        shutdown_grace_seconds=0.2,
    )
    runtime.start()
    assert entered.wait(timeout=2)
    started = time.monotonic()
    runtime.stop()
    assert time.monotonic() - started < 0.8
    release.set()
    time.sleep(0.1)
    persisted = ledger.get_run(run.id)
    assert persisted.status == "running"
    engine.dispose()



def test_daemon_worker_queue_does_not_block_subprocess_exit() -> None:
    code = """
import time
from server.source_dispatcher import _DaemonWorkerPool
pool = _DaemonWorkerPool(1, 1)
pool.submit(lambda: time.sleep(60))
time.sleep(0.1)
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        timeout=3,
        check=False,
    )
    assert completed.returncode == 0
