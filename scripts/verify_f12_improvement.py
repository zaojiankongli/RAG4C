"""F1.2 改进的端到端验收：v2 模板 + 门独立判定是否真的降低了幻觉率。

计划里 F1.2 的口径是"得到六个数"，但**改进也需要验收**。本次改动的判据在
``docs/2026-10-04-abstention-misses-off-topic-answers.md`` 里写明：

    改完后幻觉率应下降而 avg_groundedness 基本不动。两个数一起掉说明把
    "真的答错了"也判成 topic_only，那是提高门槛而不是做准判断。

本脚本拿两份报告（v1 基线 / v2 报告）跑这个判据，并额外检查两个容易被
"幻觉率降了"掩盖的副作用：

1. **过度拒答率不能同步暴涨**。幻觉率从 0.64 降到 0.20 若伴随过度拒答从
   0.04 涨到 0.30，那是把门关死而不是关准——总量没变，只是拒答对象换了。
2. **可答样本的答案不能变短**。若 v2 让生成器变保守（因为它看到更多拒答），
   有据性会跟着掉，指标就"看起来降了幻觉"。

所以本脚本除了比六个数，还报 **总作答率** 与 **有据性/相关性的配对变化**，
后者是"降幻觉"与"降质量"的唯一可靠区分点。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

#: 六个质量指标（顺序即报告顺序）。
METRICS = (
    "refusal_rate",
    "over_refusal_rate",
    "hallucination_rate",
    "avg_groundedness",
    "avg_relevance",
    "citation_failure_rate",
    "degraded_rate",
)

#: 判据的上下界。改动的目标只有一个：幻觉率降、其他基本不动。
#:
#: 边界不是拍脑袋来的：``over_refusal_rate`` 的容差取 0.08——F1.2 实测
#: 过度拒答只有 0.0407，允许它涨到 0.12 仍属"没有把门关死"；再高就说明
#: 系统是靠"多拒"而不是"判准"来降幻觉的。
TOLERANCE = {
    "over_refusal_rate": 0.08,
    "avg_groundedness": 0.05,
    "avg_relevance": 0.05,
    "citation_failure_rate": 0.05,
    "degraded_rate": 0.05,
    "refusal_rate": None,  # 只报不判：它本来就该随幻觉率一起降
}


def verdict(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """按判据给出通过 / 不通过，并说明每个结论的依据。"""
    m1, m2 = before["metrics"], after["metrics"]
    rows: list[dict[str, Any]] = []
    for key in METRICS:
        a, b = m1.get(key), m2.get(key)
        if a is None or b is None:
            continue
        rows.append({"metric": key, "before": round(a, 4), "after": round(b, 4), "delta": round(b - a, 4)})

    hallucination_delta = m2.get("hallucination_rate", 0) - m1.get("hallucination_rate", 0)
    failures: list[str] = []
    notes: list[str] = []

    if hallucination_delta >= 0:
        failures.append(f"幻觉率未下降（{hallucination_delta:+.4f}）——改动没起作用")
    for key, tol in TOLERANCE.items():
        if tol is None or key not in m1 or key not in m2:
            continue
        delta = m2[key] - m1[key]
        if delta > tol:
            failures.append(f"{key} 涨了 {delta:+.4f}（超过容忍 {tol}）——门是被关死而不是关准")
        elif key.startswith("avg_") and delta < -tol:
            failures.append(
                f"{key} 掉了 {-delta:.4f}（超过容忍 {tol}）——"
                f"与幻觉率同向下跌说明把真答错的也判成 topic_only 了"
            )

    # 总作答率：幻觉率降 + 过度拒答涨，两者之和才是"拒答总量"。
    answered_before = 1 - m1.get("refusal_rate", 0)
    answered_after = 1 - m2.get("refusal_rate", 0)
    if answered_after < answered_before - 0.05:
        notes.append(
            f"总作答率从 {answered_before:.4f} 降到 {answered_after:.4f}——"
            f"拒答总量上升，确认幻觉率的降幅里有「多拒」成分"
        )
    else:
        notes.append(
            f"总作答率 {answered_before:.4f} -> {answered_after:.4f}，"
            f"幻觉率的降幅不是靠多拒答换来的"
        )

    return {
        "rows": rows,
        "hallucination_delta": round(hallucination_delta, 4),
        "answered_rate": {
            "before": round(answered_before, 4),
            "after": round(answered_after, 4),
        },
        "failures": failures,
        "notes": notes,
        "passed": not failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F1.2 改进的端到端验收")
    parser.add_argument("--v1", default="eval/baseline.json", help="改动前的基线")
    parser.add_argument("--v2", default="eval/baseline-v2.json", help="改动后的报告")
    parser.add_argument("--out", default="eval/.cache/f12-acceptance.json")
    args = parser.parse_args(argv)

    before = json.loads(Path(args.v1).read_text(encoding="utf-8"))
    after = json.loads(Path(args.v2).read_text(encoding="utf-8"))
    result = verdict(before, after)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 68)
    print("F1.2 改进验收：v1 有据性模板 -> v2 答案相关性模板 + 门独立判定")
    print("=" * 68)
    print(f"{'指标':<24}{'改动前':>10}{'改动后':>10}{'delta':>10}")
    print("-" * 68)
    for row in result["rows"]:
        flag = ""
        if row["metric"] == "hallucination_rate":
            flag = "  <= 目标"
        print(f"{row['metric']:<24}{row['before']:>10.4f}{row['after']:>10.4f}{row['delta']:>+10.4f}{flag}")

    print()
    for note in result["notes"]:
        print(f"  · {note}")
    if result["failures"]:
        print("\n验收不通过：")
        for item in result["failures"]:
            print(f"  FAIL {item}")
    else:
        print("\n验收通过：幻觉率下降，且没有出现「靠多拒答换幻觉」或「质量同步下跌」。")
    print(f"\n已写入 {args.out}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
