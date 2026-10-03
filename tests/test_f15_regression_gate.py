"""F1.5 基线接入发布门禁的守卫测试。

计划要求："``--gate`` 带上 F1.2 的基线，作为之后每次改动的对照。验收：
故意把阈值调坏一次，门禁变红。"

这里补的是一个真实缺口：改造前的门禁只对**绝对阈值**判定。计划那句
"作为之后每次改动的对照"在绝对阈值下是做不到的——有据性从 0.78 掉到 0.62
仍然高于 0.60 那条线，门禁照样放行。一次"仍然达标但明显变差"的改动就这么
静默通过了。

守卫六条：
1. 绝对阈值判定行为不变（既有调用方不传 baseline 时结果一致）；
2. 劣化带**单边**：变好永远通过，只有变差才失败；
3. 在容忍带内的轻微下降通过、超出则失败——这才是"对照"；
4. 比率类与质量类的劣化方向相反（幻觉率涨=坏，有据性跌=坏）；
5. 绝对线与劣化带是**两道独立的闸**：过了一道不过另一道仍要红；
6. 基线缺某个指标时跳过而不是拿 0 当基线（否则"上次没这个数"会被误报成
   "上次是 0、这次涨了"）。
"""
from __future__ import annotations

from eval.run_eval import (
    DEFAULT_REGRESSION_TOLERANCE,
    DEFAULT_RELEASE_THRESHOLDS,
    as_v2,
    release_gate,
)


def _report(**metrics: float):
    base = {
        "hallucination_rate": 0.05,
        "citation_failure_rate": 0.10,
        "over_refusal_rate": 0.10,
        "avg_groundedness": 0.78,
        "avg_relevance": 0.80,
        "degraded_rate": 0.0,
    }
    base.update(metrics)
    return as_v2(_fake(base))


def _fake(metrics: dict[str, float]):
    from eval.run_eval import EvalReport

    return EvalReport(metrics=metrics)


# ---------------------------------------------------------------------------
# 1：绝对阈值行为不变
# ---------------------------------------------------------------------------


def test_absolute_gate_still_works_without_baseline() -> None:
    ok = release_gate(_report())
    assert ok["passed"] is True
    assert ok["regressions"] == []

    bad = release_gate(_report(hallucination_rate=0.5))
    assert bad["passed"] is False
    assert "hallucination_rate" in bad["failures"]


def test_degraded_rate_is_still_gated_by_absolute_threshold() -> None:
    """降级率是"这轮数字能不能信"，必须由绝对线拦，不依赖基线。"""
    result = release_gate(_report(degraded_rate=0.30))
    assert result["passed"] is False
    assert "degraded_rate" in result["failures"]


# ---------------------------------------------------------------------------
# 2 / 3：劣化带单边
# ---------------------------------------------------------------------------


def test_improvement_never_fails_the_regression_band() -> None:
    """单边：变好永远不拦。只拦变差，否则"越改越好"会被门禁挡住。"""
    baseline = _report(avg_groundedness=0.70, hallucination_rate=0.20)
    better = _report(avg_groundedness=0.95, hallucination_rate=0.01)
    result = release_gate(better, baseline=baseline)
    assert result["regressions"] == []
    assert result["passed"] is True


def test_small_drift_within_tolerance_passes() -> None:
    # 刻意取 0.01（带内 0.02 的一半）而不是"带 - 0.001"：后者在二进制浮点
    # 下会算出 0.020000000000000018 > 0.02，让这条测试变成在测浮点误差。
    baseline = _report(avg_groundedness=0.780)
    drifted = _report(avg_groundedness=0.770)
    result = release_gate(drifted, baseline=baseline)
    assert result["regressions"] == []
    assert result["passed"] is True


def test_drift_exactly_at_tolerance_boundary() -> None:
    """边界语义：变差量 **超过** 容忍带才失败，正好等于不算。

    断言写成"相对关系"而不是硬编码数值——``0.78 - 0.02`` 在二进制浮点下
    并不精确等于 0.76，硬编码会把这条测试变成在测浮点表示误差。
    """
    base_value = 0.78
    tol = DEFAULT_REGRESSION_TOLERANCE["avg_groundedness"]
    baseline = _report(avg_groundedness=base_value)

    at = release_gate(_report(avg_groundedness=base_value - tol), baseline=baseline)
    assert at["regressions"] == [], "变差量正好等于容忍带时应通过"

    beyond = release_gate(
        _report(avg_groundedness=base_value - tol - 0.05), baseline=baseline
    )
    regressed = {r["metric"] for r in beyond["regressions"]}
    assert "avg_groundedness" in regressed
    # 明确超出容忍带（留 1e-6 余量避开浮点表示）
    assert beyond["regressions"][0]["worse_by"] > tol + 1e-6


