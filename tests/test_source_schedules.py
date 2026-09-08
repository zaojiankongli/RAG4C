from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import threading

import pytest
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import Session

from core.catalog_schema import upgrade_catalog
from core.db_clock import read_db_utc
from core.knowledge_governance import AuditContext
from core.source_sync_ledger import SourceSyncLedger
from models.orm import (
    DataSourceRecord,
    Dataset,
    KnowledgeAuditEvent,
    SourceSchedule,
    SourceSyncRun,
    Tenant,
)


def _authority(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'source-schedules.db').as_posix()}"
    upgrade_catalog(url)
    engine = create_engine(url, connect_args={"timeout": 30})
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant", plan="enterprise"))
        session.add(Dataset(id="dataset-1", tenant_id="tenant-1", name="Dataset"))
        session.add(
            DataSourceRecord(
                id="source-1",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
                name="Source",
                source_type="local_dir",
                status="active",
                last_cursor={"cursor": "before"},
            )
        )
        session.commit()
    from core.source_schedules import SourceScheduleRepository

    return engine, SourceScheduleRepository(engine)


def _audit(request_id: str = "request-1") -> AuditContext:
    return AuditContext(actor_id="operator-1", request_id=request_id, request_ip="127.0.0.1")


def _make_due(engine, schedule_id: str, *, intervals_ago: int = 1):
    with Session(engine) as session:
        schedule = session.get(SourceSchedule, schedule_id)
        assert schedule is not None
        now = read_db_utc(session)
        planned = now - timedelta(seconds=schedule.interval_seconds * intervals_ago + 5)
        schedule.next_run_at = planned
        session.commit()
        return planned


def test_schedule_put_uses_positive_revision_cas_and_database_time(tmp_path: Path) -> None:
    from core.source_schedules import SourceScheduleConflict

    engine, repository = _authority(tmp_path)
    with Session(engine) as session:
        before = read_db_utc(session)
    created = repository.put(
        "tenant-1",
        "dataset-1",
        "source-1",
        expected_revision=0,
        interval_seconds=300,
        force_full=False,
        status=None,
        audit=_audit(),
    )
    with Session(engine) as session:
        after = read_db_utc(session)
    assert created.revision == 1
    assert created.status == "active"
    assert created.next_run_at > before + timedelta(seconds=299)
    assert created.next_run_at <= after + timedelta(seconds=300)
    assert created.created_at == created.updated_at

    with pytest.raises(SourceScheduleConflict, match="revision"):
        repository.put(
            "tenant-1", "dataset-1", "source-1",
            expected_revision=0, interval_seconds=600, force_full=True,
            status="paused", audit=_audit("request-stale-create"),
        )

    updated = repository.put(
        "tenant-1", "dataset-1", "source-1",
        expected_revision=1, interval_seconds=600, force_full=True,
        status="paused", audit=_audit("request-update"),
    )
    assert updated.id == created.id
    assert updated.revision == 2
    assert updated.status == "paused"
    assert updated.interval_seconds == 600
    assert updated.force_full is True
    assert updated.next_run_at > created.next_run_at

    with pytest.raises(SourceScheduleConflict, match="revision"):
        repository.put(
            "tenant-1", "dataset-1", "source-1",
            expected_revision=1, interval_seconds=900, force_full=False,
            status="active", audit=_audit("request-stale-update"),
        )

    with Session(engine) as session:
        actions = list(
            session.scalars(
                select(KnowledgeAuditEvent.action).where(
                    KnowledgeAuditEvent.resource_id == created.id
                ).order_by(KnowledgeAuditEvent.sequence)
            )
        )
    assert actions == ["source.schedule.created", "source.schedule.updated"]
    engine.dispose()


def test_schedule_lifecycle_is_revisioned_and_archived_fact_can_be_reconfigured(
    tmp_path: Path,
) -> None:
    from core.source_schedules import SourceScheduleConflict

    engine, repository = _authority(tmp_path)
    schedule = repository.put(
        "tenant-1", "dataset-1", "source-1", expected_revision=0,
        interval_seconds=300, force_full=False, status="active", audit=_audit(),
    )
    paused = repository.pause(
        "tenant-1", "dataset-1", "source-1",
        expected_revision=schedule.revision, audit=_audit("pause"),
    )
    assert (paused.status, paused.revision) == ("paused", 2)
    with pytest.raises(SourceScheduleConflict, match="active"):
        repository.pause(
            "tenant-1", "dataset-1", "source-1",
            expected_revision=paused.revision, audit=_audit("pause-again"),
        )
    resumed = repository.resume(
        "tenant-1", "dataset-1", "source-1",
        expected_revision=paused.revision, audit=_audit("resume"),
    )
    assert (resumed.status, resumed.revision) == ("active", 3)
    archived = repository.archive(
        "tenant-1", "dataset-1", "source-1",
        expected_revision=resumed.revision, audit=_audit("archive"),
    )
    assert (archived.status, archived.revision) == ("archived", 4)
    with pytest.raises(SourceScheduleConflict, match="paused"):
        repository.resume(
            "tenant-1", "dataset-1", "source-1",
            expected_revision=archived.revision, audit=_audit("resume-archived"),
        )

    reconfigured = repository.put(
        "tenant-1", "dataset-1", "source-1",
        expected_revision=archived.revision, interval_seconds=900,
        force_full=True, status="active", audit=_audit("reactivate"),
    )
    assert reconfigured.id == schedule.id
    assert (reconfigured.status, reconfigured.revision) == ("active", 5)
    engine.dispose()


