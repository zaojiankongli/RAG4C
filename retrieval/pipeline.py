"""检索管线：门控 -> 改写 -> 路由 -> 嵌入 -> 混合检索 -> [查询端增强] ->
来源多样性 -> [图谱分支] -> 重排序 -> [检索端增强] -> 裁剪。

编排顺序（任务书 5.x）：
1. **复杂度门控**：查询已具体明确时跳过改写与路由（零 LLM 成本），
   直接走 hybrid 默认路由（置信度 1.0）。
2. **查询改写**：复杂查询先 LLM 改写（失败静默降级为原文）。
3. **意图路由**：改写结果参与路由（嵌入相似度为主，LLM 兜底），
   低置信度自动降级，全程不抛错。
4. **嵌入**：BGE-M3 稠密嵌入；HyDE 可选增强在嵌入前生效（见 4a）。
5. **混合检索**：BGE-M3 稠密 + Milvus 内置 BM25，按 ``top_k * 2`` 请求候选
   给重排留余量；支持 ACL 过滤表达式与按 doc 分组。

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
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from config.settings import get_settings, resolve_tenant
from core.document_serving import DocumentServingContext, KnowledgeChanged, current_document_serving
from core.reranker import RerankError
from core.tracing import current_run_observer, current_trace
from models.schemas import Chunk, RetrievedChunk, RouteDecision

from .diversity import mmr_select
from .graph_retriever import GraphRetrieverError

# 需要图谱分支的路由目标
_GRAPH_TARGETS: tuple[str, ...] = ("vector_graph_rag", "full")


def _notify_observer(method: str, *args: Any, **kwargs: Any) -> Any:
    """Best-effort run-event emission that can never affect retrieval."""
    observer = current_run_observer()
    if observer is None:
        return None
    try:
        return getattr(observer, method)(*args, **kwargs)
    except Exception:
        # The observer is strictly out-of-band. This also protects callers that
        # bind a custom observer facade rather than core.run_events.RunObserver.
        return None


def _start_node(node_id: str, **attributes: Any) -> None:
    _notify_observer("start_node", node_id, attributes=attributes or None)


def _complete_node(node_id: str, duration_ms: float | None = None, **attributes: Any) -> None:
    _notify_observer(
        "complete_node",
        node_id,
        duration_ms=duration_ms,
        attributes=attributes or None,
    )


def _fail_node(
    node_id: str,
    *,
    error_type: str,
    reason: str,
    recoverable: bool,
    duration_ms: float | None = None,
    **attributes: Any,
) -> None:
    _notify_observer(
        "fail_node",
        node_id,
        duration_ms=duration_ms,
        error_type=error_type,
        error_code=reason,
        recoverable=recoverable,
        attributes={"reason": reason, **attributes},
    )


def _skip_node(node_id: str, reason: str, **attributes: Any) -> None:
    _notify_observer("skip_node", node_id, attributes={"reason": reason, **attributes})


def _degraded(node_id: str, reason: str, **attributes: Any) -> None:
    _notify_observer("degraded", node_id, attributes={"reason": reason, **attributes})


def _selected_route(
    node_id: str,
    route: RouteDecision,
    *,
    effective: str | None = None,
) -> None:
    _notify_observer(
        "route_selected",
        node_id,
        route=effective or route.target,
        confidence=float(route.confidence),
        degraded=bool(route.degraded),
    )


def _report_rerank_degraded(kind: str, detail: str) -> None:
    """重排降级时对外发一次信号（指标 + 日志）。

    在此之前，重排失败**只**往 ``traces`` 里追加一行。traces 是单次问答的调试
    轨迹，不落盘、不聚合、不告警——也就是说重排彻底挂掉时，服务照样 200，
    日志一片干净，唯一的症状是"检索质量莫名其妙变差了"。这是最难查的那类故障：
    没有任何一个信号告诉你系统正在降级运行。

    降级本身是对的（重排挂了也该出答案，只是顺序退回融合序），错的是**降级
    得无声无息**。所以这里只加可观测性，不改降级行为：
    - ``retrieval.rerank.degraded`` 计数器，可在 /api/metrics 看到、可告警；
    - warning 级日志，带上具体原因，便于定位是鉴权、超时还是协议不符。

    埋点异常一律吞掉：可观测性代码把主链路搞挂就本末倒置了。
    """
    try:
        from core.metrics import get_metrics
        from core.observability import get_logger

        get_metrics().incr("retrieval.rerank.degraded")
        get_logger(__name__).warning("重排降级（%s），改用召回顺序：%s", kind, detail)
    except Exception:
        pass


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
        reranker_cb: Any = None,
        serving_guard: Any = None,
    ) -> None:
        self.embedder = embedder
        self.milvus = milvus
        self.reranker = reranker
        # 重排熔断器（可选）。为 None 时行为与接入前完全一致。
        self.reranker_cb = reranker_cb
        self.rewriter = rewriter
        self.router = router
        self.graph_retriever = graph_retriever
        self.hyde = hyde
        self.subqueries = subqueries
        self.stepback = stepback
        self.sentence_window = sentence_window
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
    # 追踪
    # ------------------------------------------------------------------ #
    @staticmethod
    def _add_span(name: str, duration_ms: float, traces: list[str]) -> None:
        """记录 span 到当前 trace（若存在）与结果 traces。"""
        trace = current_trace()
        if trace is not None:
            trace.add_span(name, duration_ms)
        traces.append(f"{name}:{duration_ms:.2f}ms")

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
    _ENHANCER_NODE_IDS = {
        "hyde": "plugin.hyde.expand",
        "subqueries": "plugin.subqueries.expand",
        "stepback": "plugin.stepback.expand",
    }

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

    @staticmethod
    def _timed_generate(component: Any, query: str) -> tuple[Any, float, Optional[Exception]]:
        """跑一次 ``component.generate(query)``，返回 ``(产出, 耗时ms, 异常)``。

        异常在这里就地接住而不是让它穿过 ``Future``：三个增强器**互不影响**
        是原本就有的语义（每个都各自 try / 各自降级），放进线程池以后要保持
        这一点，就必须让每个任务自己收尾。耗时也在这里量——量的是这次生成
        真正花了多久，而不是主线程等了多久，否则先取回的那个会把后面几个的
        等待时间算到自己头上。
        """
        t0 = time.perf_counter()
        try:
            return component.generate(query), (time.perf_counter() - t0) * 1000.0, None
        except Exception as exc:  # noqa: BLE001 - 由调用方按各自语义降级
            return None, (time.perf_counter() - t0) * 1000.0, exc

    def _start_enhancers(self, query: str, traces: list[str]) -> dict[str, Future]:
        """把已启用的查询端增强的 LLM 生成**同时**发出去。

        三者都只吃 ``search_query``，彼此没有任何数据依赖，从前却是一次
        HyDE、等它回来做完检索、再一次子查询、再一次后退提问——默认全开时
        这是三次完整的 LLM 往返，一条接一条地摞在用户等待里。它们唯一的时
        序约束是 HyDE 必须早于 ``embed_query``，而那个约束在下面取回结果的
        地方自然满足（用到谁才等谁）。

        只有一个（或没有）增强器启用时不动线程池：那时并发无从谈起，多绕
        一层反而把栈搞复杂。返回空字典即表示"就地同步生成"，见
        :meth:`_take_enhancer`。
        """
        enabled = [
            (name, component)
            for name, on, component in (
                ("hyde", self.pipeline.hyde_on, self.hyde),
                ("subqueries", self.pipeline.subqueries_on, self.subqueries),
                ("stepback", self.pipeline.stepback_on, self.stepback),
            )
            if on and component is not None
        ]
        for name, _component in enabled:
            _start_node(self._ENHANCER_NODE_IDS[name])
        if len(enabled) < 2:
            return {}
        try:
            executor = self._ensure_enhance_executor()
            jobs = {
                name: executor.submit(self._timed_generate, component, query)
                for name, component in enabled
            }
        except Exception as exc:  # noqa: BLE001 - 线程池不可用就退回串行
            traces.append(f"查询端增强并发生成不可用，退回串行: {exc}")
            return {}
        traces.append(
            "查询端增强并发生成："
            + " / ".join(name for name, _ in enabled)
            + " 的 LLM 调用已同时发出（各自的 span 因此相互重叠）"
        )
        return jobs

    def _take_enhancer(
        self, jobs: dict[str, Future], name: str, component: Any, query: str
    ) -> tuple[Any, float, Optional[Exception]]:
        """取回预先发出的生成结果；没预发过就地同步生成（语义完全一致）。"""
        job = jobs.pop(name, None)
        if job is None:
            return self._timed_generate(component, query)
        try:
            return job.result()
        except Exception as exc:  # noqa: BLE001 - 线程池自身出问题（已关闭等）
            return None, 0.0, exc

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
        """
        p = self.pipeline
        traces: list[str] = []
        # 租户强制解析（enforced=True 时空 / None 回退 default_tenant）
        tenant = resolve_tenant(tenant_id, self.settings)
        # 知识库维度不做强制回退：空即全域检索
        dataset = (dataset_id or "").strip()
        serving_context = self._serving_context(tenant, dataset)

        # ------------------------------------------------------------------ #
        # 1. 复杂度门控 + 2. 查询改写（简单查询一次调用即完成两者）
        # ------------------------------------------------------------------ #
        if p.complexity_gate_on:
            _start_node("complexity_gate")
        else:
            _skip_node("complexity_gate", "disabled")
        t0 = time.perf_counter()
        rewritten, changed = query, False
        gate_skipped = False
        try:
            if p.complexity_gate_on:
                rewritten, changed = self.rewriter.rewrite(query)
                gate_skipped = not changed  # 具体查询：跳过改写与路由
        except Exception as exc:
            gate_ms = (time.perf_counter() - t0) * 1000.0
            _fail_node(
                "complexity_gate",
                error_type=type(exc).__name__,
                reason="evaluation_failed",
                recoverable=False,
                duration_ms=gate_ms,
            )
            raise
        gate_ms = (time.perf_counter() - t0) * 1000.0
        self._add_span("gate", gate_ms, traces)
        if p.complexity_gate_on:
            _complete_node(
                "complexity_gate",
                gate_ms,
                rewrite_required=bool(changed),
            )

        route = RouteDecision(target="hybrid", confidence=1.0, degraded=False)
        search_query = query
        if gate_skipped:
            _skip_node("rewrite", "gate_simple")
            self._add_span("rewrite", 0.0, traces)
            _skip_node("route", "gate_simple")
            self._add_span("route", 0.0, traces)
        else:
            if not p.complexity_gate_on:
                # 门控关闭：此处显式执行改写
                _start_node("rewrite")
                t0 = time.perf_counter()
                try:
                    rewritten, changed = self.rewriter.rewrite(query)
                except Exception as exc:
                    rewrite_ms = (time.perf_counter() - t0) * 1000.0
                    _fail_node(
                        "rewrite",
                        error_type=type(exc).__name__,
                        reason="rewrite_failed",
                        recoverable=False,
                        duration_ms=rewrite_ms,
                    )
                    raise
                rewrite_ms = (time.perf_counter() - t0) * 1000.0
                self._add_span("rewrite", rewrite_ms, traces)
                _complete_node("rewrite", rewrite_ms, changed=bool(changed))
            else:
                # 门控开启且查询复杂：改写已在门控阶段完成
                _start_node("rewrite")
                self._add_span("rewrite", 0.0, traces)
                _complete_node("rewrite", 0.0, changed=bool(changed))
            search_query = (rewritten or "").strip() or query
            _start_node("route")
            t0 = time.perf_counter()
            try:
                route = self.router.route(search_query)
            except Exception as exc:
                route_ms = (time.perf_counter() - t0) * 1000.0
                _fail_node(
                    "route",
                    error_type=type(exc).__name__,
                    reason="route_failed",
                    recoverable=False,
                    duration_ms=route_ms,
                )
                raise
            route_ms = (time.perf_counter() - t0) * 1000.0
            self._add_span("route", route_ms, traces)
            _complete_node("route", route_ms)
        _selected_route("route", route)

        # ------------------------------------------------------------------ #
        # 3. 嵌入（HyDE 可选增强在嵌入前：假设文档向量替换查询向量）
        # ------------------------------------------------------------------ #
        # 3a. HyDE：开关开启且注入 hyde 组件时，先用 LLM 生成"假设文档"，
        #     稠密检索用假设文档的向量，BM25 文本检索仍用 search_query
        #     （混合检索语义不变）。生成失败 / 无产出时静默降级为原查询
        #     嵌入（span 记 0ms），绝不中断管线。
        #
        # 三个查询端增强的生成在这里一起发出（见 _start_enhancers）：它们都
        # 只依赖 search_query，各自的结果在下面各自的阶段里取回。
        enhance_jobs = self._start_enhancers(search_query, traces)

        hyde_doc: Optional[str] = None
        if not p.hyde_on:
            _skip_node("plugin.hyde.expand", "disabled")
        elif self.hyde is None:
            _skip_node("plugin.hyde.expand", "unavailable")
        else:
            hyde_doc, hyde_ms, hyde_exc = self._take_enhancer(
                enhance_jobs, "hyde", self.hyde, search_query
            )
            hyde_work_ms = hyde_ms
            if hyde_exc is not None:
                traces.append(f"hyde 生成失败，降级为普通嵌入: {hyde_exc}")
                hyde_doc = None
            if not (isinstance(hyde_doc, str) and hyde_doc.strip()):
                hyde_ms = 0.0  # 未产出假设文档：不改变嵌入
                hyde_doc = None
                traces.append("hyde 未产出假设文档，使用原查询嵌入")
            self._add_span("hyde", hyde_ms, traces)
            if hyde_exc is not None:
                _fail_node(
                    "plugin.hyde.expand",
                    error_type=type(hyde_exc).__name__,
                    reason="generation_failed",
                    recoverable=True,
                    duration_ms=hyde_work_ms,
                )
                _degraded("plugin.hyde.expand", "generation_failed")
            elif hyde_doc is None:
                _fail_node(
                    "plugin.hyde.expand",
                    error_type="EmptyResult",
                    reason="no_output",
                    recoverable=True,
                    duration_ms=hyde_ms,
                )
                _degraded("plugin.hyde.expand", "no_output")
            else:
                _complete_node("plugin.hyde.expand", hyde_ms, output_present=True)

        _start_node("embed", source="hyde" if hyde_doc else "query")
        t0 = time.perf_counter()
        try:
            query_vec = self.embedder.embed_query(hyde_doc if hyde_doc else search_query)
        except Exception as exc:
            embed_ms = (time.perf_counter() - t0) * 1000.0
            _fail_node(
                "embed",
                error_type=type(exc).__name__,
                reason="embed_failed",
                recoverable=False,
                duration_ms=embed_ms,
            )
            raise
        embed_ms = (time.perf_counter() - t0) * 1000.0
        self._add_span("embed", embed_ms, traces)
        _complete_node("embed", embed_ms, source="hyde" if hyde_doc else "query")

        # ------------------------------------------------------------------ #
        # 4. 混合检索（候选放大到 top_k * 2，重排后裁剪）
        # ------------------------------------------------------------------ #
        group_by_field: Optional[str] = None
        group_size: Optional[int] = None
        if p.source_diversity in ("group_only", "group_mmr"):
            group_by_field = p.group_by_field
            group_size = p.group_size

        # 租户 + 知识库 + ACL 过滤表达式（AND 组合）：tenant 无条件启用，
        # dataset 非空时参与，ACL 受 acl_filter_on 控制（与图谱分支语义一致）。
        # 组合逻辑统一收敛到 RagMilvusClient.build_filters，避免此处与客户端
        # 各写一份而漏掉维度（dataset_id 此前就是这样被漏掉的）。
        filter_expr = self.milvus.build_filters(
            tenant_id=tenant,
            acl=acl,
            acl_filter_on=p.acl_filter_on,
            dataset_id=dataset,
        )

        # rerank 一旦不生效，score 就退化成 RRF 融合分，弃权闸那道"知识库有没有
        # 相关内容"的检查会整个失效（见 verify.abstention 的说明）。稠密余弦是
        # 那种情况下唯一还能用的信号，但取回向量要额外传 dim×4 字节/条，所以
        # **只在确实用得上时才要**。
        #
        # 这里只能预判 rerank 的三条"事前就知道"的失效路径（关闭 / 熔断 /
        # 没有重排器）；第四条——调用到一半抛错——事前无从得知，那种情况仍旧
        # 退回"如实承认不可评估"。为这一条罕见路径给每次查询都加上传输代价，
        # 不划算。
        #
        # HyDE 打开时 query_vec 是**假设文档**的向量，不是查询本身的；余弦因此
        # 落在另一个分布上，而阈值是按真实查询量出来的。这种情况下不索取，
        # 免得拿一把没校准过的尺子去判弃权。
        want_cosine = hyde_doc is None and (
            not p.rerank_on
            or self.reranker is None
            or (self.reranker_cb is not None and not self.reranker_cb.allow())
        )

        search_mode = "hybrid" if p.hybrid_search_on else "dense"
        _start_node("search", mode=search_mode, requested_count=p.top_k * 2)
        t0 = time.perf_counter()
        try:
            candidates = self.milvus.hybrid_search(
                query_dense=query_vec,
                top_k=p.top_k * 2,
                query_text=self._bm25_text(search_query),
                group_by_field=group_by_field,
                group_size=group_size,
                filter_expr=filter_expr,
                with_cosine=want_cosine,
            )
        except Exception as exc:
            search_ms = (time.perf_counter() - t0) * 1000.0
            _fail_node(
                "search",
                error_type=type(exc).__name__,
                reason="search_failed",
                recoverable=False,
                duration_ms=search_ms,
                mode=search_mode,
            )
            raise
        if not p.hybrid_search_on:
            traces.append("hybrid_search 关闭：仅执行稠密语义检索（无 BM25 分支）")
        search_ms = (time.perf_counter() - t0) * 1000.0
        self._add_span("search", search_ms, traces)
        _complete_node(
            "search",
            search_ms,
            mode=search_mode,
            candidate_count=len(candidates),
        )
        candidates = self._filter_serving_candidates(
            candidates, serving_context, traces, stage="milvus.initial"
        )

        # ------------------------------------------------------------------ #
        # 4b. 子查询拆解（SubQueries，可选增强）：拆解多主题查询分别检索后合并
        # ------------------------------------------------------------------ #
        # 编排理由：查询端增强的目的是**扩大候选**（提升召回），因此放在
        # 混合检索之后、来源多样性之前——增强合并出的候选先经过多样性
        # / MMR 的文档级去重与过滤，再进入图谱分支与重排，避免增强结果
        # 被多样性之前的裁剪丢弃；图谱分支保持在其后（不改动既有编排）。
        # 多次检索均带与主检索一致的 group_by_field / group_size /
        # filter_expr，保证分组与 ACL 语义一致。
        if not p.subqueries_on:
            _skip_node("plugin.subqueries.expand", "disabled")
        elif self.subqueries is None:
            _skip_node("plugin.subqueries.expand", "unavailable")
        if p.subqueries_on and self.subqueries is not None:
            subs: list[str] = []
            raw, sub_gen_ms, sub_exc = self._take_enhancer(
                enhance_jobs, "subqueries", self.subqueries, search_query
            )
            sub_failure_reason: Optional[str] = None
            sub_error_type = "SubqueryFailure"
            sub_failure_count = 0
            sub_success_count = 0
            sub_added_count = 0
            # 计时器起点在取回生成结果**之后**：这一段要报的是"子查询这一步
            # 花了多少"，而不是"主线程在这里坐了多久"。并发以后主线程可能在
            # 这里等（HyDE 更慢时）也可能一秒不等（生成早已完成），把等待算
            # 进来会让同一份工作量在两次查询里报出完全不同的数。生成耗时由
            # 线程内自测后加回来，串行退化时两者之和与从前逐字相等。
            t0 = time.perf_counter()
            if sub_exc is not None:
                traces.append(f"subqueries 生成失败，使用主查询: {sub_exc}")
                sub_failure_reason = "generation_failed"
                sub_error_type = type(sub_exc).__name__
            elif isinstance(raw, list):
                subs = [s for s in raw if isinstance(s, str) and s.strip() and s != search_query]
            else:
                sub_failure_reason = "invalid_output"
                sub_error_type = "InvalidSubqueryOutput"
            if subs:
                # 性能优化：N 路子查询原先是 N 次「串行 embed_query + 串行
                # hybrid_search」，每路都是一次独立网络往返，总延迟随子
                # 查询数线性叠加。改为：
                #   1) 一次 embed_texts 批量嵌入全部子查询（单次请求），
                #   2) 用实例级复用的线程池并发发起 N 次 hybrid_search。
                # 单路失败仍逐路捕获、跳过，不影响其余路（语义与原实现
                # 一致）；批量嵌入整体失败按「全部子查询检索失败」降级。
                searched_any = False
                try:
                    sub_vecs = self.embedder.embed_texts(subs)
                except Exception as exc:
                    traces.append(f"subqueries 批量嵌入失败，跳过全部子查询: {exc}")
                    sub_vecs = []
                    sub_failure_reason = "batch_embed_failed"
                    sub_error_type = type(exc).__name__
                    sub_failure_count = len(subs)
                if sub_vecs and len(sub_vecs) == len(subs):
                    executor = self._ensure_fanout_executor()
                    futures = [
                        (
                            sub,
                            executor.submit(
                                self.milvus.hybrid_search,
                                query_dense=sub_vec,
                                top_k=p.enhance_candidate_k,
                                query_text=self._bm25_text(sub),
                                group_by_field=group_by_field,
                                group_size=group_size,
                                filter_expr=filter_expr,
                            ),
                        )
                        for sub, sub_vec in zip(subs, sub_vecs)
                    ]
                    # 整批共用一个截止时刻，而不是每路各给一份超时：后者会
                    # 累加成 N × timeout，N 路子查询全卡住时用户要等的是总和。
                    # 超时本身是保险丝而非调优项——不带超时的 result() 会让
                    # 一路卡死的 hybrid_search 把整条 HTTP 请求永久挂住，那个
                    # 请求还占着一个准入名额，几路下来服务对外就是整体不可用。
                    budget = float(getattr(p, "subquery_fanout_timeout_s", 0.0) or 0.0)
                    deadline = (time.perf_counter() + budget) if budget > 0 else None
                    for sub, future in futures:
                        try:
                            if deadline is None:
                                extra = future.result()
                            else:
                                # 剩余预算可能已经是负数，留一点点让最后一路
                                # 也有机会立刻交出已完成的结果。
                                extra = future.result(
                                    timeout=max(0.001, deadline - time.perf_counter())
                                )
                        except FuturesTimeoutError:
                            sub_failure_reason = "search_timeout"
                            sub_error_type = "TimeoutError"
                            sub_failure_count += 1
                            # 超时不取消已发出的检索（线程还占着），但本次
                            # 查询照常带着主检索结果返回，不陪着一起卡死。
                            traces.append(
                                f"subqueries 子查询检索超时（{budget:.1f}s 预算耗尽），"
                                f"放弃剩余路（{sub!r}）"
                            )
                            break
                        except Exception as exc:
                            sub_failure_reason = "search_failed"
                            sub_error_type = type(exc).__name__
                            sub_failure_count += 1
                            # 单路子查询检索失败：跳过该路，不影响其余路
                            traces.append(f"subqueries 子查询检索失败，跳过（{sub!r}）: {exc}")
                            continue
                        sub_added_count += self._merge_extra(candidates, extra)
                        sub_success_count += 1
                        searched_any = True
                elif sub_vecs:
                    traces.append("subqueries 批量嵌入返回数量与子查询数不符，跳过全部子查询")
                    sub_failure_reason = "invalid_embedding_count"
                    sub_error_type = "InvalidEmbeddingCount"
                    sub_failure_count = len(subs)
                else:
                    sub_failure_reason = "empty_embeddings"
                    sub_error_type = "EmptyEmbeddingBatch"
                    sub_failure_count = len(subs)
                if not searched_any:
                    # 全部子查询都失败：回退单次主检索结果（候选保持不变）
                    traces.append("subqueries 全部子查询检索失败，回退主查询结果")
            sub_ms = sub_gen_ms + (time.perf_counter() - t0) * 1000.0
            self._add_span("subqueries", sub_ms, traces)
            if sub_failure_count and sub_success_count:
                sub_failure_reason = "partial_search_failure"
            if sub_failure_reason is not None:
                _fail_node(
                    "plugin.subqueries.expand",
                    error_type=sub_error_type,
                    reason=sub_failure_reason,
                    recoverable=True,
                    duration_ms=sub_ms,
                    subquery_count=len(subs),
                    successful_count=sub_success_count,
                    failure_count=sub_failure_count,
                    added_count=sub_added_count,
                )
                _degraded(
                    "plugin.subqueries.expand",
                    sub_failure_reason,
                    successful_count=sub_success_count,
                    failure_count=sub_failure_count,
                )
            else:
                _complete_node(
                    "plugin.subqueries.expand",
                    sub_ms,
                    subquery_count=len(subs),
                    successful_count=sub_success_count,
                    added_count=sub_added_count,
                )

        # ------------------------------------------------------------------ #
        # 4c. Stepback 后退式提问（可选增强）：抽象问题补充检索后合并
        # ------------------------------------------------------------------ #
        if not p.stepback_on:
            _skip_node("plugin.stepback.expand", "disabled")
        elif self.stepback is None:
            _skip_node("plugin.stepback.expand", "unavailable")
        if p.stepback_on and self.stepback is not None:
            sb_query, sb_gen_ms, sb_exc = self._take_enhancer(
                enhance_jobs, "stepback", self.stepback, search_query
            )
            sb_failure_reason: Optional[str] = None
            sb_error_type = "StepbackFailure"
            sb_added_count = 0
            t0 = time.perf_counter()  # 起点在取回之后，理由同 4b
            if sb_exc is not None:
                traces.append(f"stepback 生成失败，跳过: {sb_exc}")
                sb_failure_reason = "generation_failed"
                sb_error_type = type(sb_exc).__name__
                sb_query = None
            if isinstance(sb_query, str) and sb_query.strip() and sb_query != search_query:
                try:
                    sb_vec = self.embedder.embed_query(sb_query)
                    extra = self.milvus.hybrid_search(
                        query_dense=sb_vec,
                        top_k=p.enhance_candidate_k,
                        query_text=self._bm25_text(sb_query),
                        group_by_field=group_by_field,
                        group_size=group_size,
                        filter_expr=filter_expr,
                    )
                    sb_added_count = self._merge_extra(candidates, extra)
                except Exception as exc:
                    traces.append(f"stepback 检索失败，跳过: {exc}")
                    sb_failure_reason = "search_failed"
                    sb_error_type = type(exc).__name__
            elif sb_exc is None:
                sb_failure_reason = "no_output"
                sb_error_type = "EmptyResult"
            sb_ms = sb_gen_ms + (time.perf_counter() - t0) * 1000.0
            self._add_span("stepback", sb_ms, traces)
            if sb_failure_reason is not None:
                _fail_node(
                    "plugin.stepback.expand",
                    error_type=sb_error_type,
                    reason=sb_failure_reason,
                    recoverable=True,
                    duration_ms=sb_ms,
                    added_count=sb_added_count,
                )
                _degraded("plugin.stepback.expand", sb_failure_reason)
            else:
                _complete_node(
                    "plugin.stepback.expand",
                    sb_ms,
                    added_count=sb_added_count,
                )

        candidates = self._filter_serving_candidates(
            candidates, serving_context, traces, stage="milvus.enhanced"
        )

        # ------------------------------------------------------------------ #
        # 5. 来源多样性（group_mmr：分组检索后再做文档感知 MMR）
        # ------------------------------------------------------------------ #
        diversity_before = len(candidates)
        if p.source_diversity == "off":
            _skip_node("diversity", "disabled", mode="off")
        else:
            _start_node("diversity", mode=p.source_diversity)
        t0 = time.perf_counter()
        if p.source_diversity == "group_mmr":
            candidates = mmr_select(
                candidates,
                lambda_=p.mmr_lambda,
                k=p.top_k * 2,
            )
        diversity_ms = (time.perf_counter() - t0) * 1000.0
        self._add_span("diversity", diversity_ms, traces)
        if p.source_diversity != "off":
            _complete_node(
                "diversity",
                diversity_ms,
                mode=p.source_diversity,
                input_count=diversity_before,
                output_count=len(candidates),
            )

        # ------------------------------------------------------------------ #
        # 6. 图谱分支（vector_graph_rag / full 路由）
        # ------------------------------------------------------------------ #
        graph_selected = route.target in _GRAPH_TARGETS
        graph_available = p.graph_retrieval_on and self.graph_retriever is not None
        if graph_selected and graph_available:
            _start_node("graph.retrieve", configured_route=route.target)
        t0 = time.perf_counter()
        if graph_selected:
            try:
                route, graph_fact = self._run_graph_branch(
                    route=route,
                    search_query=search_query,
                    candidates=candidates,
                    acl=acl,
                    tenant=tenant,
                    traces=traces,
                    serving_context=serving_context,
                )
            except Exception as exc:
                graph_ms = (time.perf_counter() - t0) * 1000.0
                _fail_node(
                    "graph.retrieve",
                    error_type=type(exc).__name__,
                    reason="graph_failed",
                    recoverable=False,
                    duration_ms=graph_ms,
                )
                raise
        else:
            graph_fact = {"status": "skipped", "reason": "route_not_selected"}
        graph_ms = (time.perf_counter() - t0) * 1000.0
        self._add_span("graph", graph_ms, traces)
        graph_status = graph_fact["status"]
        graph_reason = str(graph_fact.get("reason") or "")
        if graph_status == "skipped":
            _skip_node("graph.retrieve", graph_reason)
        elif graph_status == "failed":
            _fail_node(
                "graph.retrieve",
                error_type=str(graph_fact["error_type"]),
                reason=graph_reason,
                recoverable=True,
                duration_ms=graph_ms,
                added_count=int(graph_fact.get("added_count") or 0),
            )
        else:
            _complete_node(
                "graph.retrieve",
                graph_ms,
                added_count=int(graph_fact.get("added_count") or 0),
            )
        if graph_status == "failed" or (graph_selected and route.degraded):
            _degraded("graph.retrieve", graph_reason, effective_route="hybrid")
        if graph_selected:
            _selected_route(
                "graph.retrieve",
                route,
                effective="hybrid" if route.degraded else route.target,
            )

        # ------------------------------------------------------------------ #
        # 7. 重排序（关闭或失败时保持召回顺序）
        # ------------------------------------------------------------------ #
        if not p.rerank_on:
            rerank_skip_reason: Optional[str] = "disabled"
        elif self.reranker is None:
            rerank_skip_reason = "unavailable"
        elif self.reranker_cb is not None and not self.reranker_cb.allow():
            rerank_skip_reason = "circuit_open"
        else:
            rerank_skip_reason = None
        if rerank_skip_reason is None:
            _start_node("rerank", candidate_count=len(candidates))
        else:
            _skip_node("rerank", rerank_skip_reason)

        t0 = time.perf_counter()
        # 只有走到「分数数量对得上」那一支才算重排真正生效——其余每一条路
        # 留在 item.score 里的都是 RRF 融合分，量纲和 reranker 相关度完全不同。
        reranked = False
        rerank_failure_reason: Optional[str] = None
        rerank_error_type = "RerankFailure"
        if rerank_skip_reason == "disabled":
            traces.append("rerank 关闭：保留召回顺序（RRF 融合序）")
        elif rerank_skip_reason == "unavailable":
            _report_rerank_degraded("未注入 reranker", "rerank_on=True 但 reranker 为 None")
            traces.append("rerank 未注入：保留召回顺序（RRF 融合序）")
        elif rerank_skip_reason == "circuit_open":
            _report_rerank_degraded("熔断打开", f"{self.reranker_cb.name} 冷却中")
            traces.append("rerank 熔断打开：快速跳过，使用召回顺序")
        else:
            try:
                scores = self.reranker.rerank(search_query, [c.chunk for c in candidates])
                if len(scores) != len(candidates):
                    if self.reranker_cb is not None:
                        self.reranker_cb.record_failure()
                    _report_rerank_degraded(
                        "分数数量不符",
                        f"期望 {len(candidates)} 个，实际 {len(scores)} 个",
                    )
                    traces.append("rerank 失败：分数数量不符，使用原始顺序")
                    rerank_failure_reason = "invalid_score_count"
                    rerank_error_type = "InvalidRerankScoreCount"
                else:
                    if self.reranker_cb is not None:
                        self.reranker_cb.record_success()
                    paired = sorted(
                        zip(candidates, scores),
                        key=lambda item: item[1],
                        reverse=True,
                    )
                    candidates = [c for c, _ in paired]
                    for rank, (item, score) in enumerate(paired):
                        item.rank = rank
                        item.score = float(score)
                    reranked = True
            except RerankError as exc:
                if self.reranker_cb is not None:
                    self.reranker_cb.record_failure()
                _report_rerank_degraded("调用失败", f"{type(exc).__name__}: {exc}")
                traces.append("rerank 失败，使用原始顺序")
                rerank_failure_reason = "rerank_failed"
                rerank_error_type = type(exc).__name__
            except Exception as exc:
                rerank_ms = (time.perf_counter() - t0) * 1000.0
                _fail_node(
                    "rerank",
                    error_type=type(exc).__name__,
                    reason="rerank_failed",
                    recoverable=False,
                    duration_ms=rerank_ms,
                )
                raise
        if not reranked:
            # 说清楚代价：这不只是"顺序没优化"，而是**检索分数这把尺子作废了**。
            # 有稠密余弦时换那把尺子接着量；没有时才真正让这道闸空着。
            if any(rc.dense_cosine is not None for rc in candidates):
                traces.append("rerank 未生效：分数为 RRF 融合序，改用稠密余弦参与弃权判定")
            else:
                traces.append("rerank 未生效：分数为 RRF 融合序，检索阈值本轮不参与弃权判定")
        rerank_ms = (time.perf_counter() - t0) * 1000.0
        self._add_span("rerank", rerank_ms, traces)
        if rerank_skip_reason is None:
            if rerank_failure_reason is not None:
                _fail_node(
                    "rerank",
                    error_type=rerank_error_type,
                    reason=rerank_failure_reason,
                    recoverable=True,
                    duration_ms=rerank_ms,
                    candidate_count=len(candidates),
                )
                _degraded("rerank", rerank_failure_reason)
            else:
                _complete_node(
                    "rerank",
                    rerank_ms,
                    candidate_count=len(candidates),
                )
        elif rerank_skip_reason != "disabled":
            _degraded("rerank", rerank_skip_reason)

        # ------------------------------------------------------------------ #
        # 7b. SentenceWindow 父块回取（可选增强）：重排后展开，父块参与裁剪
        # ------------------------------------------------------------------ #
        # 编排理由：放在重排序之后、裁剪之前——展开依赖命中项的 rank /
        # score 继承（父块继承子块的最小 rank），重排后展开可让父块以完整
        # 上下文参与最终 top_k 裁剪；展开失败时组件内部已降级（原样返回），
        # 此处再兜底一次异常，绝不中断管线。
        if not p.sentence_window_on:
            _skip_node("sentence_window", "disabled")
        elif self.sentence_window is None:
            _skip_node("sentence_window", "unavailable")
        if p.sentence_window_on and self.sentence_window is not None:
            _start_node("sentence_window", input_count=len(candidates))
            sentence_failure_reason: Optional[str] = None
            sentence_error_type = "SentenceWindowFailure"
            t0 = time.perf_counter()
            try:
                expanded = self.sentence_window.expand(candidates)
                if isinstance(expanded, list):
                    # 防御性租户二次过滤：展开可能回取父块（get_chunks_by_ids），
                    # 父块必须归属当前租户（空 tenant_id 视为存量 / 桩数据放行）
                    candidates = [
                        rc
                        for rc in expanded
                        if not tenant or not rc.chunk.tenant_id or rc.chunk.tenant_id == tenant
                    ]
                else:
                    traces.append("sentence_window 展开返回异常类型，保留原结果")
                    sentence_failure_reason = "invalid_output"
                    sentence_error_type = "InvalidSentenceWindowOutput"
            except Exception as exc:
                traces.append(f"sentence_window 展开失败，保留原结果: {exc}")
                sentence_failure_reason = "expand_failed"
                sentence_error_type = type(exc).__name__
            sentence_ms = (time.perf_counter() - t0) * 1000.0
            self._add_span("sentence_window", sentence_ms, traces)
            if sentence_failure_reason is not None:
                _fail_node(
                    "sentence_window",
                    error_type=sentence_error_type,
                    reason=sentence_failure_reason,
                    recoverable=True,
                    duration_ms=sentence_ms,
                    output_count=len(candidates),
                )
                _degraded("sentence_window", sentence_failure_reason)
            else:
                _complete_node(
                    "sentence_window",
                    sentence_ms,
                    output_count=len(candidates),
                )

        candidates = self._filter_serving_candidates(
            candidates, serving_context, traces, stage="rerank.final"
        )

        # ------------------------------------------------------------------ #
        # 8. 知识库范围收口 + 裁剪到 top_k
        # ------------------------------------------------------------------ #
        # 图谱分支与 sentence_window 父块回取都按 id 直取，绕过了向量检索的
        # filter_expr，可能带回其它知识库的 chunk。此处统一收口一次，语义与
        # build_dataset_filter 严格一致（dataset_id == x），因此空 dataset_id
        # 的存量行在指定知识库时同样被排除——两条路径判定必须一致，否则
        # 主路径过滤掉的行会从旁路漏回来。
        if dataset:
            _start_node("dataset_scope", scoped=True, input_count=len(candidates))
            before = len(candidates)
            candidates = [rc for rc in candidates if rc.chunk.dataset_id == dataset]
            if len(candidates) != before:
                traces.append(
                    f"知识库收口：{before - len(candidates)} 条越界 chunk 被剔除"
                    f"（dataset_id != {dataset!r}）"
                )
            _complete_node(
                "dataset_scope",
                scoped=True,
                input_count=before,
                output_count=len(candidates),
                removed_count=before - len(candidates),
            )
        else:
            _skip_node("dataset_scope", "unscoped", scoped=False)

        _start_node("truncate", input_count=len(candidates), top_k=p.top_k)
        chunks = candidates[: p.top_k]
        _complete_node(
            "truncate",
            input_count=len(candidates),
            output_count=len(chunks),
            top_k=p.top_k,
        )
        if serving_context is not None:
            # The next orchestration step is generation.  No evidence may cross
            # that boundary if durable delete advanced the serving generation.
            serving_context.guard.assert_current(serving_context.snapshot)

        return RetrievalResult(
            query=query,
            chunks=chunks,
            route=route,
            traces=traces,
            reranked=reranked,
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
