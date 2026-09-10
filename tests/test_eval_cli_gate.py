"""R5-A：eval CLI release gate 接线测试（eval.run_eval.main）。

覆盖：
- `--gate` 干跑（桩裁判分数不达标）→ exit 1（RELEASE GATE FAILED）；
- 无 `--gate` 干跑 → exit 0（不破坏原行为）；
- `--baseline` 存在时输出对比（不崩溃）；
- `--baseline` 指向不存在的文件 → exit 1（明确报错）。
"""
from __future__ import annotations

from pathlib import Path

from eval.run_eval import main


def _run(*args: str) -> int:
    return main([*args, "--out", str(Path("eval/results-cli-test.json"))])


def test_gate_dry_run_fails_on_stub_scores() -> None:
    # 干跑桩裁判 avg_groundedness=0.5 < 0.6 阈值 → gate FAIL → exit 1
    assert _run("--dry-run", "--gate") == 1


def test_no_gate_dry_run_succeeds() -> None:
    assert _run("--dry-run") == 0


def test_baseline_missing_returns_error() -> None:
    assert _run("--dry-run", "--baseline", "eval/nonexistent-baseline.json") == 1


def test_baseline_compare_runs() -> None:
    # 先用一次评测产出 baseline，再带 baseline 对比跑（应 exit 0，对比不崩）
    from eval.run_eval import as_v2, run_dry_run, save_report
    from eval.dataset_sample import SAMPLE_DATASET

    baseline = Path("eval/results-cli-baseline.json")
    save_report(as_v2(run_dry_run(SAMPLE_DATASET), dataset_spec="sample", pipeline_spec="dry"), baseline)
    try:
        assert _run("--dry-run", "--baseline", str(baseline)) == 0
    finally:
        baseline.unlink(missing_ok=True)


def test_gate_thresholds_custom_makes_dry_run_pass(tmp_path: Path) -> None:
    # 自定义阈值放宽（干跑桩分数 avg_groundedness=0.5 >= 0.1）→ gate PASS → exit 0
    th = tmp_path / "th.json"
    th.write_text(
        '{"hallucination_rate": ["le", 1.0], "over_refusal_rate": ["le", 1.0], '
        '"citation_failure_rate": ["le", 1.0], "avg_groundedness": ["ge", 0.1], '
        '"avg_relevance": ["ge", 0.1]}',
        encoding="utf-8",
    )
    assert _run("--dry-run", "--gate", "--gate-thresholds", str(th)) == 0


def test_gate_thresholds_with_bom_is_parsed(tmp_path: Path) -> None:
    # Windows 编辑器生成的带 BOM 的 JSON 应能正常读取（utf-8-sig 兼容）
    th = tmp_path / "th-bom.json"
    th.write_bytes(b"\xef\xbb\xbf" + b'{"hallucination_rate": ["le", 1.0]}')
    assert _run("--dry-run", "--gate", "--gate-thresholds", str(th)) == 0


def test_gate_thresholds_missing_file_returns_error() -> None:
    assert _run("--dry-run", "--gate", "--gate-thresholds", "eval/nonexistent-th.json") == 1