def test_schedule_scope_validation_and_inactive_gates(tmp_path: Path) -> None:
    from core.source_schedules import SourceScheduleConflict, SourceScheduleNotFound

    engine, repository = _authority(tmp_path)
    with pytest.raises(SourceScheduleNotFound):
        repository.get_scoped("tenant-2", "dataset-1", "source-1")
    with pytest.raises(ValueError, match="interval_seconds"):
        repository.put(
            "tenant-1", "dataset-1", "source-1", expected_revision=0,
            interval_seconds=299, force_full=False, status=None, audit=_audit(),
        )

    with Session(engine) as session:
        session.get(DataSourceRecord, "source-1").status = "disabled"
        session.commit()
    with pytest.raises(SourceScheduleConflict, match="active"):
        repository.put(
            "tenant-1", "dataset-1", "source-1", expected_revision=0,
            interval_seconds=300, force_full=False, status=None, audit=_audit("inactive"),
        )

    with Session(engine) as session:
        session.get(DataSourceRecord, "source-1").status = "active"
        session.get(Dataset, "dataset-1").status = "disabled"
        session.commit()
    with pytest.raises(SourceScheduleConflict, match="active"):
        repository.put(
            "tenant-1", "dataset-1", "source-1", expected_revision=0,
            interval_seconds=300, force_full=False, status=None,
            audit=_audit("inactive-dataset"),
        )
    engine.dispose()


def test_schedule_write_and_audit_are_atomic(tmp_path: Path) -> None:
    engine, repository = _authority(tmp_path)

    def fail_audit(_mapper, _connection, target) -> None:
        if target.action == "source.schedule.created":
            raise RuntimeError("audit unavailable")

    event.listen(KnowledgeAuditEvent, "before_insert", fail_audit)
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            repository.put(
                "tenant-1", "dataset-1", "source-1", expected_revision=0,
                interval_seconds=300, force_full=False, status=None, audit=_audit(),
            )
    finally:
        event.remove(KnowledgeAuditEvent, "before_insert", fail_audit)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SourceSchedule)) == 0
        assert session.scalar(select(func.count()).select_from(KnowledgeAuditEvent)) == 0
    engine.dispose()


def test_due_schedule_coalesces_missed_windows_and_is_restart_recoverable(
    tmp_path: Path,
) -> None:
    engine, repository = _authority(tmp_path)
    schedule = repository.put(
        "tenant-1", "dataset-1", "source-1", expected_revision=0,
        interval_seconds=300, force_full=True, status="active", audit=_audit(),
    )
    planned = _make_due(engine, schedule.id, intervals_ago=4)

    enqueued = repository.enqueue_due(limit=10)
    assert len(enqueued) == 1
    result = enqueued[0]
    assert result.schedule_id == schedule.id
    assert result.planned_at == planned

    with Session(engine) as session:
        now = read_db_utc(session)
        refreshed = session.get(SourceSchedule, schedule.id)
        run = session.get(SourceSyncRun, result.run_id)
        assert refreshed is not None and run is not None
        assert refreshed.next_run_at > now
        assert refreshed.next_run_at <= now + timedelta(seconds=300)
        assert refreshed.last_enqueued_at is not None
        assert refreshed.last_run_id == run.id
        assert run.trigger == "scheduled"
        assert run.schedule_id == schedule.id
        assert run.schedule_revision == schedule.revision
        assert run.planned_at == planned
        assert run.status == "running"
        assert run.execution_state == "pending"
        assert run.force_full == 1
        assert run.dry_run == 0
        assert run.source_generation == 0
        assert run.dataset_generation == 0
        assert run.idempotency_key == result.idempotency_key
        assert run.request_hash == result.request_hash
        assert planned.strftime("%Y%m%dT%H%M%S.%f") in run.idempotency_key

    restarted = type(repository)(engine)
    assert restarted.enqueue_due(limit=10) == []
    assert SourceSyncLedger(engine).list_dispatchable_run_ids(limit=10) == [result.run_id]
    engine.dispose()


