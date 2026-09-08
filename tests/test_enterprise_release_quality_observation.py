from __future__ import annotations

from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from core.enterprise_release_quality_operations import (
    canonical_observation,
    evaluate_release_quality_slo,
    resolve_slo_policy_projection,
)
from models.orm import (
    DatasetChannelRelease,
    DatasetReleaseQualityAlert,
    DatasetReleaseQualityObservation,
    DatasetReleaseRecertificationJob,
    DatasetReleaseManifest,
    TenantReleaseQualityScanRun,
    TenantReleaseQualityScanSchedule,
)

SCHEDULER_MODULE = "core.enterprise_release_quality_scheduler"
NOW = datetime(2026, 8, 28, 12, 0, 0)
UTC_NOW = NOW.replace(tzinfo=timezone.utc)


@pytest.fixture
def scheduler_module() -> Any:
    """Load the missing persistence seam at test time so this file remains RED."""

    return import_module(SCHEDULER_MODULE)


@pytest.fixture
def authority(tmp_path: Path, scheduler_module: Any) -> tuple[Engine, TenantReleaseQualityScanRun]:
    from test_enterprise_knowledge_base_release_snapshot import _release_engine

    engine, now = _release_engine(tmp_path)
    with Session(engine) as session:
        session.add(
            _slo_policy(
                tenant_id="tenant-a",
                policy_id="slo-policy-global",
                created_at=now,
            )
        )
        session.flush()
        session.add(
            _schedule(
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                schedule_id="schedule-a",
                policy_id="slo-policy-global",
                planned_at=NOW - timedelta(minutes=1),
                created_at=now,
            )
        )
        session.add(
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
                reason="observation fixture",
                request_id="request-observation-manifest",
            )
        )
        session.flush()
        session.add(
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
                request_id="request-observation-binding",
                reason="observation fixture binding",
                created_at=now,
                updated_at=now,
                updated_by="owner-a",
            )
        )
        session.commit()

    _call(
        scheduler_module,
        "enqueue_due_quality_scans",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        now=NOW,
        limit=1,
    )
    run = _run(engine, "tenant-a", "dataset-a", "schedule-a", NOW - timedelta(minutes=1))
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
    _call(
        scheduler_module,
        "heartbeat_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-a",
        lease_seconds=60,
        now=NOW + timedelta(seconds=1),
    )
    try:
        yield (
            engine,
            _run(engine, "tenant-a", "dataset-a", "schedule-a", NOW - timedelta(minutes=1)),
        )
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
        created_at=created_at,
        created_by="owner-a",
        updated_at=created_at,
        updated_by="owner-a",
    )


def _call(module: Any, name: str, engine: Engine, **kwargs: Any) -> Any:
    function = getattr(module, name, None)
    assert callable(function), f"scheduler API is missing: {name}"
    return function(engine, **kwargs)


def _run(
    engine: Engine,
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


def _raw_observation(run_id: str, **overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "tenant_id": "tenant-a",
        "dataset_id": "dataset-a",
        "release_id": "release-active",
        "channel_id": "channel-production",
        "scan_run_id": run_id,
        "slo_policy_id": "slo-policy-global",
        "slo_policy_revision": 1,
        "release_role": "active",
        "gate_state": "unavailable",
        "gate_reason": "quality_authority_unavailable",
        "severity": "unavailable",
        "observation_digest": "0" * 64,
        "observed_at": NOW,
        "observed_by": "system:quality-scanner",
        "request_id": "request-observation",
        "manifest_digest": "c" * 64,
        "expected_manifest_digest": "c" * 64,
        "channel_revision": 1,
        "expected_channel_revision": 1,
        "query": "private customer query",
        "result_body": "private retrieval body",
        "judgment_note": "private reviewer note",
        "ticket": "opaque-ticket-123",
        "credential": "token=topsecret",
        "idempotency_key": "raw-idempotency-must-not-persist",
    }
    raw.update(overrides)
    return raw


def _object_id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value["id"])
    return str(value.id)


