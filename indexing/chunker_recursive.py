"""递归固定切分器（简单文档专用）。

与 StructureAwareChunker（难文档：结构感知 + 父子块）互补：本切分器面向
简单文档（短文、无复杂版面结构），按分隔符优先级递归降级硬切，产出
等长扁平 chunk（无父块）：

    标题行 -> 空行 -> 换行 -> 句末标点（。！？） -> 空白 -> 字符窗口

- 固定目标大小 + 相邻重叠（语义连续性）；
- 不产出父块（简单文档无需 small-to-big 回取）；
- chunk_id 与 StructureAwareChunker 同规则（doc_id::seq::sha256 前 12 位），
  幂等写入与增量重索引兼容。

本模块纯标准库实现，离线可用。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from indexing.hashing import text_hash
from models.schemas import Chunk

# 分隔符优先级（从粗到细）
_SEPARATORS: list[str] = ["\n## ", "\n# ", "\n\n", "\n", "。", "！", "？", " ", ""]


def _sha256_short(text: str, length: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


class RecursiveChunker:
    """递归固定切分器。

    Args:
        max_chunk_chars: 目标 chunk 最大字符数（硬上限）。
        overlap_chars: 相邻 chunk 重叠字符数（0 <= overlap < max）。
        min_chunk_chars: 短片段合并阈值（避免碎片块）。
    """

    def __init__(
        self,
        max_chunk_chars: int = 1200,
        overlap_chars: int = 80,
        min_chunk_chars: int = 100,
    ) -> None:
        if max_chunk_chars <= 0:
            raise ValueError("max_chunk_chars 必须为正数")
        if not 0 <= overlap_chars < max_chunk_chars:
            raise ValueError("overlap_chars 必须在 [0, max_chunk_chars) 范围内")
        self.max_chunk_chars = max_chunk_chars
        self.overlap_chars = overlap_chars
        self.min_chunk_chars = min_chunk_chars

    # ------------------------------------------------------------------ #
    # 公开入口（与 StructureAwareChunker 同协议）
    # ------------------------------------------------------------------ #
    def chunk_document(
        self,
        doc_id: str,
        text: str,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        start_seq: int = 0,
    ) -> list[Chunk]:
        """把整篇文档递归切分为扁平 Chunk 列表（无父块）。"""
        if not isinstance(text, str):
            raise ValueError("text 必须是 str")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata 必须是 dict 或 None")
        if not text.strip():
            return []

        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        pieces = self._recursive_split(normalized)
        merged = self._merge_small(pieces)

        chunks: list[Chunk] = []
        seq = start_seq
        now = datetime.now(timezone.utc)
        for piece in merged:
            chunk = self._make_chunk(doc_id, piece, source, metadata, seq, now)
            chunks.append(chunk)
            seq += 1
        return chunks

    # ------------------------------------------------------------------ #
    # 递归切分
    # ------------------------------------------------------------------ #
    def _recursive_split(self, text: str, level: int = 0) -> list[str]:
        """按分隔符优先级递归降级切分。"""
        if len(text) <= self.max_chunk_chars:
            return [text] if text.strip() else []
        if level >= len(_SEPARATORS):
            # 兜底：字符窗口硬切
            size = self.max_chunk_chars
            return [text[i : i + size] for i in range(0, len(text), size)]

        separator = _SEPARATORS[level]
        if separator == "":
            return self._recursive_split(text, level + 1)

        # 按分隔符切分（保留分隔符于片段尾部，避免信息丢失）
        parts = text.split(separator)
        if len(parts) == 1:
            return self._recursive_split(text, level + 1)
        out: list[str] = []
        for i, part in enumerate(parts):
            if not part:
                continue
            piece = part if i == len(parts) - 1 else part + separator
            if len(piece) <= self.max_chunk_chars:
                out.append(piece)
            else:
                out.extend(self._recursive_split(piece, level + 1))
        return out

    def _merge_small(self, pieces: list[str]) -> list[str]:
        """把过短片段合并到前一片段（减少碎片块）。"""
        out: list[str] = []
        for piece in pieces:
            if (
                out
                and len(piece) < self.min_chunk_chars
                and len(out[-1]) + len(piece) <= self.max_chunk_chars
            ):
                out[-1] = out[-1] + piece
            else:
                out.append(piece)
        return out

    # ------------------------------------------------------------------ #
    # Chunk 构造（id 规则与 StructureAwareChunker 一致）
    # ------------------------------------------------------------------ #
    def _make_chunk(
        self,
        doc_id: str,
        text: str,
        source: str | None,
        metadata: dict[str, Any] | None,
        seq: int,
        now: datetime,
    ) -> Chunk:
        merged = dict(metadata or {})
        merged["chunk_index"] = seq
        merged["chunk_mode"] = "recursive"
        try:
            json.dumps(merged, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"metadata 必须可 JSON 序列化: {exc}") from exc
        chunk_id = f"{doc_id}::{seq:04d}::{_sha256_short(text)}"
        return Chunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            text=text,
            text_hash=text_hash(text),
            created_at=now,
            updated_at=now,
            source=source,
            parent_chunk_id=None,
            metadata=merged,
        )


__all__ = ["RecursiveChunker"]
