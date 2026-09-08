from __future__ import annotations

from datetime import datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from models.orm import (
    Dataset,
    TenantReleaseQualityScanRun,
    TenantReleaseQualityScanSchedule,
)

SCHEDULER_MODULE = "core.enterprise_release_quality_scheduler"
NOW = datetime(2026, 8, 28, 12, 0, 0)


@pytest.fixture
def scheduler_module() -> Any:
    """Load the production scheduler only after collection so this stays a RED contract."""

    return import_module(SCHEDULER_MODULE)


@pytest.fixture
def authority(tmp_path: Path, scheduler_module: Any) -> tuple[Engine, datetime]:
    """Build a local two-Dataset authority for Dataset-scoped Run tests."""

    from test_enterprise_knowledge_base_release_snapshot import _release_engine

    engine, now = _release_engine(tmp_path)
    with Session(engine) as session:
        session.add(
            Dataset(
                id="dataset-b",
                tenant_id="tenant-a",
                name="Dataset B",
                status="active",
                profile_revision=1,
                visibility="private",
                profile_json={},
                parser_policy={},
                chunk_policy={},
                retrieval_policy={},
                retention_policy={},
                metadata_policy={},
                default_language="zh-CN",
                graph_enabled=False,
                qa_enabled=True,
                mutation_generation=0,
                serving_generation=0,
                doc_count=0,
                chunk_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            _slo_policy(
                tenant_id="tenant-a",
                policy_id="slo-policy-global",
                created_at=now,
            )
        )
        session.flush()
        session.add_all(
            [
                _schedule(
                    tenant_id="tenant-a",
                    dataset_id="dataset-a",
                    schedule_id="schedule-a",
                    policy_id="slo-policy-global",
                    planned_at=NOW - timedelta(minutes=1),
                    created_at=now,
                ),
                _schedule(
                    tenant_id="tenant-a",
                    dataset_id="dataset-b",
                    schedule_id="schedule-b",
                    policy_id="slo-policy-global",
                    planned_at=NOW - timedelta(minutes=1),
                    created_at=now,
                ),
            ]
        )
        session.commit()
    try:
        yield engine, now
    finally:
        engine.dispose()


def _slo_policy(*, tenant_id: str, policy_id: str, created_at: datetime) -> Any:
    from models.orm import TenantReleaseQualitySloPolicy

    return TenantReleaseQualitySloPolicy(
        id=policy_id,
        tenant_id=tenant_id,
        name="Quality SLO",
        scope_type="global",
        scope_value="*",
        channel_id=None,
        active_scope_key="global:*",
        status="active",
        revision=1,
        certification_warning_minutes=60,
        certification_critical_minutes=15,
        waiver_warning_minutes=30,
        max_open_alerts=20,
        auto_queue_recertification=False,
        require_passing_certification=True,
        allow_active_waiver=True,
        policy_digest="a" * 64,
        created_at=created_at,
        created_by="owner-a",
        updated_at=created_at,
        updated_by="owner-a",
    )


def _schedule(
    *,
    tenant_id: str,
    dataset_id: str,
    schedule_id: str,
    policy_id: str,
    planned_at: datetime,
    created_at: datetime,
) -> Any:
    return TenantReleaseQualityScanSchedule(
        id=schedule_id,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        slo_policy_id=policy_id,
        status="active",
        active_policy_slot=policy_id,
        revision=1,
        interval_seconds=300,
        next_run_at=planned_at,
        last_enqueued_at=None,
        created_at=created_at,
        created_by="owner-a",
        updated_at=created_at,
        updated_by="owner-a",
        paused_at=None,
        paused_by=None,
        archived_at=None,
        archived_by=None,
    )


def _call(module: Any, name: str, engine: Engine, **kwargs: Any) -> Any:
    function = getattr(module, name, None)
    assert callable(function), f"scheduler API is missing: {name}"
    return function(engine, **kwargs)


def _run(
    engine: Engine,
    *,
    tenant_id: str,
    dataset_id: str,
    schedule_id: str,
    planned_at: datetime,
) -> TenantReleaseQualityScanRun:
    with Session(engine) as session:
        row = session.scalar(
            select(TenantReleaseQualityScanRun).where(
                TenantReleaseQualityScanRun.tenant_id == tenant_id,
                TenantReleaseQualityScanRun.dataset_id == dataset_id,
                TenantReleaseQualityScanRun.schedule_id == schedule_id,
                TenantReleaseQualityScanRun.planned_at == planned_at,
            )
        )
        assert row is not None
        return row


def _runs(engine: Engine) -> list[TenantReleaseQualityScanRun]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(TenantReleaseQualityScanRun).order_by(TenantReleaseQualityScanRun.id)
            )
        )


