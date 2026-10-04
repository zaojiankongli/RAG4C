"""F1.4 裁判可信度：用「双提示词一致性」做可自动化的那部分。

计划要求"抽 30 条，人工判定与裁判判定对比，算一致率"。人工标注这一半必须
由人做（`scripts/judge_credibility_check.py` 生成的抽检表就是为此）。

但"裁判是否可信"还有一个**可自动化**的旁证，而且它比单次抽样更便宜：
**同一批用例、两个独立提示词（v1 有据性 / v2 答案相关性），结论一致吗？**

为什么这个信号有意义：v1 与 v2 是两次独立的模型调用，提示词不同、判定维度
不同（v1 判"声明能否由证据推出"，v2 额外判"证据是否回答了问题"）。若两者
在"这条是否该被拒答"上高度一致，说明判定不是提示词的偶然产物；若大面积
不一致，说明至少有一个提示词的判定站不住。

**这个信号不能替代人工一致率**，它测的是"提示词之间的稳定性"而不是
"与人的一致性"。所以本脚本的输出只作参考，不进 release gate。

判据（与 F1.2 的验证口径一致）：v2 的目标是**拦 topic_only 且不动有据性**。
所以"v2 与 v1 一致"该体现在有据性一侧；拒答侧的差异是 v2 的设计意图本身，
不算不一致。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def compare(baseline: dict, v2_report: dict) -> dict[str, object]:
    """按用例 id 对齐两份报告，比六个质量指标。"""
    a = {c["id"]: c for c in baseline.get("cases", [])}
    b = {c["id"]: c for c in v2_report.get("cases", [])}
    common = sorted(set(a) & set(b))
    if not common:
        raise SystemExit("两份报告没有可对齐的用例")

    rows: list[dict[str, object]] = []
    for cid in common:
        ca, cb = a[cid], b[cid]
        # 拒答与否是"行为"；其余是"打分"。分开看，因为 v2 就是要改变拒答行为。
        rows.append(
            {
                "id": cid,
                "abstained_v1": bool(ca.get("abstained")),
                "abstained_v2": bool(cb.get("abstained")),
                "groundedness_v1": ca.get("groundedness"),
                "groundedness_v2": cb.get("groundedness"),
                "relevance_v1": ca.get("relevance"),
                "relevance_v2": cb.get("relevance"),
            }
        )

    same_abstain = sum(1 for r in rows if r["abstained_v1"] == r["abstained_v2"])
    g_pairs = [
        (r["groundedness_v1"], r["groundedness_v2"])
        for r in rows
        if r["groundedness_v1"] is not None and r["groundedness_v2"] is not None
    ]
    r_pairs = [
        (r["relevance_v1"], r["relevance_v2"])
        for r in rows
        if r["relevance_v1"] is not None and r["relevance_v2"] is not None
    ]

    def bucket(value: float | None, kind: str) -> str | None:
        if value is None:
            return None
        if kind == "g":
            return "mostly" if value >= 0.75 else "half" if value >= 0.5 else "few"
        return "high" if value >= 0.75 else "mid" if value >= 0.5 else "low"

    g_agree = sum(1 for x, y in g_pairs if bucket(x, "g") == bucket(y, "g"))
    r_agree = sum(1 for x, y in r_pairs if bucket(x, "r") == bucket(y, "r"))

    def deltas(key: str) -> list[float]:
        return [
            r[f"{key}_v2"] - r[f"{key}_v1"]
            for r in rows
            if r[f"{key}_v1"] is not None and r[f"{key}_v2"] is not None
        ]

    return {
        "common_cases": len(common),
        "behaviour": {
            "same_abstain": same_abstain,
            "agreement": round(same_abstain / len(common), 4),
            # v2 的设计意图就是多拦 topic_only，所以"多拒答"是预期而非回归。
            "newly_abstained": sum(
                1 for r in rows if not r["abstained_v1"] and r["abstained_v2"]
            ),
            "newly_answered": sum(
                1 for r in rows if r["abstained_v1"] and not r["abstained_v2"]
            ),
        },
        "scores": {
            "groundedness": {
                "pairs": len(g_pairs),
                "agreement": round(g_agree / len(g_pairs), 4) if g_pairs else None,
                "mean_delta": round(statistics.mean(deltas("groundedness")), 4)
                if deltas("groundedness")
                else None,
            },
            "relevance": {
                "pairs": len(r_pairs),
                "agreement": round(r_agree / len(r_pairs), 4) if r_pairs else None,
                "mean_delta": round(statistics.mean(deltas("relevance")), 4)
                if deltas("relevance")
                else None,
            },
        },
        "per_case": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F1.4 双提示词一致性")
    parser.add_argument("--v1", default="eval/baseline.json", help="v1 模板的基线报告")
    parser.add_argument("--v2", default="eval/baseline-v2.json", help="v2 模板的报告")
    parser.add_argument("--out", default="eval/.cache/f14-cross-template.json")
    args = parser.parse_args(argv)

    v1 = json.loads(Path(args.v1).read_text(encoding="utf-8"))
    v2 = json.loads(Path(args.v2).read_text(encoding="utf-8"))
    result = compare(v1, v2)
    Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    m1, m2 = v1["metrics"], v2["metrics"]
    print("=" * 66)
    print("指标对比（v1 有据性模板 vs v2 答案相关性模板）")
    print("=" * 66)
    print(f"{'指标':<24}{'v1':>10}{'v2':>10}{'delta':>10}")
    for key in (
        "refusal_rate", "over_refusal_rate", "hallucination_rate",
        "avg_groundedness", "avg_relevance", "citation_failure_rate", "degraded_rate",
    ):
        a, b = m1.get(key), m2.get(key)
        if a is None or b is None:
            continue
        print(f"{key:<24}{a:>10.4f}{b:>10.4f}{b - a:>+10.4f}")

    b = result["behaviour"]
    s = result["scores"]
    print()
    print("=" * 66)
    print("双提示词一致性（稳定性旁证，非人工一致率）")
    print("=" * 66)
    print(f"  对齐用例      {result['common_cases']}")
    print(f"  拒答行为一致  {b['same_abstain']}/{result['common_cases']} ({b['agreement']:.1%})")
    print(f"    v2 新拒答   {b['newly_abstained']}  <- 设计意图：拦 topic_only")
    print(f"    v2 新作答   {b['newly_answered']}  <- 若非 0 需要解释")
    print(f"  有据性一致    {s['groundedness']['agreement']} "
          f"（{s['groundedness']['pairs']} 条可比，mean delta {s['groundedness']['mean_delta']}）")
    print(f"  相关性一致    {s['relevance']['agreement']} "
          f"（{s['relevance']['pairs']} 条可比，mean delta {s['relevance']['mean_delta']}）")
    print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
