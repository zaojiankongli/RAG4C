"""检索层离线评测包。

为什么要单开一个包：仓库里原有的 ``eval/`` 只测答案层指标
（groundedness / relevance / 弃权率 / 引用失败率），**没有任何一个指标回答
"检索本身召回了什么"**。召回率、排序质量这些量不出来，"优化检索"就只能靠
主观感觉。这个包把「语料 → 索引 → 检索 → 打分」做成一条可复现的离线链路：

- :mod:`metrics`  纯函数指标（Recall@K / P@K / MRR@K / NDCG@K），无副作用
- :mod:`corpus`   把仓库文档切成确定性的检索语料（切片结果可复现）
- :mod:`gold`     人工标定的「问题 → 应命中切片」评测集
- :mod:`store`    本地向量库（numpy 余弦）+ 嵌入缓存（避免重复花钱）
- :mod:`runner`   跑通若干检索策略并产出报告

设计边界：**不碰生产检索链路**。这里用本地向量库替代 Milvus，只是为了在没有
Milvus 实例的机器上也能量到"嵌入 + 重排 + 多样性"的真实效果；重排器用的是
生产同一个（真实 bge-reranker-v2-m3），所以排序收益是可信的。
"""

from .metrics import (
    METRIC_NAMES,
    mrr_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    score_case,
)

__all__ = [
    "METRIC_NAMES",
    "mrr_at_k",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
    "score_case",
]
