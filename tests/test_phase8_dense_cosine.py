"""阶段 8：rerank 缺席时，用真实稠密余弦顶上那把作废的尺子。

阶段 2 修掉的是"降级 = 100% 弃权"：rerank 没跑成时 ``score`` 残留 Milvus 的
RRF 融合分，而 RRF 只由名次决定、**不含任何相似度信息**，拿它比阈值会把完美
命中判成"知识库无相关内容"。当时的处置是如实承认不可评估——跳过检索闸。

代价也很清楚：**那条路上检索闸是空的**。rerank 一挂，"库里根本没这个内容"
就没人拦了，全靠蕴含闸。本阶段把这道闸补回来，办法不是给 RRF 分做缩放
（信号不存在，任何缩放都变不出来），而是换一个真的存在的信号：稠密分支本来
就按 COSINE 检索，只是那个余弦被 RRF 吃掉、没有回传。让 Milvus 顺带把命中向量
带回来，与查询向量当场算一次余弦，闸门就重新有据可依。

本文件守四件事：

1. ``safe_cosine`` 对"另一边根本不是向量"返回 None 而不是 0.0——0.0 会被读成
   "极不相关"，把一条缺字段的命中变成一次弃权。
2. 向量回传是**按需**的：没人要就不传（一次 top_k=16 的查询要多传约 64KB），
   要了就得真的到达闸门。
3. **两把尺子不共用一个数**（阶段 6 立的规矩，这里是它的第二次应用）。实测
   八个明显离题的查询余弦最低 0.379，沿用 0.3 会让它们全部过闸——等于没修。
4. 三个编排器**真的把余弦传进了闸门**。这是阶段 7 用血换来的教训：能力写在
   模块里、没有调用方，测试照样全绿。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from core.vectors import cosine_similarity, safe_cosine
from models.schemas import Chunk, RetrievedChunk
from retrieval.pipeline import RetrievalPipeline, dense_cosines_of
from retrieval.router import RouteDecision
from verify.abstention import AbstentionGate

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)

#: 双路 RRF 的理论满分：dense 和 sparse 双双把它排在第 1 名。
RRF_PERFECT_HIT = 2.0 / 61.0

#: 生产默认值（config/settings.py）。
RETRIEVAL_THRESHOLD = 0.3
ENTAILMENT_THRESHOLD = 0.6
DENSE_COSINE_THRESHOLD = 0.52

#: 实测分布（8369 行官方文档库，见 config/settings.py 的注释）。
COSINE_ANSWERABLE_MIN = 0.5762
COSINE_UNANSWERABLE_MAX = 0.4919


def make_chunk(chunk_id: str, score: float, cosine: float | None = None) -> RetrievedChunk:
    chunk = Chunk(
        chunk_id=chunk_id,
        doc_id="doc-1",
        text=f"正文 {chunk_id}",
        text_hash=f"hash-{chunk_id}",
        created_at=_NOW,
        updated_at=_NOW,
    )
    return RetrievedChunk(
        chunk=chunk, score=score, rank=0, branch="hybrid", dense_cosine=cosine
    )


def gate() -> AbstentionGate:
    return AbstentionGate(
        retrieval_threshold=RETRIEVAL_THRESHOLD,
        entailment_threshold=ENTAILMENT_THRESHOLD,
        dense_cosine_threshold=DENSE_COSINE_THRESHOLD,
    )


# --------------------------------------------------------------------------- #
# 1. core.vectors：缺失的向量必须是 None，不能是 0.0
# --------------------------------------------------------------------------- #

def test_cosine_of_identical_vectors_is_one() -> None:
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_of_orthogonal_vectors_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "label,other",
    [
        ("字段压根没回传", None),
        ("回传了但不是向量", "not-a-vector"),
        ("回传了个空列表", []),
        ("维度对不上", [1.0, 2.0]),
        ("回传了个字典", {"dense_vector": [1.0, 0.0, 0.0]}),
    ],
)
def test_safe_cosine_says_none_not_zero_when_there_is_no_vector(
    label: str, other: Any
) -> None:
    # 这是本阶段最容易写错、后果最重的一行。0.0 是一个**合法的余弦值**，
    # 意思是"完全正交、毫不相关"。用它表示"我不知道"，闸门会把一条只是缺了
    # 字段的命中当成铁证如山的不相关，直接弃权——而且是静默的：日志里没有
    # 任何异常，用户只看到"知识库无相关内容"。
    got = safe_cosine([1.0, 0.0, 0.0], other)
    assert got is None, f"{label}: 应为 None（未知），实得 {got!r}"


def test_safe_cosine_returns_the_real_number_when_the_vector_is_there() -> None:
    assert safe_cosine([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_verifier_and_milvus_share_one_cosine_implementation() -> None:
    # 两处各写一份迟早会漂：一处改了归一化 / 零向量处理，另一处不知道。
    from verify import verifier

    assert verifier._cosine_similarity([1.0, 2.0], [2.0, 4.0]) == pytest.approx(
        cosine_similarity([1.0, 2.0], [2.0, 4.0])
    )


# --------------------------------------------------------------------------- #
# 2. 闸门：两把尺子，各用各的数
# --------------------------------------------------------------------------- #

def test_off_topic_cosine_now_abstains() -> None:
    # 这就是本阶段要补的洞：从前 rerank 一挂，这条离题查询直接过闸。
    abstain, reason = gate().decide(
        [RRF_PERFECT_HIT],
        None,
        retrieval_scores_comparable=False,
        dense_cosines=[COSINE_UNANSWERABLE_MAX],
    )
    assert abstain and reason == "知识库无相关内容"


def test_on_topic_cosine_still_passes() -> None:
    abstain, reason = gate().decide(
        [RRF_PERFECT_HIT],
        None,
        retrieval_scores_comparable=False,
        dense_cosines=[COSINE_ANSWERABLE_MIN],
    )
    assert not abstain, f"答得上的查询被拦了：{reason!r}"


def test_reusing_the_retrieval_threshold_would_make_this_gate_a_no_op() -> None:
    # 实测：八个明显离题的查询，top-1 余弦最低 0.379。0.3 一个都拦不住。
    # 阶段 6 立的规矩——两把不同的尺子绝不共用一个数——在这里第二次被证实。
    assert COSINE_UNANSWERABLE_MAX > RETRIEVAL_THRESHOLD
    assert 0.3785 > RETRIEVAL_THRESHOLD
    # 而两组之间是有干净间隔的，阈值落在里面。
    assert COSINE_UNANSWERABLE_MAX < DENSE_COSINE_THRESHOLD < COSINE_ANSWERABLE_MIN


def test_cosines_are_ignored_while_rerank_scores_are_usable() -> None:
    # elif 不是 if：rerank 跑成了，就该看 rerank 那把尺子。此处 rerank 分
    # 0.9 远超阈值（不该弃权），而余弦 0.1 低于余弦阈值——若写成两个独立的
    # if，这条会被余弦拦下，等于让一把辅助尺子否决了主尺子的判断。
    abstain, reason = gate().decide(
        [0.9], None, retrieval_scores_comparable=True, dense_cosines=[0.1]
    )
    assert not abstain, f"rerank 可用时不该看余弦，实得 {reason!r}"


def test_rrf_scores_are_never_compared_against_the_cosine_threshold() -> None:
    # 反向也不许串：RRF 满分 0.0328 远低于 0.52，若拿它去比余弦阈值，
    # 完美命中会被判成"知识库无相关内容"——正是阶段 2 修掉的那个缺陷复活。
    abstain, reason = gate().decide(
        [RRF_PERFECT_HIT],
        None,
        retrieval_scores_comparable=False,
        dense_cosines=[COSINE_ANSWERABLE_MIN],
    )
    assert not abstain, f"RRF 分泄漏到了余弦这把尺子上：{reason!r}"


def test_no_cosines_degrades_honestly_instead_of_guessing() -> None:
    # 调用方没索取余弦（或索取了但库里没回传）：这一路信号确实不存在。
    # 此时仍旧如实承认不可评估，跳过这道闸，而不是拿 0 或别的默认值硬判。
    abstain, reason = gate().decide(
        [RRF_PERFECT_HIT], None, retrieval_scores_comparable=False, dense_cosines=[]
    )
    assert not abstain, f"没有余弦不等于不相关：{reason!r}"

    abstain, _ = gate().decide(
        [RRF_PERFECT_HIT], None, retrieval_scores_comparable=False, dense_cosines=None
    )
    assert not abstain


def test_empty_retrieval_still_abstains_regardless_of_cosines() -> None:
    # "一条都没召回"任何时候都该弃权，余弦分支不该把这条路盖住。
    abstain, reason = gate().decide(
        [], None, retrieval_scores_comparable=False, dense_cosines=[0.99]
    )
    assert abstain and reason == "无检索结果"


def test_entailment_gate_still_runs_after_the_cosine_gate_passes() -> None:
    abstain, reason = gate().decide(
        [RRF_PERFECT_HIT],
        [0.4],
        retrieval_scores_comparable=False,
        dense_cosines=[COSINE_ANSWERABLE_MIN],
    )
    assert abstain and reason == "证据不足以支撑可靠声明"


def test_threshold_comes_from_settings_not_a_hardcoded_default() -> None:
    from config.settings import PipelineSettings

    settings = type("S", (), {})()
    settings.pipeline = PipelineSettings(dense_cosine_threshold=0.77)
    assert AbstentionGate.from_settings(settings).dense_cosine_threshold == 0.77


# --------------------------------------------------------------------------- #
# 3. Milvus 客户端：向量按需回传，回来了就算成余弦
# --------------------------------------------------------------------------- #

class _FakeRawClient:
    """假的 pymilvus MilvusClient，只记录调用参数并回放固定命中。"""

    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self._hits = hits
        self.calls: list[dict[str, Any]] = []

    def hybrid_search(self, **kwargs: Any) -> list[list[dict[str, Any]]]:
        self.calls.append(kwargs)
        return [self._hits]


def _hit(chunk_id: str, dense_vector: Any = "__absent__") -> dict[str, Any]:
    entity: dict[str, Any] = {
        "chunk_id": chunk_id,
        "doc_id": "doc-1",
        "text": "正文",
        "text_hash": "h",
        "created_at": 0,
        "updated_at": 0,
        "tenant_id": "t1",
        "dataset_id": "ds1",
        "metadata": {},
    }
    if dense_vector != "__absent__":
        entity["dense_vector"] = dense_vector
    return {"distance": RRF_PERFECT_HIT, "entity": entity}


def _client_with(hits: list[dict[str, Any]]):
    from config.settings import MilvusSettings
    from core.milvus_client import RagMilvusClient

    mc = RagMilvusClient(MilvusSettings(uri="./x.db", collection_name="c"))
    raw = _FakeRawClient(hits)
    mc._client = raw
    return mc, raw


def test_vectors_are_not_fetched_unless_asked() -> None:
    # 默认不取是因为它要花钱：1024 维每条多传约 4KB，top_k=16 一次约 64KB。
    # 这个项目的目标之一就是并发性能，无条件多传是实打实的退步。
    mc, raw = _client_with([_hit("c1", [1.0, 0.0, 0.0])])
    out = mc.hybrid_search([1.0, 0.0, 0.0], top_k=1, query_text="报销")

    assert "dense_vector" not in raw.calls[0]["output_fields"]
    assert out[0].dense_cosine is None


def test_asking_for_cosines_fetches_the_vector_and_computes_it() -> None:
    mc, raw = _client_with([_hit("c1", [1.0, 1.0, 0.0])])
    out = mc.hybrid_search(
        [1.0, 0.0, 0.0], top_k=1, query_text="报销", with_cosine=True
    )

    assert "dense_vector" in raw.calls[0]["output_fields"]
    assert out[0].dense_cosine == pytest.approx(0.7071, abs=1e-4)


def test_a_hit_without_the_vector_field_yields_none_not_zero() -> None:
    # 服务端没回传这个字段（版本差异 / 字段被裁剪）时，不能悄悄记成 0.0。
    mc, _ = _client_with([_hit("c1")])
    out = mc.hybrid_search(
        [1.0, 0.0, 0.0], top_k=1, query_text="报销", with_cosine=True
    )
    assert out[0].dense_cosine is None


def test_the_raw_vector_never_lands_on_the_returned_model() -> None:
    # 1024 维数组一旦挂到 RetrievedChunk 上，就会跟着"读回来再 upsert"的
    # 往返进存储，并撑爆每一层缓存与序列化。只留一个 float。
    mc, _ = _client_with([_hit("c1", [1.0, 0.0, 0.0])])
    out = mc.hybrid_search(
        [1.0, 0.0, 0.0], top_k=1, query_text="报销", with_cosine=True
    )
    dumped = out[0].model_dump()
    assert "dense_vector" not in dumped
    assert "dense_vector" not in dumped.get("chunk", {})


# --------------------------------------------------------------------------- #
# 4. 管线：只在 rerank 确定不会生效时才索取向量
# --------------------------------------------------------------------------- #

class _RecordingMilvus:
    """记录 hybrid_search 收到的 with_cosine，并按需回填余弦。"""

    def __init__(self, cosine: float = 0.9) -> None:
        self.with_cosine_seen: list[bool] = []
        self.cosine = cosine

    def hybrid_search(self, query_dense, top_k: int, **kwargs) -> list[RetrievedChunk]:
        wanted = bool(kwargs.get("with_cosine"))
        self.with_cosine_seen.append(wanted)
        return [make_chunk("c1", RRF_PERFECT_HIT, self.cosine if wanted else None)]

    @classmethod
    def build_filters(cls, *args, **kwargs) -> str | None:
        return None


class _FakeEmbedder:
    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0] for _ in texts]


class _FakeRouter:
    def route(self, query: str) -> RouteDecision:
        return RouteDecision(target="hybrid", confidence=1.0)


class _FakeRewriter:
    def rewrite(self, query: str) -> tuple[str, bool]:
        return query, False


class _OkReranker:
    def rerank(self, query: str, candidates) -> list[float]:
        return [0.92 for _ in candidates]


class _FailingReranker:
    def rerank(self, query: str, candidates) -> list[float]:
        from core.reranker import RerankError

        raise RerankError("桩：模拟重排服务不可用")


class _OpenBreaker:
    name = "reranker"

    def allow(self) -> bool:
        return False

    def record_success(self) -> None:  # pragma: no cover
        raise AssertionError("熔断打开时不应记成功")

    def record_failure(self) -> None:  # pragma: no cover
        raise AssertionError("熔断打开时不应记失败")


class _Hyde:
    def generate(self, query: str) -> str:
        return "一段假设性回答文本"


def _pipeline(milvus, reranker=_OkReranker, rerank_on: bool = True, breaker=None, hyde=None):
    from config.settings import PipelineSettings

    return RetrievalPipeline(
        embedder=_FakeEmbedder(),
        milvus=milvus,
        # 默认值用类本身当哨兵：显式传 None（"没注入 reranker"）是被测路径之一，
        # 用 `reranker or _OkReranker()` 会把它悄悄替换掉，测了个寂寞。
        reranker=_OkReranker() if reranker is _OkReranker else reranker,
        rewriter=_FakeRewriter(),
        router=_FakeRouter(),
        settings=PipelineSettings(
            rerank_on=rerank_on,
            complexity_gate_on=False,
            source_diversity="off",
            hyde_on=hyde is not None,
        ),
        reranker_cb=breaker,
        hyde=hyde,
    )


@pytest.mark.parametrize(
    "label,kwargs",
    [
        ("rerank 关闭", {"rerank_on": False}),
        ("没注入 reranker", {"reranker": None}),
        ("熔断打开", {"breaker": _OpenBreaker()}),
    ],
)
def test_cosines_are_requested_on_the_knowable_rerank_skip_paths(
    label: str, kwargs: dict
) -> None:
    # 四条 rerank 缺席路径里，只有这三条在发查询之前就能判定。第四条
    # （调用到一半抛 RerankError）事前不可知，为它无条件多传 64KB 不划算。
    milvus = _RecordingMilvus()
    result = _pipeline(milvus, **kwargs).run("报销流程")

    assert milvus.with_cosine_seen == [True], f"{label}: 应索取余弦"
    assert result.reranked is False
    assert result.dense_cosines() == [pytest.approx(0.9)], f"{label}: 余弦没到达结果"


def test_a_missing_reranker_degrades_instead_of_killing_the_query() -> None:
    # rerank_on=True 但没注入 reranker：从前会抛 AttributeError，而它不在
    # `except RerankError` 的捕获范围内——整条查询失败。一个可选增强的缺席
    # 不该带走主链路。
    milvus = _RecordingMilvus()
    result = _pipeline(milvus, reranker=None).run("报销流程")

    assert result.chunks, "没注入 reranker 不该把结果清空"
    assert any("rerank 未注入" in t for t in result.traces)


def test_no_cosines_are_fetched_when_rerank_will_work() -> None:
    milvus = _RecordingMilvus()
    result = _pipeline(milvus).run("报销流程")

    assert milvus.with_cosine_seen == [False], "rerank 能用就别多传向量"
    assert result.reranked is True
    assert result.dense_cosines() == []


def test_mid_call_rerank_failure_has_no_cosine_and_says_so() -> None:
    # 事前不可知的那条路：照旧如实承认这道闸空着。
    milvus = _RecordingMilvus()
    result = _pipeline(milvus, reranker=_FailingReranker()).run("报销流程")

    assert milvus.with_cosine_seen == [False]
    assert result.dense_cosines() == []
    assert any("检索阈值本轮不参与弃权判定" in t for t in result.traces)


def test_hyde_suppresses_the_cosine_because_the_threshold_would_not_apply() -> None:
    # HyDE 下查询向量嵌的是一段"假设性回答"，而不是用户的问题。算出来的余弦
    # 落在另一个分布上，拿 0.52 去卡它是在用一把没校准过的尺子。
    milvus = _RecordingMilvus()
    result = _pipeline(milvus, rerank_on=False, hyde=_Hyde()).run("报销流程")

    assert milvus.with_cosine_seen == [False], "HyDE 时不该索取余弦"
    assert result.dense_cosines() == []


def test_the_trace_distinguishes_the_two_degradation_flavors() -> None:
    milvus = _RecordingMilvus()
    result = _pipeline(milvus, rerank_on=False).run("报销流程")

    # 有余弦顶上和这道闸真的空着，是两种不同的运行状态，运维得能分清。
    assert any("改用稠密余弦参与弃权判定" in t for t in result.traces)
    assert not any("检索阈值本轮不参与弃权判定" in t for t in result.traces)


def test_dense_cosines_of_skips_chunks_without_one() -> None:
    # 父块回取 / 图谱直取出来的 chunk 天然没有余弦，跳过而不是记成 0。
    chunks = [
        make_chunk("c1", RRF_PERFECT_HIT, 0.8),
        make_chunk("parent", RRF_PERFECT_HIT, None),
        make_chunk("c2", RRF_PERFECT_HIT, 0.6),
    ]
    assert dense_cosines_of(chunks) == [pytest.approx(0.8), pytest.approx(0.6)]


# --------------------------------------------------------------------------- #
# 5. 编排器调用点：能力必须真的被调用
# --------------------------------------------------------------------------- #
# 阶段 7 的教训：`catalog.register_synced_document` 测试全绿，而
# `sources/runner.py` 从头到尾没调过它。测能力不等于测调用点。这里直接盯住
# 三个编排器传给 gate.decide 的实参。

class _SpyGate:
    """把每次 decide 的入参录下来；永不弃权，好让链路一路走到底。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def decide(self, retrieval_scores, entailment_scores=None, **kwargs):
        self.calls.append(
            {
                "scores": list(retrieval_scores),
                "entailment": entailment_scores,
                "comparable": kwargs.get("retrieval_scores_comparable", True),
                "dense_cosines": kwargs.get("dense_cosines", "__missing__"),
            }
        )
        return False, ""


