"""双重阈值弃权（abstention）门控。

弃权决策**不使用 LLM 自我判断**，而是基于两个可离线校验的分数阈值：

1. 检索分数阈值（retrieval_score_threshold）：
   若检索结果最高分仍低于阈值，说明知识库与该问题无实质相关内容，
   直接弃权（"知识库无相关内容"）。
2. 蕴含分数阈值（entailment_score_threshold）：
   检索有内容但证据对声明的支撑度不足时弃权（"证据不足以支撑可靠声明"）。

阈值通过评估集 unanswerable 数据的检索分数分位数校准：
见 :func:`calibrate_retrieval_threshold`。

本模块为纯函数实现，不依赖 LLM / 网络 / 向量库，可完全离线测试。
"""
from __future__ import annotations

import math

from config.settings import get_settings


def _numpy_available() -> bool:
    """numpy 是否可用（惰性探测，避免强制依赖）。"""
    try:
        import numpy  # noqa: F401

        return True
    except ImportError:
        return False


class AbstentionGate:
    """双重阈值弃权门。

    Args:
        retrieval_threshold: 检索分数阈值（低于则"知识库无相关内容"）。
        entailment_threshold: 蕴含分数阈值（低于则"证据不足以支撑可靠声明"）。
        dense_cosine_threshold: 稠密余弦阈值，**另一把尺子**，只在 rerank 未
            生效、改用真实余弦把关时使用。

    典型用法（阈值来自 settings.pipeline）::

        gate = AbstentionGate.from_settings()
        abstain, reason = gate.decide(retrieval_scores=[0.82], entailment_scores=[0.91])
    """

    def __init__(
        self,
        retrieval_threshold: float,
        entailment_threshold: float,
        dense_cosine_threshold: float = 0.52,
    ) -> None:
        self.retrieval_threshold = retrieval_threshold
        self.entailment_threshold = entailment_threshold
        self.dense_cosine_threshold = dense_cosine_threshold

    def decide(
        self,
        retrieval_scores: list[float],
        entailment_scores: list[float] | None = None,
        *,
        retrieval_scores_comparable: bool = True,
        dense_cosines: list[float] | None = None,
        answer_status: str = "",
    ) -> tuple[bool, str]:
        """做出弃权决策。

        判定顺序（短路返回，与任务书 6.4 一致）：
        1. 无检索结果 -> 弃权（"无检索结果"）；
        2. 最高检索分数低于阈值 -> 弃权（"知识库无相关内容"）；
           分数不可比时改看稠密余弦（若有），低于余弦阈值同样弃权；
        3. ``answer_status`` 判为 ``topic_only`` / ``irrelevant`` -> 弃权；
        4. 提供了蕴含分数且（为空或最高分低于阈值）-> 弃权
           （"证据不足以支撑可靠声明"）；
        5. 其余情况 -> 不弃权（(False, "")）。

        Args:
            retrieval_scores: 本次检索的分数列表（可为空）。
            entailment_scores: 逐条声明的蕴含分数（可为 None 表示不参与判定，
                或为空列表表示有证据但无可用蕴含分数）。
            retrieval_scores_comparable: 分数是否落在与
                ``retrieval_threshold`` 同一把尺子上。为 False 时跳过第 2 步，
                但**第 1 步照做**——"一条都没召回"和"召回了但分数不可比"是
                两回事，前者任何时候都该弃权。
            dense_cosines: 查询向量与各命中 chunk 向量的真实余弦。仅在
                ``retrieval_scores_comparable=False`` 时参与判定——它存在的
                意义就是补上那种情况下缺失的那把尺子。为 None 表示这一路
                信号本次也没有（调用方没索取，或索取了但库里没回传）。
            answer_status: L3 v2 给出的整段判定（见
                :attr:`VerificationResult.answer_status`）。**它必须独立成一路**：
                第 4 步对逐条分数取 ``max``，而 F1.2 那批幻觉样本的形态是
                "5 条声明里 4 条 supported + 整段答非所问"——把主题相关性混进
                逐条分数里，``max`` 仍是 1.0，门照样放行（实测确认）。

        Returns:
            (是否弃权, 弃权原因)；不弃权时原因为空串。

        Note:
            ``retrieval_scores_comparable=False`` 的唯一来源是 rerank 未生效
            （关闭 / 熔断 / 调用失败）。此时 ``RetrievedChunk.score`` 残留的是
            Milvus 的 RRF 融合分，而 **RRF 分只由名次决定，不含任何相似度信息**：

                fused(d) = Σ_r 1/(k + rank_r(d))

            返回列表的 top-1 必定在某一路排第 1，故其分数恒 >= 1/(k+1)，
            与查询和知识库是否相关无关。k=60 时该值是 0.0164，
            而阈值是 0.3 —— 于是**完美命中也会被判定为"知识库无相关内容"**。
            把它归一化到 [0,1] 同样不成立：top-1 名次恒为 1，归一化分恒 >= 1/路数
            （双路 >= 0.5），闸门会从"全拒"翻成"全放"。

            两个方向都是失效。信号本身不存在，任何缩放都变不出来。

            **补的办法不是缩放，是换一个真的存在的信号。** 稠密分支本来就按
            COSINE 度量检索，只是那个余弦被 RRF 融合吃掉了、没有回传。让
            ``hybrid_search(with_cosine=True)`` 把每条命中的向量取回来，与查询
            向量当场算一次余弦，这道闸就重新有据可依了——``dense_cosines``
            就是这个。

            这把尺子的量级与 ``retrieval_threshold`` 完全不同，实测（8369 行的
            官方文档库）：

                答得上 top1 余弦：min 0.576 / 中位 0.700 / max 0.800
                答不上 top1 余弦：min 0.379 / 中位 0.451 / max 0.492

            八个明显离题的查询，余弦最低也有 0.379——**沿用 0.3 会让它们全部
            过闸**。所以它必须有自己的阈值（``dense_cosine_threshold``，默认
            0.52，落在两组之间的干净间隔里）。这是阶段 6 就立下的规矩：两把不
            同的尺子不共用一个数。

            两路信号都没有时（调用方没索取余弦），仍然如实承认不可评估：
            跳过这道闸，由蕴含闸继续把关，并在 trace 里明示降级。
        """
        if not retrieval_scores:
            return True, "无检索结果"
        if retrieval_scores_comparable:
            if max(retrieval_scores) < self.retrieval_threshold:
                return True, "知识库无相关内容"
        elif dense_cosines:
            # rerank 那把尺子这次不可用，改用稠密余弦这把——注意是 elif：
            # 两把尺子各自独立，绝不能拿余弦去比 retrieval_threshold，
            # 也不能拿 RRF 分去比 dense_cosine_threshold。
            if max(dense_cosines) < self.dense_cosine_threshold:
                return True, "知识库无相关内容"
        if answer_status in ("topic_only", "irrelevant"):
            return True, (
                "证据主题相关但不包含所问事实"
                if answer_status == "topic_only"
                else "证据与问题无关"
            )
        if entailment_scores is not None:
            if not entailment_scores or max(entailment_scores) < self.entailment_threshold:
                return True, "证据不足以支撑可靠声明"
        return False, ""

    @classmethod
    def from_settings(cls, settings=None) -> "AbstentionGate":
        """由配置构造（读取 pipeline.retrieval_score_threshold 等）。"""
        settings = settings or get_settings()
        return cls(
            retrieval_threshold=settings.pipeline.retrieval_score_threshold,
            entailment_threshold=settings.pipeline.entailment_score_threshold,
            dense_cosine_threshold=settings.pipeline.dense_cosine_threshold,
        )


