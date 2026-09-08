"""来源多样性：基于文档的 MMR（Maximal Marginal Relevance）后过滤。

在混合检索返回的候选上做贪心 MMR 挑选，惩罚与已选结果来自同一文档的候选，
从而提升结果来源（doc）的多样性。完全确定性的纯 Python 实现，无外部依赖。

打分公式（``lambda_`` 权衡相关度与多样性）：
    score = lambda_ * rel(i) + (1 - lambda_) * (1 - same_doc_penalty(i))

- ``rel(i)``：候选原始分数经 min-max 归一化到 [0,1]。
- ``same_doc_penalty(i)``：已选集合为空时为 0；否则若候选与任一已选结果
  同 doc_id 则为 1，否则为 0。
"""
from __future__ import annotations

from models.schemas import RetrievedChunk


def mmr_select(
    items: list[RetrievedChunk],
    lambda_: float = 0.7,
    k: int = 8,
) -> list[RetrievedChunk]:
    """贪心文档感知 MMR：从 ``items`` 中选出最多 ``k`` 条。

    Args:
        items: 混合检索候选（已按相关度排序）。
        lambda_: 相关度权重（0~1）；越小越偏向多样性。
        k: 目标条数（超出候选数时返回全部）。

    Returns:
        按 MMR 分数从高到低排列的候选子集。结果确定性由“首个遇到的
        最高分候选优先”（严格大于才替换）保证。
    """
    if not items or k <= 0:
        return []
    k = min(k, len(items))

    # min-max 归一化相关度（全相等时取 1.0）
    raw_scores = [item.score for item in items]
    lo, hi = min(raw_scores), max(raw_scores)
    span = hi - lo
    rel = [1.0 if span == 0.0 else (s - lo) / span for s in raw_scores]

    remaining = list(range(len(items)))
    selected: list[int] = []

    while len(selected) < k and remaining:
        best_idx: int | None = None
        best_val = -1.0
        for i in remaining:
            diversity = 1.0
            for j in selected:
                if items[i].chunk.doc_id == items[j].chunk.doc_id:
                    diversity = 0.0
                    break
            value = lambda_ * rel[i] + (1.0 - lambda_) * diversity
            if value > best_val:
                best_val = value
                best_idx = i
        if best_idx is None:
            break
        selected.append(best_idx)
        remaining.remove(best_idx)

    return [items[i] for i in selected]


__all__ = ["mmr_select"]
