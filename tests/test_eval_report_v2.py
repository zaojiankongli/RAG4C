"""EvalReport v2 / baseline / compare / release gate 回归测试。

覆盖 eval/run_eval.py 的增量功能（不改变 Evaluator / extract_evidence）：
- as_v2 提升：注入 schema_version / generated_at / dataset_spec / pipeline_spec；
- save_report / load_report 往返：JSON 快照可无损读回；
- compare_reports：逐指标 delta + 方向语义（lower/higher is better）；
- release_gate：固定阈值质量墙，逐项通过/失败 + overall passed。
"""
from __future__ import annotations

from eval.run_eval import (
    EvalReport,
    EvalReportV2,
    as_v2,
    compare_reports,
    load_report,
    release_gate,
    save_report,
)


def _report(**overrides: float) -> EvalReport:
    metrics = {
        "refusal_rate": 0.1,
        "over_refusal_rate": 0.2,
        "hallucination_rate": 0.05,
        "avg_groundedness": 0.75,
        "avg_relevance": 0.8,
        "citation_failure_rate": 0.1,
    }
    metrics.update(overrides)
    return EvalReport(metrics=metrics, cases=[])


def test_as_v2_injects_metadata() -> None:
    v2 = as_v2(_report(), dataset_spec="eval/x.py:SAMPLE", pipeline_spec="rag:answer_query")
    assert v2.schema_version == 2
    assert v2.dataset_spec == "eval/x.py:SAMPLE"
    assert v2.pipeline_spec == "rag:answer_query"
    assert v2.generated_at  # 非空
    assert v2.cases == []
    assert v2.metrics["avg_groundedness"] == 0.75


def test_save_and_load_roundtrip(tmp_path) -> None:
    v2 = as_v2(_report(), dataset_spec="spec", pipeline_spec="pipe")
    path = save_report(v2, tmp_path / "report.json")
    loaded = load_report(path)
    assert isinstance(loaded, EvalReportV2)
    assert loaded.metrics == v2.metrics
    assert loaded.dataset_spec == "spec"
    assert loaded.pipeline_spec == "pipe"
    assert loaded.schema_version == 2


def test_compare_reports_delta_and_direction() -> None:
    current = as_v2(_report(hallucination_rate=0.02, avg_groundedness=0.85))
    baseline = as_v2(_report(hallucination_rate=0.05, avg_groundedness=0.75))
    diff = compare_reports(current, baseline)
    assert diff["delta"]["hallucination_rate"] == -0.03
    assert diff["direction"]["hallucination_rate"] == "improved"  # lower is better
    assert diff["direction"]["avg_groundedness"] == "improved"  # higher is better
    assert diff["delta"]["avg_groundedness"] == 0.10


def test_compare_reports_reports_direction_for_known_metrics_only() -> None:
    current = as_v2(_report())
    baseline = as_v2(_report())
    diff = compare_reports(current, baseline)
    assert diff["direction"]["refusal_rate"] == "unchanged"
    # 未知指标方向标注为 unknown
    current.metrics["custom_metric"] = 1.0
    diff2 = compare_reports(current, baseline)
    assert diff2["direction"]["custom_metric"] == "unknown"


def test_release_gate_passes_when_all_within_threshold() -> None:
    v2 = as_v2(_report())  # 全部在默认阈值内
    result = release_gate(v2)
    assert result["passed"] is True
    assert result["failures"] == []


def test_release_gate_fails_on_quality_regression() -> None:
    v2 = as_v2(_report(hallucination_rate=0.5, avg_groundedness=0.3))
    result = release_gate(v2)
    assert result["passed"] is False
    assert set(result["failures"]) >= {"hallucination_rate", "avg_groundedness"}


def test_release_gate_custom_thresholds() -> None:
    v2 = as_v2(_report(hallucination_rate=0.2))
    result = release_gate(v2, thresholds={"hallucination_rate": ("le", 0.25)})
    assert result["passed"] is True
    result2 = release_gate(v2, thresholds={"hallucination_rate": ("le", 0.1)})
    assert result2["passed"] is False