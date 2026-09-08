from __future__ import annotations

import threading
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from core.catalog_schema import upgrade_catalog
from core.db_clock import read_db_utc
from core.knowledge_governance import AuditContext
from core.source_schedules import SourceScheduleRepository
from core.source_sync_ledger import SourceSyncLedger
from models.orm import DataSourceRecord, Dataset, SourceSchedule, SourceSyncRun, Tenant
from server import source_dispatcher
from server.source_dispatcher import SourceDispatchRuntime


def _authority(tmp_path: Path) -> tuple[Any, SourceSyncLedger, SourceScheduleRepository]:
    url = f"sqlite:///{(tmp_path / 'schedule-dispatch.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant"))
        session.add(Dataset(id="dataset-1", tenant_id="tenant-1", name="Dataset"))
        session.add(
            DataSourceRecord(
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
                status="active",
            )
        )
        session.commit()
    return engine, SourceSyncLedger(engine), SourceScheduleRepository(engine)


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


def _runtime(app: Any) -> SourceDispatchRuntime:
    return SourceDispatchRuntime(
        app,
        poll_interval_seconds=0.05,
        lease_seconds=2,
        heartbeat_seconds=0.1,
        max_workers=1,
        batch_size=10,
        reservation_seconds=0.2,
        shutdown_grace_seconds=1,
    )


def _due_schedule(engine: Any, repository: SourceScheduleRepository) -> SourceSchedule:
    schedule = repository.put(
        "tenant-1",
        "dataset-1",
        "source-1",
        expected_revision=0,
        interval_seconds=300,
        force_full=False,
        status="active",
        audit=AuditContext.system("operator", "create-schedule"),
    )
    with Session(engine) as session:
        now = read_db_utc(session)
        row = session.get(SourceSchedule, schedule.id)
        row.next_run_at = now - timedelta(seconds=5)
        session.commit()
    return schedule


def _wait_completed(ledger: SourceSyncLedger, run_id: str) -> SourceSyncRun:
    deadline = time.time() + 3
    while time.time() < deadline:
        run = ledger.get_run_scoped("tenant-1", "dataset-1", "source-1", run_id)
        if run.execution_state == "completed":
            return run
        time.sleep(0.02)
    return ledger.get_run_scoped("tenant-1", "dataset-1", "source-1", run_id)


def test_poll_cycle_asks_schedule_repository_before_pending_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    class FakeSchedules:
        def __init__(self, _engine) -> None:
            pass

        def enqueue_due(self, *, limit: int):
            assert limit == 10
            order.append("schedules")
            return []

    class FakeLedger:
        def __init__(self, _engine) -> None:
            pass

        def reserve_dispatch_batch(self, *, owner: str, lease_seconds: float, limit: int):
            assert owner and lease_seconds == 0.2 and limit == 10
            order.append("dispatch")
            return []

    monkeypatch.setattr(source_dispatcher, "SourceScheduleRepository", FakeSchedules)
    monkeypatch.setattr(source_dispatcher, "SourceSyncLedger", FakeLedger)
    runtime = _runtime(_app(object(), lambda _spec, _run_id: None))
    try:
        runtime._poll_once()
    finally:
        runtime.stop()
    assert order == ["schedules", "dispatch"]


def test_schedule_enqueue_failure_does_not_block_existing_pending_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    class FailingSchedules:
        def __init__(self, _engine) -> None:
            pass

        def enqueue_due(self, *, limit: int):
            order.append("schedules")
            raise RuntimeError("schedule store unavailable")

    class FakeLedger:
        def __init__(self, _engine) -> None:
            pass

        def reserve_dispatch_batch(self, *, owner: str, lease_seconds: float, limit: int):
            order.append("dispatch")
            return []

    monkeypatch.setattr(source_dispatcher, "SourceScheduleRepository", FailingSchedules)
    monkeypatch.setattr(source_dispatcher, "SourceSyncLedger", FakeLedger)
    runtime = _runtime(_app(object(), lambda _spec, _run_id: None))
    try:
        runtime._poll_once()
    finally:
        runtime.stop()
    assert order == ["schedules", "dispatch"]


