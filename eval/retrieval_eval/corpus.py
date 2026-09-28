"""把仓库里的设计/说明文档切成确定性的检索语料。

为什么用仓库文档当语料：生产语料（MySQL catalog 里的 98 篇文档）现在连不上
（``192.168.100.128`` 不可达），而评测不能等到环境齐了才开始。仓库文档是
**真实、稳定、可版本化**的中文技术语料，覆盖架构/检索/管道/性能等主题，
足以承载一轮"检索层能不能把正确的段落捞出来"的测量。

切片口径刻意做得朴素且可复现：
- 先按 Markdown 标题切段（保留所属标题，方便人工标注）；
- 段内按字符数滚动切片（默认 600 字 / 100 字重叠），不引入任何随机性；
- chunk_id = ``<相对路径>#<序号>``，同一份文档每次切出来完全一致。

这样"换嵌入模型 / 换重排策略"时，语料侧是常量，指标变化只能来自被测策略。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from models.schemas import Chunk

#: 默认语料（相对仓库根目录）。选的都是长期稳定的设计/说明文档，
#: 刻意不选手记式 handoff（天天变，会让基线漂）。
DEFAULT_CORPUS_FILES: tuple[str, ...] = (
    "CONTEXT.md",
    "README.md",
    "docs/模块实现说明.md",
    "docs/数据管道说明.md",
    "docs/RAG策略矩阵.md",
    "docs/文档源接入层.md",
    "docs/后端可插拔与高并发改造.md",
    "docs/compose/spec/backend-extensibility-standard.md",
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^\s*```")


@dataclass(frozen=True)
class CorpusChunk:
    """一条语料切片 + 它的出处信息（供人工标注与报告展示）。"""

    chunk: Chunk
    source: str
    heading: str
    order: int

    @property
    def chunk_id(self) -> str:
        return self.chunk.chunk_id

    def preview(self, limit: int = 90) -> str:
        text = self.chunk.text.replace("\n", " ").strip()
        return text[:limit] + ("…" if len(text) > limit else "")


def _split_sections(text: str) -> list[tuple[str, str]]:
    """按 Markdown 标题切成 ``(标题, 正文)`` 列表；代码块不参与标题判定。"""
    sections: list[tuple[str, str]] = []
    heading = ""
    buffer: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        if not in_fence:
            matched = _HEADING_RE.match(line)
            if matched:
                if buffer:
                    sections.append((heading, "\n".join(buffer)))
                    buffer = []
                heading = matched.group(2).strip()
                continue
        buffer.append(line)
    if buffer:
        sections.append((heading, "\n".join(buffer)))
    return sections


def _split_size(body: str, size: int, overlap: int) -> list[str]:
    """按字符数滚动切片（overlap 让跨切片的句子不至于被拦腰截断）。"""
    body = body.strip()
    if not body:
        return []
    if len(body) <= size:
        return [body]
    step = max(1, size - overlap)
    pieces: list[str] = []
    start = 0
    while start < len(body):
        pieces.append(body[start : start + size])
        start += step
    return pieces


def build_corpus(
    root: Path,
    *,
    files: Sequence[str] = DEFAULT_CORPUS_FILES,
    size: int = 600,
    overlap: int = 100,
    tenant_id: str = "eval",
    dataset_id: str = "eval-retrieval",
) -> list[CorpusChunk]:
    """把仓库文档切成检索语料（纯本地、确定性，不联网）。"""
    root = Path(root)
    out: list[CorpusChunk] = []
    fixed_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for rel in files:
        path = root / rel
        if not path.exists():
            continue
        doc_id = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16]
        text = path.read_text(encoding="utf-8", errors="ignore")
        for heading, body in _split_sections(text):
            for piece in _split_size(body, size, overlap):
                piece = piece.strip()
                if len(piece) < 80:  # 太碎的片段（标题行、表格分隔）不进语料
                    continue
                order = len(out)
                chunk_id = f"{rel}#{order}"
                out.append(
                    CorpusChunk(
                        chunk=Chunk(
                            chunk_id=chunk_id,
                            doc_id=doc_id,
                            text=piece,
                            text_hash=hashlib.sha1(piece.encode("utf-8")).hexdigest(),
                            created_at=fixed_time,
                            updated_at=fixed_time,
                            tenant_id=tenant_id,
                            dataset_id=dataset_id,
                            source=rel,
                            metadata={"heading": heading, "source": rel},
                        ),
                        source=rel,
                        heading=heading,
                        order=order,
                    )
                )
    return out


def corpus_fingerprint(chunks: Sequence[CorpusChunk]) -> str:
    """语料指纹：切片口径一变，指纹就变，缓存的嵌入自动失效。"""
    digest = hashlib.sha256()
    for item in chunks:
        digest.update(item.chunk_id.encode("utf-8"))
        digest.update(item.chunk.text_hash.encode("utf-8"))
    return digest.hexdigest()[:16]
