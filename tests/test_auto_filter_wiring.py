"""P3：自动元数据过滤（auto_filter）接入检索管线的接线测试。

覆盖：
- auto_filter 生成表达式时，与 build_filters 结果 AND 组合（注入到检索）；
- auto_filter 返回 None（不生成/LLM 失败）时，filter_expr 不变（降级不过滤）；
- auto_filter 抛异常时，检索不中断（降级 + trace）；
- 未注入 auto_filter（None）时行为与接入前一致。
"""
from __future__ import annotations

from typing import Any

from config.settings import PipelineSettings
from models.schemas import RetrievedChunk, RouteDecision
from retrieval.pipeline import RetrievalPipeline


class _FakeEmbedder:
    def embed_query(self, text: str):
        return [0.1, 0.2, 0.3]


class _RecordingMilvus:
    """记录 build_filters 结果与检索调用，返回空结果。"""

    def __init__(self, base_expr: str = "tenant_id == 'default'") -> None:
        self.base_expr = base_expr
        self.last_filter_expr: str | None = None

    @classmethod
    def build_filters(cls, *args, **kwargs) -> str:
        return kwargs.get("base_expr") if kwargs.get("base_expr") is not None else cls._default_expr

    _default_expr = "tenant_id == 'default'"

    def hybrid_search(self, query_dense, top_k: int, **kwargs) -> list[RetrievedChunk]:
        self.last_filter_expr = kwargs.get("filter_expr")
        return []

    def search(self, query_dense, top_k: int, **kwargs) -> list[RetrievedChunk]:
        return []


class _FakeRewriter:
    def rewrite(self, query: str):
        return query, False


class _FakeRouter:
    def route(self, query: str) -> RouteDecision:
        return RouteDecision(target="hybrid", confidence=1.0)


class _OkReranker:
    def rerank(self, query: str, chunks: list):
        return [0.92 for _ in chunks]


def _pipeline(milvus: _RecordingMilvus, auto_filter: Any = None) -> RetrievalPipeline:
    return RetrievalPipeline(
        embedder=_FakeEmbedder(),
        milvus=milvus,
        reranker=_OkReranker(),
        rewriter=_FakeRewriter(),
        router=_FakeRouter(),
        settings=PipelineSettings(
            rerank_on=True,
            complexity_gate_on=False,
            source_diversity="off",
            hybrid_search_on=False,
        ),
        auto_filter=auto_filter,
    )


class _StubAutoFilter:
    def __init__(self, expr: str | None, raise_on_call: bool = False) -> None:
        self.expr = expr
        self.raise_on_call = raise_on_call
        self.calls = 0

    def generate(self, query: str, user_fields: list):
        self.calls += 1
        if self.raise_on_call:
            raise RuntimeError("llm down")
        return self.expr


def _run_and_get_filter(pipeline: RetrievalPipeline, milvus: _RecordingMilvus):
    result = pipeline.run("query", tenant_id="default")
    return result, milvus.last_filter_expr


def test_auto_filter_expr_is_and_composed_with_base_filter() -> None:
    milvus = _RecordingMilvus()
    af = _StubAutoFilter(expr="category == 'finance'")
    pipeline = _pipeline(milvus, auto_filter=af)
    result, filter_expr = _run_and_get_filter(pipeline, milvus)
    assert af.calls == 1
    assert result is not None
    assert "category == 'finance'" in filter_expr
    assert "tenant_id == 'default'" in filter_expr


def test_auto_filter_none_keeps_base_filter_unchanged() -> None:
    milvus = _RecordingMilvus()
    af = _StubAutoFilter(expr=None)  # LLM 不生成 → 不过滤
    pipeline = _pipeline(milvus, auto_filter=af)
    _, filter_expr = _run_and_get_filter(pipeline, milvus)
    assert af.calls == 1
    assert filter_expr is None or "category" not in filter_expr  # 未组合


def test_auto_filter_exception_degrades_without_breaking_retrieval() -> None:
    milvus = _RecordingMilvus()
    af = _StubAutoFilter(expr=None, raise_on_call=True)
    pipeline = _pipeline(milvus, auto_filter=af)
    result, filter_expr = _run_and_get_filter(pipeline, milvus)
    assert result is not None  # 检索不中断
    assert "category" not in (filter_expr or "")  # 降级不过滤
    assert any("auto_filter 不可用" in t for t in result.traces)


def test_no_auto_filter_keeps_previous_behavior() -> None:
    milvus = _RecordingMilvus()
    pipeline = _pipeline(milvus, auto_filter=None)
    result, filter_expr = _run_and_get_filter(pipeline, milvus)
    assert result is not None
    assert "category" not in (filter_expr or "")
