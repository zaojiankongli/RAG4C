"""DeepDoc 双引擎路由冒烟（真实 pdf-inspector + stub vision 引擎）。

运行：python scripts/smoke_parsers.py
"""
from __future__ import annotations

import sys
import importlib.util
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from indexing.parsers.base import ParsedDocument  # noqa: E402
from indexing.parsers.pdf_inspector import PdfInspectorParser  # noqa: E402
from indexing.parsers.router import DocumentRouter, create_document_parser  # noqa: E402

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


class StubVisionParser:
    """stub vision 引擎（MinerU 占位，验证路由决策而非 OCR 本身）。"""

    def supports(self, file_path: str) -> bool:
        return True

    def parse(self, file_path: str) -> ParsedDocument:
        return ParsedDocument(
            text="vision 解析结果（stub）",
            engine="vision",
            metadata={"provider": "stub"},
        )


DATA = _PROJECT_ROOT / ".testdata"
text_pdf = DATA / "text-based.pdf"
image_pdf = DATA / "image-based.pdf"

router = DocumentRouter(vision_parser=StubVisionParser(), router_on=True)

pdf_inspector_available = importlib.util.find_spec("pdf_inspector") is not None
if pdf_inspector_available:
    print("== 1. 文本型 PDF -> fast 引擎 ==")
    decision = router.route(str(text_pdf))
    check("路由 fast", decision["engine"] == "fast", str(decision))
    check("分类 text_based", decision["pdf_type"] == "text_based", str(decision))
    parsed = router.parse(str(text_pdf))
    check("engine 标记 fast", parsed.engine == "fast", parsed.engine)
    check("layout 非空", len(parsed.layout) > 0, str(len(parsed.layout)))
    types = {b.type for b in parsed.layout}
    check("layout 含 title", "title" in types, str(types))
    check("layout 含 table", "table" in types, str(types))
    check("layout 含 text", "text" in types, str(types))
    check("metadata 含 pdf_type", parsed.metadata.get("pdf_type") == "text_based", str(parsed.metadata))

    print("== 2. 扫描/图像型 PDF -> vision 引擎 ==")
    decision2 = router.route(str(image_pdf))
    check("路由 vision", decision2["engine"] == "vision", str(decision2))
    check("分类 scanned/image_based", decision2["pdf_type"] in ("scanned", "image_based"), str(decision2))
    parsed2 = router.parse(str(image_pdf))
    check("engine 标记 vision", parsed2.engine == "vision", parsed2.engine)
else:
    print("== 1/2. pdf-inspector 可选依赖缺失 -> vision 降级 ==")
    decision = router.route(str(text_pdf))
    check("Fast 插件缺失时路由 vision", decision["engine"] == "vision", str(decision))
    check("降级原因可观测", bool(decision.get("fallback_reason")), str(decision))
    parsed = router.parse(str(text_pdf))
    check("降级解析成功", parsed.engine == "vision", parsed.engine)

print("== 3. 路由开关关闭 -> 全走 vision ==")
router_off = DocumentRouter(vision_parser=StubVisionParser(), router_on=False)
decision3 = router_off.route(str(text_pdf))
check("关闭路由后 vision", decision3["engine"] == "vision", str(decision3))

print("== 4. 非 PDF -> vision ==")
md_file = DATA / "sample.md"
md_file.write_text("# 标题\n\n正文内容。\n", encoding="utf-8")
decision4 = router.route(str(md_file))
check("非 PDF 走 vision", decision4["engine"] == "vision", str(decision4))

print("== 5. classify 独立接口（路由决策数据源） ==")
if pdf_inspector_available:
    fast = PdfInspectorParser()
    cls = fast.classify(str(text_pdf))
    check("classify 字段完整", all(k in cls for k in ("pdf_type", "confidence", "page_count", "pages_needing_ocr")), str(cls))
else:
    print("  [SKIP] pdf-inspector 未安装，真实 classify 集成跳过")

print("== 6. 真实 MinerU Vision 引擎（CLI 可用时集成验证） ==")
import shutil  # noqa: E402

if shutil.which("mineru-open-api") is not None and pdf_inspector_available:
    from config.settings import get_settings  # noqa: E402
    from indexing.parsers.base import create_parser  # noqa: E402

    vision = create_parser(get_settings().mineru)
    parsed_vision = vision.parse(str(text_pdf))
    check("真实 MinerU 解析成功", bool(parsed_vision.text.strip()), parsed_vision.text[:80])
    check("provider 标记 cli", parsed_vision.metadata.get("provider") == "cli")
    check("TSR 输入形态（HTML 表格）", "<table" in parsed_vision.text or "|" in parsed_vision.text)
    # 真实双引擎端到端：fast + vision 同构 ParsedDocument
    real_router = create_document_parser(get_settings())
    fast_parsed = real_router.parse(str(text_pdf))
    check("真实路由 fast 引擎", fast_parsed.engine == "fast", fast_parsed.engine)
else:
    print("  [SKIP] MinerU CLI 或 pdf-inspector 未安装，真实双引擎集成跳过")

print()
print(f"结果：{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
