"""结构感知切分器（父块子块，small-to-big 检索的前置环节）。

设计要点：
- 按 Markdown 标题行（``#`` 到 ``######``）切分章节，标题作为章节上下文
  拼接到章节内容之前（"标题即上下文"）；
- 代码围栏（````` ``` ````` 包裹的代码块）整体保留：围栏内部的空行、
  标题行均不参与切分；
- 表格块（连续以 ``|`` 开头的行）整体保留，避免表格被段落切分截断；
- 长度小于 ``min_chunk_chars`` 的短章节合并到前一章节，避免碎片化空块；
- 长度大于 ``max_chunk_chars`` 的章节拆分为多个子块（相邻子块间带
  ``overlap_chars`` 字符重叠），同时生成一个包含整章内容的父块
  （超出 ``parent_max_chars`` 时截断）。子块通过 ``parent_chunk_id``
  指向父块，检索命中子块后可回取父块实现小到大检索；
- ``chunk_id`` 由 ``doc_id``、序号与文本 sha256 摘要共同确定：
  相同输入必定得到相同 id，可用于幂等写入与去重。

本模块不依赖任何外部服务，可完全离线运行与测试。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from models.schemas import Chunk
from indexing.hashing import text_hash


def _sha256_short(text: str, length: int = 12) -> str:
    """文本摘要的前 ``length`` 位（用于 chunk_id 的确定性部分）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