def test_concurrent_due_claims_create_exactly_one_run_for_planned_slot(tmp_path: Path) -> None:
    engine, repository = _authority(tmp_path)
    schedule = repository.put(
        "tenant-1", "dataset-1", "source-1", expected_revision=0,
        interval_seconds=300, force_full=False, status="active", audit=_audit(),
    )
    _make_due(engine, schedule.id)
    barrier = threading.Barrier(2)

    def poll():
        barrier.wait(timeout=5)
        return type(repository)(engine).enqueue_due(limit=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        batches = [future.result(timeout=10) for future in (pool.submit(poll), pool.submit(poll))]
    created = [item for batch in batches for item in batch]
    assert len(created) == 1
    with Session(engine) as session:
        assert session.scalar(
            select(func.count()).select_from(SourceSyncRun).where(
                SourceSyncRun.trigger == "scheduled"
            )
        ) == 1
    engine.dispose()


def test_due_enqueue_rolls_back_schedule_advance_when_run_or_audit_fails(tmp_path: Path) -> None:
    engine, repository = _authority(tmp_path)
    schedule = repository.put(
        "tenant-1", "dataset-1", "source-1", expected_revision=0,
        interval_seconds=300, force_full=False, status="active", audit=_audit(),
    )
    _make_due(engine, schedule.id)
    with Session(engine) as session:
        before = session.get(SourceSchedule, schedule.id).next_run_at

    def fail_run(_mapper, _connection, target) -> None:
        if target.trigger == "scheduled":
            raise RuntimeError("run insert unavailable")

    event.listen(SourceSyncRun, "before_insert", fail_run)
    try:
        with pytest.raises(RuntimeError, match="run insert unavailable"):
            repository.enqueue_due(limit=1)
    finally:
        event.remove(SourceSyncRun, "before_insert", fail_run)
    with Session(engine) as session:
        refreshed = session.get(SourceSchedule, schedule.id)
        assert refreshed.next_run_at == before
        assert refreshed.last_run_id is None
        assert session.scalar(select(func.count()).select_from(SourceSyncRun)) == 0
    engine.dispose()


@pytest.mark.parametrize("blocked", ["paused", "archived", "source", "dataset"])
def test_paused_archived_or_inactive_scope_never_fires(tmp_path: Path, blocked: str) -> None:
    engine, repository = _authority(tmp_path)
    schedule = repository.put(
        "tenant-1", "dataset-1", "source-1", expected_revision=0,
        interval_seconds=300, force_full=False, status="active", audit=_audit(),
    )
    _make_due(engine, schedule.id)
    with Session(engine) as session:
        if blocked in {"paused", "archived"}:
            session.get(SourceSchedule, schedule.id).status = blocked
        elif blocked == "source":
            session.get(DataSourceRecord, "source-1").status = "disabled"
        else:
            session.get(Dataset, "dataset-1").status = "disabled"
        before = session.get(SourceSchedule, schedule.id).next_run_at
        session.commit()
    assert repository.enqueue_due(limit=10) == []
    with Session(engine) as session:
        assert session.get(SourceSchedule, schedule.id).next_run_at == before
        assert session.scalar(select(func.count()).select_from(SourceSyncRun)) == 0
    engine.dispose()


def test_new_revision_produces_new_schedule_intent_key(tmp_path: Path) -> None:
    engine, repository = _authority(tmp_path)
    schedule = repository.put(
        "tenant-1", "dataset-1", "source-1", expected_revision=0,
        interval_seconds=300, force_full=False, status="active", audit=_audit(),
    )
    first_planned = _make_due(engine, schedule.id)
    first = repository.enqueue_due(limit=1)[0]
    updated = repository.put(
        "tenant-1", "dataset-1", "source-1", expected_revision=1,
        interval_seconds=600, force_full=True, status="active", audit=_audit("revision-2"),
    )
    with Session(engine) as session:
        session.execute(
            update(SourceSchedule).where(SourceSchedule.id == updated.id).values(
                next_run_at=first_planned
            )
        )
        session.commit()
    second = repository.enqueue_due(limit=1)[0]
    assert second.planned_at == first.planned_at
    assert second.idempotency_key != first.idempotency_key
    assert second.request_hash != first.request_hash
    with Session(engine) as session:
        keys = list(
            session.scalars(
                select(SourceSyncRun.idempotency_key).order_by(SourceSyncRun.created_at)
            )
        )
    assert keys == [first.idempotency_key, second.idempotency_key]
    engine.dispose()