def calibrate_retrieval_threshold(
    unanswerable_scores: list[float],
    percentile: float = 0.85,
) -> float:
    """用评估集 unanswerable 数据的检索分数分位数校准检索阈值。

    用评估集中"应拒答 / 无答案"样本的检索分数分布，取 ``percentile``
    分位数作为检索阈值：正常问答的检索分数应落在该分位数之上，
    无答案样本的分数集中在分位数之下，从而把"知识库无相关内容"
    的误拒率控制在该分位数水平。

    Args:
        unanswerable_scores: 评估集 unanswerable 样本的检索分数列表。
        percentile: 分位数（0~1，默认 0.85）。

    Returns:
        校准后的检索阈值（float）。

    Raises:
        ValueError: ``unanswerable_scores`` 为空。

    Notes:
        - numpy 可用时用 ``numpy.percentile``（线性插值）；
        - 否则用最近秩（nearest-rank）手动实现，保证无 numpy 也离线可用。
    """
    if not unanswerable_scores:
        raise ValueError("unanswerable_scores 不能为空，无法校准阈值")
    if _numpy_available():
        import numpy as np

        return float(np.percentile(unanswerable_scores, percentile * 100.0))
    # 手动最近秩实现：rank = ceil(percentile * n)，取第 rank 小的值
    sorted_scores = sorted(unanswerable_scores)
    n = len(sorted_scores)
    rank = max(1, math.ceil(percentile * n))
    return float(sorted_scores[min(rank, n) - 1])


__all__ = [
    "AbstentionGate",
    "calibrate_retrieval_threshold",
]
