"""F4.1 成本核算的守卫测试。

台账里只有 token，而 **token 不是钱**。这个工具把 token 换算成金额，目的
是让「+19% token」这种无法支撑决策的数字变成「每条问答多花 0.0003」。

守卫三个最容易出错的地方：

1. **单位**。``core/llm_usage.py`` 的口径是**每 1K token**，平台按**每 1M**
   标价——差 1000 倍。这是最容易错、错了最难发现的（金额看着合理）。
2. **未计价不许算成 0**。缺价格时必须报 ``unpriced_tokens``，而不是静默
   当免费——本仓一贯的口径是「宁可说算不出钱，也不编一个数」。
3. **键是槽位不是模型**。报告的 ``usage.by_slot`` 里**没有 model 字段**
   （实测确认），按模型配价会全部落空、且不报错。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from typing import Any

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "estimate_cost.py"
_spec = importlib.util.spec_from_file_location("estimate_cost", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

price_usage = _mod.price_usage
per_million_to_per_1k = _mod.per_million_to_per_1k


def _report(slots: list[dict[str, Any]], cases: int = 100) -> dict[str, Any]:
    return {
        "cases": [{}] * cases,
        "usage": {
            "total_tokens": sum(s["total_tokens"] for s in slots),
            "by_slot": slots,
        },
    }


def _slot(slot: str, prompt: float, completion: float, calls: float = 10) -> dict[str, Any]:
    return {
        "slot": slot,
        "calls": calls,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


# ---------------------------------------------------------------------------
# 1：单位
# ---------------------------------------------------------------------------


def test_per_million_to_per_1k_divides_by_1000() -> None:
    """平台按每 1M 标价，本仓按每 1K —— 差 1000 倍。"""
    assert per_million_to_per_1k(0.10) == 0.0001
    assert per_million_to_per_1k(0.15) == 0.00015
    # 这个断言是为了让"少除/多除 1000"立刻变红
    assert per_million_to_per_1k(1.0) == 0.001


def test_amount_uses_per_1k_multiplication() -> None:
    """1000 prompt + 1000 completion @ $0.1/$0.15 每 M = $0.00025。"""
    report = _report([_slot("generation", 1000, 1000)])
    table = {"generation": {"prompt": 0.0001, "completion": 0.00015}}
    result = price_usage(report, table)
    # float 尾数：用 approx 而不是等号（0.0001*1 + 0.00015*1 的和不是精确的 0.00025）
    assert result["total_amount"] == pytest.approx(0.00025)


# ---------------------------------------------------------------------------
# 2：未计价
# ---------------------------------------------------------------------------


def test_unpriced_slots_are_reported_not_priced_at_zero() -> None:
    """缺价格的槽位不许静默当免费。

    这是本仓一贯的口径（`core/llm_usage.py` 的 docstring：「宁可说算不出钱，
    也不编一个数」）。工具若把缺价格算成 0，读者会以为那部分真的免费。
    """
    report = _report([_slot("judge", 1000, 1000), _slot("mystery_slot", 500, 500)])
    result = price_usage(report, {"judge": {"prompt": 0.0001, "completion": 0.00015}})
    assert result["unpriced_tokens"] == 1000
    assert result["priced_ratio"] == pytest.approx(0.6667, abs=0.001)
    assert result["total_amount"] == pytest.approx(0.00025)  # 只含 judge 那部分


def test_star_fallback_covers_unspecified_slots() -> None:
    """`*` 兜底：没单独配价的槽位用同一个价（与 Ledger 同一思路）。"""
    report = _report([_slot("judge", 1000, 1000), _slot("brand_new", 1000, 1000)])
    table = {
        "judge": {"prompt": 0.0001, "completion": 0.00015},
        "*": {"prompt": 0.0001, "completion": 0.00015},
    }
    result = price_usage(report, table)
    assert result["unpriced_tokens"] == 0
    assert result["total_amount"] == pytest.approx(0.0005)


def test_missing_completion_price_prices_nothing_for_that_slot() -> None:
    """缺 prompt 或 completion 任一项就整段不计价（与 Ledger 逐字一致）。

    只按一半算钱会给出「看起来有数字、但系统性偏低」的结果——
    比明确报 0 更危险。
    """
    report = _report([_slot("judge", 1000, 1000)])
    result = price_usage(report, {"judge": {"prompt": 0.0001}})
    assert result["total_amount"] == 0.0
    assert result["unpriced_tokens"] == 2000


def test_slot_sum_below_grand_total_counts_as_unpriced() -> None:
    """by_slot 汇总 < 顶层 total 时，差额算未计价。

    差额**不能**当 0：老报告没有 by_slot，或某个槽位的账本丢失，
    那部分钱就是算不出来。宁可报"算不出"，不能漏算成免费。
    """
    report = _report([_slot("judge", 1000, 1000)])
    report["usage"]["total_tokens"] = 5000  # 顶层说 5000，逐槽位只有 2000
    result = price_usage(report, {"judge": {"prompt": 0.0001, "completion": 0.00015}})
    assert result["unpriced_tokens"] == 3000
    assert result["priced_ratio"] == 0.4


# ---------------------------------------------------------------------------
# 3：键是槽位
# ---------------------------------------------------------------------------


def test_pricing_is_keyed_by_slot_not_model() -> None:
    """报告的 by_slot 里没有 model 字段——按模型配价会静默全部落空。

    这条是实测踩出来的：最初的实现按 model 查表，而 `by_slot` 的键只有
    slot / calls / cached_calls / failures / prompt_tokens /
    completion_tokens / total_tokens / saved_* / cost，**没有 model**。
    按模型配价的后果不是报错，而是全部落空、总额 0。
    """
    report = _report([_slot("generation", 1000, 1000)])
    assert "model" not in report["usage"]["by_slot"][0]
    # 按 model 配 -> 落空
    assert price_usage(report, {"Qwen/Qwen3.5-9B": {"prompt": 0.1, "completion": 0.2}})[
        "total_amount"
    ] == 0.0
    # 按 slot 配 -> 命中（float 相加有尾数误差，用 approx 而非等号）
    assert price_usage(report, {"generation": {"prompt": 0.1, "completion": 0.2}})[
        "total_amount"
    ] == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# 每条问答
# ---------------------------------------------------------------------------


def test_per_case_amount_divides_by_case_count() -> None:
    """总额随用例数线性变化，单价才反映效率——只有单价能用来比"换模型值不值"。"""
    table = {"judge": {"prompt": 0.0001, "completion": 0.00015}}
    one = price_usage(_report([_slot("judge", 1000, 1000)], cases=10), table)
    ten = price_usage(_report([_slot("judge", 1000, 1000)], cases=100), table)
    assert one["total_amount"] == ten["total_amount"]
    # 同样 2000 token、同样金额，10 条用例的单价是 100 条的 10 倍
    assert one["per_case_amount"] == pytest.approx(ten["per_case_amount"] * 10)
