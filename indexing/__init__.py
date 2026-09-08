"""索引模块：文本哈希、结构感知切分、文档入库管线。

对外暴露：
- :func:`text_hash` 文本 sha256 摘要；
- :class:`StructureAwareChunker` 结构感知切分器（父块子块）；
- :class:`IngestPipeline` / :class:`IngestError` 入库管线。

本模块不依赖 pymilvus / FlagEmbedding，可完全离线导入与测试。
"""
from __future__ import annotations

from indexing.hashing import text_hash
from indexing.chunker import StructureAwareChunker
from indexing.ingest import IngestPipeline, IngestError

__all__ = [
    "text_hash",
    "StructureAwareChunker",
    "IngestPipeline",
    "IngestError",
]
