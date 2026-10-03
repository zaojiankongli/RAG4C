"""F2.3 英文子集失败归因：把「英文差」拆成可行动的类别。

计划要求："英文 dense_rerank 0.735 vs 中文 0.923，差距最大。把英文里正确
chunk 排在 10 名之外的用例逐条看：是切分问题、嵌入问题、还是标注问题。
验收：失败按类别计数；每类给出『改哪里能解』的判断。"

**先看数**：126 条基线里 en n=79 ndcg@10=0.7354 recall@10=0.8836，
zh n=44 ndcg@10=0.9197 recall@10=0.9659。召回只差 0.082、NDCG 差 0.184，
且 MRR 差 0.215（0.698 vs 0.913）。**差距主要在"排得靠前"而不是"召不回"**
——这直接排除了"切片没把答案切开"这一类（那会同时打低 recall）。

分类口径（按可行动的修复位置分，而不是按症状分）：

- ``ranking``：gold 召回了但排在 10 名之后。→ 嵌入表征 / 重排器 / 查询改写
- ``missed``：gold 完全没进 top-10。→ 切片粒度 / 切分丢内容 / 语料缺该事实
- ``partial``：多条 gold 只命中一部分。→ 同上，但更可能是切片边界问题
- ``exact_top1``：gold 就在第 1 位。→ 无问题（对照组）

每类给出"改哪里能解"的判断，并明确写出**这一类不该动什么**——归因的价值
在于排除，只列"该改哪里"会让人把时间花在错的地方。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

#: 每一类"改哪里能解"的判断。刻意写"不该动什么"——归因的价值在于排除。
REMEDIES: dict[str, dict[str, str]] = {
    "exact_top1": {
        "fix": "无需处理",
        "do_not_touch": "这一类是健康样本，不要为了「优化」它去动 embedding 或 reranker",
    },
    "ranking": {
        "fix": "嵌入表征 / reranker / 查询改写：gold 在集合里，缺的是把它排上去的判别力",
        "do_not_touch": "不要改切片粒度——recall@10 已经 0.884，切片不是瓶颈",
    },
    "partial": {
        "fix": "切片边界：答案被切在两个 chunk 里，合并或调整窗口能解",
        "do_not_touch": "不要改 embedding——内容确实在库里，只是没被完整召回",
    },
    "missed": {
        "fix": "先分清是「切片丢了内容」还是「语料根本没有」：前者调切分，后者要补文档",
        "do_not_touch": "不要先动模型——先确认 gold 那一段的原文是否真的存在于语料里",
    },
}


def _classify_note() -> str:
    return (
        "分类按「最靠前的 gold 排第几」判定，不按命中条数二次分档："
        "排第 2 与排第 30 是同一类问题（排序判别力不足），拆成两类只会让"
        "「ranking」这一格看起来更严重、从而误导修复优先级。partial 是"
        "兜底类——有召回记录但排不到第 1、也没落到 ranking/missed 时才用它。"
    )


def classify(case: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """给单条用例定类别 + 附上判断依据。

    只看检索结果与 gold 的关系，不看分数高低——分数是症状，位置才是证据。

    优先读 runner 落盘的 ``gold_ranks`` / ``top10``（新报告有）；读不到时退回
    从 top5/top10 列表里现算，让工具也能吃旧报告。
    """
    top10 = list(
        case.get("top10")
        or case.get("retrieved10")
        or case.get("top5")
        or case.get("retrieved")
        or []
    )
    gold = list(case.get("gold") or case.get("gold_chunk_ids") or [])
    missed_gold = list(case.get("missed_gold") or [])
    ndcg = float(case.get("ndcg@10") or case.get("metrics", {}).get("ndcg@10") or 0.0)

    ranks = case.get("gold_ranks")
    # `gold_ranks` **存在但为空**与**字段缺失**是两件事：前者 = 有 gold 但一
    # 条都没命中（missed），后者 = 不可答用例（没有 gold 可言）。用 `is None`
    # 判前者，用真值判会把空列表算成"命中 -1 条"，分类整个错位。
    if ranks is not None:
        gold_rank: int | None = min(ranks) if ranks else None
        gold_hit = len(ranks)
    else:
        gold_rank = next((r for r, cid in enumerate(top10, start=1) if cid in gold), None)
        gold_hit = (len(gold) - len(missed_gold)) if gold else 0

    detail: dict[str, Any] = {
        "case_id": case.get("case_id") or case.get("id"),
        "question": case.get("question", ""),
        "ndcg@10": round(ndcg, 4),
        "gold_total": gold_hit + len(missed_gold) if missed_gold else gold_hit,
        "gold_in_top10": gold_hit,
        "gold_rank": gold_rank,
    }

    if ranks is None and not gold and not missed_gold:
        return "unanswerable", detail
    if gold_rank == 1:
        return "exact_top1", detail
    if gold_rank is not None:
        return "ranking", detail
    if gold_hit:
        return "partial", detail
    return "missed", detail


def analyze(report: dict[str, Any], strategy: str = "dense_rerank") -> dict[str, Any]:
    block = report["strategies"][strategy]
    cases = block["cases"]
    by_lang: dict[str, Counter] = {}
    details: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        lang = case.get("corpus_language", "undeclared")
        kind, detail = classify(case)
        by_lang.setdefault(lang, Counter())[kind] += 1
        details.setdefault(lang, []).append({**detail, "kind": kind})

    return {
        "strategy": strategy,
        "by_language": {
            lang: {
                "cases": sum(counter.values()),
                "kinds": dict(counter),
                # 只统计可答用例的失败率：unanswerable 不进召回统计
                "answerable": sum(v for k, v in counter.items() if k != "unanswerable"),
                "failures": sum(
                    v for k, v in counter.items() if k in ("ranking", "partial", "missed")
                ),
                "metrics": next(
                    (
                        v.get("metrics", {})
                        for v in block.get("by_language", {}).values()
                        if True
                    ),
                    {},
                ),
            }
            for lang, counter in sorted(by_lang.items())
        },
        "details": details,
        "remedies": REMEDIES,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F2.3 英文子集失败归因")
    parser.add_argument(
        "--report", default="eval/.cache/repro-126-20261004.json", help="126 条检索评测报告"
    )
    parser.add_argument("--strategy", default="dense_rerank", help="要归因的策略")
    parser.add_argument("--out", default="eval/.cache/f23-attribution.json", help="输出路径")
    args = parser.parse_args(argv)

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    result = analyze(report, args.strategy)
    # 把 by_language 的真实指标（不是上面那个占位）补上
    for lang, entry in result["by_language"].items():
        entry["metrics"] = report["strategies"][args.strategy].get("by_language", {}).get(
            lang, {}
        ).get("metrics", {})
    Path(args.out).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"策略 {args.strategy}｜报告 {args.report}")
    print("=" * 72)
    for lang, entry in result["by_language"].items():
        m = entry["metrics"]
        print(f"\n[{lang}] {entry['cases']} 条（可答 {entry['answerable']}，失败 {entry['failures']}）")
        if m:
            print(
                f"  ndcg@10={m.get('ndcg@10', 0):.4f} recall@10={m.get('recall@10', 0):.4f} "
                f"mrr@10={m.get('mrr@10', 0):.4f}"
            )
        print(f"  分类计数: {entry['kinds']}")
    print("\n" + "=" * 72)
    print("各类「改哪里能解」")
    print("=" * 72)
    for kind, advice in REMEDIES.items():
        print(f"\n  {kind}:")
        print(f"    改: {advice['fix']}")
        print(f"    别动: {advice['do_not_touch']}")
    print(f"\n已写入 {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