def _set_due(engine: Engine, schedule_id: str, planned_at: datetime) -> None:
    with Session(engine) as session:
        schedule = session.get(TenantReleaseQualityScanSchedule, schedule_id)
        assert schedule is not None
        schedule.next_run_at = planned_at
        session.commit()


def _expire_lease(engine: Engine, run_id: str) -> None:
    with Session(engine) as session:
        run = session.get(TenantReleaseQualityScanRun, run_id)
        assert run is not None
        run.claim_lease_until = NOW - timedelta(seconds=1)
        session.commit()


def _target_key(target: Any) -> tuple[str, str, str, str]:
    if isinstance(target, dict):
        return (
            str(target["dataset_id"]),
            str(target["release_id"]),
            str(target["channel_id"]),
            str(target["release_role"]),
        )
    return (
        str(target.dataset_id),
        str(target.release_id),
        str(target.channel_id),
        str(target.release_role),
    )


def test_dataset_scope_is_required_in_schedule_run_identity_and_foreign_keys() -> None:
    schedule = TenantReleaseQualityScanSchedule.__table__
    run = TenantReleaseQualityScanRun.__table__

    assert "dataset_id" in schedule.c
    assert "dataset_id" in run.c

    schedule_uniques = {
        tuple(constraint.columns.keys())
        for constraint in schedule.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("tenant_id", "dataset_id", "active_policy_slot") in schedule_uniques

    run_uniques = {
        tuple(constraint.columns.keys())
        for constraint in run.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("tenant_id", "dataset_id", "schedule_id", "planned_at") in run_uniques

    schedule_dataset_fk = next(
        constraint
        for constraint in schedule.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.referred_table.name == "datasets"
    )
    assert tuple(schedule_dataset_fk.column_keys) == ("tenant_id", "dataset_id")
    assert tuple(element.column.name for element in schedule_dataset_fk.elements) == (
        "tenant_id",
        "id",
    )

    run_dataset_fk = next(
        constraint
        for constraint in run.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.referred_table.name == "datasets"
    )
    assert tuple(run_dataset_fk.column_keys) == ("tenant_id", "dataset_id")
    assert tuple(element.column.name for element in run_dataset_fk.elements) == (
        "tenant_id",
        "id",
    )

    run_schedule_fk = next(
        constraint
        for constraint in run.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.referred_table.name == "tenant_release_quality_scan_schedules"
    )
    assert tuple(run_schedule_fk.column_keys) == (
        "tenant_id",
        "dataset_id",
        "schedule_id",
        "slo_policy_id",
    )


def test_enqueue_is_deterministic_by_dataset_schedule_slot_and_replays_without_duplicates(
    scheduler_module: Any,
    authority: tuple[Engine, datetime],
) -> None:
    engine, _ = authority
    planned_at = NOW - timedelta(minutes=1)

    _call(
        scheduler_module,
        "enqueue_due_quality_scans",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        now=NOW,
        limit=10,
    )
    first = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=planned_at,
    )
    assert first.dataset_id == "dataset-a"
    assert len(first.idempotency_key_digest) == 64
    assert len(first.request_hash) == 64

    # Rewind only the controlled local schedule slot to model a process replay
    # after the schedule commit. The unique Dataset/Schedule/slot identity must
    # return the same Run instead of creating a second row.
    _set_due(engine, "schedule-a", planned_at)
    _call(
        scheduler_module,
        "enqueue_due_quality_scans",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        now=NOW,
        limit=10,
    )
    replay = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=planned_at,
    )

    assert replay.id == first.id
    assert replay.idempotency_key_digest == first.idempotency_key_digest
    assert replay.request_hash == first.request_hash
    assert (
        len(
            [
                row
                for row in _runs(engine)
                if row.tenant_id == "tenant-a"
                and row.dataset_id == "dataset-a"
                and row.schedule_id == "schedule-a"
                and row.planned_at == planned_at
            ]
        )
        == 1
    )

    # The same UTC slot and SLO policy in another Dataset is a different Run
    # identity and must not collide with Dataset A.
    _call(
        scheduler_module,
        "enqueue_due_quality_scans",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-b",
        now=NOW,
        limit=10,
    )
    second_dataset = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-b",
        schedule_id="schedule-b",
        planned_at=planned_at,
    )
    assert second_dataset.dataset_id == "dataset-b"
    assert second_dataset.id != first.id
    assert second_dataset.idempotency_key_digest != first.idempotency_key_digest


