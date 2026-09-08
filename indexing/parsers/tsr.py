"""表格结构识别（TSR）：5 标签结构化 + LLM 友好句子转换。

标签（DeepDoc TSR 五元组）：
- column             ：普通数据列
- row                ：普通数据行
- column_header      ：列头（表头行，层级表头支持多行）
- projected_row_header：投影行头（首列承担行标题语义）
- spanning_cell      ：跨单元格（HTML colspan / rowspan 解析）

输入两种形态：
- Markdown 表格（fast 引擎 / MinerU 输出）：首行 + 分隔行识别列头，
  首列非空识别投影行头；合并单元格语义由 HTML 输入承载；
- HTML 表格（MinerU 中间产物）：解析 colspan / rowspan 得 spanning_cell。

输出：
- TableStructure（结构化单元格 + 标签）；
- to_sentence()：转 LLM 友好句子（RAG 检索片段中的表格语义增强）。

本模块零依赖（正则 + 手写解析），可离线导入。
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any


class TsrError(ValueError):
    """表格结构解析失败。"""


@dataclass
class TsrCell:
    """结构化单元格。"""

    row: int
    col: int
    text: str
    tags: list[str] = field(default_factory=list)  # column/row/column_header/projected_row_header/spanning_cell
    colspan: int = 1
    rowspan: int = 1


@dataclass
class TableStructure:
    """TSR 解析结果。"""

    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    cells: list[TsrCell] = field(default_factory=list)
    source: str = "markdown"  # markdown | html

    def to_sentence(self) -> str:
        """转 LLM 友好句子（表头 + 逐行「行头：列头=值」）。"""
        if not self.headers:
            return ""
        parts = ["表格（表头：" + " / ".join(h for h in self.headers if h) + "）"]
        for row in self.rows:
            pairs = []
            for i, cell in enumerate(row):
                if i == 0 and self._has_row_headers:
                    pairs.append(f"行[{cell}]")
                    continue
                header = self.headers[i] if i < len(self.headers) else f"列{i}"
                pairs.append(f"{header}={cell}" if cell else f"{header}=（空）")
            if pairs:
                parts.append("；".join(pairs))
        return "。".join(parts) + "。"

    @property
    def _has_row_headers(self) -> bool:
        return any("projected_row_header" in c.tags for c in self.cells)


def parse_markdown_table(md_table: str) -> TableStructure:
    """解析 Markdown 表格（含表头分隔行）。

    Args:
        md_table: Markdown 表格文本（多行，含 | --- | 分隔行）。

    Returns:
        TableStructure：首行=列头（column_header），首列非空=投影行头。

    Raises:
        TsrError: 无法识别为合法表格。
    """
    lines = [ln.strip() for ln in md_table.strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        raise TsrError("表格行数不足")
    rows: list[list[str]] = []
    sep_idx = -1
    for i, ln in enumerate(lines):
        if _TABLE_SEP_RE.match(ln):
            sep_idx = i
            break
        rows.append(_split_row(ln))
    if sep_idx < 0 or not rows:
        raise TsrError("缺少表头分隔行")
    headers = rows[0]
    data_rows = [_split_row(ln) for ln in lines[sep_idx + 1 :]]
    cells: list[TsrCell] = []
    # 表头行：column_header
    for j, h in enumerate(headers):
        cells.append(TsrCell(row=0, col=j, text=h, tags=["column_header"]))
    has_row_headers = any(
        (row[0].strip() if row else "") and not re.fullmatch(r"\d+", row[0].strip())
        for row in data_rows
    )
    for i, row in enumerate(data_rows):
        for j, cell in enumerate(row):
            tags: list[str] = []
            if j == 0 and has_row_headers:
                tags.append("projected_row_header")
            cells.append(TsrCell(row=i + 1, col=j, text=cell, tags=tags))
    return TableStructure(
        headers=headers,
        rows=data_rows,
        cells=cells,
        source="markdown",
    )


def parse_html_table(html_table: str) -> TableStructure:
    """解析 HTML 表格（含 colspan / rowspan -> spanning_cell）。

    Raises:
        TsrError: 无法解析。
    """
    text = html.unescape(html_table)
    trs = re.findall(r"<tr[^>]*>(.*?)</tr>", text, flags=re.S | re.I)
    if not trs:
        raise TsrError("HTML 表格无 <tr> 行")
    grid: dict[tuple[int, int], tuple[str, int, int]] = {}
    max_col = 0
    row_idx = 0
    for tr in trs:
        tds = re.findall(r"(<t[hd][^>]*>)(.*?)</t[hd]>", tr, flags=re.S | re.I)
        col = 0
        for open_tag, inner in tds:
            while (row_idx, col) in grid:
                col += 1
            colspan = 1
            rowspan = 1
            m = re.search(r"colspan\s*=\s*[\"']?(\d+)", open_tag, flags=re.I)
            if m:
                colspan = int(m.group(1))
            m = re.search(r"rowspan\s*=\s*[\"']?(\d+)", open_tag, flags=re.I)
            if m:
                rowspan = int(m.group(1))
            cell_text = re.sub(r"<[^>]+>", "", inner).strip()
            grid[(row_idx, col)] = (cell_text, colspan, rowspan)
            max_col = max(max_col, col + colspan)
            col += colspan
        row_idx += 1
    n_rows = row_idx
    cells: list[TsrCell] = []
    headers: list[str] = [""] * max_col
    rows: list[list[str]] = []
    is_header = True  # 首行视为表头
    for r in range(n_rows):
        row_vals: list[str] = []
        for c in range(max_col):
            item = grid.get((r, c))
            if item is None:
                row_vals.append("")
                continue
            text, colspan, rowspan = item
            tags: list[str] = []
            if is_header:
                tags.append("column_header")
                headers[c] = text
            if colspan > 1 or rowspan > 1:
                tags.append("spanning_cell")
            cells.append(TsrCell(row=r, col=c, text=text, tags=tags, colspan=colspan, rowspan=rowspan))
            row_vals.append(text)
        rows.append(row_vals)
        is_header = False
    return TableStructure(headers=headers, rows=rows[1:], cells=cells, source="html")


def enrich_table_block(block: Any, tsr_on: bool = True) -> None:
    """给版面表格块附加 TSR 结构（写入 block.meta["tsr"]）。

    失败静默（表格解析失败不中断主流程——TSR 是增强非硬约束）。
    """
    if not tsr_on or getattr(block, "type", "") != "table":
        return
    try:
        raw = getattr(block, "text", "") or ""
        if raw.strip().lower().startswith("<table"):
            structure = parse_html_table(raw)
        else:
            structure = parse_markdown_table(raw)
        block.meta["tsr"] = {
            "headers": structure.headers,
            "row_count": len(structure.rows),
            "spanning_cells": sum(1 for c in structure.cells if "spanning_cell" in c.tags),
            "sentence": structure.to_sentence(),
        }
    except TsrError:
        block.meta["tsr"] = {"error": "表格结构解析失败"}
    except Exception:
        pass


_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}.*\|\s*$")


def _split_row(line: str) -> list[str]:
    """切分 Markdown 表格行（去除首尾管道符后按 | 切分）。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [cell.strip() for cell in s.split("|")]


__all__ = [
    "TsrCell",
    "TableStructure",
    "TsrError",
    "parse_markdown_table",
    "parse_html_table",
    "enrich_table_block",
]
