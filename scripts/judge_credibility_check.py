"""F1.4 裁判可信度抽检：把「裁判打的分」与「人工判定」对齐。

计划要求："幻觉率/有据性是 **LLM 裁判**打的分。抽 30 条，人工判定与裁判判定
对比，算一致率。验收：一致率有数字；一致率低就先别拿幻觉率做决策，回头调
裁判提示词。"

为什么这件事必须做：F1.2 的六个数里有三个（幻觉率、平均有据性、平均相关性）
直接来自裁判。裁判不是真值——它只是另一个模型。若它系统性偏松（比如把
无支撑的声明判成 supported），那么"幻觉率 0.09"这个数就只是"裁判说有据"，
拿它去调提示词、调阈值会全部走偏。

本工具做三件事，且**不假装自动化人工标注**：

1. **抽样**：从基线报告里分层抽 N 条（可答/不可答 × 作答/拒答 × 有分/无分），
   覆盖裁判最可能出错的格子；输出可直接阅读的抽检表（含问题、答案、证据、
   裁判的逐条判定与分数），供人工填写 ``human_*`` 列。
2. **判定**：人工填完后回读，算有据性/相关性的**一致率**与 **Cohen's kappa**。
3. **分歧清单**：把人工与裁判不一致的条目单独列出，供提示词升版本时定位。

关于 kappa 的能力边界（实测，`tests/test_f14_judge_credibility.py` 里有钉住它的
测试）：本场景是**两分类**（分三档但实际分布高度集中），kappa 在
``(observed - expected) / (1 - expected)`` 上对"误判 20% / 0% / 双向对称"
三种构造**全部返回 1.0**——只要两边边缘分布都接近一半，expected 会与 observed
同步下降。所以 kappa 在这里**不是敏感度指标**，拿它当"裁判偏松"的检测器会
得到虚假安心。

真正能暴露偏松的是 `disagreements` 清单：裁判系统性把无支撑判成有据时，清单里
会出现**大量同方向**的分歧（judge=mostly / human=few），那才是可行动的证据。
两个数都报，但结论以清单为准。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# 抽样
# ---------------------------------------------------------------------------


def _stratum(case: dict[str, Any]) -> str:
    """分层标签：可答/不可答 × 作答/拒答 × 有分/无分。

    分层的意义在于"难样本更容易落在小格子里"。全部已作答且有分的样本占绝对
    多数，均匀随机抽 30 条几乎全落在同一格里，抽检就变成了复读机。
    """
    answerable = "answerable" if not case.get("unanswerable") else "unanswerable"
    answered = "answered" if case.get("answered") else "abstained"
    scored = "scored" if case.get("groundedness") is not None else "unscored"
    return f"{answerable}/{answered}/{scored}"


def sample(report: dict[str, Any], n: int = 30, seed: int = 20261004) -> list[dict[str, Any]]:
    """分层抽样：先每格取一个（轮转），再从最大的格子补齐到 n。"""
    cases = report.get("cases", [])
    buckets: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        buckets.setdefault(_stratum(case), []).append(case)

    rng = random.Random(seed)
    for group in buckets.values():
        rng.shuffle(group)

    picked: list[dict[str, Any]] = []
    # 第一轮：每个格子最多取 1 条（保证覆盖）
    for key in sorted(buckets):
        if buckets[key] and len(picked) < n:
            picked.append(buckets[key].pop())
    # 第二轮：从剩余最多的格子轮转补齐
    while len(picked) < n:
        progressed = False
        for key in sorted(buckets, key=lambda k: -len(buckets[k])):
            if buckets[key]:
                picked.append(buckets[key].pop())
                progressed = True
                if len(picked) >= n:
                    break
        if not progressed:
            break
    return picked


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------


def _cohen_kappa(pairs: list[tuple[Any, Any]], categories: list[Any]) -> float | None:
    """Cohen's kappa：扣除「按分布瞎猜也能对」的部分。

    只有两个标注者时用不上加权；这里刻意不实现加权版本——有据性/相关性都
    先二值化或粗分档再比，加权矩阵会让口径变得难以口头核对。

    退化情形只有一种需要报 None：**人工标注的类别完全退化**（所有抽检样本
    都被人工判进同一个类目）。那时人工没有表达出任何区分，一致率与 kappa
    都只是"人工全都选了同一档"的复述，报告它等于什么都没测。

    期望一致率恰好为 1 的另一种情况（人工的边缘分布与裁判完全重合）返回
    1.0：分母为 0 是因为两份标注在类目分布上一样，而不是因为谁没在判。

    特别地，**裁判单档不报 None**。裁判全判 supported、人工给出分歧，恰恰是
    最有价值的信号——它就是"裁判偏松"的直接证据，此时一致率低、kappa 也低，
    两个数都在正确地报警。把它归为"不可计算"会把失效模式藏起来。
    """
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(1 for a, b in pairs if a == b) / n
    # 人工标注的类目分布：全落在一个类目里 = 人工没在区分，kappa 无意义
    human_categories = {a for a, _ in pairs}
    if len(human_categories) < 2:
        return None
    # 期望一致率 = 按各自的边缘分布瞎猜
    expected = 0.0
    for ca in categories:
        for cb in categories:
            expected += (sum(1 for a, _ in pairs if a == ca) / n) * (
                sum(1 for _, b in pairs if b == cb) / n
            )
    if abs(1.0 - expected) < 1e-12:
        return 1.0
    return round((observed - expected) / (1.0 - expected), 6)


def _bucket_relevance(score: float | None) -> str | None:
    """相关性 0..1 三档：低 / 中 / 高。

    直接比浮点分是没意义的（裁判给 0.75 人工给 0.8 算不算一致？）。分档后
    口径能被人口头复述，代价是丢分辨率——这个交换对"裁判可不可信"这个问题
    是划算的。
    """
    if score is None:
        return None
    if score < 0.5:
        return "low"
    if score < 0.75:
        return "mid"
    return "high"


def _bucket_grounded(score: float | None) -> str | None:
    """有据性 0..1 三档：多数支撑 / 半数 / 少数。

    裁判的原始分是 supported/total，取 0.5 为界会与"全部 unsupported"这种
    极端情况混同，所以按多数/半数/少数分。
    """
    if score is None:
        return None
    if score >= 0.75:
        return "mostly"
    if score >= 0.5:
        return "half"
    return "few"


def judge_report(annotations: list[dict[str, Any]]) -> dict[str, Any]:
    """比对人工与裁判判定，产出一致率 / kappa / 分歧清单。"""
    g_pairs: list[tuple[str, str]] = []
    r_pairs: list[tuple[str, str]] = []
    disagreements: list[dict[str, Any]] = []

    for row in annotations:
        case_id = row.get("id")
        g_judge = _bucket_grounded(row.get("groundedness"))
        g_human = _bucket_grounded(row.get("human_groundedness"))
        r_judge = _bucket_relevance(row.get("relevance"))
        r_human = _bucket_relevance(row.get("human_relevance"))
        if g_judge and g_human:
            g_pairs.append((g_human, g_judge))
        if r_judge and r_human:
            r_pairs.append((r_human, r_judge))
        if (g_judge and g_human and g_judge != g_human) or (
            r_judge and r_human and r_judge != r_human
        ):
            disagreements.append(
                {
                    "id": case_id,
                    "groundedness_judge": g_judge,
                    "groundedness_human": g_human,
                    "relevance_judge": r_judge,
                    "relevance_human": r_human,
                    "note": row.get("human_note", ""),
                }
            )

    g_agree = sum(1 for a, b in g_pairs if a == b)
    r_agree = sum(1 for a, b in r_pairs if a == b)
    return {
        "annotated": len(annotations),
        "with_groundedness_pair": len(g_pairs),
        "with_relevance_pair": len(r_pairs),
        "groundedness": {
            "agreement": round(g_agree / len(g_pairs), 4) if g_pairs else None,
            "kappa": _cohen_kappa(g_pairs, ["few", "half", "mostly"]),
        },
        "relevance": {
            "agreement": round(r_agree / len(r_pairs), 4) if r_pairs else None,
            "kappa": _cohen_kappa(r_pairs, ["low", "mid", "high"]),
        },
        "disagreements": disagreements,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _render_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# F1.4 裁判可信度抽检表",
        "",
        "填写规则：",
        "- `human_groundedness` / `human_relevance`：0~1 浮点，按你对答案的判断给分；",
        "  拿不准就按有据性 = 被引用证据支撑的声明数 / 声明总数 来估。",
        "- 留空表示没填（不计入一致率）。",
        "- `human_note`：分歧原因，分歧时必填。",
        "",
        "改完存回同一路径，再跑 `python scripts/judge_credibility_check.py --judge <本文件>`。",
        "",
    ]
    for row in rows:
        lines.append(f"## {row['id']}")
        lines.append("")
        lines.append(f"- 问题：{row['question']}")
        lines.append(f"- 标记：{_stratum(row)}")
        lines.append(f"- 裁判有据性：{row.get('groundedness')}（分档：{_bucket_grounded(row.get('groundedness'))}）")
        lines.append(f"- 裁判相关性：{row.get('relevance')}（分档：{_bucket_relevance(row.get('relevance'))}）")
        lines.append(f"- 引用：{row.get('citations_total')} 条，失败 {row.get('citations_failed')} 条")
        if row.get("unsupported_claims"):
            lines.append(f"- 裁判判为无支撑的声明：{row['unsupported_claims']}")
        # 显示管线的弃权原因——它是**第三个独立信号**，人工标注时可以拿它
        # 交叉参考：若裁判说「有据 0.75 / 相关 0.21」而管线已因「主题相关但
        # 不包含所问事实」弃权，那这条正是 F1.2 里 79% 幻觉样本的形态，
        # 人工的判断应当与弃权原因对得上。
        if row.get("abstained") or row.get("answered") is False:
            reason = _abstain_reason(row.get("notes"))
            lines.append(f"- 已弃权：True｜原因：{reason or '(未记录)'}")
        lines.append("- 人工有据性（待填）：")
        lines.append("- 人工相关性（待填）：")
        lines.append("- 备注（待填）：")
        lines.append("")
    return "\n".join(lines)


def _abstain_reason(notes: str | None) -> str:
    """从 notes 里取出管线的弃权原因。

    notes 现在并入了管线诊断 trace（见 ``eval/run_eval.py`` 的
    ``_run_case``），弃权以 ``弃权: <原因>`` 出现在其中，但它前面可能还挂着
    标注者写的证据摘要（``原文：xxx；弃权: ...``），所以不能用前缀匹配——
    那样会在最常见的形态下失配。直接搜关键字位置更稳。
    """
    text = notes or ""
    marker = "弃权:"
    idx = text.find(marker)
    if idx < 0:
        return ""
    reason = text[idx + len(marker) :]
    # 原因只到下一个分隔符为止（notes 用 | 与；串起多条诊断）
    for stop in ("|", "；", "\n"):
        cut = reason.find(stop)
        if cut >= 0:
            reason = reason[:cut]
    return reason.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F1.4 裁判可信度抽检")
    parser.add_argument("--report", default="eval/baseline.json", help="F1.2 基线报告")
    parser.add_argument("--n", type=int, default=30, help="抽检条数")
    parser.add_argument("--seed", type=int, default=20261004, help="抽样随机种子（固定以便复现）")
    parser.add_argument("--out", default="eval/.cache/f14-sample.md", help="抽检表输出路径")
    parser.add_argument("--judge", default="", help="已填写的抽检表 JSON 路径；给了就出统计")
    parser.add_argument("--out-json", default="eval/.cache/f14-agreement.json", help="一致率结果输出")
    args = parser.parse_args(argv)

    if args.judge:
        annotations = json.loads(Path(args.judge).read_text(encoding="utf-8"))
        if isinstance(annotations, dict):
            annotations = annotations.get("rows", [])
        result = judge_report(annotations)
        Path(args.out_json).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已标注 {result['annotated']} 条")
        pairs = {
            "groundedness": result["with_groundedness_pair"],
            "relevance": result["with_relevance_pair"],
        }
        for name in ("groundedness", "relevance"):
            block = result[name]
            print(
                f"  {name:14s} 可比 {pairs[name]:3d} 条  "
                f"一致率={block['agreement']}  kappa={block['kappa']}"
            )
        print(f"  分歧 {len(result['disagreements'])} 条 -> {args.out_json}")
        return 0

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    rows = sample(report, n=args.n, seed=args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_render_table(rows), encoding="utf-8")
    strata: dict[str, int] = {}
    for row in rows:
        strata[_stratum(row)] = strata.get(_stratum(row), 0) + 1
    print(f"已抽 {len(rows)} 条 -> {out}")
    print("分层覆盖：" + ", ".join(f"{k}={v}" for k, v in sorted(strata.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