def _append(
    module: Any,
    engine: Engine,
    observation: dict[str, Any],
    *,
    worker_id: str = "worker-a",
    now: datetime | None = None,
) -> Any:
    kwargs: dict[str, Any] = {
        "tenant_id": observation["tenant_id"],
        "dataset_id": observation["dataset_id"],
        "scan_run_id": observation["scan_run_id"],
        "observation": observation,
        "worker_id": worker_id,
        "now": NOW if now is None else now,
    }
    return _call(module, "append_quality_observation", engine, **kwargs)


def _row(engine: Engine, observation_id: str) -> DatasetReleaseQualityObservation:
    with Session(engine) as session:
        row = session.get(DatasetReleaseQualityObservation, observation_id)
        assert row is not None
        return row


def test_observation_fk_is_tenant_dataset_leading_for_scan_run() -> None:
    observation = DatasetReleaseQualityObservation.__table__
    run = TenantReleaseQualityScanRun.__table__

    assert "dataset_id" in run.c
    assert "dataset_id" in observation.c

    scan_run_fk = next(
        constraint
        for constraint in observation.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.referred_table.name == "tenant_release_quality_scan_runs"
    )
    assert tuple(scan_run_fk.column_keys) == ("tenant_id", "dataset_id", "scan_run_id")
    assert tuple(element.column.name for element in scan_run_fk.elements) == (
        "tenant_id",
        "dataset_id",
        "id",
    )

    observation_uniques = {
        tuple(constraint.columns.keys())
        for constraint in observation.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert (
        "tenant_id",
        "scan_run_id",
        "dataset_id",
        "release_id",
        "channel_id",
        "release_role",
    ) in observation_uniques


def test_canonical_observation_keeps_dataset_run_scope_minutes_and_digest_body_free() -> None:
    policy = {
        "id": "slo-policy-global",
        "tenant_id": "tenant-a",
        "name": "Quality SLO",
        "scope_type": "global",
        "scope_value": "*",
        "active_scope_key": "global:*",
        "status": "active",
        "revision": 1,
        "certification_warning_minutes": 60,
        "certification_critical_minutes": 15,
        "waiver_warning_minutes": 30,
        "max_open_alerts": 20,
        "auto_queue_recertification": False,
        "require_passing_certification": True,
        "allow_active_waiver": True,
        "policy_digest": "a" * 64,
    }
    resolved = resolve_slo_policy_projection(
        [policy],
        tenant_id="tenant-a",
        channel_id="channel-production",
        risk_tier="high",
    )
    gate = {
        "state": "passed",
        "reason": "certification_current",
        "channel": {"id": "channel-production", "risk_tier": "high"},
        "certification": {
            "id": "certification-a",
            "status": "passed",
            "certification_digest": "b" * 64,
            "valid_until": UTC_NOW + timedelta(minutes=61),
        },
    }
    evaluation = evaluate_release_quality_slo(UTC_NOW, resolved, gate)
    first = canonical_observation(
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        release_id="release-active",
        channel_id="channel-production",
        scan_run_id="quality-run-a",
        release_role="active",
        observed_at=UTC_NOW,
        observed_by="system:quality-scanner",
        request_id="request-a",
        slo_policy=resolved,
        gate=gate,
        evaluation=evaluation,
        query="must not persist",
        body="must not persist",
        judgment_note="must not persist",
        ticket="must not persist",
        credential="token=must-not-persist",
    )
    second = canonical_observation(
        dict(reversed(list(first.items()))),
        query="different query",
        body="different body",
        judgment_note="different note",
        ticket="different ticket",
        credential="token=different-secret",
    )

    assert first["tenant_id"] == "tenant-a"
    assert first["dataset_id"] == "dataset-a"
    assert first["scan_run_id"] == "quality-run-a"
    assert first["minutes_to_certification_expiry"] == 61
    assert first["observation_digest"] == second["observation_digest"]
    rendered = repr(first)
    for forbidden in (
        "must not persist",
        "different query",
        "different body",
        "different note",
        "different ticket",
        "token=",
    ):
        assert forbidden not in rendered
    assert "query" not in first
    assert "body" not in first
    assert "judgment_note" not in first
    assert "ticket" not in first
    assert "credential" not in first


def test_append_observation_is_one_immutable_fact_per_dataset_run_authority_and_replay_safe(
    scheduler_module: Any,
    authority: tuple[Engine, TenantReleaseQualityScanRun],
) -> None:
    engine, run = authority
    raw = _raw_observation(run.id)
    first = _append(scheduler_module, engine, raw)
    first_id = _object_id(first)

    replay = _append(
        scheduler_module,
        engine,
        _raw_observation(
            run.id,
            query="a different query",
            result_body="a different body",
            judgment_note="a different note",
            ticket="a different ticket",
            credential="token=different-secret",
            idempotency_key="a different raw key",
        ),
    )
    assert _object_id(replay) == first_id

    with Session(engine) as session:
        rows = list(
            session.scalars(
                select(DatasetReleaseQualityObservation).where(
                    DatasetReleaseQualityObservation.tenant_id == "tenant-a",
                    DatasetReleaseQualityObservation.dataset_id == "dataset-a",
                    DatasetReleaseQualityObservation.scan_run_id == run.id,
                )
            )
        )
    assert len(rows) == 1
    assert rows[0].observation_digest == _row(engine, first_id).observation_digest

    with pytest.raises(Exception):
        _append(
            scheduler_module,
            engine,
            _raw_observation(run.id, gate_reason="a different authoritative reason"),
        )
    with Session(engine) as session:
        assert (
            session.scalar(
                select(DatasetReleaseQualityObservation.id).where(
                    DatasetReleaseQualityObservation.scan_run_id == run.id
                )
            )
            == first_id
        )


def test_observation_update_and_delete_are_rejected_by_append_only_authority(
    scheduler_module: Any,
    authority: tuple[Engine, TenantReleaseQualityScanRun],
) -> None:
    engine, run = authority
    first = _append(scheduler_module, engine, _raw_observation(run.id))
    observation_id = _object_id(first)

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            row = session.get(DatasetReleaseQualityObservation, observation_id)
            assert row is not None
            row.gate_reason = "tampered"
            session.commit()

    with pytest.raises(IntegrityError):
        with Session(engine) as session:
            row = session.get(DatasetReleaseQualityObservation, observation_id)
            assert row is not None
            session.delete(row)
            session.commit()


def test_cross_tenant_cross_dataset_and_stale_manifest_policy_channel_facts_fail_closed(
    scheduler_module: Any,
    authority: tuple[Engine, TenantReleaseQualityScanRun],
) -> None:
    engine, run = authority
    cases = (
        {"tenant_id": "tenant-b"},
        {"dataset_id": "dataset-b"},
        {"manifest_digest": "9" * 64, "expected_manifest_digest": "9" * 64},
        {"slo_policy_revision": 0},
        {"channel_revision": 2, "expected_channel_revision": 1},
    )
    for overrides in cases:
        with pytest.raises(Exception):
            _append(scheduler_module, engine, _raw_observation(run.id, **overrides))

    with Session(engine) as session:
        assert (
            session.scalar(
                select(DatasetReleaseQualityObservation.id).where(
                    DatasetReleaseQualityObservation.scan_run_id == run.id
                )
            )
            is None
        )


def test_fixed_lock_order_revalidation_happens_before_observation_write(
    scheduler_module: Any,
    authority: tuple[Engine, TenantReleaseQualityScanRun],
) -> None:
    engine, run = authority
    raw = _raw_observation(run.id)

    from models.orm import TenantReleaseChannel, TenantReleaseQualitySloPolicy

    with Session(engine) as session:
        policy = session.get(TenantReleaseQualitySloPolicy, "slo-policy-global")
        assert policy is not None
        policy.revision = 2
        session.commit()
    with pytest.raises(Exception):
        _append(scheduler_module, engine, raw | {"slo_policy_revision": 1})

    with Session(engine) as session:
        policy = session.get(TenantReleaseQualitySloPolicy, "slo-policy-global")
        assert policy is not None
        policy.revision = 1
        session.commit()

    with Session(engine) as session:
        manifest = session.get(DatasetReleaseManifest, "release-active")
        assert manifest is not None
        current_digest = manifest.manifest_digest
        session.commit()
    with pytest.raises(Exception):
        _append(
            scheduler_module,
            engine,
            raw
            | {
                "manifest_digest": "9" * 64,
                "expected_manifest_digest": current_digest,
            },
        )

    with Session(engine) as session:
        channel = session.get(TenantReleaseChannel, "channel-production")
        assert channel is not None
        channel_revision = channel.revision
        session.commit()
    with pytest.raises(Exception):
        _append(
            scheduler_module,
            engine,
            raw
            | {
                "channel_revision": channel_revision + 1,
                "expected_channel_revision": channel_revision,
            },
        )

    with Session(engine) as session:
        assert (
            session.scalar(
                select(DatasetReleaseQualityObservation.id).where(
                    DatasetReleaseQualityObservation.scan_run_id == run.id
                )
            )
            is None
        )


def test_observation_persistence_has_no_raw_idempotency_query_body_note_ticket_or_credential(
    scheduler_module: Any,
    authority: tuple[Engine, TenantReleaseQualityScanRun],
) -> None:
    engine, run = authority
    first = _append(scheduler_module, engine, _raw_observation(run.id))
    observation_id = _object_id(first)
    row = _row(engine, observation_id)
    columns = set(DatasetReleaseQualityObservation.__table__.c.keys())

    assert "idempotency_key" not in columns
    assert "query" not in columns
    assert "result_body" not in columns
    assert "judgment_note" not in columns
    assert "ticket" not in columns
    assert "credential" not in columns
    assert row.dataset_id == "dataset-a"
    assert row.scan_run_id == run.id
    assert row.observed_at == NOW
    assert row.observation_digest
    assert row.minutes_to_certification_expiry is None
    assert row.minutes_to_waiver_expiry is None
    persisted = repr(row.__dict__)
    for forbidden in (
        "raw-idempotency-must-not-persist",
        "private customer query",
        "private retrieval body",
        "private reviewer note",
        "opaque-ticket-123",
        "topsecret",
    ):
        assert forbidden not in persisted


def test_append_rejects_old_worker_after_lease_reclaim(
    scheduler_module: Any,
    authority: tuple[Engine, TenantReleaseQualityScanRun],
) -> None:
    engine, run = authority
    with Session(engine) as session:
        current = session.get(TenantReleaseQualityScanRun, run.id)
        assert current is not None
        current.claim_lease_until = NOW + timedelta(seconds=1)
        session.commit()

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
    _call(
        scheduler_module,
        "heartbeat_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-b",
        lease_seconds=60,
        now=NOW + timedelta(seconds=2),
    )

    with pytest.raises(Exception, match="lease|ownership|worker"):
        _append(
            scheduler_module,
            engine,
            _raw_observation(
                run.id,
                observed_at=NOW + timedelta(seconds=3),
                request_id="stale-worker-observation",
            ),
            worker_id="worker-a",
            now=NOW + timedelta(seconds=3),
        )

    with Session(engine) as session:
        assert (
            session.scalar(
                select(DatasetReleaseQualityObservation.id).where(
                    DatasetReleaseQualityObservation.scan_run_id == run.id
                )
            )
            is None
        )


def test_append_requires_worker_id_for_running_scan_lease(
    scheduler_module: Any,
    authority: tuple[Engine, TenantReleaseQualityScanRun],
) -> None:
    engine, run = authority
    with pytest.raises(TypeError):
        scheduler_module.append_quality_observation(
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            scan_run_id=run.id,
            observation=_raw_observation(run.id),
        )


def test_execute_rejects_old_worker_after_lease_reclaim_before_observation_or_side_effects(
    scheduler_module: Any,
    authority: tuple[Engine, TenantReleaseQualityScanRun],
) -> None:
    engine, run = authority
    with Session(engine) as session:
        current = session.get(TenantReleaseQualityScanRun, run.id)
        assert current is not None
        current.claim_lease_until = NOW + timedelta(seconds=1)
        session.commit()

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
    _call(
        scheduler_module,
        "heartbeat_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-b",
        lease_seconds=60,
        now=NOW + timedelta(seconds=2),
    )

    with pytest.raises(Exception):
        _call(
            scheduler_module,
            "execute_quality_scan_run",
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            run_id=run.id,
            worker_id="worker-a",
            now=NOW + timedelta(seconds=3),
        )

    with Session(engine) as session:
        stored = session.get(TenantReleaseQualityScanRun, run.id)
        assert stored is not None
        assert stored.status == "running"
        assert stored.claim_owner == "worker-b"
        assert (
            session.scalar(
                select(DatasetReleaseQualityObservation.id).where(
                    DatasetReleaseQualityObservation.scan_run_id == run.id
                )
            )
            is None
        )
        assert (
            session.scalar(
                select(DatasetReleaseQualityAlert.id).where(
                    DatasetReleaseQualityAlert.dataset_id == "dataset-a"
                )
            )
            is None
        )
        assert (
            session.scalar(
                select(DatasetReleaseRecertificationJob.id).where(
                    DatasetReleaseRecertificationJob.dataset_id == "dataset-a"
                )
            )
            is None
        )


def test_execute_creates_alert_and_counts_missing_recertification_authority(
    scheduler_module: Any,
    authority: tuple[Engine, TenantReleaseQualityScanRun],
) -> None:
    engine, run = authority
    from models.orm import TenantReleaseQualitySloPolicy

    with Session(engine) as session:
        policy = session.get(TenantReleaseQualitySloPolicy, "slo-policy-global")
        assert policy is not None
        policy.auto_queue_recertification = True
        session.commit()

    result = _call(
        scheduler_module,
        "execute_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        worker_id="worker-a",
        now=NOW + timedelta(seconds=2),
    )

    assert result.status == "completed"
    assert result.observation_count == 1
    assert result.alert_count == 1
    assert result.recertification_job_count == 0
    with Session(engine) as session:
        assert (
            session.scalar(
                select(DatasetReleaseQualityObservation.id).where(
                    DatasetReleaseQualityObservation.scan_run_id == run.id
                )
            )
            is not None
        )
        assert (
            session.scalar(
                select(DatasetReleaseQualityAlert.id).where(
                    DatasetReleaseQualityAlert.dataset_id == "dataset-a"
                )
            )
            is not None
        )
        assert (
            session.scalar(
                select(DatasetReleaseRecertificationJob.id).where(
                    DatasetReleaseRecertificationJob.dataset_id == "dataset-a"
                )
            )
            is None
        )


def test_finish_computes_summary_counts_from_persisted_rows_not_caller_values(
    scheduler_module: Any,
    authority: tuple[Engine, TenantReleaseQualityScanRun],
) -> None:
    engine, run = authority
    _append(scheduler_module, engine, _raw_observation(run.id))

    result = _call(
        scheduler_module,
        "finish_quality_scan_run",
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id="worker-a",
        outcome="completed",
        observation_count=999,
        alert_count=999,
        recertification_job_count=999,
        now=NOW + timedelta(seconds=3),
    )

    assert result.observation_count == 1
    assert result.alert_count == 0
    assert result.recertification_job_count == 0
    with Session(engine) as session:
        stored = session.get(TenantReleaseQualityScanRun, run.id)
        assert stored is not None
        assert stored.observation_count == 1
        assert stored.alert_count == 0
        assert stored.recertification_job_count == 0
