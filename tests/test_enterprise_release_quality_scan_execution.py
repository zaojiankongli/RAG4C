from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from core.enterprise_release_quality_service import certify_release as stage20_certify_release
from models.orm import (
    Dataset,
    DatasetChannelRelease,
    DatasetReleaseQualityAlert,
    DatasetReleaseQualityCertification,
    DatasetReleaseQualityObservation,
    DatasetReleaseRecertificationJob,
    TenantReleaseQualityScanRun,
    TenantReleaseQualityScanSchedule,
)
from test_enterprise_release_quality_recertification import _seed_authority

SCHEDULER_MODULE = "core.enterprise_release_quality_scheduler"
NOW = datetime(2026, 8, 28, 12, 0, 0)


def _scheduler() -> Any:
    from importlib import import_module

    return import_module(SCHEDULER_MODULE)


def _schedule(*, planned_at: datetime) -> TenantReleaseQualityScanSchedule:
    return TenantReleaseQualityScanSchedule(
        id="schedule-execution",
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        slo_policy_id="slo-policy-a",
        status="active",
        active_policy_slot="slo-policy-a",
        revision=1,
        interval_seconds=300,
        next_run_at=planned_at,
        last_enqueued_at=None,
        created_at=NOW,
        created_by="owner-a",
        updated_at=NOW,
        updated_by="owner-a",
        paused_at=None,
        paused_by=None,
        archived_at=None,
        archived_by=None,
    )


def _prepare_expired_execution_authority(
    tmp_path: Path,
) -> tuple[Engine, datetime, dict[str, str]]:
    engine, authority = _seed_authority(tmp_path)
    stage20_certify_release(
        engine,
        tenant_id="tenant-a",
        actor_id="owner-a",
        dataset_id="dataset-a",
        release_id=authority["release_id"],
        channel_id="channel-production",
        baseline_id=authority["baseline_id"],
        policy_id=authority["policy_id"],
        expected_policy_revision=1,
        expected_channel_revision=1,
        reason="seed expired certification for scan execution",
        request_id="scan-execution-certification",
        request_ip="127.0.0.1",
        idempotency_key="scan-execution-certification-key",
    )
    # 本 helper 的语义是「认证已失效之后」再执行扫描。原先写死 `NOW + 7 天`，
    # 但认证的 valid_until 由 quality policy 的 max_certification_age_minutes 决定，
    # 且 certify_release 用的是**真实时钟**（它没有 now 参数——认证是实时事实）。
    # 于是「7 天后必然过期」这个前提并不成立：实测 valid_until ≈ real_now + 14 天，
    # 扫描看到的仍是 healthy，告警数为 0，两条用例因此长期误挂。
    # 改为从认证行真实的 valid_until 推导，helper 的名字与行为重新一致。
    with Session(engine) as session:
        valid_until = session.scalar(
            select(DatasetReleaseQualityCertification.valid_until)
            .where(
                DatasetReleaseQualityCertification.tenant_id == "tenant-a",
                DatasetReleaseQualityCertification.dataset_id == "dataset-a",
            )
            .order_by(DatasetReleaseQualityCertification.created_at.desc())
        )
    assert valid_until is not None
    execution_now = valid_until + timedelta(minutes=1)
    with Session(engine) as session:
        dataset = session.get(Dataset, "dataset-a")
        assert dataset is not None
        dataset.owner_id = "owner-a"
        dataset.serving_release_id = authority["release_id"]
        dataset.release_revision = 2
        session.add(
            DatasetChannelRelease(
                id="binding-execution",
                tenant_id="tenant-a",
                dataset_id="dataset-a",
                channel_id="channel-production",
                active_release_id=authority["release_id"],
                previous_release_id=None,
                status="active",
                active_slot="active",
                revision=1,
                activated_at=NOW,
                activated_by="owner-a",
                request_id="scan-execution-binding",
                reason="scan execution fixture",
                created_at=NOW,
                updated_at=NOW,
                updated_by="owner-a",
            )
        )
        session.add(_schedule(planned_at=execution_now - timedelta(minutes=1)))
        session.commit()
    return engine, execution_now, authority


def _enqueue_claim(module: Any, engine: Engine, *, now: datetime, owner: str) -> Any:
    module.enqueue_due_quality_scans(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        now=now,
        limit=1,
    )
    with Session(engine) as session:
        run = session.scalar(
            select(TenantReleaseQualityScanRun)
            .where(
                TenantReleaseQualityScanRun.tenant_id == "tenant-a",
                TenantReleaseQualityScanRun.dataset_id == "dataset-a",
                TenantReleaseQualityScanRun.schedule_id == "schedule-execution",
            )
            .order_by(TenantReleaseQualityScanRun.planned_at.desc())
        )
    if run is None:
        with Session(engine) as session:
            run = session.scalar(
                select(TenantReleaseQualityScanRun)
                .where(
                    TenantReleaseQualityScanRun.tenant_id == "tenant-a",
                    TenantReleaseQualityScanRun.dataset_id == "dataset-a",
                )
                .order_by(TenantReleaseQualityScanRun.planned_at.desc())
            )
    assert run is not None
    claimed = module.claim_quality_scan_run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=run.id,
        owner_id=owner,
        lease_seconds=300,
        now=now,
    )
    return module.heartbeat_quality_scan_run(
        engine,
        tenant_id="tenant-a",
        dataset_id="dataset-a",
        run_id=claimed.id,
        owner_id=owner,
        lease_seconds=300,
        now=now + timedelta(seconds=1),
    )


def test_execute_persists_alert_and_coalesces_recertification_job_from_current_authority(
    tmp_path: Path,
) -> None:
    module = _scheduler()
    engine, execution_now, authority = _prepare_expired_execution_authority(tmp_path)
    try:
        run = _enqueue_claim(module, engine, now=execution_now, owner="worker-a")
        first = module.execute_quality_scan_run(
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            run_id=run.id,
            worker_id="worker-a",
            now=execution_now,
        )

        assert first.status == "completed"
        assert first.observation_count == 1
        assert first.alert_count == 1
        assert first.recertification_job_count == 1
        with Session(engine) as session:
            assert (
                session.scalar(select(func.count()).select_from(DatasetReleaseQualityObservation))
                == 1
            )
            assert session.scalar(select(func.count()).select_from(DatasetReleaseQualityAlert)) == 1
            assert (
                session.scalar(select(func.count()).select_from(DatasetReleaseRecertificationJob))
                == 1
            )
            job = session.scalar(select(DatasetReleaseRecertificationJob))
            assert job is not None
            assert job.baseline_id == authority["baseline_id"]
            assert job.policy_id == authority["policy_id"]
            assert job.slo_policy_id == "slo-policy-a"
            assert job.expected_manifest_digest
            assert job.expected_evidence_digest
            assert job.expected_channel_revision == 1

        second_now = execution_now + timedelta(minutes=6)
        with Session(engine) as session:
            schedule = session.get(TenantReleaseQualityScanSchedule, "schedule-execution")
            assert schedule is not None
            schedule.next_run_at = second_now - timedelta(minutes=1)
            session.commit()
        second_run = _enqueue_claim(module, engine, now=second_now, owner="worker-b")
        second = module.execute_quality_scan_run(
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            run_id=second_run.id,
            worker_id="worker-b",
            now=second_now,
        )
        assert second.status == "completed"
        with Session(engine) as session:
            assert (
                session.scalar(select(func.count()).select_from(DatasetReleaseRecertificationJob))
                == 1
            )
    finally:
        engine.dispose()
