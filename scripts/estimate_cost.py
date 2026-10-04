"""F4.1 成本核算：把用量台账换算成金额，并给出「每条问答多少钱」。

## 为什么需要这个工具

台账里只有 token（``eval/baseline*.json`` 的 ``usage``），而 token 不是钱。
F1.2 那一轮把 total token 从 2,499,996 涨到 2,963,457（+19%），但
**「+19%」无法支撑任何决策**——19% 是 3 块钱还是 3000 块？只有金额能回答
「值不值」。

## 价格从哪来

**刻意不内置价格。** 价格会变，写死就是在骗人——所以本仓的
``settings.llm.price_table`` 默认是空字典（见 ``config/settings.py`` 的
注释），没配价格时 ``cost`` 记 0 并把这部分 token 如实报进
``unpriced_total_tokens``（「宁可说算不出钱，也不编一个数」）。

本工具读价格表。**填价格是部署方的决策**（本仓 `.env` 不入库），本工具
只负责把填好的价格算成可比较的数字。

### 键用「槽位」而不是「模型」

评测报告的 ``usage.by_slot`` 每条**只有** ``slot``，**没有** ``model``
（实测确认：键是 slot / calls / cached_calls / failures / prompt_tokens /
completion_tokens / total_tokens / saved_* / cost）。所以按模型配价在这里
行不通——报告根本没告诉你每个槽位用了哪个模型。

而槽位恰恰是**更有用的粒度**：一个部署可以「生成用大模型、裁判用小模型」，
这正是成本优化的主要手段。用槽位做键，配置写起来也更直白：

    {"generation": {"prompt": 0.0001, "completion": 0.00015},
     "judge":     {"prompt": 0.0001, "completion": 0.00015}}

（上面那组数字是 Qwen3.5-9B 的真实牌价换算而来：$0.10/$0.15 每 1M token。）

单位与 ``core/llm_usage.py`` 的口径一致：**每 1K token**。
平台多按**每 1M token** 标价，换算除 1000。

## 输出的三个口径

1. **整轮总额**——一轮基线烧多少钱。
2. **每条问答**——除以用例数。这是唯一能用来做「换模型值不值」比较的数字：
   总额随用例数线性变化，单价才反映效率。
3. **未计价 token**——占总量多少。占比高说明价格表没配全，金额不可信。

刻意不做的事：**不把金额算进任何门禁阈值**。价格会变，用价格当门禁会让
同一个代码在不同月份「突然失败」；成本决策应该人来做，工具只给数字。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

#: ``core/llm_usage.py`` 的口径是每 1K token；平台多按每 1M 标价。
PER_MILLION = 1_000.0


def load_price_table(url_or_path: str) -> dict[str, dict[str, float]]:
    """读价格表：接受 JSON 文件路径或 inline JSON。

    inline JSON 是为了配合 ``RAG4C_LLM_PRICE_TABLE`` 那种写法——
    同一个字符串既能被 settings 读，也能直接喂给本工具。
    """
    text = url_or_path.strip()
    if text.startswith("{"):
        return json.loads(text)
    return json.loads(Path(text).read_text(encoding="utf-8"))


def per_million_to_per_1k(per_million: float) -> float:
    """平台价（每 1M token）-> 本仓口径（每 1K token）。"""
    return per_million / PER_MILLION


def price_usage(report: dict[str, Any], table: dict[str, dict[str, float]]) -> dict[str, Any]:
    """按价格表给一份报告的成本。

    口径与 ``core/llm_usage.py.Ledger.cost`` 逐字一致（每 1K token、
    缺 prompt/completion 任一项就整段不计价）。自己实现而不是 import
    Ledger，是因为报告里没有逐次账本、只有按槽位的合计。

    键是**槽位**（报告的 ``by_slot`` 里没有 model 字段，见模块 docstring）。
    支持 ``*`` 兜底，与 Ledger 的模型名/``*`` 兜底同一思路。
    """
    usage = report.get("usage") or {}
    slots = usage.get("by_slot") or []
    rows: list[dict[str, Any]] = []
    total = 0.0
    unpriced = 0.0
    for entry in slots:
        slot = str(entry.get("slot") or "-")
        prompt = float(entry.get("prompt_tokens") or 0.0)
        completion = float(entry.get("completion_tokens") or 0.0)
        tok = float(entry.get("total_tokens") or 0.0)
        spec = table.get(slot) or table.get("*") or {}
        p_rate = spec.get("prompt")
        c_rate = spec.get("completion")
        if p_rate is None or c_rate is None:
            unpriced += tok
            amount = None
        else:
            amount = prompt / 1000.0 * float(p_rate) + completion / 1000.0 * float(c_rate)
            total += amount
        rows.append(
            {
                "slot": slot,
                "calls": float(entry.get("calls") or 0.0),
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": tok,
                # 同上：不 round，金额量级太小。
            "amount": amount,
            }
        )

    grand = float(usage.get("total_tokens") or 0.0)
    # 汇总层的 token 与逐槽位之和可能不等（老报告没有 by_slot）。差额算未计价，
    # 宁可报"算不出"也不要漏算成 0。
    slot_sum = sum(r["total_tokens"] for r in rows)
    if rows and slot_sum < grand:
        unpriced += grand - slot_sum

    cases = len(report.get("cases") or [])
    return {
        "currency_note": "金额单位与价格表一致（部署方自定）；不做 round——"
                         "金额量级常在 1e-6，round 会吃掉 20% 的相对精度",
        "total_amount": total,
        "total_tokens": grand,
        "unpriced_tokens": round(unpriced, 3),
        "priced_ratio": round((grand - unpriced) / grand, 4) if grand else None,
        "cases": cases,
        # 刻意**不** round：金额量级常在 1e-6 ~ 1e-3，round(x, 6) 在
        # 2.5e-06 上会给出 3e-06（相对误差 20%）。这类数字是给人做
        # 「换模型值不值」判断用的，精度比"看起来整齐"重要。
        "per_case_amount": (total / cases) if cases else None,
        "by_slot": sorted(rows, key=lambda r: -r["total_tokens"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F4.1 成本核算")
    parser.add_argument("--report", default="eval/baseline-v3.json", help="用量台账所在报告")
    parser.add_argument(
        "--prices", required=True,
        help="价格表：JSON 文件路径，或 inline JSON。"
             "口径是**每 1K token**（平台按每 1M 标价要除 1000）。",
    )
    parser.add_argument("--compare", default="", help="对比报告（可选，给出 delta）")
    parser.add_argument("--out", default="eval/.cache/f41-cost.json")
    args = parser.parse_args(argv)

    table = load_price_table(args.prices)
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    result = price_usage(report, table)
    result["report"] = args.report

    if args.compare:
        other = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        other_result = price_usage(other, table)
        a, b = other_result, result
        if a["total_amount"]:
            result["delta"] = {
                "baseline": args.compare,
                "amount_delta": round(b["total_amount"] - a["total_amount"], 6),
                "amount_ratio": round(b["total_amount"] / a["total_amount"], 4),
                "token_ratio": round(b["total_tokens"] / a["total_tokens"], 4)
                if a["total_tokens"] else None,
                "per_case_delta": (
                    round(b["per_case_amount"] - a["per_case_amount"], 6)
                    if b["per_case_amount"] and a["per_case_amount"] else None
                ),
            }

    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 70)
    print("F4.1 成本核算（口径：每 1K token，与 core/llm_usage.py 一致）")
    print("=" * 70)
    print(f"报告      {args.report}")
    print(f"用例数    {result['cases']}")
    print(f"总 token  {result['total_tokens']:,.0f}")
    print(f"总金额    {result['total_amount']}")
    print(f"每条问答  {result['per_case_amount']}")
    if result["priced_ratio"] is not None:
        print(f"已计价    {result['priced_ratio']:.2%}"
              f"（未计价 {result['unpriced_tokens']:,.0f} token）")
    if result["priced_ratio"] is not None and result["priced_ratio"] < 1.0:
        print("\n⚠ 有 token 未计价，总金额**不可信**。请把价格表配全"
              "（或用 '*' 兜底）后重算。")

    print("\n按槽位（token 倒序）：")
    print(f"  {'slot':<14}{'calls':>7}{'prompt':>13}{'completion':>12}{'amount':>12}{'占比':>8}")
    grand_amt = result["total_amount"] or 1.0
    for row in result["by_slot"]:
        amt = "-" if row["amount"] is None else f"{row['amount']:.4f}"
        share = f"{row['amount'] / grand_amt:.1%}" if row["amount"] else "-"
        print(f"  {row['slot']:<14}{row['calls']:>7.0f}{row['prompt_tokens']:>13,.0f}"
              f"{row['completion_tokens']:>12,.0f}{amt:>12}{share:>8}")

    if "delta" in result:
        d = result["delta"]
        print(f"\n对比 {d['baseline']}：")
        print(f"  金额   {d['amount_delta']:+.6f}（×{d['amount_ratio']}）")
        print(f"  token  ×{d['token_ratio']}")
        if d["per_case_delta"] is not None:
            print(f"  每条   {d['per_case_delta']:+.6f}")

    print(f"\n已写入 {args.out}")
    print("提醒：金额不进任何门禁阈值——价格会变，用价格当门禁会让同一份代码")
    print("      在不同月份「突然失败」。成本决策由人做，工具只给数字。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
