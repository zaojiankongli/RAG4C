"""F1.3 阈值分布分析的守卫测试。

计划要求："在『过度拒答』与『幻觉』之间给出取舍曲线，而不是单点。" 这句话
约束的不只是产出物，还包括**口径**——两把尺子的量级不同、不可互换；过度
拒答的分母是可答总数、幻觉的分母是不可答总数。这两处一旦搞混，曲线上的
每个点都会指向相反的结论，而它看上去仍然像一条合理的曲线。

守卫六条：
1. 过度拒答 / 幻觉的分母各归各族（不是都除以总数）；
2. 阈值语义是「**低于**阈值即弃权」；
3. 两族完全分离时曲线能给出零错误点；
4. 两族重叠时如实报"不可分"，不假装能给出一个好阈值；
5. 同域硬负样本单独出重叠判定（离域样本会把结论冲淡）；
6. 分位数校准的读数与曲线一致——同一个阈值不许在两处给出不同代价。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "analyze_abstention_threshold.py"
_spec = importlib.util.spec_from_file_location("analyze_abstention_threshold", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

analyze = _mod.analyze
_percentiles = _mod._percentiles
_curve = _mod._curve


def _payload(answerable: list[float], unanswerable: list[float], **kw: Any) -> dict[str, Any]:
    rows = [
        {"id": f"a{i}", "unanswerable": False, "kind": "answerable", "top1_score": v}
        for i, v in enumerate(answerable)
    ] + [
        {"id": f"u{i}", "unanswerable": True, "kind": kw.get("ukind", "on_domain"), "top1_score": v}
        for i, v in enumerate(unanswerable)
    ]
    return {"mode": "score", "collection": "test", "rows": rows}


# ---------------------------------------------------------------------------
# 1 / 2：口径
# ---------------------------------------------------------------------------


def test_curve_uses_each_group_as_its_own_denominator() -> None:
    """过度拒答分母 = 可答数，幻觉分母 = 不可答数。

    两条都除以总数的话，"幻觉率"会被可答样本稀释 4 倍（123 : 22），
    曲线上所有点都偏乐观，而形状看起来仍然完全正常。

    阈值取**观测到的**分数值：候选集就是观测值集合（外加校准分位数），
    两个观测值之间的任何阈值与左端点等价，所以不必另取中间值。
    """
    points = {p["threshold"]: p for p in _curve([0.9, 0.8, 0.7, 0.6], [0.3, 0.2], "top1_score")}
    # 阈值 0.6：可答四条中 0.6 不低于阈值 -> 放行；不可答两条 0.3/0.2 低于 -> 全拒
    assert points[0.6]["over_refusal_rate"] == 0.0
    assert points[0.6]["hallucination_rate"] == 0.0
    # 阈值 0.9：可答里 0.8/0.7/0.6 被拒 -> 3/4；不可答两条仍全被拒
    assert points[0.9]["over_refusal_rate"] == 0.75
    assert points[0.9]["hallucination_rate"] == 0.0
    # 阈值 0.2：不可答的 0.3 与 0.2 都**不低于**阈值 -> 两条全放行 -> 幻觉 1.0
    # （"不低于"含等于：分数刚好压在阈值上算过闸）
    assert points[0.2]["hallucination_rate"] == 1.0
    # 阈值 0.3：只有 0.3 那条被放行，0.2 被拒 -> 幻觉 1/2
    assert points[0.3]["hallucination_rate"] == 0.5


def test_threshold_semantics_is_below_means_abstain() -> None:
    """分数**低于**阈值 = 判"知识库无相关内容" = 弃权。方向反了整条曲线就反了。"""
    points = _curve([0.10], [0.90], "top1_score")
    low = next(p for p in points if p["threshold"] == 0.10)
    # 阈值 0.10：可答那条 0.10 不低于阈值 -> 放行；不可答 0.90 远高于 -> 幻觉
    assert low["over_refusal_rate"] == 0.0
    assert low["hallucination_rate"] == 1.0


# ---------------------------------------------------------------------------
# 3 / 4：可分与不可分
# ---------------------------------------------------------------------------


def test_separable_distributions_yield_a_zero_error_point() -> None:
    result = analyze(_payload([0.80 + i * 0.01 for i in range(10)], [0.10 + i * 0.01 for i in range(5)]))
    assert result["overlap"]["all"]["separable"] is True
    best = min(result["tradeoff_curve"], key=lambda p: p["total_error"])
    assert best["total_error"] == 0.0


def test_overlapping_distributions_are_reported_as_not_separable() -> None:
    """重叠时必须如实说"不可分"——给个"最优阈值"等于替决策者掩盖了代价。"""
    result = analyze(_payload([0.50 + i * 0.01 for i in range(10)], [0.45 + i * 0.01 for i in range(10)]))
    assert result["overlap"]["all"]["separable"] is False
    assert "重叠" in result["overlap"]["all"]["note"]
    # 曲线里不该存在零错误点——这正是"没有免费阈值"的可执行表述
    assert min(p["total_error"] for p in result["tradeoff_curve"]) > 0.0


# ---------------------------------------------------------------------------
# 5：同域硬负样本单独判定
# ---------------------------------------------------------------------------


def test_on_domain_hard_negatives_are_judged_separately() -> None:
    """离域样本会把结论冲淡：余弦阈值就是被同域硬负样本顶穿的。

    构造：可答 0.60~0.69，离域不可答 0.05（同域阈值抓得住），同域不可答
    0.58~0.63（顶穿阈值）。合并判定会以为可分，单独判定才看得出真相。
    """
    payload = {
        "mode": "cosine",
        "collection": "test",
        "rows": (
            [{"id": f"a{i}", "unanswerable": False, "kind": "answerable", "top1_cosine": 0.60 + i * 0.01} for i in range(10)]
            + [{"id": f"off{i}", "unanswerable": True, "kind": "off_domain", "top1_cosine": 0.05} for i in range(3)]
            + [{"id": f"on{i}", "unanswerable": True, "kind": "on_domain", "top1_cosine": 0.58 + i * 0.01} for i in range(5)]
        ),
    }
    result = analyze(payload)
    assert result["counts"]["unanswerable_off_domain"] == 3
    assert result["counts"]["unanswerable_on_domain"] == 5
    assert result["distributions"]["metric"] == "top1_cosine"
    # 离域分布与可答分布不重叠，但同域硬负样本与可答分布重叠
    assert result["overlap"]["on_domain_only"]["separable"] is False


# ---------------------------------------------------------------------------
# 6：校准读数与曲线一致
# ---------------------------------------------------------------------------


def test_calibrated_reading_agrees_with_the_curve() -> None:
    """同一阈值在"校准读数"与"曲线"里必须给出同一个代价，否则报告自相矛盾。"""
    result = analyze(_payload([0.80 + i * 0.01 for i in range(10)], [0.20 + i * 0.02 for i in range(5)]))
    curve_by_threshold = {p["threshold"]: p for p in result["tradeoff_curve"]}
    for entry in result["calibrated"].values():
        point = curve_by_threshold.get(entry["threshold"])
        assert point is not None, f"校准阈值 {entry['threshold']} 不在曲线上"
        assert point["over_refusal_rate"] == entry["over_refusal_rate"]
        assert point["hallucination_rate"] == entry["hallucination_rate"]


def test_percentiles_handle_empty_input() -> None:
    assert _percentiles([]) == {}
    assert _percentiles([0.5])["p50"] == 0.5


def test_rows_with_errors_are_excluded() -> None:
    """采集失败的行不得进入分布——0 会被当成"分数极低"而把阈值带偏。"""
    payload = _payload([0.9, 0.8], [0.1, 0.2])
    payload["rows"].append({"id": "boom", "unanswerable": True, "kind": "on_domain", "error": "x"})
    result = analyze(payload)
    assert result["counts"]["unanswerable"] == 2


@pytest.mark.parametrize("mode,key", [("score", "top1_score"), ("cosine", "top1_cosine")])
def test_metric_follows_mode(mode: str, key: str) -> None:
    """两把尺子不可互换：读哪把尺子由 mode 决定，不能靠猜。"""
    payload = {
        "mode": mode,
        "collection": "test",
        "rows": [
            {"id": "a0", "unanswerable": False, "kind": "answerable", key: 0.9, "top1_score": 0.9, "top1_cosine": 0.9},
            {"id": "u0", "unanswerable": True, "kind": "on_domain", key: 0.1, "top1_score": 0.1, "top1_cosine": 0.1},
        ],
    }
    assert analyze(payload)["distributions"]["metric"] == key
