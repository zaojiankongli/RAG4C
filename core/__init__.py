"""RAG4C 核心基础模块入口。

核心模块均为**惰性依赖**：pymilvus / FlagEmbedding / openai 未安装时
本包仍可正常 import，只有真正调用对应功能时才报错。
"""
from core.tracing import TraceContext, current_trace, trace_session
from core.llm import (
    LLMClient,
    LLMError,
    LLMDependencyError,
    ParseFallbackError,
    create_client,
)
from core.embedding import (
    EmbeddingService,
    EmbeddingError,
    EmbeddingDependencyError,
    Bge3LocalEmbedder,
    ApiEmbedder,
    create_embedder,
)
from core.reranker import (
    Reranker,
    RerankError,
    RerankDependencyError,
    BgeReranker,
    LlmReranker,
)
from core.milvus_client import RagMilvusClient, RagMilvusError

__all__ = [
    # tracing
    "TraceContext",
    "current_trace",
    "trace_session",
    # llm
    "LLMClient",
    "LLMError",
    "LLMDependencyError",
    "ParseFallbackError",
    "create_client",
    # embedding
    "EmbeddingService",
    "EmbeddingError",
    "EmbeddingDependencyError",
    "Bge3LocalEmbedder",
    "ApiEmbedder",
    "create_embedder",
    # reranker
    "Reranker",
    "RerankError",
    "RerankDependencyError",
    "BgeReranker",
    "LlmReranker",
    # milvus
    "RagMilvusClient",
    "RagMilvusError",
]
