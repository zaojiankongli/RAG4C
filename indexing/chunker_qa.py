"""CSV / 表格数据 -> QA 对切分器。

CSV 数据的存储方案（回答「CSV 不知道怎么存」）：

- 不存原始表格：每行转成一个 QA 对 chunk（检索友好——问题语义与查询
  高度对齐），text 字段 = 问 + 答；
- 原始行完整数据（列名 -> 值）存入 chunk.metadata["row"]（JSON），
  可过滤、可回显、可溯源，L2 引用验证基于 QA 文本照常工作；
- 原文件保留在 file_path，需要原始 CSV 时随时重解析；
- 支持两种输入：CSV 原文（.csv 直读）与 Markdown 表格（xlsx 经 MinerU
  转换后的形态，复用行解析）。

QA 对文本形态：

    问：{首列值} 的 {其余列名} 分别是多少？
    答：{列名}={值}；{列名}={值}；…

chunk_level="qa" 标记在 metadata，供检索端按层级过滤。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from typing import Any

from indexing.hashing import text_hash
from models.schemas import Chunk

# Markdown 表格分隔行（| --- | --- |）
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}.*\|\s*$")


def _sha256_short(text: str, length: int = 12) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _parse_csv_text(text: str) -> list[list[str]]:
    """解析 CSV 原文为行列表（csv 模块，容错处理）。"""
    rows: list[list[str]] = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if any(cell.strip() for cell in row):
            rows.append([cell.strip() for cell in row])
    return rows


def _parse_markdown_table(text: str) -> list[list[str]]:
    """解析 Markdown 表格为行列表（首行为表头，跳过分隔行）。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    rows: list[list[str]] = []
    for ln in lines:
        if _TABLE_SEP_RE.match(ln):
            continue
        s = ln.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|"):
            s = s[:-1]
        rows.append([cell.strip() for cell in s.split("|")])
    return rows


class CsvQaChunker:
    """CSV / 表格 -> QA 对切分器。

    Args:
        question_template: 问题模板（{row0} 为首列值，{cols} 为其余列名）。
    """

    def __init__(self, question_template: str = "{row0} 的 {cols} 分别是多少？") -> None:
        self.question_template = question_template

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
        """把 CSV / 表格文本切分为 QA 对 Chunk 列表（每行一个）。"""
        if not isinstance(text, str):
            raise ValueError("text 必须是 str")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata 必须是 dict 或 None")
        if not text.strip():
            return []

        # Markdown 表格（含 | --- | 分隔行，xlsx 经 MinerU 的形态）优先检测；
        # 否则按 CSV 原文解析（csv 模块）
        if any(_TABLE_SEP_RE.match(ln.strip()) for ln in text.splitlines()):
            rows = _parse_markdown_table(text)
        else:
            rows = _parse_csv_text(text)
        if len(rows) < 2:
            raise ValueError("无法解析为表格数据（需表头 + 至少一行数据）")

        headers = rows[0]
        chunks: list[Chunk] = []
        seq = start_seq
        now = datetime.now(timezone.utc)
        for row in rows[1:]:
            chunk = self._make_qa_chunk(doc_id, headers, row, source, metadata, seq, now)
            chunks.append(chunk)
            seq += 1
        return chunks

    # ------------------------------------------------------------------ #
    # QA 对构造
    # ------------------------------------------------------------------ #
    def _make_qa_chunk(
        self,
        doc_id: str,
        headers: list[str],
        row: list[str],
        source: str | None,
        metadata: dict[str, Any] | None,
        seq: int,
        now: datetime,
    ) -> Chunk:
        # 对齐列数（行短于表头时补空串）
        values = (row + [""] * len(headers))[: len(headers)]
        row0 = values[0] if values else ""
        rest_cols = [h for h, v in zip(headers[1:], values[1:]) if h]

        question = self.question_template.replace("{row0}", row0).replace(
            "{cols}", "、".join(rest_cols),
        )
        answer = "；".join(f"{h}={v}" for h, v in zip(headers, values) if h)
        text = f"问：{question}\n答：{answer}"

        merged = dict(metadata or {})
        merged["chunk_index"] = seq
        merged["chunk_mode"] = "qa"
        merged["chunk_level"] = "qa"
        merged["headers"] = headers
        merged["row"] = dict(zip(headers, values))
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


__all__ = ["CsvQaChunker"]
