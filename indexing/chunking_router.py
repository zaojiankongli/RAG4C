"""切分路由（ChunkingRouter）：按文档复杂度 / 类型选择切分模式。

切分模式由 ``CHUNKING_MODES`` 注册表提供，与切分器一一对应：

- recursive（递归固定切分）：简单文档——短文本、无版面结构、无标题。
  等长扁平 chunk，无父块开销（RecursiveChunker）；
- parent_child（父子切分）：难文档——长文档、有标题结构、有版面组件。
  结构感知 + 超长章节父块/子块，small-to-big 回取
  （StructureAwareChunker，项目既有能力）；
- qa（QA 对）：CSV / 表格数据——每行转 QA 对（CsvQaChunker）。

路由模式（mode 配置）：
- auto（默认）：按 ``_ROUTING_RULES`` 有序判定表择一；
- 其余取值：显式固定模式（整库统一），名字必须在注册表里。

新增一种切分模式 = 写一个 mode 类 + 注册一次；要被 auto 选中再加一条判定规则。
本模块不存在按模式名的 if 链，也没有"未知模式悄悄按 parent_child 处理"的兜底——
拼错的模式名会当场报错，而不是安静地换一种切分（切分结果不可复现比切分失败更难查）。

与 IngestPipeline 集成：替换 chunker 调用点，doc 级决策（一次决策，
该文档全部 segment 走同一模式），chunk_id 规则各模式一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from indexing.chunker import StructureAwareChunker
from indexing.chunker_qa import CsvQaChunker
from indexing.chunker_recursive import RecursiveChunker
from indexing.doc_types import is_table_like_doc_type
from core.providers import ProviderRegistry
from models.schemas import Chunk

# 简单文档判定：文本总长阈值（auto 模式）
DEFAULT_SIMPLE_MAX_CHARS = 4000


@dataclass(frozen=True)
class ChunkingRequest:
    """一次切分请求（doc 级决策后逐段复用同一模式）。"""

    doc_id: str
    text: str
    source: str | None = None
    metadata: dict[str, Any] | None = None
    start_seq: int = 0


class ChunkingMode(Protocol):
    """一种切分模式。``reason_code`` 供诊断展示与前端筛选使用。"""

    name: str
    reason_code: str

    def chunk(self, request: ChunkingRequest) -> list[Chunk]: ...


@dataclass(frozen=True)
class ChunkingModeOverrides:
    """``create`` 的配置：允许注入既有切分器实例（测试与复用场景）。"""

    recursive: RecursiveChunker | None = None
    parent_child: StructureAwareChunker | None = None
    qa: CsvQaChunker | None = None


class RecursiveChunkingMode:
    name = "recursive"
    reason_code = "simple_short_no_layout"

    def __init__(self, chunker: RecursiveChunker | None = None) -> None:
        self._chunker = chunker or RecursiveChunker()

    def chunk(self, request: ChunkingRequest) -> list[Chunk]:
        return self._chunker.chunk_document(
            request.doc_id,
            request.text,
            source=request.source,
            metadata=request.metadata,
            start_seq=request.start_seq,
        )


class ParentChildChunkingMode:
    name = "parent_child"
    reason_code = "complex_or_structured"

    def __init__(self, chunker: StructureAwareChunker | None = None) -> None:
        self._chunker = chunker or StructureAwareChunker()

    def chunk(self, request: ChunkingRequest) -> list[Chunk]:
        return self._chunker.chunk_document(
            request.doc_id,
            request.text,
            source=request.source,
            metadata=request.metadata,
            start_seq=request.start_seq,
        )


class QaChunkingMode:
    name = "qa"
    reason_code = "table_doc_type"

    def __init__(self, chunker: CsvQaChunker | None = None) -> None:
        self._chunker = chunker or CsvQaChunker()

    def chunk(self, request: ChunkingRequest) -> list[Chunk]:
        return self._chunker.chunk_document(
            request.doc_id,
            request.text,
            source=request.source,
            metadata=request.metadata,
            start_seq=request.start_seq,
        )


CHUNKING_MODES: ProviderRegistry[ChunkingModeOverrides, ChunkingMode] = ProviderRegistry(
    "chunking mode"
)
CHUNKING_MODES.register(
    "recursive", lambda cfg: RecursiveChunkingMode(cfg.recursive if cfg else None)
)
CHUNKING_MODES.register(
    "parent_child", lambda cfg: ParentChildChunkingMode(cfg.parent_child if cfg else None)
)
CHUNKING_MODES.register("qa", lambda cfg: QaChunkingMode(cfg.qa if cfg else None))


@dataclass(frozen=True)
class _RoutingRule:
    """auto 模式判定表的一条规则：谓词命中即选该模式并给出操作员可读理由。"""

    mode: str
    reason_code: str
    matches: Callable[[str, int, int, int], bool]
    reason: Callable[[str, str, int, int, int], str]


def _is_table_type(doc_type: str, _chars: int, _blocks: int, _limit: int) -> bool:
    """表格类判定：实时查 :mod:`indexing.doc_types`，加一种格式不动这里。"""
    return is_table_like_doc_type(doc_type)


def _is_simple(doc_type: str, chars: int, blocks: int, limit: int) -> bool:
    return chars <= limit and blocks == 0


def _always(doc_type: str, chars: int, blocks: int, limit: int) -> bool:
    return True


_ROUTING_RULES: tuple[_RoutingRule, ...] = (
    _RoutingRule(
        mode="qa",
        reason_code="table_doc_type",
        matches=_is_table_type,
        reason=lambda doc_type, _mode, chars, blocks, limit: (
            f"文档类型「{doc_type}」属表格类，路由为 qa（逐行问答切分）"
        ),
    ),
    _RoutingRule(
        mode="recursive",
        reason_code="simple_short_no_layout",
        matches=_is_simple,
        reason=lambda doc_type, _mode, chars, blocks, limit: (
            f"全文 {chars} 字 ≤ 阈值 {limit} 且版面块为 0，判定为简单文档 → recursive"
        ),
    ),
    _RoutingRule(
        mode="parent_child",
        reason_code="complex_or_structured",
        matches=_always,
        reason=lambda doc_type, _mode, chars, blocks, limit: (
            f"文本 {chars} 字或存在 {blocks} 个版面块，"
            f"超出简单文档条件（阈值 {limit}）→ parent_child"
        ),
    ),
)


class ChunkingRouter:
    """切分模式路由器（对外协议与 chunker 一致：chunk_document）。"""

    def __init__(
        self,
        mode: str = "auto",
        simple_max_chars: int = DEFAULT_SIMPLE_MAX_CHARS,
        recursive: RecursiveChunker | None = None,
        parent_child: StructureAwareChunker | None = None,
        qa: CsvQaChunker | None = None,
    ) -> None:
        overrides = ChunkingModeOverrides(
            recursive=recursive, parent_child=parent_child, qa=qa
        )
        self._modes: dict[str, ChunkingMode] = {
            name: CHUNKING_MODES.create(name, overrides)
            for name in CHUNKING_MODES.names()
        }
        if mode != "auto" and mode not in self._modes:
            raise ValueError(
                f"非法切分模式: {mode!r}（auto/{'/'.join(self._modes)}）"
            )
        self.mode = mode
        self.simple_max_chars = simple_max_chars
        # 最近一次决策（供 traces / 调试）
        self.last_decision: dict[str, Any] = {}

    @property
    def available_modes(self) -> tuple[str, ...]:
        return tuple(self._modes)

    # ------------------------------------------------------------------ #
    # 路由决策
    # ------------------------------------------------------------------ #
    def explain_decision(
        self, doc_type: str, text: str, layout: list[Any] | None = None
    ) -> dict[str, Any]:
        """可解释路由决策：mode + reason_code + reason + facts。

        与 :meth:`decide` 同一判定表；额外输出操作员可读理由，供
        ingest ``parser_meta`` 持久化与前端诊断展示。
        """
        text_chars = len(text or "")
        layout_blocks = len(layout or [])
        normalized_type = (doc_type or "").lower()
        facts: dict[str, Any] = {
            "doc_type": doc_type or "",
            "text_chars": text_chars,
            "layout_blocks": layout_blocks,
            "simple_max_chars": self.simple_max_chars,
            "configured_mode": self.mode,
        }
        if self.mode != "auto":
            return {
                "mode": self.mode,
                "configured_mode": self.mode,
                "reason_code": "explicit_mode",
                "reason": f"配置强制 chunking_mode={self.mode}，不按文档特征路由",
                "facts": facts,
            }
        for rule in _ROUTING_RULES:
            if rule.matches(normalized_type, text_chars, layout_blocks, self.simple_max_chars):
                return {
                    "mode": rule.mode,
                    "configured_mode": self.mode,
                    "reason_code": rule.reason_code,
                    "reason": rule.reason(
                        doc_type or "", rule.mode, text_chars, layout_blocks, self.simple_max_chars
                    ),
                    "facts": facts,
                }
        # 判定表以兜底规则结尾；不可达，但注册表允许他人只加规则时给出可操作错误。
        raise ValueError("切分路由判定表没有任何规则命中，请检查 _ROUTING_RULES")

    def decide(self, doc_type: str, text: str, layout: list[Any] | None = None) -> str:
        """按文档类型 / 复杂度决策切分模式。

        Returns:
            注册表里的某个模式名。
        """
        return str(self.explain_decision(doc_type, text, layout)["mode"])

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
        doc_type: str = "",
        layout: list[Any] | None = None,
    ) -> list[Chunk]:
        """按决策把文本切分为 Chunk 列表（doc 级决策）。"""
        decision_info = self.explain_decision(doc_type, text, layout)
        decision = str(decision_info["mode"])
        self.last_decision = {
            "doc_type": doc_type,
            "mode": decision,
            "text_chars": len(text),
            "reason_code": decision_info.get("reason_code"),
            "reason": decision_info.get("reason"),
            "facts": decision_info.get("facts") or {},
        }
        return self.chunk_with_mode(
            decision, doc_id, text, source=source, metadata=metadata, start_seq=start_seq,
        )

    def chunk_with_mode(
        self,
        mode: str,
        doc_id: str,
        text: str,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        start_seq: int = 0,
    ) -> list[Chunk]:
        """按显式模式切分（doc 级决策后逐段复用，保证段间模式一致）。"""
        resolved = self._modes.get(mode)
        if resolved is None:
            raise ValueError(
                f"未知切分模式: {mode!r}（可用: {'/'.join(self._modes)}）"
            )
        return resolved.chunk(
            ChunkingRequest(
                doc_id=doc_id,
                text=text,
                source=source,
                metadata=metadata,
                start_seq=start_seq,
            )
        )


__all__ = [
    "CHUNKING_MODES",
    "ChunkingMode",
    "ChunkingModeOverrides",
    "ChunkingRequest",
    "ChunkingRouter",
    "DEFAULT_SIMPLE_MAX_CHARS",
    "ParentChildChunkingMode",
    "QaChunkingMode",
    "RecursiveChunkingMode",
]