def test_enqueue_and_claim_reject_cross_dataset_scope_without_mutating_the_run(
    scheduler_module: Any,
    authority: tuple[Engine, datetime],
) -> None:
    engine, _ = authority
    planned_at = NOW - timedelta(minutes=1)
    _call(
        scheduler_module,
        "enqueue_due_quality_scans",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        now=NOW,
        limit=1,
    )
    run = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=planned_at,
    )

    with pytest.raises(Exception):
        _call(
            scheduler_module,
            "claim_quality_scan_run",
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-b",
            run_id=run.id,
            owner_id="worker-b",
            lease_seconds=60,
            now=NOW,
        )

    untouched = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=planned_at,
    )
    assert untouched.status == "pending"
    assert untouched.claim_owner is None
    assert untouched.claim_lease_until is None

    claimed = _call(
        scheduler_module,
        "claim_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-a",
        lease_seconds=60,
        now=NOW,
    )
    assert getattr(claimed, "dataset_id", None) == "dataset-a"
    current = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=planned_at,
    )
    assert current.status == "claimed"
    assert current.claim_owner == "worker-a"
    assert current.claim_lease_until is not None

    with pytest.raises(Exception):
        _call(
            scheduler_module,
            "claim_quality_scan_run",
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-b",
            run_id=run.id,
            owner_id="worker-b",
            lease_seconds=60,
            now=NOW,
        )
    current = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=planned_at,
    )
    assert current.claim_owner == "worker-a"


def test_claim_heartbeat_and_terminal_cancel_are_lease_and_owner_fenced(
    scheduler_module: Any,
    authority: tuple[Engine, datetime],
) -> None:
    engine, _ = authority
    planned_at = NOW - timedelta(minutes=1)
    _call(
        scheduler_module,
        "enqueue_due_quality_scans",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        now=NOW,
        limit=1,
    )
    run = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=planned_at,
    )
    _call(
        scheduler_module,
        "claim_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-a",
        lease_seconds=60,
        now=NOW,
    )

    with pytest.raises(Exception):
        _call(
            scheduler_module,
            "heartbeat_quality_scan_run",
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            run_id=run.id,
            owner_id="worker-b",
            lease_seconds=60,
            now=NOW + timedelta(seconds=10),
        )

    heartbeat = _call(
        scheduler_module,
        "heartbeat_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-a",
        lease_seconds=60,
        now=NOW + timedelta(seconds=10),
    )
    assert getattr(heartbeat, "dataset_id", None) == "dataset-a"
    current = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=planned_at,
    )
    assert current.status == "running"
    assert current.claim_owner == "worker-a"
    assert current.heartbeat_at == NOW + timedelta(seconds=10)
    assert current.claim_lease_until == NOW + timedelta(seconds=70)

    with pytest.raises(Exception):
        _call(
            scheduler_module,
            "cancel_quality_scan_run",
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            run_id=run.id,
            owner_id="worker-b",
            reason="ticket=must-not-authorize",
            now=NOW + timedelta(seconds=20),
        )

    cancelled = _call(
        scheduler_module,
        "cancel_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-a",
        reason="operator cancelled",
        now=NOW + timedelta(seconds=20),
    )
    assert getattr(cancelled, "status", None) == "cancelled"
    current = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=planned_at,
    )
    assert current.status == "cancelled"
    assert current.claim_owner is None
    assert current.claim_lease_until is None
    assert current.heartbeat_at is None
    assert current.finished_at is not None