def test_drop_beyond_tolerance_fails_even_though_absolute_line_passes() -> None:
    """这是 F1.5 的核心场景：0.62 仍高于绝对线 0.60，但比基线掉了 0.16。"""
    baseline = _report(avg_groundedness=0.78)
    degraded = _report(avg_groundedness=0.62)
    absolute_only = release_gate(degraded)
    assert absolute_only["passed"] is True, "绝对线确实放行了——这正是缺口"
    with_baseline = release_gate(degraded, baseline=baseline)
    assert with_baseline["passed"] is False
    assert with_baseline["failures"] == [], "失败原因应记在 regressions 而非 failures"
    assert with_baseline["regressions"][0]["metric"] == "avg_groundedness"
    assert with_baseline["regressions"][0]["worse_by"] > 0.15


# ---------------------------------------------------------------------------
# 4：劣化方向
# ---------------------------------------------------------------------------


def test_ratio_and_quality_metrics_degrade_in_opposite_directions() -> None:
    baseline = _report(hallucination_rate=0.05, avg_groundedness=0.78)
    worse = _report(hallucination_rate=0.20, avg_groundedness=0.78)
    result = release_gate(worse, baseline=baseline)
    metrics = {r["metric"] for r in result["regressions"]}
    assert "hallucination_rate" in metrics
    assert "avg_groundedness" not in metrics

    # 反过来：有据性掉、幻觉率不变
    result2 = release_gate(_report(avg_groundedness=0.60), baseline=baseline)
    assert "avg_groundedness" in {r["metric"] for r in result2["regressions"]}


def test_better_groundedness_is_not_a_regression() -> None:
    baseline = _report(avg_groundedness=0.60)
    result = release_gate(_report(avg_groundedness=0.90), baseline=baseline)
    assert "avg_groundedness" not in {r["metric"] for r in result["regressions"]}


# ---------------------------------------------------------------------------
# 5：两道闸独立
# ---------------------------------------------------------------------------


def test_absolute_and_regression_are_two_independent_gates() -> None:
    baseline = _report(hallucination_rate=0.02, avg_groundedness=0.78)
    # 幻觉率绝对线已破（>0.10），有据性相对也劣化 -> 两道都红
    result = release_gate(
        _report(hallucination_rate=0.40, avg_groundedness=0.60), baseline=baseline
    )
    assert "hallucination_rate" in result["failures"]
    assert "avg_groundedness" in {r["metric"] for r in result["regressions"]}
    assert result["passed"] is False


# ---------------------------------------------------------------------------
# 6：基线缺指标
# ---------------------------------------------------------------------------


def test_metric_missing_from_baseline_is_skipped_not_treated_as_zero() -> None:
    """基线里没有这个指标时必须跳过。

    拿 0 当基线会把"上次没这个数"误报成"上次是 0、这次涨了"——而
    degraded_rate 正是 F1.2 之后才加进报告的，旧基线里必然没有。
    """
    legacy = _fake({"hallucination_rate": 0.05, "avg_groundedness": 0.78})  # 无 degraded_rate
    current = _report(degraded_rate=0.20)
    result = release_gate(current, baseline=as_v2(legacy))
    assert "degraded_rate" not in {r["metric"] for r in result["regressions"]}


def test_every_gated_metric_has_a_tolerance_or_is_intentionally_absent() -> None:
    """绝对线里出现的指标都该有劣化带，除非有意识地不参与相对检查。"""
    missing = set(DEFAULT_RELEASE_THRESHOLDS) - set(DEFAULT_REGRESSION_TOLERANCE)
    # 允许的缺席项必须在注释里有理由；当前设计里没有缺席项。
    assert missing == set(), f"这些指标进了绝对门禁却没有劣化带：{missing}"
