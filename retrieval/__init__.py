"""检索模块：查询改写、意图路由、来源多样性、检索管线。

对外统一入口：
- :class:`retrieval.pipeline.RetrievalPipeline`：编排整条检索链路。
- :class:`retrieval.rewrite.QueryRewriter`：查询改写（含复杂度门控）。
- :class:`retrieval.router.EmbeddingRouter` / :class:`retrieval.router.RouteResolver`：
  意图路由。
- :func:`retrieval.diversity.mmr_select`：文档感知 MMR 多样性过滤。

所有组件均以协议注入依赖（embedder / milvus / reranker / llm），
可完全离线运行与测试。
"""
from .diversity import mmr_select
from .pipeline import RetrievalPipeline, RetrievalResult
from .rewrite import QueryRewriter
from .router import (
    DEFAULT_EXEMPLARS,
    EmbeddingRouter,
    LlmRouterFallback,
    RouteResolver,
)

__all__ = [
    "QueryRewriter",
    "EmbeddingRouter",
    "LlmRouterFallback",
    "RouteResolver",
    "DEFAULT_EXEMPLARS",
    "mmr_select",
    "RetrievalPipeline",
    "RetrievalResult",
]
