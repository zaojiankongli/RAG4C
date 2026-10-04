"""F1.2 改进验收判据的守卫测试。

这个脚本的作用是**防止改动被"看起来降了幻觉"骗过去**。它必须能区分三种
情况，而这三者在指标上都有各自的形态：

1. **真改进**：幻觉率降，过度拒答与有据性基本不动；
2. **关门了**：幻觉率降，但过度拒答暴涨——拒答总量没变，只是对象换了；
3. **提门槛不是做判准**：幻觉率降，但有据性/相关性同步下跌——把"真的答错
   了"也判成 topic_only。

第 2、3 种都会让"幻觉率"这个数字变好看，而项目并没有变好。所以判据不只看
幻觉率，而是看**配对变化**。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "verify_f12_improvement.py"
_spec = importlib.util.spec_from_file_location("verify_f12_improvement", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

verdict = _mod.verdict


def _report(**metrics: float) -> dict[str, Any]:
    base = {
        "refusal_rate": 0.09,
        "over_refusal_rate": 0.04,
        "hallucination_rate": 0.64,
        "avg_groundedness": 0.83,
        "avg_relevance": 0.82,
        "citation_failure_rate": 0.25,
        "degraded_rate": 0.10,
    }
    base.update(metrics)
    return {"metrics": base}


# ---------------------------------------------------------------------------
# 1：真改进
# ---------------------------------------------------------------------------


def test_genuine_improvement_passes() -> None:
    """幻觉率降、其他基本不动。"""
    result = verdict(
        _report(),
        _report(hallucination_rate=0.18, over_refusal_rate=0.08, avg_groundedness=0.81),
    )
    assert result["passed"] is True
    assert result["failures"] == []


def test_no_change_fails() -> None:
    """没改动 = 幻觉率没降，必须判不通过（自比时不能蒙混过关）。"""
    result = verdict(_report(), _report())
    assert result["passed"] is False
    assert any("幻觉率未下降" in f for f in result["failures"])


def test_hallucination_increase_fails() -> None:
    result = verdict(_report(), _report(hallucination_rate=0.70))
    assert result["passed"] is False
    assert any("幻觉率未下降" in f for f in result["failures"])


# ---------------------------------------------------------------------------
# 2：关门了（幻觉降但过度拒答暴涨）
# ---------------------------------------------------------------------------


def test_closing_the_gate_does_not_count_as_improvement() -> None:
    """幻觉 0.64 -> 0.10 但过度拒答 0.04 -> 0.34：拒答总量没变，只是对象换了。"""
    result = verdict(
        _report(),
        _report(hallucination_rate=0.10, over_refusal_rate=0.34, refusal_rate=0.40),
    )
    assert result["passed"] is False
    assert any("over_refusal_rate" in f for f in result["failures"])


def test_answered_rate_note_flags_the_tradeoff() -> None:
    """总作答率下降时必须明说"降幅里有多拒成分"。"""
    result = verdict(
        _report(refusal_rate=0.09),
        _report(hallucination_rate=0.10, over_refusal_rate=0.30, refusal_rate=0.36),
    )
    assert any("多拒" in n for n in result["notes"])


def test_answered_rate_note_credits_real_reduction() -> None:
    """幻觉降但总作答率几乎不降时，要说清降幅不是靠多拒答换来的。

    拒答率 0.09 -> 0.11（总作答率 0.91 -> 0.89，跌 0.02，在 0.05 容差内），
    幻觉率 0.64 -> 0.18：这是真降而非多拒。
    """
    result = verdict(
        _report(refusal_rate=0.09),
        _report(hallucination_rate=0.18, over_refusal_rate=0.09, refusal_rate=0.11),
    )
    assert any("不是靠多拒答换来的" in n for n in result["notes"])


# ---------------------------------------------------------------------------
# 3：提门槛而不是做判准
# ---------------------------------------------------------------------------


def test_raising_the_bar_is_not_judgement_accuracy() -> None:
    """幻觉降但有据性同步下跌 -> 把"真的答错了"也判成 topic_only 了。"""
    result = verdict(
        _report(),
        _report(hallucination_rate=0.20, avg_groundedness=0.70, avg_relevance=0.70),
    )
    assert result["passed"] is False
    assert any("avg_groundedness" in f for f in result["failures"])
    assert any("topic_only" in f for f in result["failures"])


def test_groundedness_may_drop_slightly_without_failing() -> None:
    """有据性小跌（容差内）不该判不通过——v2 多了一个维度，噪声会有一点。"""
    result = verdict(_report(), _report(hallucination_rate=0.20, avg_groundedness=0.79))
    assert result["passed"] is True


def test_degraded_rate_increase_is_caught() -> None:
    """降级率涨说明这轮数字更不可信，不能算改进。"""
    result = verdict(_report(), _report(hallucination_rate=0.20, degraded_rate=0.30))
    assert result["passed"] is False
    assert any("degraded_rate" in f for f in result["failures"])


# ---------------------------------------------------------------------------
# 表格
# ---------------------------------------------------------------------------


def test_every_metric_is_reported_even_unjudged() -> None:
    """refusal_rate 只报不判（它本来就该随幻觉率降），但仍要在表里。"""
    result = verdict(_report(), _report(hallucination_rate=0.20, refusal_rate=0.30))
    metrics = {row["metric"] for row in result["rows"]}
    assert "refusal_rate" in metrics
    assert len(metrics) == len(_mod.METRICS)