def test_every_orchestrator_passes_dense_cosines_to_the_gate() -> None:
    """三个编排器都必须把余弦递到闸门——漏一个，那条链路的闸就还是空的。

    用源码级断言而不是跑整条链路：三个编排器各自需要 LLM / 验证器 / langgraph
    一整套桩，跑起来的成本远大于它能多证明的东西。这里要钉死的事实很窄——
    "``decide`` 的每一个调用点都带上了 ``dense_cosines``"——而这件事在语法树上
    是确定可判的。
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("rag.py", "rag_stream.py", "rag_graph.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "decide"
        ]
        assert calls, f"{name}: 一个 gate.decide 调用都没找到，测试自身失效了"
        for call in calls:
            kwargs = {kw.arg for kw in call.keywords}
            assert "dense_cosines" in kwargs, (
                f"{name}:{call.lineno} 的 gate.decide 没传 dense_cosines——"
                f"这条链路上 rerank 一挂，检索闸就是空的"
            )
            assert "retrieval_scores_comparable" in kwargs, (
                f"{name}:{call.lineno} 少了 retrieval_scores_comparable"
            )


def test_the_two_rulers_are_measured_over_the_same_chunks() -> None:
    """分数和余弦必须取自同一批 chunk。

    二轮补救会把两轮证据合并。若分数对 ``merged`` 现算、余弦却还留着第一轮的，
    闸门就在拿 A 组的相关度否决 B 组的证据。三个编排器里每一处重算
    ``retrieval_scores`` 的地方，紧邻位置都必须一起重算余弦。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("rag.py", "rag_stream.py", "rag_graph.py"):
        text = (root / name).read_text(encoding="utf-8")
        # 形如 `retrieval_scores = [rc.score for rc in X]` 或
        # `"retrieval_scores": [rc.score for rc in X]`
        recomputes = re.findall(r"retrieval_scores\"?\]?\s*[:=]\s*\[rc\.score for rc in (\w+)\]", text)
        assert recomputes, f"{name}: 没找到 retrieval_scores 的计算点"
        cosines = re.findall(r"dense_cosines\"?\]?\s*[:=]\s*dense_cosines_of\((\w+)\)", text)
        assert sorted(recomputes) == sorted(cosines), (
            f"{name}: 分数算的是 {sorted(recomputes)}，余弦算的是 {sorted(cosines)}——"
            f"两把尺子量的不是同一批 chunk"
        )


def test_graph_state_seeds_dense_cosines() -> None:
    # 图编排走 TypedDict 状态：初始值漏了这个键，第一次读就 KeyError。
    import rag_graph

    assert "dense_cosines" in rag_graph._State.__annotations__
