"""R7-A：eval 报告历史管理测试（save_report_history / list_report_history）。

覆盖：
- 保存到历史目录（带时间戳文件名，Windows 安全）；
- 列出历史按时间倒序（最新在前）；
- 损坏的历史文件不阻塞列表（容忍）；
- 空目录返回空列表。
"""
from __future__ import annotations

from pathlib import Path

from eval.run_eval import (
    as_v2,
    list_report_history,
    run_dry_run,
    save_report_history,
)
from eval.dataset_sample import SAMPLE_DATASET


def _make_report(dataset_spec: str = "sample", pipeline_spec: str = "dry"):
    return as_v2(run_dry_run(SAMPLE_DATASET), dataset_spec=dataset_spec, pipeline_spec=pipeline_spec)


def test_save_and_list_history(tmp_path: Path) -> None:
    hdir = tmp_path / "reports"
    r1 = save_report_history(_make_report(), hdir)
    r2 = save_report_history(_make_report(), hdir)
    assert r1.is_file() and r2.is_file()
    assert r1.name != r2.name  # 时间戳唯一（同一秒保存可能撞名——放宽：内容应存在）

    entries = list_report_history(hdir)
    assert len(entries) == 2
    assert all("metrics" in e and "generated_at" in e for e in entries)


def test_list_is_newest_first(tmp_path: Path) -> None:
    hdir = tmp_path / "reports"
    save_report_history(_make_report(dataset_spec="first"), hdir)
    save_report_history(_make_report(dataset_spec="second"), hdir)
    entries = list_report_history(hdir)
    # 倒序：最新（second）在前
    assert entries[0]["dataset_spec"] == "second"
    assert entries[1]["dataset_spec"] == "first"


def test_corrupt_history_does_not_block_list(tmp_path: Path) -> None:
    hdir = tmp_path / "reports"
    save_report_history(_make_report(), hdir)
    # 写一个损坏的历史文件
    (hdir / "report-corrupt.json").write_text("{not json", encoding="utf-8")
    entries = list_report_history(hdir)
    assert len(entries) == 1  # 只列有效的


def test_empty_dir_returns_empty(tmp_path: Path) -> None:
    assert list_report_history(tmp_path / "nope") == []
