"""F2.2 路由分布采集的守卫测试。

计划要求"先查清路由器实际把多少比例的查询分到 hybrid / dense 等路径"。
这一步不是走过场——它是 A/B 的**前提**：

- 如果 99% 的查询都走同一条路，"换默认策略"的影响面就小到不值得做 A/B；
- 如果分布分散，才值得投入做三列对比表。

而"分布"这件事特别容易读错：路由决策里有 ``degraded``（低置信度降级）这个
维度，而低置信度时走的是**安全默认 hybrid**。把降级的那部分算进某个路由，
会让分布看起来比实际更集中——进而得出"分布集中、换默认没意义"这个相反结论。

守卫五条：
1. 占比以**有效样本**（无 error）为分母，出错的查询不算进任何路由；
2. 降级率单列，不混进路由占比；
3. 按语言分组时也要给条数——某语言只有 3 条时，占比不可读；
4. 置信度给 min/p50/max 而不是均值：均值会被极端值拉走；
5. 采集失败要能被看见（errors 计数），不能静默变成"该查询没走任何路由"。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "collect_routing_distribution.py"
_spec = importlib.util.spec_from_file_location("collect_routing_distribution", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

analyze = _mod.analyze


def _payload(rows: list[dict]) -> dict:
    return {"total": len(rows), "rows": rows}


# ---------------------------------------------------------------------------
# 1：分母
# ---------------------------------------------------------------------------


def test_shares_use_valid_rows_as_denominator() -> None:
    """出错的查询不算进任何路由：否则占比之和 > 100%，或某路由被虚高。"""
    rows = [
        {"target": "full", "confidence": 0.5, "degraded": False, "ms": 1.0, "corpus_language": "en"},
        {"target": "hybrid", "confidence": 0.1, "degraded": True, "ms": 1.0, "corpus_language": "en"},
        {"error": "boom", "ms": 1.0, "corpus_language": "en"},
    ]
    result = analyze(_payload(rows))
    assert result["errors"] == 1
    assert result["targets"] == {"full": 1, "hybrid": 1}
    assert abs(sum(result["shares"].values()) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# 2：降级单列
# ---------------------------------------------------------------------------


def test_degraded_rate_is_separate_from_target_share() -> None:
    """低置信度降级必须单列。

    降级时走的是**安全默认 hybrid**。把它算进 hybrid 的占比，会让分布看起来
    比实际更集中，进而得出"分布集中、换默认没意义"这个与事实相反的结论。
    """
    rows = [
        {"target": "hybrid", "confidence": 0.02, "degraded": True, "ms": 1.0, "corpus_language": "en"}
        for _ in range(8)
    ] + [{"target": "full", "confidence": 0.4, "degraded": False, "ms": 1.0, "corpus_language": "en"}]
    result = analyze(_payload(rows))
    assert result["degraded"] == 8
    assert result["degraded_rate"] == 0.8889
    assert result["shares"]["hybrid"] == 0.8889
    # 且降级信息必须在结果里可见，不能只体现在占比里
    assert "degraded_rate" in result and "degraded" in result


# ---------------------------------------------------------------------------
# 3：语言分组
# ---------------------------------------------------------------------------


def test_by_language_reports_counts_not_just_shares() -> None:
    """某语言只有 3 条时，占比读不出可靠性，必须带条数。"""
    rows = [
        {"target": "full", "confidence": 0.3, "degraded": False, "ms": 1.0, "corpus_language": "en"}
        for _ in range(10)
    ] + [
        {"target": "hybrid", "confidence": 0.2, "degraded": True, "ms": 1.0, "corpus_language": "zh"}
        for _ in range(3)
    ]
    by_lang = analyze(_payload(rows))["by_language"]
    assert by_lang["en"] == {"full": 10}
    assert by_lang["zh"] == {"hybrid": 3}
    # 分组存的是计数而非占比（占比需要分母，分母在同一段里就有）
    assert sum(by_lang["en"].values()) == 10


# ---------------------------------------------------------------------------
# 4：置信度
# ---------------------------------------------------------------------------


def test_confidence_uses_median_not_mean() -> None:
    """均值会被极端值拉走；路由置信度恰恰常有极端值。"""
    rows = [
        {"target": "hybrid", "confidence": c, "degraded": False, "ms": 1.0, "corpus_language": "en"}
        for c in (0.01, 0.02, 0.03, 0.99)
    ]
    conf = analyze(_payload(rows))["confidence"]
    assert conf["n"] == 4
    assert conf["min"] == 0.01 and conf["max"] == 0.99
    # 中位落在 0.02/0.03 之间；均值会落在 0.2625 附近，明显偏高
    assert 0.02 <= conf["p50"] <= 0.03


# ---------------------------------------------------------------------------
# 5：失败可见
# ---------------------------------------------------------------------------


def test_errors_are_counted_and_excluded_from_targets() -> None:
    """采集失败必须能被看见，不能静默变成"该查询没走任何路由"。"""
    rows = [{"error": "boom", "ms": 1.0, "corpus_language": "en"} for _ in range(3)]
    result = analyze(_payload(rows))
    assert result["errors"] == 3
    assert result["targets"] == {}, "失败的查询不该落进任何路由"
    assert result["shares"] == {}
    # 置信度也不能凭空造数
    assert result["confidence"]["n"] == 0