def test_poll_cycle_enqueues_due_schedule_then_executes_normal_outbox(tmp_path: Path) -> None:
    engine, ledger, schedules = _authority(tmp_path)
    schedule = _due_schedule(engine, schedules)
    called = threading.Event()
    app = _app(engine, lambda _spec, _run_id: called.set())
    runtime = _runtime(app)
    try:
        runtime._poll_once()
        assert called.wait(timeout=3)
        with Session(engine) as session:
            runs = list(
                session.scalars(
                    select(SourceSyncRun).where(SourceSyncRun.trigger == "scheduled")
                )
            )
        assert len(runs) == 1
        completed = _wait_completed(ledger, runs[0].id)
        assert completed.execution_state == "completed"
        runtime._poll_once()
        with Session(engine) as session:
            assert session.scalar(
                select(func.count()).select_from(SourceSyncRun).where(
                    SourceSyncRun.trigger == "scheduled"
                )
            ) == 1
            refreshed = session.get(SourceSchedule, schedule.id)
            assert refreshed.last_run_id == completed.id
    finally:
        runtime.stop()
        engine.dispose()


def test_submit_crash_after_schedule_commit_is_recovered_without_duplicate_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, ledger, schedules = _authority(tmp_path)
    _due_schedule(engine, schedules)
    first_runtime = _runtime(_app(engine, lambda _spec, _run_id: None))

    def fail_submit(_callback):
        raise RuntimeError("process died before worker submit")

    monkeypatch.setattr(first_runtime._executor, "submit", fail_submit)
    first_runtime._poll_once()
    with Session(engine) as session:
        run = session.scalar(select(SourceSyncRun).where(SourceSyncRun.trigger == "scheduled"))
        assert run is not None
        assert run.execution_state == "pending"
        assert run.reservation_owner
        now = read_db_utc(session)
        run.reservation_lease_until = now - timedelta(seconds=1)
        session.commit()
        run_id = run.id
    first_runtime.stop()

    recovered = threading.Event()
    second_runtime = _runtime(_app(engine, lambda _spec, _run_id: recovered.set()))
    try:
        second_runtime._poll_once()
        assert recovered.wait(timeout=3)
        assert _wait_completed(ledger, run_id).execution_state == "completed"
        with Session(engine) as session:
            assert session.scalar(
                select(func.count()).select_from(SourceSyncRun).where(
                    SourceSyncRun.trigger == "scheduled"
                )
            ) == 1
    finally:
        second_runtime.stop()
        engine.dispose()


@pytest.mark.parametrize(
    "blocked",
    ["paused", "archived", "revision", "source", "dataset", "source_generation", "dataset_generation"],
)
def test_committed_scheduled_run_is_fenced_if_authority_becomes_inactive_before_claim(
    tmp_path: Path,
    blocked: str,
) -> None:
    engine, ledger, schedules = _authority(tmp_path)
    schedule = _due_schedule(engine, schedules)
    queued = schedules.enqueue_due(limit=1)[0]
    called = threading.Event()
    with Session(engine) as session:
        if blocked in {"paused", "archived"}:
            session.get(SourceSchedule, schedule.id).status = blocked
        elif blocked == "revision":
            session.get(SourceSchedule, schedule.id).revision += 1
        elif blocked == "source":
            session.get(DataSourceRecord, "source-1").status = "disabled"
        elif blocked == "dataset":
            session.get(Dataset, "dataset-1").status = "disabled"
        elif blocked == "source_generation":
            session.get(DataSourceRecord, "source-1").mutation_generation += 1
        else:
            session.get(Dataset, "dataset-1").mutation_generation += 1
        session.commit()

    runtime = _runtime(_app(engine, lambda _spec, _run_id: called.set()))
    try:
        runtime._poll_once()
        time.sleep(0.1)
        assert not called.is_set()
        persisted = ledger.get_run_scoped(
            "tenant-1", "dataset-1", "source-1", queued.run_id
        )
        assert persisted.status == "superseded"
        assert persisted.execution_state == "completed"
    finally:
        runtime.stop()
        engine.dispose()
