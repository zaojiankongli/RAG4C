from __future__ import annotations

from datetime import datetime, timezone

import pytest

from models.schemas import Chunk, RetrievedChunk
from retrieval.pipeline import RetrievalPipeline
from retrieval.router import RouteDecision
from verify.abstention import AbstentionGate
from verify.verifier import VerificationResult

# 本文件守的是审查里最重要的一条：**服务被设计成"优雅降级"，但降级的实际结果是
# 100% 弃权——不是答得差，是完全不答，而日志写着"降级成功"。**
#
# 三条互相独立的路径都会走到这个结果：
#   (1) rerank 未生效（关闭 / 熔断 / 调用失败 / 分数数量不符）
#       -> item.score 残留 Milvus RRF 融合分，双路满分只有 2/61 = 0.0328，
#          而 retrieval_score_threshold = 0.3，**完美命中也低 9 倍**；
#   (2) entailment_mode="skip"  -> 蕴含 dict 原封不动 -> 从前传出 [] -> 门判弃权；
#   (3) L3 judge 故障           -> 从前给每条声明合成 0.5 分，而阈值是 0.6 -> 弃权。
#
# 关键在于 RRF 分**不是"尺度不对"，是根本不含相似度信息**：
#     fused(d) = Σ_r 1/(k + rank_r(d))
# top-1 必定在某一路排第 1，故分数恒 >= 1/(k+1)，与相关性无关。所以归一化也救不了
# （归一化后 top-1 恒 >= 1/路数 > 0.3，闸门从"全拒"翻成"全放"）。正确做法是如实
# 承认"不可评估"，跳过这道闸、由蕴含闸继续把关。

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: 双路 RRF 的理论满分：dense 和 sparse 双双把它排在第 1 名。
RRF_PERFECT_HIT = 2.0 / 61.0

#: 生产默认值（config/settings.py）。
RETRIEVAL_THRESHOLD = 0.3
ENTAILMENT_THRESHOLD = 0.6


def make_chunk(chunk_id: str, score: float) -> RetrievedChunk:
    chunk = Chunk(
        chunk_id=chunk_id,
        doc_id="doc-1",
        text=f"正文 {chunk_id}",
        text_hash=f"hash-{chunk_id}",
        created_at=_NOW,
        updated_at=_NOW,
    )
    return RetrievedChunk(chunk=chunk, score=score, rank=0, branch="hybrid")


class FakeEmbedder:
    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class FakeMilvus:
    """只回一条"完美命中"：双路都排第 1，RRF 融合分 2/61。"""

    def __init__(self) -> None:
        self.canned = [make_chunk("c1", RRF_PERFECT_HIT)]

    def hybrid_search(self, query_dense, top_k: int, **kwargs) -> list[RetrievedChunk]:
        return [rc.model_copy(deep=True) for rc in self.canned]

    @classmethod
    def build_filters(cls, *args, **kwargs) -> str | None:
        return None


class FakeRouter:
    def route(self, query: str) -> RouteDecision:
        return RouteDecision(target="hybrid", confidence=1.0)


class FakeRewriter:
    def rewrite(self, query: str) -> tuple[str, bool]:
        return query, False


class OkReranker:
    def rerank(self, query: str, candidates) -> list[float]:
        # reranker 相关度：0~1，与阈值同尺。
        return [0.92 for _ in candidates]


class FailingReranker:
    def rerank(self, query: str, candidates) -> list[float]:
        from core.reranker import RerankError

        raise RerankError("桩：模拟重排服务不可用")


class ShortReranker:
    """返回的分数条数对不上——管线会当成失败降级。"""

    def rerank(self, query: str, candidates) -> list[float]:
        # 刻意少给一个。写死条数会在候选只有一条时意外"对上"，反而测不到降级分支。
        return [0.9] * (len(candidates) - 1)


class OpenBreaker:
    name = "reranker"

    def allow(self) -> bool:
        return False

    def record_success(self) -> None:  # pragma: no cover - 熔断打开时不会被调到
        raise AssertionError("熔断打开时不应记成功")

    def record_failure(self) -> None:  # pragma: no cover
        raise AssertionError("熔断打开时不应记失败")


def build_pipeline(reranker=None, rerank_on: bool = True, breaker=None) -> RetrievalPipeline:
    from config.settings import PipelineSettings

    return RetrievalPipeline(
        embedder=FakeEmbedder(),
        milvus=FakeMilvus(),
        reranker=reranker or OkReranker(),
        rewriter=FakeRewriter(),
        router=FakeRouter(),
        settings=PipelineSettings(
            rerank_on=rerank_on,
            complexity_gate_on=False,
            source_diversity="off",
        ),
        reranker_cb=breaker,
    )


def gate() -> AbstentionGate:
    return AbstentionGate(
        retrieval_threshold=RETRIEVAL_THRESHOLD,
        entailment_threshold=ENTAILMENT_THRESHOLD,
    )


# --------------------------------------------------------------------------- #
# 前提：确认 RRF 分确实过不了阈值（缺陷本身还在，只是不再被喂给闸门）
# --------------------------------------------------------------------------- #

