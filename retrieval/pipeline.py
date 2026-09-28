"""检索管线：门控 -> 改写 -> 路由 -> 嵌入 -> 混合检索 -> [查询端增强] ->
来源多样性 -> [图谱分支] -> 重排序 -> [检索端增强] -> 裁剪。

**编排的真相在 `retrieval/stages.py` 的注册表里，不在下面这份编号里。**
`RetrievalPipeline.run()` 只做 `StageRunner(retrieval_stage_order()).run(...)`；
新增一个阶段是往 `RETRIEVAL_STAGES` 注册，不是改本文件的分支。下面的编号保留的是
**各阶段的语义与为什么在那个位置**（仍然成立且有用），但阶段清单与实际次序请以
`retrieval_stage_order()` 为准（当前 19 项，其中 5 项是不单独出 trace 节点的
次序守卫与预取：`enhancers.prefetch`、`search.filter_expr`、三道 `serving_fence.*`）。

各阶段语义（编号沿用迁移前的流程叙述）：
1. **复杂度门控**：查询已具体明确时跳过改写与路由（零 LLM 成本），
   直接走 hybrid 默认路由（置信度 1.0）。
2. **查询改写**：复杂查询先 LLM 改写（失败静默降级为原文）。
3. **意图路由**：改写结果参与路由（嵌入相似度为主，LLM 兜底），
   低置信度自动降级，全程不抛错。
4. **嵌入**：BGE-M3 稠密嵌入；HyDE 可选增强在嵌入前生效（见 4a）。
5. **混合检索**：BGE-M3 稠密 + Milvus 内置 BM25，按
   ``top_k * search_candidate_factor`` 请求候选给重排留余量（系数可配置，
   默认 2 = 改造前行为）；支持 ACL 过滤表达式与按 doc 分组。

   查询端可选增强（默认关闭；开关开启且注入对应组件时生效，失败一律
   静默降级、只记录 trace，绝不中断管线）：
   - **4a. HyDE**（嵌入阶段前）：``hyde_on`` 且注入 hyde 组件时，先用
     LLM 生成"假设文档"，稠密检索用假设文档的向量，BM25 文本检索仍用
     改写后的查询（保持混合检索语义）；未产出 / 生成失败时退回原查询
     嵌入（span 记 0ms）。
   - **4b. SubQueries**（混合检索后、多样性前）：``subqueries_on`` 且
     注入 subqueries 组件时，把查询拆成多个子查询分别检索，结果按
     chunk_id 去重合并进候选（扩大召回）；单路子查询失败跳过该路，
     全部失败回退主查询结果。
   - **4c. Stepback**（混合检索后、多样性前）：``stepback_on`` 且注入
     stepback 组件时，额外用后退式抽象问题检索一次并合并候选（同样
     按 chunk_id 去重）；生成或检索失败静默跳过。
6. **来源多样性**：``source_diversity`` 三态开关，``group_mmr`` 额外做
   文档感知 MMR 后过滤。查询端增强的合并发生在多样性之前，让增强扩大
   的候选参与多样性过滤（增强扩召回、多样性控质量）。
7. **图谱分支**：路由命中 ``vector_graph_rag`` / ``full`` 且
   ``graph_retrieval_on`` 开启、注入了图检索器时，执行图检索并取回
   passage，与混合结果按 chunk_id 去重合并（``vector_graph_rag`` 图结果
   优先，``full`` 混合结果优先）。图分支失败 / 无命中 / 未启用时
   降级为纯 hybrid 结果并标记 ``degraded=True``。
8. **重排序**：可插拔 reranker 对合并后的候选重排，失败时保持原始顺序
   并记录 trace。
9. **检索端可选增强（重排后、裁剪前）**：
   - **SentenceWindow**：``sentence_window_on`` 且注入 sentence_window
     组件时，把命中子块展开为其父块（small-to-big 父块回取），让父块
     以完整上下文参与最终 top_k 裁剪；展开失败原样保留结果。
10. **裁剪**：取前 ``top_k`` 条返回。

全流程离线可测：依赖均以协议（duck-typing）注入，不直接 import
pymilvus / FlagEmbedding / openai。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from config.settings import get_settings
from core.document_serving import DocumentServingContext, KnowledgeChanged, current_document_serving
from models.schemas import Chunk, RetrievedChunk, RouteDecision

from .graph_retriever import GraphRetrieverError
from .stages import (
    RetrievalState,
    StageContext,
    StageRunner,
    add_span as _add_span_record,
    retrieval_stage_order,
)


def dense_cosines_of(chunks: list[RetrievedChunk]) -> list[float]:
    """从一批 chunk 里取出真实稠密余弦（没有的跳过）。

    编排器手里的 ``chunks`` 常常不是某一个 ``RetrievalResult`` 的原物——二轮
    检索会把两轮合并，图谱分支会掺进按 id 直取的行。所以这里做成对 chunk 列表
    的自由函数，谁手里是哪批 chunk 就对哪批算，避免"分数取自合并后的列表、
    余弦取自第一轮的结果"这种两边对不上的错位。

    返回空列表有两种含义，对下游是同一件事（这一路信号没有）：调用方压根没
    索取余弦（``with_cosine=False``），或索取了但命中项没带回向量。父块回取
    （sentence_window）与图谱直取出来的 chunk 天然没有余弦，会被跳过——它们
    本来就不是向量检索命中的那批。
    """
    return [rc.dense_cosine for rc in chunks if rc.dense_cosine is not None]


@dataclass
class RetrievalResult:
    """一次检索的完整结果。

    Attributes:
        query: 用户原始查询（未经改写）。
        chunks: 最终结果（最多 ``top_k`` 条，按重排分数降序）。
        route: 实际路由决策（含降级标记）。
        traces: 各阶段耗时与降级说明（``"name:xx.xxms"`` 形式）。
        reranked: 重排是否真的跑成功了。**决定 ``chunk.score`` 落在哪把尺子上**：
            为 True 时是 reranker 相关度（0~1，可与 ``retrieval_score_threshold``
            比较）；为 False 时是 Milvus 的 RRF 融合分——只由名次决定，不含相似度
            信息，拿它跟任何阈值比都没有意义（详见
            :meth:`verify.abstention.AbstentionGate.decide`）。
            下游据此决定检索闸门是否参与判定，别再去猜分数的量纲。
    """

    query: str
    chunks: list[RetrievedChunk]
    route: RouteDecision
    traces: list[str] = field(default_factory=list)
    reranked: bool = False

    def dense_cosines(self) -> list[float]:
        """本次结果里各 chunk 的真实稠密余弦（委托 :func:`dense_cosines_of`）。

        故意做成现算的方法而不是再开一个字段：多存一份就多一条会跟 ``chunks``
        走散的路。管线里 chunks 在裁剪、租户收口、sentence_window 展开之后还会
        变，字段拷贝一旦早于这些步骤，弃权判定看到的就是一批已经被剔掉的
        chunk 的分数。
        """
        return dense_cosines_of(self.chunks)


class RetrievalPipeline:
    """检索管线。

    Args:
        embedder: 嵌入服务（``embed_query`` / ``embed_texts``）。
        milvus: Milvus 客户端。注入契约：``hybrid_search`` /
            ``build_filters``（后者组合 tenant + dataset + ACL + 用户表达式，
            由客户端实现以便不同向量库提供各自的过滤方言）。
        reranker: 重排序器（``rerank(query, chunks) -> list[float]``）。
        rewriter: 查询改写器（``rewrite(query) -> (str, bool)``）。
        router: 路由解析器（``route(query) -> RouteDecision``）。
        settings: 配置对象（含 ``pipeline`` 段）；为 None 时取全局单例。
        hyde: HyDE 假设文档生成器（``generate(query) -> str | None``），
            默认 None（禁用）。
        subqueries: 子查询拆解器（``generate(query) -> list[str]``），
            默认 None（禁用）。
        stepback: 后退式提问生成器（``generate(query) -> str | None``），
            默认 None（禁用）。
        sentence_window: 父块回取展开器
            （``expand(items) -> list[RetrievedChunk]``），默认 None（禁用）。
        components: 注册表驱动的可选策略组件（``{组件名: 组件 | None}``）。
            具名参数保留是为了既有调用方；**新增一个可选策略走这里，不必再改
            本签名**（阶段与装配点都从 ``retrieval.stages`` 的注册表拿名字）。
    """

    def __init__(
        self,
        embedder: Any,
        milvus: Any,
        reranker: Any,
        rewriter: Any,
        router: Any,
        settings: Any = None,
        graph_retriever: Any = None,
        hyde: Any = None,
        subqueries: Any = None,
        stepback: Any = None,
        sentence_window: Any = None,
        auto_filter: Any = None,
        reranker_cb: Any = None,
        serving_guard: Any = None,
        components: Optional[dict[str, Any]] = None,
    ) -> None:
        self.embedder = embedder
        self.milvus = milvus
        self.reranker = reranker
        # 重排熔断器（可选）。为 None 时行为与接入前完全一致。
        self.reranker_cb = reranker_cb
        self.rewriter = rewriter
        self.router = router
        # 可选策略组件的唯一存放处；具名属性是它的镜像（历史接线与
        # ``rag_topology.available_components_from_pipeline`` 都按属性读）。
        self._components: dict[str, Any] = {
            "hyde": hyde,
            "subqueries": subqueries,
            "stepback": stepback,
            "sentence_window": sentence_window,
            "graph_retriever": graph_retriever,
            "auto_filter": auto_filter,
        }
        for name, value in (components or {}).items():
            self._components[name] = value
        for name, value in self._components.items():
            setattr(self, name, value)
        self.serving_guard = serving_guard
        self.settings = settings if settings is not None else get_settings()
        # 兼容传入完整 Settings（含 .pipeline）或直接传入 PipelineSettings
        self.pipeline = getattr(self.settings, "pipeline", self.settings)
        # 子查询（SubQueries）并发检索线程池：惰性创建、实例级复用。
        # RetrievalPipeline 由 rag.get_pipeline() 构造为进程级单例，
        # 因此这里创建一次即可被所有查询复用，避免每次查询反复建池。
        self._fanout_executor: Optional[ThreadPoolExecutor] = None
        self._fanout_lock = threading.Lock()
        # 查询端增强（HyDE / 子查询 / 后退提问）的生成线程池。与上面那个分
        # 开是刻意的：这两个池服务于关键路径上**先后**发生的两件事（先生成、
        # 后扇出检索），共用一个池就意味着子查询的池宽被调小时，三次生成也
        # 跟着串行回去——两个无关的旋钮被绑在了一起。
        self._enhance_executor: Optional[ThreadPoolExecutor] = None
        self._enhance_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 可选策略组件存取（注册表名 ↔ 属性）
    # ------------------------------------------------------------------ #
    def component(self, name: str) -> Any:
        """按注册表名取可选策略组件；未装配 / 装配失败即为 None。"""
        return self._components.get(name)

    def set_component(self, name: str, value: Any) -> None:
        """登记一个可选策略组件（属性与注册表字典保持同源）。"""
        self._components[name] = value
        setattr(self, name, value)

    # ------------------------------------------------------------------ #
    # 追踪
    # ------------------------------------------------------------------ #
    @staticmethod
    def _add_span(name: str, duration_ms: float, traces: list[str]) -> None:
        """记录 span 到当前 trace（若存在）与结果 traces。"""
        _add_span_record(name, duration_ms, traces)

    # ------------------------------------------------------------------ #
    # 子查询并发检索执行器（性能：把 N 路子查询检索从串行变并发）
    # ------------------------------------------------------------------ #
    def _ensure_fanout_executor(self) -> ThreadPoolExecutor:
        """惰性创建子查询并发检索线程池（实例级复用，跨查询共用）。

        宽度从配置读（``pipeline.subquery_fanout_workers``）。从前是写死的
        4：这个池是**进程级单例**，由所有并发查询共用，而 HTTP 侧最多放行
        32 路并发、每路最多拆 ``max_sub_queries`` 个子查询——最坏情况下几十
        个检索任务排在 4 个 worker 后面，"并发扇出"就退化成了排队，排队的
        时间一分不少地算在用户等待里。既然池宽取决于部署（Milvus 扛得住
        多少 vs 能忍多少延迟），它就不该是个字面量。

        池只创建一次，所以调整这个值需要重建管线（配置热更新会
        ``reset_pipeline``，走的正是这条路）。
        """
        if self._fanout_executor is not None:
            return self._fanout_executor
        with self._fanout_lock:
            if self._fanout_executor is None:
                workers = int(getattr(self.pipeline, "subquery_fanout_workers", 4) or 4)
                self._fanout_executor = ThreadPoolExecutor(
                    max_workers=max(1, workers), thread_name_prefix="rag-subquery"
                )
            return self._fanout_executor

    def shutdown(self) -> None:
        """关闭本管线持有的线程池（组件重建 / 进程关闭时调用，幂等）。"""
        with self._fanout_lock:
            executor = self._fanout_executor
            self._fanout_executor = None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        with self._enhance_lock:
            enhance = self._enhance_executor
            self._enhance_executor = None
        if enhance is not None:
            enhance.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------ #
    # 查询端增强的并发生成（性能：三次串行 LLM 往返 -> 一次并发）
    # ------------------------------------------------------------------ #
    #: 三个查询端增强器的名字，顺序即池宽的上界（每个增强器一条线程）。
    _ENHANCER_NAMES = ("hyde", "subqueries", "stepback")

    def _ensure_enhance_executor(self) -> ThreadPoolExecutor:
        """惰性创建查询端增强的生成线程池（实例级复用，跨查询共用）。

        宽度写死为增强器个数，**不做成配置项**：这里的并发度不是"多少合适"
        的调优问题，而是"这三件事本来就该同时做"的结构问题——多一条线程无
        处可用，少一条就有一个增强器要排队等另一个。真正该调的旋钮是每个
        增强器开不开（``hyde_on`` / ``subqueries_on`` / ``stepback_on``）。
        """
        if self._enhance_executor is not None:
            return self._enhance_executor
        with self._enhance_lock:
            if self._enhance_executor is None:
                self._enhance_executor = ThreadPoolExecutor(
                    max_workers=len(self._ENHANCER_NAMES),
                    thread_name_prefix="rag-enhance",
                )
            return self._enhance_executor

    # ------------------------------------------------------------------ #
    # 混合检索开关
    # ------------------------------------------------------------------ #
    def _bm25_text(self, text: str) -> Optional[str]:
        """按 ``hybrid_search_on`` 决定是否参与 BM25 稀疏分支。

        Milvus 客户端以 ``query_text`` 非空为条件追加 BM25 分支
        （见 :meth:`core.milvus_client.RagMilvusClient.hybrid_search`），
        因此关闭混合检索时传 None 即降级为纯稠密（语义）检索。
        统一走这里，保证主检索与子查询 / stepback 增强检索语义一致。
        """
        return text if self.pipeline.hybrid_search_on else None

    def _auto_filter_expr(
        self,
        query: str,
        dataset: str,
        traces: list[str],
    ) -> Optional[str]:
        """生成并校验自动元数据过滤表达式（失败降级为 None，不阻断检索）。

        user_fields 来自 catalog 元数据 schema（按 dataset 读取，无 schema 时
        为空列表）。LLM 生成或校验失败时记 trace 并返回 None——过滤是优化，
        绝不让它阻断主链路。
        """
        try:
            from core import catalog

            fields = catalog.list_metadata_fields(dataset) if dataset else []
            user_fields = [
                {"key": str(f.get("key", "")), "value_type": str(f.get("value_type", "string"))}
                for f in fields
                if f.get("key")
            ]
            expr = self.auto_filter.generate(query, user_fields)
            return expr
        except Exception as exc:  # noqa: BLE001 - 降级：不过滤
            traces.append(f"auto_filter 不可用（降级为不过滤）: {type(exc).__name__}")
            return None

    def _serving_context(
        self,
        tenant: str,
        dataset: str,
    ) -> DocumentServingContext | None:
        context = current_document_serving()
        if context is None and self.serving_guard is not None:
            snapshot = self.serving_guard.snapshot(tenant, dataset)
            context = DocumentServingContext(self.serving_guard, snapshot)
        if context is None:
            return None
        snapshot = context.snapshot
        if snapshot.tenant_id != tenant:
            raise KnowledgeChanged("serving snapshot tenant does not match query scope")
        if snapshot.dataset_id != dataset:
            raise KnowledgeChanged("serving snapshot dataset does not match query scope")
        return context

    @staticmethod
    def _filter_serving_candidates(
        candidates: Sequence[RetrievedChunk],
        context: DocumentServingContext | None,
        traces: list[str],
        *,
        stage: str,
    ) -> list[RetrievedChunk]:
        if context is None or not candidates:
            return list(candidates)
        before = len(candidates)
        filtered = context.guard.filter_candidates(context.snapshot, candidates)
        removed = before - len(filtered)
        if removed:
            traces.append(f"serving fence({stage})：剔除 {removed} 条不可检索文档证据")
        return filtered

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def run(
        self,
        query: str,
        acl: Optional[list[str]] = None,
        tenant_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
    ) -> RetrievalResult:
        """执行一次检索。

        Args:
            query: 用户原始查询。
            acl: 访问控制列表（如 ``["fin", "legal"]``），为 None 不过滤。
            tenant_id: 租户标识（None / 空串 = 未指定）。由
                ``config.settings.resolve_tenant`` 解析为有效租户：
                enforced=True 时空 / None 回退 ``default_tenant``
                （存量调用零改动纳入隔离）；enforced=False 时空串表示
                **不过滤**（开发 / 单租户模式）。检索侧 tenant 过滤无条件
                启用（enforced 语义），ACL 过滤仍受 ``acl_filter_on`` 控制。
            dataset_id: 知识库标识（None / 空串 = 不过滤，检索全部知识库）。
                非空时只在该知识库内检索——入库侧由
                ``IngestPipeline`` 写入 chunk 的 ``dataset_id`` 标量字段。
                与 tenant 的区别：tenant 是安全边界（enforced 时强制回退到
                默认租户），dataset 是检索范围（空即全域，不做强制）。

        Returns:
            :class:`RetrievalResult`。全程不抛错：LLM / 重排 / 图谱分支
            的失败都降级为可用的默认行为。

        Note:
            阶段化之后本方法只剩编排：开作用域 -> 按注册表驱动阶段 -> 出界收口。
            每个阶段的 span / skip / fail / degrade 仪式由
            :class:`retrieval.stages.StageRunner` 统一负责。
        """
        ctx = StageContext.open(self, acl, tenant_id, dataset_id)
        state = RetrievalState.start(query)
        state = StageRunner(retrieval_stage_order()).run(state, ctx)

        # ------------------------------------------------------------------ #
        # 全部阶段（门控 / 改写 / 路由 / 预取发起 / HyDE / 嵌入 / 过滤表达式 /
        # 混合检索 / 伺服栅栏 / 子查询 / stepback / 多样性 / 图谱分支 / 重排 /
        # 父块回取 / 作用域收口 / 裁剪）都在注册表里，由上面的 StageRunner 驱动；
        # 顺序与仪式都由 retrieval/stages.py 申报，这里不再内联任何一段。
        # ------------------------------------------------------------------ #
        if ctx.serving_context is not None:
            # The next orchestration step is generation.  No evidence may cross
            # that boundary if durable delete advanced the serving generation.
            ctx.serving_context.guard.assert_current(ctx.serving_context.snapshot)

        # 降级率埋点：一次检索算一次"调用"，路由降级或"该重排却没重排成"
        # 都算降级。记的是**比率**而不是次数——只知道降级了几次，分不清是
        # 1/10 还是 1/1000，前者是故障、后者是抖动。
        try:
            from core.metrics import get_metrics

            metrics = get_metrics()
            metrics.record_outcome(
                "retrieval", degraded=bool(state.route.degraded)
            )
            if ctx.p.rerank_on:
                metrics.record_outcome("reranker", degraded=not state.reranked)
        except Exception:  # noqa: BLE001 - 埋点不得影响检索结果
            pass

        return RetrievalResult(
            query=state.query,
            chunks=state.chunks,
            route=state.route,
            traces=state.traces,
            reranked=state.reranked,
        )

    # ------------------------------------------------------------------ #
    # 图谱分支
    # ------------------------------------------------------------------ #
    def _run_graph_branch(
        self,
        route: RouteDecision,
        search_query: str,
        candidates: list[RetrievedChunk],
        acl: Optional[list[str]],
        tenant: str,
        traces: list[str],
        serving_context: DocumentServingContext | None = None,
    ) -> tuple[RouteDecision, dict[str, Any]]:
        """执行图谱检索分支，把命中的 passage 合并进候选列表。

        - ``graph_retrieval_on`` 关闭或未注入图检索器 -> 降级 hybrid；
        - 图检索失败 / 无命中 -> 降级 hybrid；
        - 命中但被 ACL / 租户过滤或与混合结果完全去重 -> 降级 hybrid；
        - 成功合并时保持原路由（``vector_graph_rag`` 图结果优先，
          ``full`` 混合结果优先）。

        Note:
            多租户隔离：图检索按 ``tenant`` 过滤（图集合已带 tenant_id）；
            且 ``get_chunks_by_ids`` 回取的 chunk 在 pipeline 层**二次校验**
            tenant_id（防御：即使 passage_ids 来自图检索，也防越权）。

        Returns:
            最终路由决策（失败时 ``degraded=True``）。
        """
        p = self.pipeline
        if not p.graph_retrieval_on or self.graph_retriever is None:
            traces.append("graph 开关关闭或未注入图检索器，降级 hybrid")
            reason = "disabled" if not p.graph_retrieval_on else "unavailable"
            return self._degrade_route(route), {"status": "skipped", "reason": reason}

        try:
            graph_result = self.graph_retriever.retrieve(search_query, tenant_id=tenant)
        except GraphRetrieverError as exc:
            traces.append(f"graph 检索失败，降级 hybrid: {exc}")
            return self._degrade_route(route), {
                "status": "failed",
                "reason": "retrieval_failed",
                "error_type": type(exc).__name__,
            }

        if graph_result.degraded or not graph_result.passage_ids:
            traces.append("graph 无命中，降级 hybrid")
            reason = "upstream_degraded" if graph_result.degraded else "no_match"
            error_type = "GraphDegraded" if graph_result.degraded else "GraphNoMatch"
            return self._degrade_route(route), {
                "status": "failed",
                "reason": reason,
                "error_type": error_type,
            }

        graph_chunks = self.milvus.get_chunks_by_ids(graph_result.passage_ids)
        # 防御性租户二次过滤：图回取 chunk 必须归属当前租户
        # （空 tenant_id 视为存量 / 桩数据，允许通过；跨租户显式标记一律剔除）
        graph_chunks = [
            c for c in graph_chunks if not tenant or not c.tenant_id or c.tenant_id == tenant
        ]
        if serving_context is not None:
            graph_chunks = serving_context.guard.filter_candidates(
                serving_context.snapshot, graph_chunks
            )
        if acl and p.acl_filter_on:
            allowed = {str(a) for a in acl}
            graph_chunks = [c for c in graph_chunks if str(c.metadata.get("acl") or "") in allowed]

        added = self._merge_graph_chunks(
            candidates,
            graph_chunks,
            graph_first=(route.target == "vector_graph_rag"),
        )
        if added == 0:
            traces.append("graph 命中但 ACL 过滤 / 与混合结果完全重复，降级 hybrid")
            return self._degrade_route(route), {
                "status": "failed",
                "reason": "filtered_no_results",
                "error_type": "GraphResultsFiltered",
            }

        traces.append(f"graph 分支合并 {added} 条 passage")
        return route, {"status": "completed", "added_count": added}

    @staticmethod
    def _degrade_route(route: RouteDecision) -> RouteDecision:
        """构造降级路由（保留目标与置信度，标记 degraded）。"""
        return RouteDecision(
            target=route.target,
            confidence=route.confidence,
            degraded=True,
        )

    @staticmethod
    def _merge_extra(
        candidates: list[RetrievedChunk],
        extra: Sequence[RetrievedChunk],
    ) -> int:
        """把增强检索（子查询 / stepback）结果按 chunk_id 去重合并进候选。

        Args:
            candidates: 混合检索候选（原地追加）。
            extra: 增强检索返回的结果。

        Returns:
            实际新增的条数。
        """
        seen = {item.chunk.chunk_id for item in candidates}
        added = 0
        for item in extra:
            if item.chunk.chunk_id in seen:
                continue
            seen.add(item.chunk.chunk_id)
            candidates.append(item)
            added += 1
        return added

    @staticmethod
    def _merge_graph_chunks(
        candidates: list[RetrievedChunk],
        graph_chunks: Sequence[Chunk],
        graph_first: bool,
    ) -> int:
        """把图检索 chunk 合并进候选列表（按 chunk_id 去重）。

        Args:
            candidates: 混合检索候选（原地修改）。
            graph_chunks: 图检索取回的 chunk。
            graph_first: 为 True 时图结果置于混合结果之前
                （``vector_graph_rag`` 主图）；False 时追加在后
                （``full`` 以混合为主）。

        Returns:
            实际新增的条数。
        """
        seen = {item.chunk.chunk_id for item in candidates}
        graph_items: list[RetrievedChunk] = []
        for chunk in graph_chunks:
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            graph_items.append(
                RetrievedChunk(
                    chunk=chunk,
                    score=0.0,
                    rank=len(candidates) + len(graph_items),
                    branch="graph",
                )
            )
        if not graph_items:
            return 0
        if graph_first:
            candidates[:] = graph_items + candidates
        else:
            candidates.extend(graph_items)
        return len(graph_items)


__all__ = ["RetrievalPipeline", "RetrievalResult", "dense_cosines_of"]
