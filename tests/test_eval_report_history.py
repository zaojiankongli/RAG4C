"""R7-A：eval 报告历史管理测试（save_report_history / list_report_history）。

覆盖：
- 保存到历史目录（带时间戳文件名，Windows 安全）；
- 列出历史按时间倒序（最新在前）；
- 损坏的历史文件不阻塞列表（容忍）；
- 空目录返回空列表。
"""
from __future__ import annotations

import json
import os
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


def test_ordering_does_not_depend_on_filesystem_mtime_precision(tmp_path: Path) -> None:
    """排序必须只依赖文件名，不依赖 ``st_mtime``。

    真实故障：``list_report_history`` 原先按 ``st_mtime`` 倒序，而
    ``save_report_history`` 的文件名里本来就有毫秒后缀（当初为"同秒多份不
    覆盖"加的）。于是同秒写两份时 mtime 相同，``sorted`` 退化成保持 glob
    的字典序——「最新在前」的承诺不成立，``test_list_is_newest_first``
    偶发失败（实测 235 passed / 1 failed，可复现）。

    这里用**显式写出三份 mtime 完全相同**的文件来复现那个条件：任何依赖
    mtime 的排序在这里都会退化，依赖文件名的排序则稳定。
    """
    hdir = tmp_path / "reports"
    hdir.mkdir()
    payload = _make_report().model_dump(mode="json")
    for stamp in ("2026-10-04T01-00-00-000", "2026-10-04T01-00-01-000", "2026-10-04T01-00-02-000"):
        (hdir / f"report-{stamp}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    # 把 mtime 全设成同一个值——文件系统精度不足时的真实情形
    fixed = 1_700_000_000.0
    for path in hdir.iterdir():
        os.utime(path, (fixed, fixed))

    entries = list_report_history(hdir)
    names = [Path(e["path"]).name for e in entries]
    assert names == sorted(names, reverse=True), f"应按文件名倒序，实际 {names}"
    assert names[0].endswith("01-00-02-000.json"), "最新（文件名最后）在前"
