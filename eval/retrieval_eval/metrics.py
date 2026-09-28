"""检索层排序指标：全部是纯函数，不依赖任何项目组件，方便单测直接断言。

指标口径（与业界 IR 惯例一致，写在这里是为了让下一轮对比有同一把尺子）：

- ``Recall@K``     ：K 条结果里命中的 gold 数 / gold 总数。回答"找全了没"。
- ``Precision@K``  ：K 条结果里命中的 gold 数 / K。回答"前 K 条有多干净"。
- ``MRR@K``        ：第一条命中结果排名的倒数（没命中记 0）。回答"正确的那条
                     通常排第几"——单答案场景最直观的排序指标。
- ``NDCG@K``       ：二值相关性（命中=1）的折损累计增益归一化。比 MRR 更
                     看重"命中项是不是都排在前面"，多 gold 场景用它。

不可答用例（``unanswerable``）不参与 Recall/MRR/NDCG 计算：语料里根本没有
答案，算召回率没有意义。它们单独统计"误召回条数"，用来盯检索层的噪声。
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

METRIC_NAMES: tuple[str, ...] = (
    "recall@5",
    "recall@10",
    "precision@5",
    "mrr@10",
    "ndcg@10",
)


def _dedup_keep_order(ids: Iterable[str]) -> list[str]:
    """去重但保留首次出现顺序（检索结果里同一条可能来自多个分支）。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in ids:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def recall_at_k(retrieved: Sequence[str], gold: Sequence[str], k: int) -> float:
    """K 条结果覆盖了多少比例的 gold 切片。gold 为空时返回 0.0。"""
    if not gold:
        return 0.0
    top = _dedup_keep_order(list(retrieved)[:k])
    hits = len(set(top) & set(gold))
    return hits / len(set(gold))


def precision_at_k(retrieved: Sequence[str], gold: Sequence[str], k: int) -> float:
    """前 K 条里有多少比例是 gold。K<=0 时返回 0.0。"""
    if k <= 0:
        return 0.0
    top = _dedup_keep_order(list(retrieved)[:k])
    gold_set = set(gold)
    hits = sum(1 for item in top if item in gold_set)
    return hits / k


def mrr_at_k(retrieved: Sequence[str], gold: Sequence[str], k: int) -> float:
    """第一条 gold 出现名次的倒数；前 K 条没命中记 0.0。"""
    gold_set = set(gold)
    for index, item in enumerate(_dedup_keep_order(list(retrieved)[:k]), start=1):
        if item in gold_set:
            return 1.0 / index
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], gold: Sequence[str], k: int) -> float:
    """二值相关的 NDCG@K（不做 graded relevance，gold 命中即 1）。"""
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    top = _dedup_keep_order(list(retrieved)[:k])
    dcg = 0.0
    for index, item in enumerate(top, start=1):
        if item in gold_set:
            # 二值相关的标准折损：log2(rank + 1)
            dcg += 1.0 / math.log2(index + 1)
    ideal_hits = min(len(gold_set), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    if idcg <= 0.0:
        return 0.0
    return dcg / idcg


def score_case(
    retrieved: Sequence[str],
    gold: Sequence[str],
    *,
    ks: Sequence[int] = (5, 10),
) -> dict[str, float]:
    """一条用例算出全部指标，键名与 :data:`METRIC_NAMES` 对齐。

    ``ks`` 用来控制算哪些 K（默认 5/10），新增 K 不用改函数，只改入参。
    """
    k_small = min(ks)
    k_large = max(ks)
    return {
        f"recall@{k_small}": recall_at_k(retrieved, gold, k_small),
        f"recall@{k_large}": recall_at_k(retrieved, gold, k_large),
        f"precision@{k_small}": precision_at_k(retrieved, gold, k_small),
        f"mrr@{k_large}": mrr_at_k(retrieved, gold, k_large),
        f"ndcg@{k_large}": ndcg_at_k(retrieved, gold, k_large),
    }


def aggregate(case_metrics: Sequence[dict[str, float]]) -> dict[str, float]:
    """对多条用例做宏平均（每条用例权重相同，不受用例长度影响）。"""
    if not case_metrics:
        return {name: 0.0 for name in METRIC_NAMES}
    out: dict[str, float] = {}
    for name in METRIC_NAMES:
        values = [case.get(name, 0.0) for case in case_metrics]
        out[name] = sum(values) / len(values)
    return out
