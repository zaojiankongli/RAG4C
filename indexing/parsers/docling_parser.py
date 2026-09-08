"""Docling 解析引擎插件（IBM Docling：AI 布局模型 + TableFormer 表格识别）。

插拔式接入：本模块不 import docling（重依赖：torch + 模型权重），
仅在 parse() 内惰性导入；未安装时报可操作错误。

模式（统一配置 parsers.docling.mode）：
- local：本地模型（DocLayNet 布局 + TableFormer 表格 + 可插拔 OCR），
  免费，首次运行自动下载模型（数百 MB）；
- api  ：云服务（docling.api_base / api_key），付费模式预留。

输出：ParsedDocument（Markdown + layout 版面组件，docling 的条目类型
映射到 DeepDoc 10 种组件：Title->title / Table->table / Figure+Caption
->figure / Formula->formula / 其余->text 等）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from indexing.parsers.base import LayoutBlock, MineruParserError, ParsedDocument

# docling 条目类型 -> DeepDoc 版面组件
_TYPE_MAP: dict[str, str] = {
    "Title": "title",
    "SectionHeader": "title",
    "Table": "table",
    "Figure": "figure",
    "Picture": "figure",
    "Caption": "figure_caption",
    "Formula": "formula",
    "PageHeader": "header",
    "PageFooter": "footer",
    "Reference": "reference",
    "Footnote": "reference",
}


class DoclingParser:
    """DeepDoc 引擎插件：docling（与 DocumentParser 协议兼容）。"""

    engine_name = "docling"

    def __init__(self, engine_cfg: Any, docling_cfg: Any = None):
        self.mode = str(getattr(engine_cfg, "mode", "local"))
        if self.mode not in ("local", "api"):
            raise MineruParserError(f"非法 docling 模式: {self.mode!r}（local / api）")
        self.docling_cfg = docling_cfg

    def supports(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in (
            ".pdf", ".docx", ".pptx", ".xlsx", ".html", ".md", ".png", ".jpg", ".jpeg",
        )

    def parse(self, file_path: str) -> ParsedDocument:
        """本地 / 云两种模式解析。"""
        if self.mode == "api":
            return self._parse_api(file_path)
        return self._parse_local(file_path)

    # ------------------------------------------------------------------ #
    # local 模式（惰性 import docling 重依赖）
    # ------------------------------------------------------------------ #
    def _parse_local(self, file_path: str) -> ParsedDocument:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise MineruParserError(
                "docling 未安装。请执行 pip install docling（首次运行自动下载模型权重，数百 MB）"
            ) from exc
        try:
            converter = DocumentConverter()
            result = converter.convert(file_path)
        except Exception as exc:
            raise MineruParserError(f"docling 解析失败: {exc}") from exc

        markdown = result.document.export_to_markdown()
        layout: list[LayoutBlock] = []
        try:
            for item, _level in result.document.iterate_items():
                label = getattr(item, "label", None) or ""
                btype = _TYPE_MAP.get(str(label), "text")
                text = getattr(item, "text", None) or ""
                if not text.strip():
                    continue
                page = None
                prov = getattr(item, "prov", None)
                if prov is not None and getattr(prov, "page_no", None) is not None:
                    page = int(prov.page_no)
                layout.append(LayoutBlock(type=btype, text=text, page=page, meta={"label": str(label)}))
        except Exception:  # noqa: BLE001 - 版面映射失败不阻断解析主流程
            layout = []
        return ParsedDocument(
            text=markdown,
            layout=layout,
            engine=self.engine_name,
            metadata={"provider": "docling", "mode": "local", "file_name": Path(file_path).name},
        )

    # ------------------------------------------------------------------ #
    # api 模式（云服务，付费预留）
    # ------------------------------------------------------------------ #
    def _parse_api(self, file_path: str) -> ParsedDocument:
        cfg = self.docling_cfg
        base = getattr(cfg, "api_base", "") if cfg is not None else ""
        key = getattr(cfg, "api_key", "") if cfg is not None else ""
        if not base or not key:
            raise MineruParserError(
                "docling api 模式需要配置：RAG4C_DOCLING_API_BASE 与 RAG4C_DOCLING_API_KEY"
            )
        raise MineruParserError(
            "docling api 模式为预留能力（端点契约待接入），当前请使用 mode=local"
        )


__all__ = ["DoclingParser"]
