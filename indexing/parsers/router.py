"""DeepDoc 双引擎路由（DocumentRouter）。

PDF 入库时的引擎决策（pdf-inspector 采样分类，10-50ms）：

    pdf_type            -> 引擎
    --------------------------------
    text_based          -> fast（pdf-inspector 提取，~200ms，零 OCR）
    image_based         -> vision（可插拔引擎：mineru / docling）
    scanned             -> vision（可插拔引擎：mineru / docling）
    mixed               -> 按页路由：需 OCR 页走 vision，其余 fast
                           （MVP：整本走 vision + metadata 记录页级路由；
                           页级混合拼接预留 pages_needing_ocr）

非 PDF（docx / pptx / xlsx / 图片）一律走 vision 引擎；路由开关
（settings.parsers.router_on）关闭时全部直走 vision（行为与旧版一致）。

vision 引擎由插件注册表装配（indexing.parsers.registry + plugins），
mineru / docling 同构配置（enabled / mode / priority）插拔式开关；
全部禁用时抛 MineruParserError（可操作提示），调用方（入库管线）
按既有语义处理。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from indexing.parsers.base import MineruParserError, ParsedDocument
from indexing.parsers.pdf_inspector import PdfInspectorParser


class DocumentRouter:
    """DeepDoc 双引擎解析入口（对外呈现与 DocumentParser 一致的协议）。"""

    engine_name = "router"

    def __init__(
        self,
        vision_parser: Any = None,
        fast_parser: Optional[PdfInspectorParser] = None,
        router_on: bool = True,
        pdf_inspector_on: bool = True,
        tsr_on: bool = False,
        rotation_on: bool = True,
        page_limit: int = 0,
    ):
        self.vision_parser = vision_parser
        self.fast_parser = fast_parser or PdfInspectorParser()
        self.router_on = router_on
        self.pdf_inspector_on = pdf_inspector_on
        self.tsr_on = tsr_on
        self.rotation_on = rotation_on
        # 单文档页数上限；<=0 表示不限制。仅对能拿到 page_count 的 PDF
        # 生效（分类阶段顺带产出，不额外解析一遍）。
        self.page_limit = page_limit

    def _check_page_limit(self, file_path: str, decision: dict[str, Any]) -> None:
        """超过页数上限时拒绝解析（在真正解析前拦截，避免大文档拖垮入库）。

        page_count 由 pdf-inspector 分类阶段顺带产出；拿不到（非 PDF /
        路由关闭 / 分类失败）时不做限制，保持既有行为。
        """
        if self.page_limit <= 0:
            return
        pages = decision.get("page_count")
        if not isinstance(pages, int) or pages <= 0:
            return
        if pages > self.page_limit:
            raise MineruParserError(
                f"文档页数 {pages} 超过上限 {self.page_limit} 页，已拒绝解析"
                f"（{file_path!r}）。可调高 RAG4C_MINERU_PAGE_LIMIT，"
                "或先将文档拆分后再入库。"
            )

    def supports(self, file_path: str) -> bool:
        ext = Path(file_path).suffix.lower()
        if ext == ".pdf":
            return self.pdf_inspector_on or (
                self.vision_parser is not None and self.vision_parser.supports(file_path)
            )
        return self.vision_parser is not None and self.vision_parser.supports(file_path)

    def classify(self, file_path: str) -> dict[str, Any]:
        """PDF 分类（路由决策数据源；非 PDF 返回固定决策）。"""
        if Path(file_path).suffix.lower() != ".pdf":
            return {"pdf_type": "other", "confidence": 1.0, "pages_needing_ocr": []}
        if not (self.router_on and self.pdf_inspector_on):
            return {"pdf_type": "routing_disabled", "confidence": 1.0, "pages_needing_ocr": []}
        return self.fast_parser.classify(file_path)

    def route(self, file_path: str) -> dict[str, Any]:
        """路由决策。

        Returns:
            {engine: fast|vision|mixed, pdf_type, confidence, pages_needing_ocr}
        """
        ext = Path(file_path).suffix.lower()
        if ext != ".pdf" or not (self.router_on and self.pdf_inspector_on):
            return {
                "engine": "vision",
                "pdf_type": "other" if ext != ".pdf" else "routing_disabled",
                "confidence": 1.0,
                "pages_needing_ocr": [],
            }
        try:
            cls = self.fast_parser.classify(file_path)
        except Exception as exc:
            if self.vision_parser is None:
                raise
            return {
                "engine": "vision",
                "pdf_type": "classification_failed",
                "confidence": 0.0,
                "pages_needing_ocr": [],
                "fallback_reason": str(exc)[:300],
            }
        pdf_type = cls["pdf_type"]
        if pdf_type == "text_based":
            engine = "fast"
        elif pdf_type == "mixed":
            # MVP：混合型整本走 vision（页级混合拼接预留 pages_needing_ocr）
            engine = "vision"
        else:  # scanned / image_based
            engine = "vision"
        return {**cls, "engine": engine}

    def parse(self, file_path: str) -> ParsedDocument:
        """按路由决策解析文件（fast / vision），并应用后处理开关。

        后处理：
        - tsr_on：表格块附加 TSR 结构（5 标签 + LLM 友好句子）；
        - rotation_on：标记进 metadata（扫描件表格旋转校验开关，
          由 vision 引擎消费；不支持时仅记录）。
        """
        from indexing.parsers.tsr import enrich_table_block

        decision = self.route(file_path)
        self._check_page_limit(file_path, decision)
        if decision["engine"] == "fast":
            parsed = self.fast_parser.parse(file_path)
            parsed.metadata.setdefault("router", decision)
        else:
            # vision：扫描件 / Office / 路由关闭 / 混合型（插拔式引擎）
            if self.vision_parser is None:
                raise MineruParserError(
                    "无可用解析引擎：全部 vision 引擎已禁用或插件未安装"
                    "（parsers.mineru.enabled / parsers.docling.enabled）"
                )
            try:
                parsed = self.vision_parser.parse(file_path)
            except Exception as exc:
                raise MineruParserError(
                    f"Vision 引擎解析失败: {exc}"
                ) from exc
            parsed.engine = "vision"
            parsed.metadata.setdefault("router", decision)
        # TSR 增强（失败静默：增强非硬约束）
        if self.tsr_on:
            for block in parsed.layout:
                enrich_table_block(block, tsr_on=True)
        parsed.metadata["tsr_on"] = self.tsr_on
        parsed.metadata["rotation_on"] = self.rotation_on
        return parsed


# 内置引擎插件注册（indexing/parsers/plugins.py）：import 即注册（幂等）；
# 工厂惰性 import 引擎实现，disabled 引擎的重依赖（torch / docling）绝不加载。
from indexing.parsers import plugins  # noqa: E402,F401


def create_document_parser(settings: Any) -> DocumentRouter:
    """按统一配置装配 DeepDoc 双引擎解析器（入库管线工厂入口）。

    - settings.parsers.router_on / pdf_inspector_on 控制路由；
    - vision 引擎经插件注册表选择（engine=auto 按 priority，或显式
      mineru / docling；每引擎 enabled + mode 一个配置入口）；
    - settings.mineru.page_limit 作为单文档页数上限（<=0 不限制）。
    """
    from config.settings import get_settings
    from indexing.parsers.registry import create_vision_engine

    s = settings if settings is not None else get_settings()
    vision = create_vision_engine(s)  # 全部禁用时为 None
    return DocumentRouter(
        vision_parser=vision,
        router_on=bool(getattr(s.parsers, "router_on", True)),
        pdf_inspector_on=bool(getattr(s.parsers, "pdf_inspector_on", True)),
        tsr_on=bool(getattr(s.parsers, "tsr_on", False)),
        rotation_on=bool(getattr(s.parsers, "rotation_on", True)),
        page_limit=int(getattr(getattr(s, "mineru", None), "page_limit", 0) or 0),
    )


__all__ = ["DocumentRouter", "create_document_parser"]