def test_rrf_perfect_hit_is_far_below_the_threshold() -> None:
    # 双路都排第 1 —— 检索质量的上限。仍然比阈值低了 9 倍。
    assert RRF_PERFECT_HIT == pytest.approx(0.032787, abs=1e-6)
    assert RRF_PERFECT_HIT < RETRIEVAL_THRESHOLD / 9


def test_gate_would_abstain_if_rrf_scores_were_treated_as_comparable() -> None:
    abstain, reason = gate().decide([RRF_PERFECT_HIT], None)
    assert abstain and reason == "知识库无相关内容"


# --------------------------------------------------------------------------- #
# (1) rerank 未生效的四条路径，都必须标记 reranked=False
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "label,kwargs",
    [
        ("rerank 关闭", {"rerank_on": False}),
        ("调用失败", {"reranker": FailingReranker()}),
        ("分数数量不符", {"reranker": ShortReranker()}),
        ("熔断打开", {"breaker": OpenBreaker()}),
    ],
)
def test_rerank_absent_paths_are_flagged_not_reranked(label: str, kwargs: dict) -> None:
    result = build_pipeline(**kwargs).run("报销流程")

    assert result.chunks, f"{label}: 降级不应该把候选清空"
    assert result.reranked is False, f"{label}: 分数仍是 RRF 融合分，必须标记为未重排"
    # 分数确实没被覆写——这正是 reranked=False 要表达的事实。
    assert result.chunks[0].score == pytest.approx(RRF_PERFECT_HIT)


def test_rerank_success_is_flagged_and_rewrites_scores() -> None:
    result = build_pipeline().run("报销流程")

    assert result.reranked is True
    assert result.chunks[0].score == pytest.approx(0.92)


def test_rerank_absent_paths_say_so_in_traces() -> None:
    # 降级必须在 trace 里留痕，否则运维看到的仍然是"一切正常"。
    result = build_pipeline(rerank_on=False).run("报销流程")
    assert any("检索阈值本轮不参与弃权判定" in t for t in result.traces)


# --------------------------------------------------------------------------- #
# (1) 续：标记之后，闸门不再对完美命中弃权
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "label,kwargs",
    [
        ("rerank 关闭", {"rerank_on": False}),
        ("调用失败", {"reranker": FailingReranker()}),
        ("分数数量不符", {"reranker": ShortReranker()}),
        ("熔断打开", {"breaker": OpenBreaker()}),
    ],
)
def test_reranker_outage_no_longer_forces_abstention(label: str, kwargs: dict) -> None:
    result = build_pipeline(**kwargs).run("报销流程")
    scores = [rc.score for rc in result.chunks]

    abstain, reason = gate().decide(
        scores, None, retrieval_scores_comparable=result.reranked
    )
    assert not abstain, f"{label}: 重排降级不该导致弃权，实得 reason={reason!r}"


def test_no_results_still_abstains_even_when_incomparable() -> None:
    # "不可比"不等于"放行一切"：一条都没召回，任何时候都该弃权。
    abstain, reason = gate().decide([], None, retrieval_scores_comparable=False)
    assert abstain and reason == "无检索结果"


def test_entailment_gate_still_applies_when_retrieval_is_incomparable() -> None:
    # 检索闸关掉后，蕴含闸是唯一的防线，必须还在。
    abstain, reason = gate().decide(
        [RRF_PERFECT_HIT], [0.4], retrieval_scores_comparable=False
    )
    assert abstain and reason == "证据不足以支撑可靠声明"


# --------------------------------------------------------------------------- #
# (2)(3) 蕴含侧：区分"没评"与"评了不达标"
# --------------------------------------------------------------------------- #

def test_l3_not_evaluated_passes_none_to_the_gate() -> None:
    # entailment_mode=skip / judge 故障 -> 我们对蕴含一无所知。
    result = VerificationResult(entailment_scores={}, entailment_evaluated=False)
    assert result.gate_entailment_scores() is None

    abstain, _ = gate().decide([0.9], result.gate_entailment_scores())
    assert not abstain, "L3 没跑不该被读成'证据不足'"


def test_l3_evaluated_but_empty_still_abstains() -> None:
    # 跑了却一条都没评上，才是真的"有证据但无可用蕴含分" -> 该弃权。
    result = VerificationResult(entailment_scores={}, entailment_evaluated=True)
    assert result.gate_entailment_scores() == []

    abstain, reason = gate().decide([0.9], result.gate_entailment_scores())
    assert abstain and reason == "证据不足以支撑可靠声明"


def test_l3_evaluated_below_threshold_abstains() -> None:
    result = VerificationResult(
        entailment_scores={"claim": 0.5}, entailment_evaluated=True
    )
    abstain, reason = gate().decide([0.9], result.gate_entailment_scores())
    assert abstain and reason == "证据不足以支撑可靠声明"


def test_l3_evaluated_above_threshold_passes() -> None:
    result = VerificationResult(
        entailment_scores={"claim": 0.9}, entailment_evaluated=True
    )
    abstain, _ = gate().decide([0.9], result.gate_entailment_scores())
    assert not abstain


def test_verification_result_defaults_to_not_evaluated() -> None:
    # 默认必须是"没评"。反过来（默认 True）会让任何忘了设标记的路径
    # 退回成弃权，正是本次要修的老行为。
    assert VerificationResult().entailment_evaluated is False
    assert VerificationResult().gate_entailment_scores() is None
