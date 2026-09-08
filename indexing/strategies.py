"""文档类型感知的分类型切分策略（pre-segmentation）。

本模块位于「清洗器」与「切分器」之间，承担入库管线的预切分环节：

    parser -> cleaner -> strategy.chunk() -> 逐 segment 调用 StructureAwareChunker

设计要点：
- ``chunk()`` 输出的是**章节级段落（segment）**——原始文本块，不是最终
  chunk；每个 segment 由下游 :class:`~indexing.chunker.StructureAwareChunker`
  进一步切分为 1500 字以内的子块，因此本模块绝不自行执行
  ``max_chunk_chars`` 级别的硬切分；
- segment 上限 ``max_segment_chars`` 默认 20000，与 chunker 的
  ``parent_max_chars`` 保持一致：保证传入 chunker 的章节不触发父块截断；
- 各策略纯标准库实现，确定性、可完全离线运行与测试；
- 空白行切分统一按 ``\n`` 处理：输入先归一化 ``\r\n`` / ``\r``；
- 按文档类型选择策略：markdown 按标题分节，pdf/word/txt 按段落分组，
  excel（MinerU 转出的 markdown 表格）按表格块切分。

仅当参数非法（如 ``max_segment_chars`` 为负数）时抛出
:class:`StrategyError`；未知文档类型回退到段落分组策略，不抛错。
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod

#: ATX 标题：1 到 6 个 ``#`` 后紧跟空白（不含空白的 ``#章`` 不算标题）。
_HEADING_RE = re.compile(r"^#{1,6}\s")


class StrategyError(RuntimeError):
    """切分策略参数错误（如 ``max_segment_chars`` 非正数）。"""


class ChunkingStrategy(ABC):
    """切分策略抽象基类：把文档文本切分为章节级段落（segment）。

    输出的是**原始文本块**而非最终 chunk——下游
    :class:`~indexing.chunker.StructureAwareChunker` 会再把每个 segment
    切分为 1500 字以内的子块并生成父块。所有策略确定性、离线可用。

    Args:
        max_segment_chars: 单个 segment 的最大字符数。段落分组类策略
            以段落为最小单位打包，绝不在段落中间断开；单个段落超过该值
            时独立成段，交由下游硬切。默认 20000（对齐 chunker 的
            ``parent_max_chars``）。

    Raises:
        StrategyError: ``max_segment_chars`` 非正数。
    """

    def __init__(self, max_segment_chars: int = 20000) -> None:
        if max_segment_chars <= 0:
            raise StrategyError("max_segment_chars 必须为正数")
        self.max_segment_chars = max_segment_chars

    @abstractmethod
    def chunk(self, text: str) -> list[str]:
        """把文档文本切分为章节级段落（segment）列表。

        Args:
            text: 文档原始文本（支持中文，换行符 ``\\r\\n`` / ``\\r``
                会被归一化为 ``\\n``）。

        Returns:
            按文档顺序排列的 segment 列表；空文本返回空列表。

        Raises:
            StrategyError: ``text`` 不是 str。
        """

    def _prepare(self, text: str) -> str:
        """校验输入类型并统一换行符为 ``\\n``。"""
        if not isinstance(text, str):
            raise StrategyError("text 必须是 str")
        return text.replace("\r\n", "\n").replace("\r", "\n")


class MarkdownStrategy(ChunkingStrategy):
    """Markdown 分节策略：按 ATX 标题（``#`` 到 ``######``）切分章节。

    规则：
    - 每个 segment = 一个标题行 + 其后的内容，直到下一个**同级或更高级**
      标题（``#`` 更少的标题）为止；子级标题（``##`` 属于 ``#``）及其中
      内容并入父 segment，不单独成段；
    - 无任何标题的文本整体成为一个 segment；标题出现前的正文（前言）
      独立成段；
    - 超过 ``max_segment_chars`` 的超长 segment 在段落边界（空白行）处
      拆分为多个不超过上限的 segment，标题始终跟随第一个分段；单个段落
      自身仍超过上限时独立成段，交由下游硬切。

    注意：本策略只输出章节级 segment，不做 1500 字级别的最终切分。
    """

    _HEADING_RE = re.compile(r"^#{1,6}\s")

    def chunk(self, text: str) -> list[str]:
        """按标题分节，并对超长章节做段落边界的二次拆分。"""
        normalized = self._prepare(text)
        if not normalized.strip():
            return []
        segments: list[str] = []
        for segment in self._split_by_headings(normalized):
            if len(segment) <= self.max_segment_chars:
                segments.append(segment)
                continue
            # 超长章节：只在段落边界处拆分，标题跟随第一个分段
            paragraphs = _split_paragraphs(segment)
            segments.extend(_group_paragraphs(paragraphs, self.max_segment_chars))
        return segments

    def _split_by_headings(self, text: str) -> list[str]:
        """按标题层级切分文本：同级或更高级标题开启新 segment。"""
        segments: list[str] = []
        lines: list[str] = []
        current_level: int | None = None

        def flush() -> None:
            nonlocal lines, current_level
            if lines:
                segments.append("\n".join(_trim_blank_edges(lines)))
            lines = []
            current_level = None

        for raw_line in text.split("\n"):
            line = raw_line.rstrip()
            stripped = line.strip()
            match = self._HEADING_RE.match(stripped)
            if match is None:
                lines.append(line)
                continue
            level = len(match.group(0).rstrip())
            if current_level is None or level <= current_level:
                # 无标题前言，或同级 / 更高级（# 更少）标题：开启新 segment
                flush()
                current_level = level
            # 子级标题（level 更大）：并入父 segment
            lines.append(line)
        flush()
        return segments


class ParagraphStrategy(ChunkingStrategy):
    """段落分组策略（pdf / word / txt 通用）。

    规则：
    - 按空白行把文本切分为段落（连续非空行），段落内部保持原样；
    - 把相邻段落贪心打包为不超过 ``max_segment_chars`` 的 segment，
      绝不在段落中间断开；
    - 单个段落超过上限时独立成段（段落本身不做硬切，交由下游
      StructureAwareChunker 处理）。
    """

    def chunk(self, text: str) -> list[str]:
        """按空白行切分为段落，再分组打包为章节级 segment。"""
        normalized = self._prepare(text)
        if not normalized.strip():
            return []
        paragraphs = _split_paragraphs(normalized)
        return _group_paragraphs(paragraphs, self.max_segment_chars)


class ExcelStrategy(ChunkingStrategy):
    """表格策略：用于 MinerU 转出的 markdown 表格文本（xlsx -> 表格）。

    规则：
    - 连续以 ``|`` 开头的行组成一个表格块（表头 + 分隔行 + 数据行），
      每个表格块整体成为一个 segment，不参与段落合并；
    - 非表格文本回落到段落分组（同 :class:`ParagraphStrategy`），
      以 ``max_segment_chars`` 为上限贪心打包；
    - 表格块出现时先落盘累积中的段落 segment，保持文档顺序。
    """

    def chunk(self, text: str) -> list[str]:
        """按表格块切分，非表格文本按段落分组。"""
        normalized = self._prepare(text)
        if not normalized.strip():
            return []
        segments: list[str] = []
        current = ""
        for kind, unit in self._split_units(normalized):
            if kind == "table":
                if current:
                    segments.append(current)
                    current = ""
                segments.append(unit)
                continue
            if current and len(current) + 2 + len(unit) > self.max_segment_chars:
                segments.append(current)
                current = unit
            else:
                current = unit if not current else current + "\n\n" + unit
        if current:
            segments.append(current)
        return segments

    @staticmethod
    def _split_units(text: str) -> list[tuple[str, str]]:
        """按文档顺序产出 ``(kind, content)`` 单元：表格块与段落。"""
        units: list[tuple[str, str]] = []
        paragraph_lines: list[str] = []
        table_lines: list[str] = []

        def flush_paragraph() -> None:
            if paragraph_lines:
                units.append(("para", "\n".join(paragraph_lines)))
                paragraph_lines.clear()

        def flush_table() -> None:
            if table_lines:
                units.append(("table", "\n".join(table_lines)))
                table_lines.clear()

        for raw_line in text.split("\n"):
            line = raw_line.rstrip()
            stripped = line.strip()
            if stripped.startswith("|"):
                flush_paragraph()
                table_lines.append(line)
                continue
            flush_table()
            if stripped:
                paragraph_lines.append(line)
            else:
                flush_paragraph()
        flush_paragraph()
        flush_table()
        return units


def _trim_blank_edges(lines: list[str]) -> list[str]:
    """去掉行列表首尾的空白行（段边界噪声），保留内部空行作段落分隔。"""
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _split_paragraphs(text: str) -> list[str]:
    """按空白行把文本切分为段落（连续非空行，行间以 ``\\n`` 连接）。"""
    paragraphs: list[str] = []
    lines: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if line.strip():
            lines.append(line)
        elif lines:
            paragraphs.append("\n".join(lines))
            lines = []
    if lines:
        paragraphs.append("\n".join(lines))
    return paragraphs


def _group_paragraphs(paragraphs: list[str], max_chars: int) -> list[str]:
    """把段落贪心打包为不超过 ``max_chars`` 的 segment。

    绝不在段落中间断开：单个段落超过 ``max_chars`` 时独立成段，由下游
    StructureAwareChunker 硬切。
    """
    segments: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if not paragraph.strip():
            continue
        if len(paragraph) > max_chars:
            if current:
                segments.append(current)
                current = ""
            segments.append(paragraph)
            continue
        if current and len(current) + 2 + len(paragraph) > max_chars:
            segments.append(current)
            current = paragraph
        else:
            current = paragraph if not current else current + "\n\n" + paragraph
    if current:
        segments.append(current)
    return segments


#: 文档类型 -> 策略类 的注册表（工厂使用）。
_STRATEGY_REGISTRY: dict[str, type[ChunkingStrategy]] = {
    "markdown": MarkdownStrategy,
    "pdf": ParagraphStrategy,
    "word": ParagraphStrategy,
    "txt": ParagraphStrategy,
    "excel": ExcelStrategy,
}


def strategy_for(doc_type: str, **kwargs) -> ChunkingStrategy:
    """按文档类型返回对应的切分策略实例。

    Args:
        doc_type: 文档类型标识（大小写不敏感），支持
            ``markdown`` / ``pdf`` / ``word`` / ``txt`` / ``excel``。
        **kwargs: 透传给策略构造函数的参数（如 ``max_segment_chars``）。

    Returns:
        对应策略实例。未知文档类型回退到 :class:`ParagraphStrategy`
        （安全默认），不抛错。

    Raises:
        StrategyError: 透传的参数非法（如 ``max_segment_chars`` 非正数）。
    """
    cls = _STRATEGY_REGISTRY.get(doc_type.strip().lower(), ParagraphStrategy)
    return cls(**kwargs)


__all__ = [
    "StrategyError",
    "ChunkingStrategy",
    "MarkdownStrategy",
    "ParagraphStrategy",
    "ExcelStrategy",
    "strategy_for",
]
