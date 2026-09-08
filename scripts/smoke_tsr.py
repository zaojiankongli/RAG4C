"""TSR 表格结构识别 + 旋转开关冒烟。

运行：python scripts/smoke_tsr.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from indexing.parsers.tsr import (  # noqa: E402
    TsrError,
    enrich_table_block,
    parse_html_table,
    parse_markdown_table,
)
from indexing.parsers.base import LayoutBlock  # noqa: E402

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


print("== 1. Markdown 表格 -> 5 标签结构 ==")
md_table = (
    "| 季度 | 请求量 | 弃权率 |\n"
    "| --- | --- | --- |\n"
    "| Q1 | 12,340 | 2.1% |\n"
    "| Q2 | 18,905 | 1.4% |\n"
)
st = parse_markdown_table(md_table)
check("列头识别", st.headers == ["季度", "请求量", "弃权率"], str(st.headers))
check("数据行 2 行", len(st.rows) == 2, str(len(st.rows)))
check("column_header 标签", any("column_header" in c.tags for c in st.cells), str(st.cells[:2]))
check("projected_row_header 标签", any("projected_row_header" in c.tags for c in st.cells))
sentence = st.to_sentence()
check("LLM 友好句子含表头", "表头" in sentence and "Q1" in sentence, sentence[:120])

print("== 2. HTML 表格 -> 跨单元格（spanning_cell） ==")
html_table = (
    "<table><tr><th colspan='2'>年度汇总</th></tr>"
    "<tr><th>季度</th><th>请求量</th></tr>"
    "<tr><td>Q1</td><td>12,340</td></tr></table>"
)
st2 = parse_html_table(html_table)
check("spanning_cell 识别", any("spanning_cell" in c.tags for c in st2.cells), str(st2.cells))
check("colspan=2", any(c.colspan == 2 for c in st2.cells))

print("== 3. 非法表格拒绝 ==")
try:
    parse_markdown_table("这不是表格\n也没有分隔行")
    check("非法表格被拒", False, "竟然通过了")
except TsrError:
    check("非法表格被拒", True)

print("== 4. enrich_table_block（router 集成协议） ==")
block = LayoutBlock(type="table", text=md_table)
enrich_table_block(block, tsr_on=True)
check("meta.tsr 结构写入", "tsr" in block.meta and "headers" in block.meta["tsr"], str(block.meta))
check("跨单元格计数", block.meta["tsr"]["spanning_cells"] == 0, str(block.meta["tsr"]))
block_off = LayoutBlock(type="table", text=md_table)
enrich_table_block(block_off, tsr_on=False)
check("tsr_on=False 不增强", "tsr" not in block_off.meta, str(block_off.meta))

print("== 5. 路由集成（tsr_on / rotation_on 透传） ==")
from indexing.parsers.router import DocumentRouter  # noqa: E402
from indexing.parsers.base import ParsedDocument  # noqa: E402


class StubVision:
    def supports(self, file_path: str) -> bool:
        return True

    def parse(self, file_path: str) -> ParsedDocument:
        return ParsedDocument(
            text="vision（stub）",
            layout=[LayoutBlock(type="table", text=md_table)],
            metadata={"provider": "stub"},
        )


router = DocumentRouter(vision_parser=StubVision(), router_on=True, tsr_on=True, rotation_on=True)
md_file = _PROJECT_ROOT / ".testdata" / "sample.md"
md_file.write_text("# 标题\n\n正文。\n", encoding="utf-8")
parsed = router.parse(str(md_file))
check("tsr_on 标记", parsed.metadata.get("tsr_on") is True, str(parsed.metadata))
check("rotation_on 标记", parsed.metadata.get("rotation_on") is True)
check("表格块被 TSR 增强", "tsr" in parsed.layout[0].meta, str(parsed.layout[0].meta))

print()
print(f"结果：{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
