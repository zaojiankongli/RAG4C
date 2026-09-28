"""文档入库管线（文件解析 -> 清洗 -> 预切分 -> 切分 -> 嵌入 -> 写入 Milvus）。

:class:`IngestPipeline` 提供两个入口：

1. :meth:`add_document` / :meth:`upsert_document` / :meth:`delete_document`
   直接操作文本：用 :class:`~indexing.chunker.StructureAwareChunker` 把文档
   切成 Chunk，用嵌入服务（:class:`~core.embedding.EmbeddingService` 协议）
   批量计算稠密向量（稀疏向量由 Milvus 内置 BM25 Function 在写路径自动生成），
   再调用 Milvus 客户端写入；
2. :meth:`add_file` 走完整文件入库链：
   ``parser -> cleaner -> strategy.chunk(segments) -> 逐段 chunk_document ->
   embed -> insert``，并可选通过 :class:`~indexing.graph_builder.GraphBuilder`
   构建知识图谱。

所有失败统一包装为 :class:`IngestError`（继承 :class:`RuntimeError`）。
若调用方处于 ``trace_session`` 上下文中，会自动记录
``parse`` / ``clean`` / ``chunk`` / ``contextual`` / ``embed`` / ``insert``
/ ``graph`` 等 span（配合 :mod:`core.tracing`）。

**Contextual Retrieval 增强（可选阶段）**：构造 :class:`IngestPipeline`
时传入 ``contextualizer`` 并置 ``contextual_enabled=True``，即可在切分后、
嵌入前，用 LLM 为每个 chunk 生成文档级定位上下文（见
:class:`~indexing.contextual.Contextualizer`），写入 chunk 的
``metadata["context"]``，供后续检索 / 重排拼接使用。该阶段默认关闭
（``contextualizer=None`` / ``contextual_enabled=False``），关闭时管线行为
与未引入前完全一致；开启后任何上下文生成失败都会静默降级，绝不影响入库。

本模块不直接依赖 pymilvus / FlagEmbedding / langgraph，可完全离线导入与测试。
"""
from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.tracing import current_trace
from core.observability import get_logger
from core.llm_usage import finish, usage_scope
from config.settings import get_settings, resolve_tenant
from indexing.chunker import StructureAwareChunker
from indexing.doc_types import (
    doc_type_for_extension,
    plain_text_extensions,
    readable_without_parser,
)

if TYPE_CHECKING:
    from core.embedding import EmbeddingService
    from core.milvus_client import RagMilvusClient
    from indexing.graph_builder import GraphBuildResult, GraphBuilder
    from indexing.parsers.base import DocumentParser, ParsedDocument
    from indexing.strategies import ChunkingStrategy
    from models.schemas import Chunk


def _json_scalar_snapshot(mapping: dict[str, Any]) -> dict[str, Any]:
    """留 JSON 标量，其余原样丢弃。

    这里过去写的是两份按名字的白名单（切分事实五个键、解析决策四个键），注释说的是
    "只保留稳定标量"，实现却是按名字点菜：生产方（``chunking_router`` 的 facts、
    parser 的 router 决策）新加一个诊断字段，必须回来加一行才会落库，忘了就
    **静默丢掉** —— 前端的诊断面板于是永远看不到它，而生产方毫无察觉。判据本来就看
    类型，那就按类型判：标量走，list/dict/对象继续挡在外面。
    """
    return {
        str(key): value
        for key, value in mapping.items()
        if value is None or isinstance(value, (bool, int, float, str))
    }


