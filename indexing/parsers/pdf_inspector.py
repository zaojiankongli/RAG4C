"""Fast 引擎：pdf-inspector（纯 Rust，PDF 分类 + 文本提取 + Markdown 化，无 OCR）。

- classify()：采样内容流分类（text_based / scanned / image_based / mixed +
  置信度 + 每页 OCR 路由），10-50ms 级；
- parse()：提取为 Markdown（标题 / 列表 / 表格 / 多栏阅读顺序），并产出
  DeepDoc 版面组件块（layout：title / table / text），按页记录页码；
- 编码问题（has_encoding_issues）标记进 metadata，供上层决定 OCR 回退。

本模块惰性导入 pdf_inspector（可选依赖：未安装时仅在使用时报错，
不影响系统其余部分）；构造与 classify 均不联网。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from indexing.parsers.base import (
    LayoutBlock,
    MineruParserError,
    ParsedDocument,
)

# 标题行：ATX 风格（# ~ ######）
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
# 表格分隔行（| --- | --- |）
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}.*\|\s*$")


def _markdown_to_layout(markdown: str, page: int | None) -> list[LayoutBlock]:
    """把单页 Markdown 切分为版面组件块（title / table / text）。"""
    blocks: list[LayoutBlock] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        heading = _HEADING_RE.match(line)
        if heading:
            blocks.append(
                LayoutBlock(
                    type="title",
                    text=heading.group(2).strip(),
                    page=page,
                    meta={"level": len(heading.group(1))},
                )
            )
            i += 1
            continue
        # 表格：以分隔行（| --- |）为标志，向上吸收表头、向下吸收数据行
        if _TABLE_SEP_RE.match(line):
            start = i
            while start > 0 and lines[start - 1].strip().startswith("|"):
                start -= 1
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            blocks.append(
                LayoutBlock(
                    type="table",
                    text="\n".join(lines[start:j]),
                    page=page,
                    meta={"rows": j - start - 1},
                )
            )
            i = j
            continue
        # 正文：连续非结构化行聚合
        buf = [line]
        j = i + 1
        while j < len(lines):
            if (
                _HEADING_RE.match(lines[j])
                or _TABLE_SEP_RE.match(lines[j])
                or lines[j].strip().startswith("|")
            ):
                break
            buf.append(lines[j])
            j += 1
        text = "\n".join(buf).strip()
        if text:
            blocks.append(LayoutBlock(type="text", text=text, page=page))
        i = j
    return blocks


class PdfInspectorParser:
    """DeepDoc Fast 引擎解析器（与 DocumentParser 协议兼容）。"""

    engine_name = "fast"

    def __init__(self, settings: Any = None):
        self.settings = settings

    @staticmethod
    def _ensure_lib() -> Any:
        try:
            import pdf_inspector
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise MineruParserError(
                "pdf-inspector 未安装。请执行 pip install pdf-inspector"
            ) from exc
        return pdf_inspector

    def supports(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() == ".pdf"

    def classify(self, file_path: str) -> dict[str, Any]:
        """PDF 智能分类（供 DocumentRouter 路由决策）。

        Returns:
            {pdf_type, confidence, page_count, pages_needing_ocr}
        """
        lib = self._ensure_lib()
        try:
            cls = lib.classify_pdf(str(file_path))
        except Exception as exc:
            raise MineruParserError(f"pdf-inspector 分类失败: {exc}") from exc
        return {
            "pdf_type": str(cls.pdf_type),
            "confidence": float(cls.confidence),
            "page_count": int(cls.page_count),
            "pages_needing_ocr": [int(p) for p in (cls.pages_needing_ocr or [])],
        }

    def parse(self, file_path: str) -> ParsedDocument:
        """Fast 引擎解析：Markdown 化 + 版面组件块（按页）。"""
        lib = self._ensure_lib()
        try:
            result = lib.process_pdf(str(file_path))
            pages_md = lib.extract_pages_markdown(str(file_path))
        except Exception as exc:
            raise MineruParserError(f"pdf-inspector 解析失败: {exc}") from exc

        layout: list[LayoutBlock] = []
        pages: list[str] = []
        raw_pages = getattr(pages_md, "pages", None) or []
        for pm in raw_pages:
            md = getattr(pm, "markdown", "") or ""
            page_no = int(getattr(pm, "page", 0) or 0)
            pages.append(md)
            layout.extend(_markdown_to_layout(md, page_no))
        if not pages:
            markdown = getattr(result, "markdown", "") or ""
            pages = [markdown]
            layout = _markdown_to_layout(markdown, 0)

        text = "\n\n".join(pages)
        return ParsedDocument(
            text=text,
            pages=pages,
            layout=layout,
            engine=self.engine_name,
            metadata={
                "pdf_type": str(getattr(result, "pdf_type", "")),
                "confidence": float(getattr(result, "confidence", 0.0)),
                "pages_needing_ocr": [
                    int(p) for p in (getattr(result, "pages_needing_ocr", None) or [])
                ],
                "pages_with_tables": [
                    int(p) for p in (getattr(result, "pages_with_tables", None) or [])
                ],
                "pages_with_columns": [
                    int(p) for p in (getattr(result, "pages_with_columns", None) or [])
                ],
                "has_encoding_issues": bool(getattr(result, "has_encoding_issues", False)),
                "is_complex_layout": bool(getattr(result, "is_complex_layout", False)),
                "processing_time_ms": float(getattr(result, "processing_time_ms", 0.0)),
                "title": str(getattr(result, "title", "") or ""),
            },
        )


__all__ = ["PdfInspectorParser"]
