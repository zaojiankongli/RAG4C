from __future__ import annotations

from pathlib import Path

import pytest

from core.enterprise_release_quality_scheduler import (
    QualityScheduleValidation,
    execute_quality_scan_run,
    get_quality_operations_summary,
    list_quality_observations,
    list_quality_scan_runs,
    list_quality_scan_schedules,
)
from test_enterprise_knowledge_base_release_snapshot import _release_engine
from test_enterprise_release_quality_scan_execution import (
    _enqueue_claim,
    _prepare_expired_execution_authority,
)


def test_operations_read_services_are_dataset_scoped_and_empty_safe(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    try:
        scope = {
            "tenant_id": "tenant-a",
            "actor_id": "owner-a",
            "dataset_id": "dataset-a",
        }
        assert list_quality_scan_schedules(engine, **scope).body == {
            "items": [],
            "next_cursor": None,
        }
        assert list_quality_scan_runs(engine, **scope).body == {
            "items": [],
            "next_cursor": None,
        }
        assert list_quality_observations(engine, **scope).body == {
            "items": [],
            "next_cursor": None,
        }
        summary = get_quality_operations_summary(engine, **scope).body
        assert summary["tenant_id"] == "tenant-a"
        assert summary["dataset_id"] == "dataset-a"
        assert summary["authorities"] == []
        assert summary["horizon_counts"] == {
            "expired": 0,
            "24_hours": 0,
            "7_days": 0,
            "30_days": 0,
            "healthy": 0,
            "unavailable": 0,
        }
    finally:
        engine.dispose()


def test_summary_and_lists_project_persisted_scan_authority(tmp_path: Path) -> None:
    engine, execution_now, authority_seed = _prepare_expired_execution_authority(tmp_path)
    try:
        run = _enqueue_claim(
            __import__("core.enterprise_release_quality_scheduler", fromlist=["execute"]),
            engine,
            now=execution_now,
            owner="worker-a",
        )
        execute_quality_scan_run(
            engine,
            tenant_id="tenant-a",
            dataset_id="dataset-a",
            run_id=run.id,
            worker_id="worker-a",
            now=execution_now,
        )
        scope = {
            "tenant_id": "tenant-a",
            "actor_id": "owner-a",
            "dataset_id": "dataset-a",
        }
        run_page = list_quality_scan_runs(engine, **scope, status="completed")
        assert [item["id"] for item in run_page.body["items"]] == [run.id]
        observation_page = list_quality_observations(engine, **scope, severity="critical")
        assert len(observation_page.body["items"]) == 1
        assert observation_page.body["items"][0]["scan_run_id"] == run.id

        summary = get_quality_operations_summary(engine, **scope).body
        assert summary["last_completed_scan_at"] is not None
        assert summary["horizon_counts"]["expired"] == 1
        assert len(summary["authorities"]) == 1
        authority = summary["authorities"][0]
        assert authority["release_id"] == authority_seed["release_id"]
        assert authority["channel_name"] == "Production"
        assert authority["horizon_band"] == "expired"
        assert authority["active_alert_count"] == 1
        assert authority["recertification_job_status"] == "pending"
    finally:
        engine.dispose()


def test_operations_read_services_reject_tampered_cursor_and_filters(tmp_path: Path) -> None:
    engine, _ = _release_engine(tmp_path)
    try:
        scope = {
            "tenant_id": "tenant-a",
            "actor_id": "owner-a",
            "dataset_id": "dataset-a",
        }
        with pytest.raises(QualityScheduleValidation, match="cursor"):
            list_quality_scan_runs(engine, **scope, cursor="tampered")
        with pytest.raises(QualityScheduleValidation, match="status"):
            list_quality_scan_runs(engine, **scope, status="mystery")
        with pytest.raises(QualityScheduleValidation, match="severity"):
            list_quality_observations(engine, **scope, severity="green")
    finally:
        engine.dispose()