def _safe_file_size(path: Path) -> int:
    """读取文件大小（失败返回 0，避免权限问题中断入库）。"""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _accepts_kwarg(fn: Any, name: str) -> bool:
    """判断可调用对象是否接受某个关键字参数。

    本模块的依赖（parser / cleaner / graph_builder / contextualizer）全部
    以协议鸭子类型注入，因此新增可选能力时不能直接改必需签名——否则会
    悄无声息地打断既有实现。这里显式探测，只对支持的实现传新参数。

    签名不可内省（C 扩展、部分内置可调用对象）时保守返回 False。
    ``**kwargs`` 形参视为接受任意关键字。
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if name in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _infer_doc_type(text: str) -> str | None:
    """按文本形态推断文档类型（无扩展名 / 无显式类型时使用）。

    启发式：含 ATX 标题行的视为 markdown；其余按段落策略（txt）。
    """
    if not text:
        return None
    for line in text.splitlines()[:50]:
        stripped = line.strip()
        if stripped.startswith("#") and len(stripped) > 1:
            return "markdown"
    return "txt"


_logger = get_logger("ingest")


class IngestError(RuntimeError):
    """入库管线错误（切分 / 嵌入 / 写入任一环节失败）。"""


class QuotaExceededError(IngestError):
    """租户配额不足，本次入库被拒。

    单独立一个子类，是为了让上层能把它和"入库炸了"区分开：配额超限是**用户
    可以理解并自行处理**的情况（删点旧文档、或者找管理员提额），既不该按 5xx
    上报、也不该进重试队列——重试多少次配额都还是不够。

    继承 IngestError 是刻意的：既有的 ``except IngestError`` 兜底不会因为新增
    这个类型而漏接。
    """


@dataclass
class FileIngestResult:
    """:meth:`IngestPipeline.add_file` 的单次入库结果。"""

    chunk_count: int = 0
    """实际写入的 Chunk 数量（空文档返回 0）。"""
    graph: "GraphBuildResult | None" = None
    """图索引构建结果；未启用图索引时为 None。"""


class IngestPipeline:
    """文档入库管线。

    Args:
        embedder: 实现 :class:`EmbeddingService` 协议的嵌入服务。
        milvus: 实现 ``insert_chunks / upsert_chunks / delete_by_doc_id /
            ensure_collection`` 接口的 Milvus 客户端
            （生产环境传 :class:`~core.milvus_client.RagMilvusClient`）。
        chunker: 切分器；缺省使用默认参数的 :class:`StructureAwareChunker`。
        parser: 文档解析器（:class:`~indexing.parsers.base.DocumentParser`），
            仅 :meth:`add_file` 使用。parser 为 None、或它不认领这个扩展名时，
            免 parser 直读的格式（见 :mod:`indexing.doc_types` 的
            ``readable_without_parser``：md / mdx / adoc / rst / txt / csv 等）
            按 UTF-8 直读，其余类型报错要求提供 parser。
        cleaner: 文本清洗器（:class:`~indexing.cleaner.Cleaner`）；为 None
            时跳过清洗环节。
        strategy: 文档类型感知的预切分策略
            （:class:`~indexing.strategies.ChunkingStrategy`）；为 None 时
            ``add_file`` 按 ``doc_type`` 用 ``strategy_for()`` 自动选择，
            再为 None 则整篇直接进入切分器。
        graph_builder: 知识图谱索引器
            （:class:`~indexing.graph_builder.GraphBuilder`）；为 None 时
            ``add_file`` 的图索引开关恒为关闭。
        graph_enabled: ``add_file`` 默认是否构建知识图谱（可被调用方
            逐次覆盖）。
        contextualizer: Contextual Retrieval 上下文生成器，实现
            ``contextualize(document, chunks) -> dict[str, str]`` 协议
            （如 :class:`~indexing.contextual.Contextualizer`）；为 None 时
            上下文增强阶段恒为关闭（默认）。
        contextual_enabled: 是否启用上下文增强阶段（仅当
            ``contextualizer`` 非 None 时生效）。启用后在切分完成、嵌入
            之前为每个 chunk 生成文档级定位上下文，写入
            ``metadata["context"]``；生成失败静默降级，不影响入库。
    """

    def __init__(
        self,
        embedder: "EmbeddingService",
        milvus: "RagMilvusClient",
        chunker: StructureAwareChunker | None = None,
        parser: "DocumentParser | None" = None,
        cleaner: Any = None,
        strategy: "ChunkingStrategy | None" = None,
        graph_builder: "GraphBuilder | None" = None,
        graph_enabled: bool = False,
        contextualizer: Any = None,
        contextual_enabled: bool = False,
        chunking_mode: str = "auto",
        simple_doc_max_chars: int = 4000,
    ) -> None:
        self.embedder = embedder
        self.milvus = milvus
        self.chunker = chunker if chunker is not None else StructureAwareChunker()
        self.parser = parser
        self.cleaner = cleaner
        self.strategy = strategy
        self.graph_builder = graph_builder
        self.graph_enabled = graph_enabled
        self.contextualizer = contextualizer
        self.contextual_enabled = contextual_enabled
        # 切分路由：按文档复杂度/类型选择切分模式（recursive / parent_child / qa）
        from indexing.chunking_router import ChunkingRouter

        self.chunking_router = ChunkingRouter(
            mode=chunking_mode,
            simple_max_chars=simple_doc_max_chars,
            parent_child=self.chunker,  # 难文档模式复用既有（可自定义参数的）chunker
        )
        self.chunking_mode = chunking_mode
        self._last_layout: list[Any] = []
        # 最近一次 parse_and_chunk 的观测值，供状态机持久化为文档元信息
        self._last_parsed_metadata: dict[str, Any] = {}
        self._last_parse_ms: float = 0.0
        self._last_chunk_decision: str = ""
        self._last_chunk_decision_meta: dict[str, Any] = {}
        self._last_stage_ms: dict[str, float] = {}
        self._last_counts: dict[str, int] = {}

    def ingest_meta(self, result: "FileIngestResult | None" = None) -> dict[str, Any]:
        """汇总最近一次入库的可观测元信息（供持久化 / 前端展示）。

        这些信息此前全部被丢弃：解析器的路由决策（走的哪个引擎、PDF 是
        文本型还是扫描件、多少页）只写进了 chunk 的 metadata，图谱构建
        结果被返回后无人读取，实际切分方式只是个局部变量。对排查「这份
        文档为什么解析得不好」来说，它们恰恰是最关键的线索。

        Args:
            result: ``embed_and_insert`` 的返回值；为 None 时只汇总解析侧信息。
        """
        parsed = self._last_parsed_metadata or {}
        router = parsed.get("router")
        stage_ms = {name: round(float(self._last_stage_ms.get(name, 0.0)), 1) for name in ("parse", "clean", "split", "contextual", "embed", "insert", "graph")}
        meta: dict[str, Any] = {
            "parse_ms": round(self._last_parse_ms, 1),
            "total_ms": round(sum(stage_ms.values()), 1),
            "stage_ms": stage_ms,
            "chunking_mode": self._last_chunk_decision,
            **self._last_counts,
        }
        decision_meta = self._last_chunk_decision_meta or {}
        if decision_meta.get("reason"):
            meta["chunking_reason"] = str(decision_meta["reason"])
        if decision_meta.get("reason_code"):
            meta["chunking_reason_code"] = str(decision_meta["reason_code"])
        facts = decision_meta.get("facts")
        if isinstance(facts, dict) and facts:
            meta["chunking_decision"] = _json_scalar_snapshot(facts)
        # 解析器侧只补齐管线没写的键：管线那批诊断键若被覆盖，一篇文档实际用的切分
        # 模式就能被某个解析器随口改掉，而诊断面正是唯一能看见它的地方。
        if isinstance(router, dict):
            for key, value in _json_scalar_snapshot(router).items():
                meta.setdefault(key, value)
        for key, value in _json_scalar_snapshot(parsed).items():
            meta.setdefault(key, value)
        graph = getattr(result, "graph", None) if result is not None else None
        if graph is not None:
            meta["graph"] = {
                "entities": getattr(graph, "entity_count", 0),
                "relations": getattr(graph, "relation_count", 0),
                "triplets": getattr(graph, "triplet_count", 0),
                "failed_chunks": getattr(graph, "failed_chunk_count", 0),
            }
        return meta

    # ------------------------------------------------------------------ #
    # 文件入库链
    # ------------------------------------------------------------------ #
    def add_file(
        self,
        file_path: str,
        doc_id: str | None = None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        doc_type: str | None = None,
        graph: bool | None = None,
        tenant_id: str | None = None,
        dataset_id: str = "",
        progress: Any = None,
    ) -> FileIngestResult:
        """解析并入库一个本地文件（可选构建知识图谱）。

        完整链路：``parser -> cleaner -> strategy.chunk(segments) ->
        逐段 chunk_document -> embed -> insert``，最后按需
        ``graph_builder.build`` 构建图索引。

        Args:
            file_path: 本地文件路径。
            doc_id: 文档标识；缺省取文件主名（``Path(file_path).stem``）。
            source: 来源标识，透传到每个 Chunk；缺省取文件路径。
            metadata: 附加元数据，会与解析器元数据合并（解析器元数据优先）。
            doc_type: 文档类型（``markdown`` / ``pdf`` / ``word`` / ``txt`` /
                ``excel``）；为 None 时按扩展名推断，无法推断时整篇直接切分。
            graph: 是否构建知识图谱；为 None 时使用构造参数
                ``graph_enabled``。仅当 ``graph_builder`` 非 None 时生效。
            tenant_id: 租户标识（None / 空串 = 未指定）。由
                :func:`config.settings.resolve_tenant` 解析为有效租户后，
                统一写入每个 chunk 与图索引（enforced=True 时空 / None
                回退 ``default_tenant``，存量调用零改动纳入隔离）。
            dataset_id: 知识库标识（写入每个 chunk 的 dataset_id 标量字段，
                检索侧按知识库过滤；空串表示未指定）。
            progress: 可选进度回调 fn(stage: str, fraction: float,
                detail: str)：stage 为 parsing / splitting / indexing，
                fraction 为该阶段内 0..1 进度（文档状态机用）。回调异常
                会被吞掉，不影响入库主流程。

        Returns:
            :class:`FileIngestResult`（chunk 数量 + 图索引结果）。

        Raises:
            IngestError: 解析 / 清洗 / 切分 / 嵌入 / 写入任一环节失败，
                或需要 parser 而未提供。
        """
        path = Path(file_path)
        doc_id = doc_id or path.stem
        source = source or str(file_path)
        meta = dict(metadata or {})
        meta.setdefault("file_name", path.name)
        meta.setdefault("file_path", str(path))
        meta.setdefault("file_size", _safe_file_size(path))
        # 租户强制解析（enforced=True 时空 / None 回退 default_tenant）
        tenant = resolve_tenant(tenant_id, get_settings())

        def _notify(stage: str, fraction: float, detail: str = "") -> None:
            """进度回调安全包装（回调异常吞掉，不影响入库主流程）。"""
            if progress is None:
                return
            try:
                progress(stage, max(0.0, min(1.0, float(fraction))), detail)
            except Exception:
                pass

        # 两段式拆分：parse_and_chunk（解析/清洗/切分） + embed_and_insert（嵌入/写入），
        # 增量重索引（indexing.reindex）复用第一段拿到未嵌入的新 chunks 做差量。
        chunks = self.parse_and_chunk(
            file_path,
            doc_id=doc_id,
            source=source,
            metadata=metadata,
            doc_type=doc_type,
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            progress=progress,
        )
        if not chunks:
            return FileIngestResult(chunk_count=0)
        tenant = resolve_tenant(tenant_id, get_settings())
        return self.embed_and_insert(chunks, tenant_id=tenant, graph=graph, progress=progress)


    # ------------------------------------------------------------------ #
    # 两段式入库（增量重索引复用第一段）
    # ------------------------------------------------------------------ #
    def parse_and_chunk(
        self,
        file_path: str,
        doc_id: str | None = None,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        doc_type: str | None = None,
        tenant_id: str | None = None,
        dataset_id: str = "",
        progress: Any = None,
    ) -> list["Chunk"]:
        """第一段：解析 -> 清洗 -> 切分（不嵌入、不写库）。

        返回未嵌入的 Chunk 列表（tenant_id / dataset_id 已写入，contextual
        已应用）。供 add_file 与增量重索引（chunk 级差量对比）共用。
        """
        trace = current_trace()
        path = Path(file_path)
        doc_id = doc_id or path.stem
        source = source or str(file_path)
        meta = dict(metadata or {})
        meta.setdefault("file_name", path.name)
        meta.setdefault("file_path", str(path))
        meta.setdefault("file_size", _safe_file_size(path))
        tenant = resolve_tenant(tenant_id, get_settings())

        def _notify(stage: str, fraction: float, detail: str = "") -> None:
            if progress is None:
                return
            try:
                progress(stage, max(0.0, min(1.0, float(fraction))), detail)
            except Exception:
                pass

        try:
            # 1. 解析：parser 优先，缺省按 UTF-8 读文本类文件
            text = self._parse_file(file_path, doc_id)
            parsed_meta = self._last_parsed_metadata
            if parsed_meta:
                meta.update(parsed_meta)
            if trace is not None:
                trace.add_span("parse", self._last_parse_ms)
            _notify("parsing", 1.0, f"解析完成（{self._last_parse_ms:.0f}ms）")

            # 2. 清洗
            if self.cleaner is not None:
                t0 = time.perf_counter()
                text = self.cleaner.clean(text)
                clean_ms = (time.perf_counter() - t0) * 1000.0
                self._last_stage_ms["clean"] = clean_ms
                if trace is not None:
                    trace.add_span("clean", clean_ms)

            # 3. 预切分：strategy（显式 -> 按 doc_type/扩展名 -> 文本启发式）
            resolved_doc_type = doc_type or doc_type_for_extension(path.suffix)
            segments = self._segment(text, resolved_doc_type)

            # 4. 逐段切分（切分路由：doc 级一次决策，逐段复用同模式）
            t0 = time.perf_counter()
            chunks: list = []
            next_seq = 0
            # doc 级决策：csv/excel -> qa；短文本无版面 -> recursive；否则 parent_child
            decision_info = self.chunking_router.explain_decision(
                resolved_doc_type, text, self._last_layout
            )
            chunk_decision = str(decision_info["mode"])
            # 供调用方（状态机）持久化到 parser_meta，让前端能显示实际切分方式与理由
            self._last_chunk_decision = chunk_decision
            self._last_chunk_decision_meta = {
                "mode": chunk_decision,
                "reason": decision_info.get("reason"),
                "reason_code": decision_info.get("reason_code"),
                "facts": decision_info.get("facts") or {},
            }
            for seg_index, segment in enumerate(segments):
                # 进度按**段序号**计算。此前误用 next_seq（累计 chunk 数）作分子，
                # 一旦第一段切出多个 chunk，分数就 >1（被 _notify 夹到 1.0），
                # 明细还会显示成「切分段 37/12」这种不可能的数字。
                _notify(
                    "splitting",
                    seg_index / max(1, len(segments)),
                    f"切分段 {seg_index + 1}/{len(segments)}",
                )
                segment_chunks = self.chunking_router.chunk_with_mode(
                    chunk_decision,
                    doc_id,
                    segment,
                    source=source,
                    metadata=meta,
                    start_seq=next_seq,
                )
                chunks.extend(segment_chunks)
                next_seq += len(segment_chunks)
            split_ms = (time.perf_counter() - t0) * 1000.0
            self._last_stage_ms["split"] = split_ms
            if trace is not None:
                trace.add_span("chunk", split_ms)
            _notify(
                "splitting", 1.0,
                f"切分完成（{len(chunks)} 个 chunk，模式 {chunk_decision}）",
            )
            if not chunks:
                return []

            # 多租户隔离：chunk 的 tenant_id 由入库管线统一赋值（chunker 不感知）
            for c in chunks:
                c.tenant_id = tenant
            # 知识库维度：dataset_id 统一写入每个 chunk（空串=未指定，检索侧不过滤）
            if dataset_id:
                for c in chunks:
                    c.dataset_id = dataset_id

            # 4.5. Contextual Retrieval 增强（可选）
            # 这一步可能有多次 LLM 往返（长文档尤其明显），必须先把进度条
            # 从「切分完成」推进到独立阶段，否则界面会在 60% 静止好几分钟。
            if self.contextualizer is not None and self.contextual_enabled:
                _notify("contextual", 0.0, f"生成片段上下文（{len(chunks)} 个片段）")
            contextual_t0 = time.perf_counter()
            self._apply_contextual(text, chunks)
            self._last_stage_ms["contextual"] = (time.perf_counter() - contextual_t0) * 1000.0
            if self.contextualizer is not None and self.contextual_enabled:
                _notify("contextual", 1.0, "片段上下文生成完成")
            self._last_counts = {
                "text_chars": len(text),
                "layout_blocks": len(self._last_layout),
                "segment_count": len(segments),
                "chunk_count": len(chunks),
            }
            return chunks
        except IngestError:
            raise
        except Exception as exc:
            raise IngestError(f"解析切分失败（doc_id={doc_id!r}）: {exc}") from exc

    def _enforce_chunk_quota(self, tenant_id: str, add_chunks: int) -> None:
        """入库前的 chunk 配额闸门；超限抛 :class:`QuotaExceededError`。

        Args:
            tenant_id: 租户标识。空串表示单租户 / 未启用租户，直接放行。
            add_chunks: 本次拟写入的 chunk 数。

        配额后端不可用时**放行**（fail-open），只记一条 warning。这是权衡后的
        选择：配额是成本与容量的护栏，不是安全边界（安全边界是租户隔离，那条
        在 core/milvus_client.py 的过滤器里且必须 fail-closed）。为了一个护栏
        让所有租户在计费库抖动时全部入库失败，代价远大于短暂超额。

        已知偏差：重试同一篇文档时，upsert 不会真的新增 chunk，但这里仍按新增
        计数，所以卡在配额边缘的文档重试可能被拒。宁可偏保守——反过来（重试
        不计数）会让恶意重试成为绕过配额的口子。
        """
        if not tenant_id or add_chunks <= 0:
            return
        try:
            from core import catalog

            ok, reason = catalog.check_quota(tenant_id, add_chunks=add_chunks)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "配额检查不可用，本次入库放行（tenant=%s, chunks=%d）: %s",
                tenant_id, add_chunks, exc,
            )
            return
        if not ok:
            raise QuotaExceededError(reason)

    def embed_and_insert(
        self,
        chunks: list["Chunk"],
        tenant_id: str = "",
        graph: bool | None = None,
        progress: Any = None,
    ) -> FileIngestResult:
        """第二段：嵌入 -> 写入 Milvus -> 可选图索引。

        Args:
            chunks: parse_and_chunk（或自定义构造）产出的 Chunk 列表。
            tenant_id: 已解析租户（图索引过滤用）。
            graph: 是否构建知识图谱；为 None 时使用 graph_enabled。
            progress: 进度回调（indexing 阶段 0..1）。
        """
        trace = current_trace()

        def _notify(fraction: float, detail: str = "", stage: str = "indexing") -> None:
            if progress is None:
                return
            try:
                progress(stage, max(0.0, min(1.0, float(fraction))), detail)
            except Exception:
                pass

        try:
            # 配额闸门必须在嵌入**之前**：嵌入是整条链路上最贵的一步（按 token
            # 计费的外部调用），放到写入前才拦等于钱已经花掉了。
            #
            # 在此之前 check_quota 在整个生产代码里**一个调用点都没有**——只有
            # smoke 脚本在调。配额能配、能在设置页显示、能在文档里被承诺，就是
            # 从来不生效。core/catalog.py 的模块 docstring 甚至写着"配额检查在
            # 入库前由 Catalog.check_quota 完成"，那句话当时是假的。
            self._enforce_chunk_quota(tenant_id, len(chunks))

            _notify(0.0, "嵌入中")
            texts = [self._embedding_text(c) for c in chunks]
            t0 = time.perf_counter()
            vectors = self.embedder.embed_texts(texts)
            _notify(0.6, "嵌入完成，写入向量库")
            embed_ms = (time.perf_counter() - t0) * 1000.0
            self._last_stage_ms["embed"] = embed_ms
            if trace is not None:
                trace.add_span("embed", embed_ms)
            t0 = time.perf_counter()
            # upsert 而非 insert：chunk_id 是 f"{doc_id}::{seq:04d}::{sha(text)}"，
            # 对同一份文档完全确定。用 insert 的话，任何一次"嵌入成功、写入超时"
            # 后的重试都会把**整篇文档再插一遍**——主键相同也照插，Milvus 不去重。
            # 于是同一段内容在库里有 N 份，检索时占满 top_k、重排看到 N 个一模
            # 一样的候选、生成端拿到 N 份重复证据。而且没有任何报错。
            self.milvus.upsert_chunks(chunks, vectors)
            insert_ms = (time.perf_counter() - t0) * 1000.0
            self._last_stage_ms["insert"] = insert_ms
            if trace is not None:
                trace.add_span("insert", insert_ms)
            _notify(1.0, f"已写入 {len(chunks)} 个片段")

            result = FileIngestResult(chunk_count=len(chunks))
            use_graph = self.graph_enabled if graph is None else graph
            if use_graph and self.graph_builder is not None:
                # 建图是整条链路最慢的一段（每个 chunk 一次 LLM 抽取）。
                # 它必须有自己的进度阶段——否则界面会停在「indexing 100%」
                # 好几分钟，看上去像是卡死了。
                t0 = time.perf_counter()

                def _graph_progress(done: int, total: int) -> None:
                    _notify(
                        done / max(1, total),
                        f"构建知识图谱 {done}/{total}",
                        stage="graph",
                    )

                _notify(0.0, f"构建知识图谱（{len(chunks)} 个片段）", stage="graph")
                # graph_builder 是鸭子类型注入的，进度回调属于可选能力：
                # 只有实现声明了 progress 参数才传，避免给既有实现
                # （自定义构建器 / 测试桩）造成不兼容。
                build_kwargs: dict[str, Any] = {"tenant_id": tenant_id}
                if _accepts_kwarg(self.graph_builder.build, "progress"):
                    build_kwargs["progress"] = _graph_progress
                result.graph = self.graph_builder.build(
                    [(c.chunk_id, c.text) for c in chunks],
                    **build_kwargs,
                )
                graph_ms = (time.perf_counter() - t0) * 1000.0
                self._last_stage_ms["graph"] = graph_ms
                if trace is not None:
                    trace.add_span("graph", graph_ms)
                built = result.graph
                _notify(
                    1.0,
                    (
                        f"图谱完成：{built.entity_count} 个实体 / {built.relation_count} 条关系"
                        if built is not None
                        else "图谱完成"
                    ),
                    stage="graph",
                )
            return result
        except IngestError:
            raise
        except Exception as exc:
            raise IngestError(f"嵌入写入失败: {exc}") from exc

    # ------------------------------------------------------------------ #
    # 内部：待嵌入文本
    # ------------------------------------------------------------------ #
    @staticmethod
    def _embedding_text(chunk: "Chunk") -> str:
        """返回该 chunk 真正送去嵌入的文本。

        Contextual Retrieval 的收益点在这里：``_apply_contextual`` 生成的
        文档级定位上下文写在 ``metadata["context"]``，嵌入时拼在片段正文
        **之前**一起向量化，让脱离上下文的片段（如只写"该比例为 15%"而
        没提主语的段落）也能被正确召回。

        未启用上下文增强时 ``metadata["context"]`` 不存在，退化为原文本，
        与增强前行为完全一致。

        Note:
            只影响嵌入向量，不改写 ``chunk.text``——写入 Milvus 与后续
            引用验证（L2 文本哈希）看到的仍是原始片段正文。
        """
        metadata = chunk.metadata if isinstance(chunk.metadata, dict) else {}
        context = metadata.get("context")
        if isinstance(context, str) and context.strip():
            return context.strip() + "\n\n" + chunk.text
        return chunk.text

    # ------------------------------------------------------------------ #
    # 内部：add_file 子步骤
    # ------------------------------------------------------------------ #
    def _parse_file(self, file_path: str, doc_id: str) -> str:
        """解析文件为文本；记录耗时、解析元数据与版面组件供 add_file 使用。"""
        self._last_parsed_metadata: dict[str, Any] = {}
        self._last_layout: list[Any] = []
        self._last_parse_ms = 0.0
        self._last_stage_ms = {name: 0.0 for name in ("parse", "clean", "split", "contextual", "embed", "insert", "graph")}
        self._last_counts = {}
        t0 = time.perf_counter()
        try:
            if self.parser is not None and self.parser.supports(file_path):
                parsed: "ParsedDocument" = self.parser.parse(file_path)
                self._last_parsed_metadata = dict(parsed.metadata)
                self._last_layout = list(getattr(parsed, "layout", []) or [])
                return parsed.text

            # 纯文本直读是**兜底**，不是「没配 parser 时的特例」。
            #
            # 原先这段只在 ``self.parser is None`` 时可达：一旦为 PDF 配上
            # MinerU，router.supports() 对 .md 返回 False，于是最简单的
            # markdown 反而入不了库——报错还写着"解析器不支持该文件类型"，
            # 把「这个 parser 管不了」说成了「系统读不了」。实测就是这么
            # 翻车的：文档管理页上 test_kb.md 一条鲜红的失败。
            # parser 的职责是啃 PDF/Office，管不到的纯文本本就该直读。
            ext = Path(file_path).suffix.lower()
            if not readable_without_parser(ext):
                hint = (
                    f"已配置的 parser（{type(self.parser).__name__}）不支持该类型"
                    if self.parser is not None
                    else "未配置 parser"
                )
                raise IngestError(
                    f"{hint}，无法解析 {file_path!r}（doc_id={doc_id!r}）："
                    f"请为 .pdf / .doc / .xlsx 等类型提供 DocumentParser。"
                    f"免 parser 直读的扩展名: {', '.join(sorted(plain_text_extensions()))}"
                )
            text = Path(file_path).read_text(encoding="utf-8", errors="replace")
            # ``errors="replace"`` 会把二进制读成一大段 U+FFFD 而不报任何错。
            # 「免 parser 直读」是一张声明表（indexing.doc_types），谁都能加一行，
            # 所以这里必须有个兜底的可见失败：含 NUL 的正文一定不是谁的文档。
            if "\x00" in text:
                raise IngestError(
                    f"{file_path!r}（doc_id={doc_id!r}）按文本直读后含 NUL 字节，"
                    "判定为二进制文件，已拒绝入库；请为它提供 DocumentParser，"
                    "或从 indexing.doc_types 的 readable_without_parser 中移除该扩展名。"
                )
            return text
        finally:
            self._last_parse_ms = (time.perf_counter() - t0) * 1000.0
            self._last_stage_ms["parse"] = self._last_parse_ms

    def _segment(self, text: str, doc_type: str | None) -> list[str]:
        """决定预切分策略并输出章节级 segment；无策略时整篇作为一个段。"""
        strategy = self.strategy
        if strategy is None:
            resolved = doc_type or _infer_doc_type(text)
            if resolved is not None:
                from indexing.strategies import strategy_for

                strategy = strategy_for(resolved)
        if strategy is None:
            return [text] if text else []
        return strategy.chunk(text)

    # ------------------------------------------------------------------ #
    # 内部：Contextual Retrieval 增强（可选阶段）
    # ------------------------------------------------------------------ #
    def _apply_contextual(self, document_text: str, chunks: list["Chunk"]) -> None:
        """为每个 chunk 生成文档级定位上下文，原地写入 ``metadata["context"]``。

        仅在 ``contextualizer`` 非 None 且 ``contextual_enabled`` 为 True
        时执行；否则直接返回（零开销、行为与未启用时完全一致）。

        流程：收集 ``[(chunk_id, text), ...]`` 调用
        ``contextualizer.contextualize(document_text, pairs)``，得到
        ``{chunk_id: context}``，再逐个把非空 context 写入对应 chunk 的
        ``metadata["context"]``（保留原 metadata 其余键）。

        防御性：
        - chunks 为空 / 文档为空：直接返回；
        - ``contextualize`` 抛任何异常（含 LLM 失败）或返回畸形 dict：
          静默吞掉，绝不干扰后续嵌入 / 写入；
        - context 非 str 或空串：跳过该 chunk，其余 chunk 不受影响。

        Args:
            document_text: 整篇文档全文（生成定位上下文的唯一依据）。
            chunks: 切分完成后的 chunk 列表（原地修改其 metadata）。

        Note:
            写入的 ``metadata["context"]`` 为字符串，满足 Milvus JSON 字段
            可序列化约束。
        """
        if self.contextualizer is None or not self.contextual_enabled:
            return
        if not document_text or not chunks:
            return
        trace = current_trace()
        pairs = [(c.chunk_id, c.text) for c in chunks]
        t0 = time.perf_counter()
        try:
            contexts = self.contextualizer.contextualize(document_text, pairs)
            if trace is not None:
                trace.add_span("contextual", (time.perf_counter() - t0) * 1000.0)
        except Exception:
            # 上下文生成失败（含 LLM 调用失败）：静默降级，不影响入库
            if trace is not None:
                trace.add_span("contextual", (time.perf_counter() - t0) * 1000.0)
            return
        if not isinstance(contexts, dict):
            return
        for chunk in chunks:
            context = contexts.get(chunk.chunk_id)
            if isinstance(context, str) and context.strip():
                chunk.metadata["context"] = context

    # ------------------------------------------------------------------ #
    def add_document(
        self,
        doc_id: str,
        text: str,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        tenant_id: str | None = None,
        dataset_id: str = "",
    ) -> int:
        """切分并入库一篇新文档。

        Args:
            doc_id: 文档标识。
            text: 文档原始文本。
            source: 来源标识，透传到每个 Chunk。
            metadata: 附加元数据（须可 JSON 序列化）。
            tenant_id: 租户标识（None / 空串 = 未指定）。由
                :func:`config.settings.resolve_tenant` 解析为有效租户后
                统一写入每个 chunk（enforced=True 时空 / None 回退
                ``default_tenant``，存量调用零改动纳入隔离）。
            dataset_id: 知识库标识（空串 = 未指定，检索侧不按知识库过滤）。
                语义与 :meth:`add_file` 的同名参数一致——文本入库与文件入库
                必须能写出同样的知识库维度，否则纯文本来源（爬取的文档、
                API 推送的内容）无法参与按知识库检索。

        Returns:
            实际写入的 Chunk 数量（空文档返回 0）。

        Raises:
            IngestError: 切分 / 嵌入 / 写入任一环节失败。
        """
        trace = current_trace()
        # 租户强制解析（enforced=True 时空 / None 回退 default_tenant）
        tenant = resolve_tenant(tenant_id, get_settings())
        # 入库期的 LLM 用量台账：contextual（片段上下文）/ triplet（三元组）/
        # classifier（自动标签）这几个槽位都是**按片段数线性放大**的，一篇大文档
        # 能把配额烧穿。没有台账就只能等账单说话，所以这里把"这篇花了多少"记下来。
        # 问答路径把台账挂在 QueryResult.usage 上；入库路径没有返回字段，
        # 因此走"指标 + 日志"留痕（core.llm_usage.finish）。
        with usage_scope() as _usage:
            try:
                chunks = self.chunker.chunk_document(
                    doc_id, text, source=source, metadata=metadata
                )
                if not chunks:
                    return 0
                # 多租户隔离：chunk 的 tenant_id 由入库管线统一赋值（chunker 不感知）
                for c in chunks:
                    c.tenant_id = tenant
                # 知识库维度：与 parse_and_chunk 一致，空串不写（保留 schema 默认）
                if dataset_id:
                    for c in chunks:
                        c.dataset_id = dataset_id
                # Contextual Retrieval 增强（可选）：整篇文档为每个 chunk
                # 生成定位上下文，写入 metadata["context"]
                contextual_t0 = time.perf_counter()
                self._apply_contextual(text, chunks)
                self._last_stage_ms["contextual"] = (time.perf_counter() - contextual_t0) * 1000.0
                self._enforce_chunk_quota(tenant, len(chunks))
                texts = [self._embedding_text(c) for c in chunks]

                t0 = time.perf_counter()
                vectors = self.embedder.embed_texts(texts)
                embed_ms = (time.perf_counter() - t0) * 1000.0
                self._last_stage_ms["embed"] = embed_ms
                if trace is not None:
                    trace.add_span("embed", embed_ms)

                t0 = time.perf_counter()
                # 同 embed_and_insert：chunk_id 确定，用 upsert 让重试幂等
                self.milvus.upsert_chunks(chunks, vectors)
                insert_ms = (time.perf_counter() - t0) * 1000.0
                self._last_stage_ms["insert"] = insert_ms
                if trace is not None:
                    trace.add_span("insert", insert_ms)
                return len(chunks)
            except IngestError:
                raise
            except Exception as exc:
                raise IngestError(f"索引文档失败（doc_id={doc_id!r}）: {exc}") from exc
            finally:
                # 成功失败都要收尾：失败的那次调用同样花了 token
                try:
                    finish(_usage, scope="ingest", prices=get_settings().llm.price_table, logger=_logger)
                except Exception:  # noqa: BLE001 - 台账永远不该影响入库结果
                    pass

    def upsert_document(
        self,
        doc_id: str,
        text: str,
        source: str | None = None,
        metadata: dict[str, Any] | None = None,
        tenant_id: str | None = None,
        dataset_id: str = "",
    ) -> int:
        """覆盖式更新文档：先删旧行，再按新内容重建。

        注意：文档修订时 chunk_id 会重新生成（索引重建改变 id 属设计使然，
        参见任务书 L1/L2 校验），依赖旧 chunk_id 的引用需在修订后刷新。

        Args:
            同 :meth:`add_document`（含 ``tenant_id`` / ``dataset_id``，
            重建时按新租户与新知识库写入）。

        Returns:
            重建后写入的 Chunk 数量。

        Raises:
            IngestError: 删除或重建任一环节失败。
        """
        try:
            self.milvus.delete_by_doc_id(doc_id)
            return self.add_document(
                doc_id,
                text,
                source=source,
                metadata=metadata,
                tenant_id=tenant_id,
                dataset_id=dataset_id,
            )
        except IngestError:
            raise
        except Exception as exc:
            raise IngestError(f"更新文档失败（doc_id={doc_id!r}）: {exc}") from exc

    def delete_document(self, doc_id: str, unregister: bool = True) -> int:
        """按 doc_id 删除该文档的全部 Chunk。

        Args:
            unregister: 同时注销关系库里的文档记录并退还用量。**替换式重新入库
                （先删后写同一个 doc_id）应当传 False**：那种场景文档还在，只是
                内容换了，注销掉会把它的配额名额一并退掉，紧接着重新登记时又要
                重新占一次——中间挤进别的写入就可能占不回来，好端端一次更新变成
                配额失败。

        Returns:
            删除条数。

        Raises:
            IngestError: 删除失败。

        用量退还放在这里而不是各个调用方，因为这是删除的唯一收口。此前它只删
        Milvus，关系库那边文档行照留、用量照挂：源同步每清理一篇上游已删的文
        档，租户 chunk_count 就永久虚高一截，只涨不落，最后把配额烧穿。
        """
        try:
            removed = int(self.milvus.delete_by_doc_id(doc_id))
        except IngestError:
            raise
        except Exception as exc:
            raise IngestError(f"删除文档失败（doc_id={doc_id!r}）: {exc}") from exc

        if unregister:
            try:
                from core import catalog

                catalog.remove_document(doc_id)
            except Exception as exc:  # noqa: BLE001
                # 与配额闸门同一套取舍（见 _enforce_chunk_quota）：记账后端抖动
                # 不该让删除本身失败——chunk 已经删掉了，抛错只会让调用方以为
                # 没删而重试。留一条 warning，靠 catalog.recount_usage 对账兜底。
                _logger.warning(
                    "文档 %s 的 chunk 已删除，但用量退还失败（可跑 recount_usage 对账）: %s",
                    doc_id, exc,
                )
        return removed

    def ensure_collection(self) -> None:
        """确保目标集合存在（含 BM25 Function 与索引），幂等。

        Raises:
            IngestError: 创建 / 加载集合失败。
        """
        try:
            self.milvus.ensure_collection()
        except IngestError:
            raise
        except Exception as exc:
            raise IngestError(f"确保集合存在失败: {exc}") from exc


__all__ = ["IngestPipeline", "IngestError", "QuotaExceededError", "FileIngestResult"]