def test_stale_owner_can_neither_heartbeat_nor_finish_and_reclaim_is_bounded_retry(
    scheduler_module: Any,
    authority: tuple[Engine, datetime],
) -> None:
    engine, _ = authority
    planned_at = NOW - timedelta(minutes=1)
    _call(
        scheduler_module,
        "enqueue_due_quality_scans",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        now=NOW,
        limit=1,
    )
    run = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=planned_at,
    )

    _call(
        scheduler_module,
        "claim_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-a",
        lease_seconds=60,
        now=NOW,
    )
    _expire_lease(engine, run.id)
    reclaimed = _call(
        scheduler_module,
        "claim_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-b",
        lease_seconds=60,
        now=NOW + timedelta(minutes=2),
    )
    assert getattr(reclaimed, "attempt_count", None) == 2

    with pytest.raises(Exception):
        _call(
            scheduler_module,
            "heartbeat_quality_scan_run",
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            run_id=run.id,
            owner_id="worker-a",
            lease_seconds=60,
            now=NOW + timedelta(minutes=2, seconds=1),
        )
    with pytest.raises(Exception):
        _call(
            scheduler_module,
            "finish_quality_scan_run",
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            run_id=run.id,
            owner_id="worker-a",
            outcome="failed",
            safe_error_code="quality_authority_unavailable",
            safe_error="query=private body=secret ticket=opaque credential=topsecret",
            now=NOW + timedelta(minutes=2, seconds=2),
        )

    _expire_lease(engine, run.id)
    _call(
        scheduler_module,
        "claim_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-c",
        lease_seconds=60,
        now=NOW + timedelta(minutes=4),
    )
    _expire_lease(engine, run.id)
    with pytest.raises(Exception):
        _call(
            scheduler_module,
            "claim_quality_scan_run",
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            run_id=run.id,
            owner_id="worker-d",
            lease_seconds=60,
            now=NOW + timedelta(minutes=6),
        )

    current = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=planned_at,
    )
    assert current.attempt_count == current.max_attempts == 3
    assert current.claim_owner == "worker-c"


def test_target_selection_is_dataset_scoped_and_uses_active_channels_plus_pinned_apps(
    scheduler_module: Any,
    authority: tuple[Engine, datetime],
) -> None:
    engine, now = authority
    from models.orm import (
        App,
        AppDatasetReference,
        DatasetChannelRelease,
        DatasetReleaseManifest,
    )

    with Session(engine) as session:
        objects = [
            DatasetReleaseManifest(
                id="release-active",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                release_number=1,
                profile_revision=4,
                ownership_revision=5,
                workspace_id="workspace-a",
                workspace_revision=2,
                mutation_generation=9,
                serving_generation=3,
                schema_version=1,
                policy_digest="b" * 64,
                manifest_digest="c" * 64,
                readiness_digest="d" * 64,
                readiness_state="ready",
                entry_count=0,
                blocker_count=0,
                readiness_blockers_json=[],
                created_at=now,
                created_by="owner-a",
                reason="active target fixture",
                request_id="request-active-target",
            ),
            DatasetReleaseManifest(
                id="release-pinned",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                release_number=2,
                profile_revision=4,
                ownership_revision=5,
                workspace_id="workspace-a",
                workspace_revision=2,
                mutation_generation=9,
                serving_generation=3,
                schema_version=1,
                policy_digest="e" * 64,
                manifest_digest="f" * 64,
                readiness_digest="0" * 64,
                readiness_state="ready",
                entry_count=0,
                blocker_count=0,
                readiness_blockers_json=[],
                created_at=now,
                created_by="owner-a",
                reason="pinned target fixture",
                request_id="request-pinned-target",
            ),
            DatasetReleaseManifest(
                id="release-candidate-only",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                release_number=3,
                profile_revision=4,
                ownership_revision=5,
                workspace_id="workspace-a",
                workspace_revision=2,
                mutation_generation=9,
                serving_generation=3,
                schema_version=1,
                policy_digest="1" * 64,
                manifest_digest="2" * 64,
                readiness_digest="3" * 64,
                readiness_state="ready",
                entry_count=0,
                blocker_count=0,
                readiness_blockers_json=[],
                created_at=now,
                created_by="owner-a",
                reason="candidate must stay out",
                request_id="request-candidate-only",
            ),
            DatasetChannelRelease(
                id="binding-production",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                channel_id="channel-production",
                active_release_id="release-active",
                previous_release_id=None,
                status="active",
                active_slot="active",
                revision=1,
                activated_at=now,
                activated_by="owner-a",
                request_id="request-binding-production",
                reason="active production binding",
                created_at=now,
                updated_at=now,
                updated_by="owner-a",
            ),
            App(id="app-a", tenant_id="tenant-a", name="Pinned Application", kind="chat"),
            AppDatasetReference(
                id="app-ref-pinned",
                tenant_id="tenant-a",
                app_id="app-a",
                dataset_id="dataset-a",
                reference_kind="knowledge",
                status="active",
                active_slot="active",
                revision=1,
                created_at=now,
                created_by="owner-a",
                updated_at=now,
                updated_by="owner-a",
                removed_at=None,
                removed_by=None,
                request_id="request-app-pinned",
                release_mode="pinned",
                release_channel_id=None,
                pinned_release_id="release-pinned",
            ),
        ]
        session.add_all([objects[0], objects[1], objects[2], objects[4]])
        session.flush()
        session.add_all([objects[3], objects[5]])
        session.commit()

    targets = _call(
        scheduler_module,
        "resolve_quality_scan_targets",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
    )
    assert {_target_key(target) for target in targets} == {
        ("dataset-a", "release-active", "channel-production", "active"),
        ("dataset-a", "release-pinned", "channel-production", "pinned"),
    }
    assert all(_target_key(target)[0] == "dataset-a" for target in targets)

    other_dataset_targets = _call(
        scheduler_module,
        "resolve_quality_scan_targets",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-b",
    )
    assert other_dataset_targets == []