class StructureAwareChunker:
    """结构感知切分器。

    Args:
        min_chunk_chars: 短章节合并阈值。章节内容（含标题）短于该值时
            合并到前一章节，避免产生碎片化空块。
        max_chunk_chars: 单个 chunk 的目标最大字符数。超过该值的章节
            会拆分为多个子块并生成父块。
        overlap_chars: 相邻子块之间的重叠字符数，用于保持切分边界语义连续。
        parent_max_chars: 父块文本的最大字符数，超出的整章内容被截断。
    """

    _HEADING_RE = re.compile(r"^#{1,6}\s+")

    def __init__(
        self,
        min_chunk_chars: int = 200,
        max_chunk_chars: int = 1500,
        overlap_chars: int = 100,
        parent_max_chars: int = 20000,
    ) -> None:
        if min_chunk_chars <= 0:
            raise ValueError("min_chunk_chars 必须为正数")
        if max_chunk_chars < min_chunk_chars:
            raise ValueError("max_chunk_chars 不能小于 min_chunk_chars")
        if not 0 <= overlap_chars < max_chunk_chars:
            raise ValueError("overlap_chars 必须在 [0, max_chunk_chars) 范围内")
        if parent_max_chars < max_chunk_chars:
            raise ValueError("parent_max_chars 不能小于 max_chunk_chars")
        self.min_chunk_chars = min_chunk_chars
        self.max_chunk_chars = max_chunk_chars
        self.overlap_chars = overlap_chars
        self.parent_max_chars = parent_max_chars

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
    ) -> list[Chunk]:
        """把整篇文档切分为 Chunk 列表。

        Args:
            doc_id: 文档标识（同一文档的所有 chunk 共享）。
            text: 文档原始文本（支持 Markdown 与纯文本中文）。
            source: 来源标识（如文件名、URL），透传到每个 Chunk。
            metadata: 调用方附加元数据，会合并进每个 Chunk 的 metadata。
                必须可 JSON 序列化（Milvus JSON 字段约束）。
            start_seq: chunk 序号与 id 的起始值。默认 0；调用方多次
                切分同一文档（如按章节预切分后逐段入库）时，应把上一次
                的返回 chunk 数累加传入，保证 ``chunk_index`` 全局连续、
                ``chunk_id`` 全局唯一。

        Returns:
            切分结果。章节小于等于 ``max_chunk_chars`` 时产出单个父级
            Chunk；更大时产出 1 个父块加若干子块。空文本返回空列表。

        Raises:
            ValueError: metadata 不是 dict 或不可 JSON 序列化。
        """
        if not isinstance(text, str):
            raise ValueError("text 必须是 str")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata 必须是 dict 或 None")
        if not text.strip():
            return []

        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        sections = self._parse_sections(normalized)
        merged = self._merge_tiny_sections(sections)

        chunks: list[Chunk] = []
        seq = start_seq
        now = datetime.now(timezone.utc)
        for heading, blocks in merged:
            section_text = self._section_text(heading, blocks)
            if len(section_text) <= self.max_chunk_chars:
                chunk, seq = self._make_chunk(
                    doc_id=doc_id,
                    text=section_text,
                    source=source,
                    metadata=metadata,
                    seq=seq,
                    now=now,
                    parent_chunk_id=None,
                    is_parent=True,
                )
                chunks.append(chunk)
                continue

            # 大章节：先产父块（整章内容，超限截断），再产子块
            parent_text = section_text[: self.parent_max_chars]
            parent_chunk, seq = self._make_chunk(
                doc_id=doc_id,
                text=parent_text,
                source=source,
                metadata=metadata,
                seq=seq,
                now=now,
                parent_chunk_id=None,
                is_parent=True,
            )
            chunks.append(parent_chunk)
            for child_text in self._split_into_children(heading, blocks):
                child, seq = self._make_chunk(
                    doc_id=doc_id,
                    text=child_text,
                    source=source,
                    metadata=metadata,
                    seq=seq,
                    now=now,
                    parent_chunk_id=parent_chunk.chunk_id,
                    is_parent=False,
                )
                chunks.append(child)
        return chunks

    # ------------------------------------------------------------------ #
    # 文本解析
    # ------------------------------------------------------------------ #
    def _parse_sections(self, text: str) -> list[tuple[str | None, list[str]]]:
        """按行解析出章节列表，每个章节为 ``(heading, blocks)``。

        blocks 中的元素是已整体保留的块：段落（连续非空行）、
        完整代码围栏、完整表格块。
        """
        sections: list[tuple[str | None, list[str]]] = []
        heading: str | None = None
        blocks: list[str] = []
        para: list[str] = []
        table: list[str] = []
        fence: list[str] = []
        in_fence = False
        in_table = False

        def flush_para() -> None:
            nonlocal para
            if para:
                blocks.append("\n".join(para))
                para = []

        def flush_table() -> None:
            nonlocal table, in_table
            if table:
                blocks.append("\n".join(table))
                table = []
            in_table = False

        def flush_section() -> None:
            nonlocal heading, blocks
            flush_para()
            flush_table()
            sections.append((heading, blocks))
            heading = None
            blocks = []

        for raw_line in text.split("\n"):
            line = raw_line.rstrip()
            stripped = line.strip()
            if in_fence:
                fence.append(line)
                if stripped.startswith("```"):
                    blocks.append("\n".join(fence))
                    fence = []
                    in_fence = False
                continue
            if stripped.startswith("```"):
                flush_para()
                flush_table()
                in_fence = True
                fence = [line]
                continue
            if self._HEADING_RE.match(stripped):
                flush_section()
                heading = stripped
                continue
            if in_table:
                if stripped.startswith("|"):
                    table.append(line)
                    continue
                flush_table()
            if stripped.startswith("|"):
                table.append(line)
                in_table = True
                continue
            if not stripped:
                flush_para()
                continue
            para.append(line)

        # 文件末尾未闭合的围栏按普通内容保留
        if in_fence and fence:
            blocks.append("\n".join(fence))
        flush_section()
        return sections

    @staticmethod
    def _section_text(heading: str | None, blocks: list[str]) -> str:
        """章节内容：标题作为上下文拼在正文之前。"""
        body = "\n\n".join(blocks)
        if heading:
            return heading + "\n" + body if body else heading
        return body

    def _merge_tiny_sections(
        self, sections: list[tuple[str | None, list[str]]]
    ) -> list[tuple[str | None, list[str]]]:
        """把小于 ``min_chunk_chars`` 的章节合并到前一章节。

        首个章节没有前驱，保留原样。合并只发生在章节层面，
        不影响已整体保留的代码围栏 / 表格块。
        """
        merged: list[tuple[str | None, list[str]]] = []
        for heading, blocks in sections:
            if heading is None and not blocks:
                continue
            if len(self._section_text(heading, blocks)) < self.min_chunk_chars and merged:
                prev_blocks = merged[-1][1]
                if heading:
                    prev_blocks.append(heading)
                prev_blocks.extend(blocks)
                continue
            merged.append((heading, list(blocks)))
        return merged

    # ------------------------------------------------------------------ #
    # 大章节拆分
    # ------------------------------------------------------------------ #
    def _split_into_children(self, heading: str | None, blocks: list[str]) -> list[str]:
        """把大章节内容拆分为多个子块文本。

        以块（段落 / 围栏 / 表格）为最小单位贪心打包；单个块仍超过
        ``max_chunk_chars`` 时按字符窗口硬切。最后统一施加重叠：
        后一个子块前拼上一子块的尾部 ``overlap_chars`` 字符。
        """
        units = list(blocks)
        if heading:
            if units:
                units[0] = heading + "\n" + units[0]
            else:
                units = [heading]

        children: list[str] = []
        current = ""
        for unit in units:
            if not unit.strip():
                continue
            if len(unit) > self.max_chunk_chars:
                if current:
                    children.append(current)
                    current = ""
                children.extend(self._hard_split(unit))
                continue
            if current and len(current) + 2 + len(unit) > self.max_chunk_chars:
                children.append(current)
                current = unit
            else:
                current = unit if not current else current + "\n\n" + unit
        if current:
            children.append(current)

        if self.overlap_chars > 0:
            for i in range(1, len(children)):
                prev_tail = children[i - 1][-self.overlap_chars :]
                children[i] = prev_tail + children[i]
        return children

    def _hard_split(self, unit: str) -> list[str]:
        """单个超长块按字符窗口硬切（窗口大小 = max_chunk_chars）。"""
        size = self.max_chunk_chars
        return [unit[i : i + size] for i in range(0, len(unit), size)]

    # ------------------------------------------------------------------ #
    # Chunk 构造
    # ------------------------------------------------------------------ #
    def _make_chunk(
        self,
        doc_id: str,
        text: str,
        source: str | None,
        metadata: dict[str, Any] | None,
        seq: int,
        now: datetime,
        parent_chunk_id: str | None,
        is_parent: bool,
    ) -> tuple[Chunk, int]:
        """构造单个 Chunk（确定性 id + 系统元数据合并 + JSON 校验）。"""
        merged = dict(metadata or {})
        merged["is_parent"] = bool(is_parent)
        merged["chunk_index"] = seq
        try:
            json.dumps(merged, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"metadata 必须可 JSON 序列化: {exc}") from exc

        chunk_id = f"{doc_id}::{seq:04d}::{_sha256_short(text)}"
        chunk = Chunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            text=text,
            text_hash=text_hash(text),
            created_at=now,
            updated_at=now,
            source=source,
            parent_chunk_id=parent_chunk_id,
            metadata=merged,
        )
        return chunk, seq + 1


__all__ = ["StructureAwareChunker"]
