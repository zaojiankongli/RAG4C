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
        # 没声明过的判定值仍是 400 类校验错，不能因为转换改写成函数就把 ValueError 漏成 500。
        with pytest.raises(QualityScheduleValidation, match="gate_state"):
            list_quality_observations(engine, **scope, gate_state="stale")
    finally:
        engine.dispose()


def test_observation_filtering_keeps_the_public_spelling_accepted_and_narrows_for_real(
    tmp_path: Path,
) -> None:
    """三件事分别钉住，避免"等于空集"把自己糊过去：

    1. 出参侧：真数据读回来是**对外**拼法（这份 fixture 里 authority 过期 → `blocked`，
       非别名值原样透传，正好证明转换没有把别的状态也改写）。
    2. 过滤真的收窄：按 `blocked` 筛得到这一行，按 `waived` 筛得到空。
    3. 入参侧两种拼写都被接受、结果互等，且都不报错 —— `passed` 是契约对外的拼法，
       库里存的是 `passing`，两者必须走同一条查询。

    **残余缺口（不藏）**：这份 fixture 只会产出 `blocked`（过期 authority），而
    `dataset_release_quality_observations` 是**append-only 带不可变触发器**的（我试过用
    UPDATE 把它摆成 `passing`，直接被 `are immutable` 拒掉 —— 那道栅栏本身是对的）。
    所以"别名两种拼各自命中同一批**非空**行"这一格只有单元级覆盖
    （`tests/test_release_quality_gate_states.py`），DB 级要等一个"健康 SLO"夹具。
    """
    engine, execution_now, _ = _prepare_expired_execution_authority(tmp_path)
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
        scope = {"tenant_id": "tenant-a", "actor_id": "owner-a", "dataset_id": "dataset-a"}
        everything = list_quality_observations(engine, **scope).body["items"]
        assert [item["scan_run_id"] for item in everything] == [run.id]
        assert {item["gate_state"] for item in everything} == {"blocked"}

        assert [
            item["scan_run_id"]
            for item in list_quality_observations(
                engine, **scope, gate_state="blocked"
            ).body["items"]
        ] == [run.id]
        assert list_quality_observations(engine, **scope, gate_state="waived").body["items"] == []

        aliased = [
            list_quality_observations(engine, **scope, gate_state=spelling).body["items"]
            for spelling in ("passed", "passing")
        ]
        assert aliased[0] == aliased[1] == []
    finally:
        engine.dispose()