def test_scan_run_persistence_never_exposes_raw_idempotency_or_request_body_fields(
    scheduler_module: Any,
    authority: tuple[Engine, datetime],
) -> None:
    engine, _ = authority
    _call(
        scheduler_module,
        "enqueue_due_quality_scans",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        now=NOW,
        limit=1,
        request_id="request-safe-run",
        idempotency_key="raw-idempotency-must-not-persist",
        body={"query": "private query", "ticket": "opaque-ticket", "credential": "secret"},
    )
    run = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=NOW - timedelta(minutes=1),
    )
    columns = set(TenantReleaseQualityScanRun.__table__.c.keys())
    assert "idempotency_key" not in columns
    assert "query" not in columns
    assert "body" not in columns
    assert "judgment_note" not in columns
    assert "ticket" not in columns
    assert "credential" not in columns
    rendered = repr(run)
    assert "raw-idempotency-must-not-persist" not in rendered
    assert "private query" not in rendered
    assert "opaque-ticket" not in rendered
    assert "secret" not in rendered


def test_failed_run_is_requeued_until_bounded_retry_budget_is_exhausted(
    scheduler_module: Any,
    authority: tuple[Engine, datetime],
) -> None:
    engine, _ = authority
    planned_at = NOW - timedelta(minutes=1)
    _call(
        scheduler_module,
        "enqueue_due_quality_scans",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        now=NOW,
        limit=1,
    )
    run = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=planned_at,
    )

    _call(
        scheduler_module,
        "claim_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-a",
        lease_seconds=60,
        now=NOW,
    )
    first_failure = _call(
        scheduler_module,
        "finish_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-a",
        outcome="failed",
        safe_error_code="quality_authority_unavailable",
        safe_error="temporary authority outage",
        now=NOW + timedelta(seconds=1),
    )
    assert first_failure.status == "pending"
    assert first_failure.finished_at is None
    assert first_failure.next_attempt_at is not None
    with pytest.raises(Exception, match="retry|attempt"):
        _call(
            scheduler_module,
            "claim_quality_scan_run",
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            run_id=run.id,
            owner_id="worker-b",
            lease_seconds=60,
            now=NOW + timedelta(seconds=2),
        )

    retry_at = first_failure.next_attempt_at
    assert retry_at is not None
    _call(
        scheduler_module,
        "claim_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-b",
        lease_seconds=60,
        now=retry_at,
    )
    second_failure = _call(
        scheduler_module,
        "finish_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-b",
        outcome="failed",
        safe_error_code="quality_authority_unavailable",
        safe_error="temporary authority outage",
        now=retry_at + timedelta(seconds=1),
    )
    assert second_failure.status == "pending"
    assert second_failure.next_attempt_at is not None

    retry_at = second_failure.next_attempt_at
    assert retry_at is not None
    _call(
        scheduler_module,
        "claim_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-c",
        lease_seconds=60,
        now=retry_at,
    )
    terminal = _call(
        scheduler_module,
        "finish_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-c",
        outcome="failed",
        safe_error_code="quality_authority_unavailable",
        safe_error="permanent authority outage",
        now=retry_at + timedelta(seconds=1),
    )
    assert terminal.status == "failed"
    assert terminal.finished_at is not None
    assert terminal.next_attempt_at is None
    with pytest.raises(Exception, match="terminal|retry|attempt"):
        _call(
            scheduler_module,
            "claim_quality_scan_run",
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            run_id=run.id,
            owner_id="worker-d",
            lease_seconds=60,
            now=terminal.finished_at + timedelta(seconds=1),
        )


