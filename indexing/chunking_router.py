"""切分路由（ChunkingRouter）：按文档复杂度 / 类型选择切分模式。

三种模式（与三个切分器一一对应）：

- recursive（递归固定切分）：简单文档——短文本、无版面结构、无标题。
  等长扁平 chunk，无父块开销（RecursiveChunker）；
- parent_child（父子切分）：难文档——长文档、有标题结构、有版面组件。
  结构感知 + 超长章节父块/子块，small-to-big 回取
  （StructureAwareChunker，项目既有能力）；
- qa（QA 对）：CSV / 表格数据——每行转 QA 对（CsvQaChunker）。

路由模式（mode 配置）：
- auto（默认）：csv -> qa；短文本且无版面组件 -> recursive；
  否则 -> parent_child；
- recursive / parent_child / qa：显式固定模式（整库统一）。

与 IngestPipeline 集成：替换 chunker 调用点，doc 级决策（一次决策，
该文档全部 segment 走同一模式），chunk_id 规则三模式一致。
"""
from __future__ import annotations

from typing import Any

from indexing.chunker import StructureAwareChunker
from indexing.chunker_qa import CsvQaChunker
from indexing.chunker_recursive import RecursiveChunker
from models.schemas import Chunk

# 简单文档判定：文本总长阈值（auto 模式）
DEFAULT_SIMPLE_MAX_CHARS = 4000

# 表格类文档类型（路由到 qa 模式）
_TABLE_DOC_TYPES = ("csv", "excel", "xlsx", "xls")


class ChunkingRouter:
    """切分模式路由器（对外协议与 chunker 一致：chunk_document）。"""

    def __init__(
        self,
        mode: str = "auto",
        simple_max_chars: int = DEFAULT_SIMPLE_MAX_CHARS,
        recursive: RecursiveChunker | None = None,
        parent_child: StructureAwareChunker | None = None,
        qa: CsvQaChunker | None = None,
    ) -> None:
        if mode not in ("auto", "recursive", "parent_child", "qa"):
            raise ValueError(f"非法切分模式: {mode!r}（auto/recursive/parent_child/qa）")
        self.mode = mode
        self.simple_max_chars = simple_max_chars
        self._recursive = recursive or RecursiveChunker()
        self._parent_child = parent_child or StructureAwareChunker()
        self._qa = qa or CsvQaChunker()
        # 最近一次决策（供 traces / 调试）
        self.last_decision: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    # 路由决策
    # ------------------------------------------------------------------ #
    def decide(self, doc_type: str, text: str, layout: list[Any] | None = None) -> str:
        """按文档类型 / 复杂度决策切分模式。

        Returns:
            recursive / parent_child / qa。
        """
        if self.mode != "auto":
            return self.mode
        if (doc_type or "").lower() in _TABLE_DOC_TYPES:
            return "qa"
        # 简单文档：短文本且无版面组件（layout 为空或 None）
        if len(text) <= self.simple_max_chars and not (layout or []):
            return "recursive"
        return "parent_child"

    # ------------------------------------------------------------------ #
    # 公开入口
    # ------------------------------------------------------------------ #
    def chunk_document(
        self,
        doc_id: str,
        text: str,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        start_seq: int = 0,
        doc_type: str = "",
        layout: list[Any] | None = None,
    ) -> list[Chunk]:
        """按决策把文本切分为 Chunk 列表（doc 级决策）。"""
        decision = self.decide(doc_type, text, layout)
        self.last_decision = {
            "doc_type": doc_type,
            "mode": decision,
            "text_chars": len(text),
        }
        return self.chunk_with_mode(
            decision, doc_id, text, source=source, metadata=metadata, start_seq=start_seq,
        )

    def chunk_with_mode(
        self,
        mode: str,
        doc_id: str,
        text: str,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        start_seq: int = 0,
    ) -> list[Chunk]:
        """按显式模式切分（doc 级决策后逐段复用，保证段间模式一致）。"""
        if mode == "qa":
            return self._qa.chunk_document(
                doc_id, text, source=source, metadata=metadata, start_seq=start_seq,
            )
        if mode == "recursive":
            return self._recursive.chunk_document(
                doc_id, text, source=source, metadata=metadata, start_seq=start_seq,
            )
        return self._parent_child.chunk_document(
            doc_id, text, source=source, metadata=metadata, start_seq=start_seq,
        )


__all__ = ["ChunkingRouter", "DEFAULT_SIMPLE_MAX_CHARS"]
