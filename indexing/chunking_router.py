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
    def explain_decision(
        self, doc_type: str, text: str, layout: list[Any] | None = None
    ) -> dict[str, Any]:
        """可解释路由决策：mode + reason_code + reason + facts。

        与 :meth:`decide` 同一判定表；额外输出操作员可读理由，供
        ingest ``parser_meta`` 持久化与前端诊断展示。
        """
        text_chars = len(text or "")
        layout_blocks = len(layout or [])
        normalized_type = (doc_type or "").lower()
        facts: dict[str, Any] = {
            "doc_type": doc_type or "",
            "text_chars": text_chars,
            "layout_blocks": layout_blocks,
            "simple_max_chars": self.simple_max_chars,
            "configured_mode": self.mode,
        }
        if self.mode != "auto":
            return {
                "mode": self.mode,
                "configured_mode": self.mode,
                "reason_code": "explicit_mode",
                "reason": f"配置强制 chunking_mode={self.mode}，不按文档特征路由",
                "facts": facts,
            }
        if normalized_type in _TABLE_DOC_TYPES:
            return {
                "mode": "qa",
                "configured_mode": self.mode,
                "reason_code": "table_doc_type",
                "reason": f"文档类型「{doc_type}」属表格类，路由为 qa（逐行问答切分）",
                "facts": facts,
            }
        if text_chars <= self.simple_max_chars and layout_blocks == 0:
            return {
                "mode": "recursive",
                "configured_mode": self.mode,
                "reason_code": "simple_short_no_layout",
                "reason": (
                    f"全文 {text_chars} 字 ≤ 阈值 {self.simple_max_chars} "
                    f"且版面块为 0，判定为简单文档 → recursive"
                ),
                "facts": facts,
            }
        return {
            "mode": "parent_child",
            "configured_mode": self.mode,
            "reason_code": "complex_or_structured",
            "reason": (
                f"文本 {text_chars} 字或存在 {layout_blocks} 个版面块，"
                f"超出简单文档条件（阈值 {self.simple_max_chars}）→ parent_child"
            ),
            "facts": facts,
        }

    def decide(self, doc_type: str, text: str, layout: list[Any] | None = None) -> str:
        """按文档类型 / 复杂度决策切分模式。

        Returns:
            recursive / parent_child / qa。
        """
        return str(self.explain_decision(doc_type, text, layout)["mode"])

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
        decision_info = self.explain_decision(doc_type, text, layout)
        decision = str(decision_info["mode"])
        self.last_decision = {
            "doc_type": doc_type,
            "mode": decision,
            "text_chars": len(text),
            "reason_code": decision_info.get("reason_code"),
            "reason": decision_info.get("reason"),
            "facts": decision_info.get("facts") or {},
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