def test_manual_scan_uses_tenant_idempotency_replay_request_hash_and_audit(
    scheduler_module: Any,
    authority: tuple[Engine, datetime],
) -> None:
    engine, _ = authority
    raw_key = "manual-quality-scan-key"
    first = _call(
        scheduler_module,
        "enqueue_due_quality_scans",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        now=NOW,
        limit=1,
        request_id="manual-scan-request-1",
        idempotency_key=raw_key,
        body={"limit": 1, "query": "must not persist"},
    )
    assert hasattr(first, "body")
    assert first.status == 202
    assert first.body["items"]

    replay = _call(
        scheduler_module,
        "enqueue_due_quality_scans",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        now=NOW,
        limit=1,
        request_id="manual-scan-request-replay",
        idempotency_key=raw_key,
        body={"limit": 1, "query": "different ignored body"},
    )
    assert replay.body == first.body

    with pytest.raises(Exception, match="idempotency|request"):
        _call(
            scheduler_module,
            "enqueue_due_quality_scans",
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            now=NOW,
            limit=2,
            request_id="manual-scan-request-conflict",
            idempotency_key=raw_key,
            body={"limit": 2},
        )

    from models.orm import TenantAuditEvent, TenantControlMutationRequest

    with Session(engine) as session:
        mutations = list(
            session.scalars(
                select(TenantControlMutationRequest).where(
                    TenantControlMutationRequest.tenant_id == "tenant-a",
                    TenantControlMutationRequest.resource_type == "quality_scan_run",
                )
            )
        )
        assert len(mutations) == 1
        assert mutations[0].status == "completed"
        assert raw_key not in repr(mutations[0])
        audits = list(
            session.scalars(
                select(TenantAuditEvent).where(
                    TenantAuditEvent.tenant_id == "tenant-a",
                    TenantAuditEvent.resource_type == "quality_scan_run",
                )
            )
        )
        assert audits
        assert any("scan" in str(row.action).casefold() for row in audits)


def test_manual_cancel_uses_idempotency_replay_request_hash_and_audit(
    scheduler_module: Any,
    authority: tuple[Engine, datetime],
) -> None:
    engine, _ = authority
    planned_at = NOW - timedelta(minutes=1)
    _call(
        scheduler_module,
        "enqueue_due_quality_scans",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        now=NOW,
        limit=1,
    )
    run = _run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        schedule_id="schedule-a",
        planned_at=planned_at,
    )
    _call(
        scheduler_module,
        "claim_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="owner-a",
        lease_seconds=60,
        now=NOW,
    )
    first = _call(
        scheduler_module,
        "cancel_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="owner-a",
        reason="cancel for maintenance",
        idempotency_key="manual-cancel-key",
        request_id="manual-cancel-request-1",
        now=NOW + timedelta(seconds=1),
    )
    assert hasattr(first, "body")
    assert first.body["run"]["status"] == "cancelled"

    replay = _call(
        scheduler_module,
        "cancel_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="owner-a",
        reason="cancel for maintenance",
        idempotency_key="manual-cancel-key",
        request_id="manual-cancel-request-replay",
        now=NOW + timedelta(seconds=2),
    )
    assert replay.body == first.body

    with pytest.raises(Exception, match="idempotency|request"):
        _call(
            scheduler_module,
            "cancel_quality_scan_run",
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            run_id=run.id,
            owner_id="owner-a",
            reason="different cancellation reason",
            idempotency_key="manual-cancel-key",
            request_id="manual-cancel-request-conflict",
            now=NOW + timedelta(seconds=3),
        )

    from models.orm import TenantAuditEvent

    with Session(engine) as session:
        audits = list(
            session.scalars(
                select(TenantAuditEvent).where(
                    TenantAuditEvent.tenant_id == "tenant-a",
                    TenantAuditEvent.resource_type == "quality_scan_run",
                    TenantAuditEvent.resource_id == run.id,
                )
            )
        )
        assert audits
        assert any("cancel" in str(row.action).casefold() for row in audits)
