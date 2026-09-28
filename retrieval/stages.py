"""检索阶段端口、状态载体与驱动器（切片 S-RS）。

``RetrievalPipeline.run()`` 原本是 787 行、14 个阶段全部内联的方法；每个阶段各自
手写一遍同一套仪式（``start_node`` / ``skip_node`` / ``complete_node`` /
``fail_node`` / ``degraded`` / ``add_span``）以及同一道"开关开着且组件注入了吗"的
闸门。本模块把那两件事分开：

* **阶段**只回答"这一步做什么、这次算成功还是降级"，产出 :class:`StageOutcome`
  这份**声明数据**；
* **驱动器** (:class:`StageRunner`) 独家拥有全部 run-event 仪式与 span 记账。

于是"忘了记一条降级"从"每个阶段都可能漏"变成"结构上不可能"：阶段拿不到发事件的
入口，而驱动器规定 **可恢复失败必定伴随 degraded**（见 :meth:`StageRunner._finish`）。

节点 id / span 名 / trace 文案**逐字沿用** :mod:`retrieval.pipeline` 原有实现，不是
重新设计：``frontend/src/strategy/traceParse.ts`` 用正则解析 ``QueryResult.traces``
并按 ``STAGE_LABELS`` 映射阶段名，``frontend/src/run/serverRunProjection.ts`` 按节点
id 认策略，``rag_topology.py`` 按节点 id 画拓扑。改名等于静默改前端阶段面板。

阶段顺序（``order``）是语义而不是排版：``dataset_scope`` 必须排在图谱分支与
sentence_window 之后、``truncate`` 之前——它是唯一天花板式作用域收口（见
:class:`DatasetScopeStage`）。:func:`retrieval_stage_order` 把这条钉成注册期校验。
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence

from config.settings import resolve_tenant
from core.document_serving import DocumentServingContext
from core.providers import ProviderRegistry
from core.reranker import RerankError
from core.tracing import current_run_observer, current_trace
from models.schemas import RetrievedChunk, RouteDecision

from .diversity import mmr_select

__all__ = [
    "Announce",
    "Degrade",
    "EnhancerPrefetch",
    "Failure",
    "Gate",
    "GRAPH_TARGETS",
    "RetrievalServices",
    "RetrievalStage",
    "RetrievalState",
    "Skip",
    "StageContext",
    "StageOutcome",
    "StageRunner",
    "BUILTIN_RETRIEVAL_STAGES",
    "ENHANCER_PREFETCH",
    "RETRIEVAL_STAGES",
    "OPTIONAL_STRATEGIES",
    "ComponentDeps",
    "OptionalStrategy",
    "BUILTIN_OPTIONAL_STRATEGIES",
    "PrefetchableStage",
    "is_strategy_enabled",
    "register_builtin_optional_strategies",
    "span_names",
    "add_span",
    "announce_route",
    "degraded_node",
    "fail_node",
    "register_retrieval_stage",
    "register_builtin_retrieval_stages",
    "register_optional_strategy",
    "build_optional_components",
    "report_rerank_degraded",
    "retrieval_stage_order",
    "skip_node",
    "span_names",
    "stage_node_ids",
    "start_node",
    "complete_node",
]

# 需要图谱分支的路由目标
GRAPH_TARGETS: tuple[str, ...] = ("vector_graph_rag", "full")

# 阶段注册名（顺序校验按名字找，避免模块内的前向引用）
SCOPE_STAGE_NAME = "dataset_scope"
GRAPH_STAGE_NAME = "graph"
SENTENCE_WINDOW_STAGE_NAME = "sentence_window"
TRUNCATE_STAGE_NAME = "truncate"


# --------------------------------------------------------------------------- #
# run-event 仪式（原先散落在每个阶段里，现在只在这里有一份）
# --------------------------------------------------------------------------- #
def notify_observer(method: str, *args: Any, **kwargs: Any) -> Any:
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


def start_node(node_id: str, **attributes: Any) -> None:
    notify_observer("start_node", node_id, attributes=attributes or None)


def complete_node(node_id: str, duration_ms: float | None = None, **attributes: Any) -> None:
    notify_observer(
        "complete_node",
        node_id,
        duration_ms=duration_ms,
        attributes=attributes or None,
    )


def fail_node(
    node_id: str,
    *,
    error_type: str,
    reason: str,
    recoverable: bool,
    duration_ms: float | None = None,
    **attributes: Any,
) -> None:
    notify_observer(
        "fail_node",
        node_id,
        duration_ms=duration_ms,
        error_type=error_type,
        error_code=reason,
        recoverable=recoverable,
        attributes={"reason": reason, **attributes},
    )


def skip_node(node_id: str, reason: str, **attributes: Any) -> None:
    notify_observer("skip_node", node_id, attributes={"reason": reason, **attributes})


def degraded_node(node_id: str, reason: str, **attributes: Any) -> None:
    notify_observer("degraded", node_id, attributes={"reason": reason, **attributes})


def announce_route(
    node_id: str,
    route: RouteDecision,
    *,
    effective: str | None = None,
) -> None:
    notify_observer(
        "route_selected",
        node_id,
        route=effective or route.target,
        confidence=float(route.confidence),
        degraded=bool(route.degraded),
    )


def report_rerank_degraded(kind: str, detail: str) -> None:
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
        # 同时走"总数 + 降级数"口径，/api/metrics 里直接给出降级率
        get_metrics().record_outcome("reranker", degraded=True)
        get_logger(__name__).warning("重排降级（%s），改用召回顺序：%s", kind, detail)
    except Exception:  # noqa: BLE001 - 可观测性不得影响主链路
        pass


def add_span(name: str, duration_ms: float, traces: list[str]) -> None:
    """记录 span 到当前 trace（若存在）与结果 traces。"""
    trace = current_trace()
    if trace is not None:
        trace.add_span(name, duration_ms)
    traces.append(f"{name}:{duration_ms:.2f}ms")


# --------------------------------------------------------------------------- #
# 状态载体
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RetrievalState:
    """一次检索的累积状态：**只读输入 + 显式派生**。

    冻结的是**绑定**（``candidates`` / ``route`` / ``reranked`` 谁在什么之后换了
    一份，靠 :meth:`with_candidates` 这类方法留痕，不再是一串同名局部变量重绑）。
    两个字段例外，因为它们的语义本来就是"只往后加"：

    * ``traces`` —— trace 账本，append-only；阶段往里写的顺序就是最终
      ``QueryResult.traces`` 的顺序（前端按顺序取第一条命中的降级说明）。
    * ``enhance_jobs`` —— 预取组的 ``Future`` 袋子，:meth:`take_enhancer`
      取走一个少一个（与重构前的 ``jobs.pop(name)`` 一致）。

    ``dense_cosines`` 故意**不是**这里的字段：chunks 在 sentence_window 展开、
    租户收口、裁剪之后还会变，早拷贝一份会让弃权判定看到已被剔除的分数。它保持
    现算（``retrieval.pipeline.dense_cosines_of``）。
    """

    query: str
    search_query: str
    rewritten: str
    rewrite_changed: bool
    gate_skipped: bool
    route: RouteDecision
    hyde_doc: Optional[str]
    query_vec: Any
    candidates: list[RetrievedChunk]
    filter_expr: Optional[str]
    group_by_field: Optional[str]
    group_size: Optional[int]
    want_cosine: bool
    reranked: bool
    traces: list[str]
    enhance_jobs: dict[str, Future]
    chunks: list[RetrievedChunk]

    @classmethod
    def start(cls, query: str) -> "RetrievalState":
        """重构前 ``run()`` 开头那几行的等价初始化。"""
        return cls(
            query=query,
            search_query=query,
            rewritten=query,
            rewrite_changed=False,
            gate_skipped=False,
            route=RouteDecision(target="hybrid", confidence=1.0, degraded=False),
            hyde_doc=None,
            query_vec=None,
            candidates=[],
            filter_expr=None,
            group_by_field=None,
            group_size=None,
            want_cosine=False,
            reranked=False,
            traces=[],
            enhance_jobs={},
            chunks=[],
        )

    # -- 显式派生（每次重绑都留名） ------------------------------------------- #
    def with_gate(
        self, *, rewritten: str, changed: bool, gate_skipped: bool
    ) -> "RetrievalState":
        return replace(self, rewritten=rewritten, rewrite_changed=changed, gate_skipped=gate_skipped)

    def with_search_query(self, search_query: str) -> "RetrievalState":
        return replace(self, search_query=search_query)

    def with_rewrite(self, *, rewritten: str, changed: bool) -> "RetrievalState":
        return replace(self, rewritten=rewritten, rewrite_changed=changed)

    def with_route(self, route: RouteDecision) -> "RetrievalState":
        return replace(self, route=route)

    def with_hyde_doc(self, hyde_doc: Optional[str]) -> "RetrievalState":
        return replace(self, hyde_doc=hyde_doc)

    def with_query_vec(self, query_vec: Any) -> "RetrievalState":
        return replace(self, query_vec=query_vec)

    def with_candidates(self, candidates: list[RetrievedChunk]) -> "RetrievalState":
        return replace(self, candidates=candidates)

    def with_search_prep(
        self,
        *,
        filter_expr: Optional[str],
        group_by_field: Optional[str],
        group_size: Optional[int],
        want_cosine: bool,
    ) -> "RetrievalState":
        return replace(
            self,
            filter_expr=filter_expr,
            group_by_field=group_by_field,
            group_size=group_size,
            want_cosine=want_cosine,
        )

    def with_reranked(self, reranked: bool) -> "RetrievalState":
        return replace(self, reranked=reranked)

    def with_chunks(self, chunks: list[RetrievedChunk]) -> "RetrievalState":
        return replace(self, chunks=chunks)

    def with_enhance_jobs(self, jobs: dict[str, Future]) -> "RetrievalState":
        return replace(self, enhance_jobs=jobs)


# --------------------------------------------------------------------------- #
# 端口的一侧：管线为阶段提供的服务（duck-typing，与全仓注入风格一致）
# --------------------------------------------------------------------------- #
class RetrievalServices(Protocol):
    """阶段能向管线要的东西。``RetrievalPipeline`` 结构性满足它。"""

    embedder: Any
    milvus: Any
    reranker: Any
    reranker_cb: Any
    rewriter: Any
    router: Any
    graph_retriever: Any
    sentence_window: Any
    auto_filter: Any
    pipeline: Any

    def _ensure_fanout_executor(self) -> ThreadPoolExecutor: ...

    def _ensure_enhance_executor(self) -> ThreadPoolExecutor: ...

    def _serving_context(self, tenant: str, dataset: str) -> DocumentServingContext | None: ...

    def component(self, name: str) -> Any: ...

    def _bm25_text(self, text: str) -> Optional[str]: ...

    def _auto_filter_expr(
        self, query: str, dataset: str, traces: list[str]
    ) -> Optional[str]: ...

    def _filter_serving_candidates(
        self,
        candidates: Sequence[RetrievedChunk],
        context: DocumentServingContext | None,
        traces: list[str],
        *,
        stage: str,
    ) -> list[RetrievedChunk]: ...

    def _run_graph_branch(
        self,
        route: RouteDecision,
        search_query: str,
        candidates: list[RetrievedChunk],
        acl: Optional[list[str]],
        tenant: str,
        traces: list[str],
        serving_context: DocumentServingContext | None = None,
    ) -> tuple[RouteDecision, dict[str, Any]]: ...

    def _merge_extra(
        self, candidates: list[RetrievedChunk], extra: Sequence[RetrievedChunk]
    ) -> int: ...


@dataclass(frozen=True)
class StageContext:
    """一次检索的只读环境：配置、作用域、伺服快照 + 管线服务。

    协作者一律经 ``pipeline`` 取（而不是构造时快照）：``pipeline.milvus = ...``
    这类运行期替换是既有测试与被依赖的接线方式，快照会让阶段看到旧对象。
    """

    services: RetrievalServices
    settings: Any
    p: Any
    tenant: str
    dataset: str
    acl: Optional[list[str]]
    serving_context: DocumentServingContext | None

    @classmethod
    def open(
        cls,
        pipeline: RetrievalServices,
        acl: Optional[list[str]],
        tenant_id: Optional[str],
        dataset_id: Optional[str],
    ) -> "StageContext":
        settings = pipeline.settings
        # 租户强制解析（enforced=True 时空 / None 回退 default_tenant）
        tenant = resolve_tenant(tenant_id, settings)
        # 知识库维度不做强制回退：空即全域检索
        dataset = (dataset_id or "").strip()
        return cls(
            services=pipeline,
            settings=settings,
            p=pipeline.pipeline,
            tenant=tenant,
            dataset=dataset,
            acl=acl,
            serving_context=pipeline._serving_context(tenant, dataset),
        )

    def component(self, name: str) -> Any:
        """按注册表名取可选策略组件（未装配即为 None）。"""
        return self.services.component(name)


# --------------------------------------------------------------------------- #
# 端口的另一侧：阶段申报的结果（声明数据，不是事件调用）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Gate:
    """驱动器在跑阶段主体**之前**要发的节点事件（互斥三态）。"""

    start: bool = False
    start_attrs: Mapping[str, Any] = field(default_factory=dict)
    skip_reason: Optional[str] = None
    skip_attrs: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def running(**attributes: Any) -> "Gate":
        return Gate(start=True, start_attrs=attributes)

    @staticmethod
    def skipped(reason: str, **attributes: Any) -> "Gate":
        return Gate(skip_reason=reason, skip_attrs=attributes)

    @staticmethod
    def silent() -> "Gate":
        """无节点可报的阶段（作用域栅栏、过滤表达式拼装）。"""
        return Gate()


@dataclass(frozen=True)
class Degrade:
    reason: str
    attrs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Failure:
    error_type: str
    reason: str
    recoverable: bool
    attrs: Mapping[str, Any] = field(default_factory=dict)
    duration_ms: Optional[float] = None
    #: 仅当 ``recoverable`` 为真时补充 degraded 的额外属性；原因沿用失败原因。
    degrade: Optional[Degrade] = None


@dataclass(frozen=True)
class Skip:
    reason: str
    attrs: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Announce:
    node_id: str
    route: RouteDecision
    effective: Optional[str] = None


class StageOutcome:
    """阶段主体跑完后交给驱动器的记账单。

    可变成有意的：它是**一次主体调用的局部值**，只允许在 ``run`` 里被填，
    交给驱动器后就只读了（驱动器不改它）。
    """

    def __init__(self) -> None:
        self.span: Optional[tuple[str, float]] = None
        self.completed: Optional[tuple[Optional[float], Mapping[str, Any]]] = None
        self.failed: Optional[Failure] = None
        self.skipped: Optional[Skip] = None
        self.degraded: Optional[Degrade] = None
        self.announced: Optional[Announce] = None
        self.state: Optional[RetrievalState] = None

    # -- 填单（每个都对应驱动器的一种事件，阶段不能直接发事件） -------------- #
    def set_span(self, name: str, duration_ms: float) -> "StageOutcome":
        self.span = (name, duration_ms)
        return self

    def complete(self, duration_ms: float | None = None, **attributes: Any) -> "StageOutcome":
        self.completed = (duration_ms, attributes)
        return self

    def fail(
        self,
        error_type: str,
        reason: str,
        *,
        recoverable: bool,
        duration_ms: float | None = None,
        degrade_attrs: Mapping[str, Any] | None = None,
        degrade_reason: str | None = None,
        **attributes: Any,
    ) -> "StageOutcome":
        degrade: Optional[Degrade] = None
        if recoverable:
            degrade = Degrade(
                degrade_reason if degrade_reason is not None else reason,
                dict(degrade_attrs or {}),
            )
        self.failed = Failure(
            error_type=error_type,
            reason=reason,
            recoverable=recoverable,
            attrs=dict(attributes),
            duration_ms=duration_ms,
            degrade=degrade,
        )
        return self

    def skip(self, reason: str, **attributes: Any) -> "StageOutcome":
        self.skipped = Skip(reason, dict(attributes))
        return self

    def degrade(self, reason: str, **attributes: Any) -> "StageOutcome":
        self.degraded = Degrade(reason, dict(attributes))
        return self

    def announce_route(
        self, node_id: str, route: RouteDecision, *, effective: str | None = None
    ) -> "StageOutcome":
        self.announced = Announce(node_id, route, effective)
        return self

    def derive(self, state: RetrievalState) -> "StageOutcome":
        """把本阶段对状态的显式派生交给驱动器（谁在什么之后改了什么因此可读）。"""
        self.state = state
        return self


class RetrievalStage(Protocol):
    """检索链路上的一个阶段。

    成员契约：

    * ``name`` —— 注册表键；``node_id`` —— run-event / 前端用的 span 节点 id，
      ``None`` 表示这一步没有自己的节点（只做数据收口）。
    * ``order`` —— 显式序号（顺序即语义，见 :func:`retrieval_stage_order`）。
    * ``eligible`` —— 那道"开关开着且组件注入了吗"的闸门；返回 ``Gate``。
    * ``run`` —— 主体，返回 :class:`StageOutcome`。
    * ``fatal_error`` —— 主体抛异常时**如何记账**（默认不记账、原样上抛，
      与重构前那些没有 try 的段落一致）。
    """

    name: str
    node_id: Optional[str]
    order: int
    #: 查询端增强成员：由预取组提前并发发起
    prefetches: bool = False
    #: 可选策略组件名（与 ``OPTIONAL_STRATEGIES`` 同源）；无组件依赖则为 None
    requires_component: Optional[str] = None
    #: 该阶段读的 pipeline 开关字段名（装配点与阶段闸门同源）
    requires_flag: Optional[str] = None

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate: ...

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome: ...

    def fatal_error(
        self, exc: BaseException, state: RetrievalState, ctx: StageContext
    ) -> Optional[Failure]: ...


# --------------------------------------------------------------------------- #
# 驱动器
# --------------------------------------------------------------------------- #
class StageRunner:
    """按 ``order`` 驱动阶段，独家拥有 span / skip / fail / degrade / 路由播报仪式。"""

    def __init__(self, stages: Sequence[RetrievalStage]) -> None:
        self.stages = tuple(stages)

    def run(self, state: RetrievalState, ctx: StageContext) -> RetrievalState:
        for stage in self.stages:
            state = self._run_one(stage, state, ctx)
        return state

    # ------------------------------------------------------------------ #
    def _run_one(self, stage: RetrievalStage, state: RetrievalState, ctx: StageContext) -> RetrievalState:
        gate = stage.eligible(state, ctx)
        if stage.node_id is not None:
            if gate.skip_reason is not None:
                skip_node(stage.node_id, gate.skip_reason, **gate.skip_attrs)
            elif gate.start:
                start_node(stage.node_id, **gate.start_attrs)

        t0 = time.perf_counter()
        try:
            outcome = stage.run(state, ctx)
        except Exception as exc:  # noqa: BLE001 - 按各阶段原有语义记账后原样上抛
            fatal = stage.fatal_error(exc, state, ctx)
            if fatal is not None and stage.node_id is not None:
                fail_node(
                    stage.node_id,
                    error_type=fatal.error_type if fatal.error_type else type(exc).__name__,
                    reason=fatal.reason,
                    recoverable=fatal.recoverable,
                    duration_ms=(
                        fatal.duration_ms
                        if fatal.duration_ms is not None
                        else (time.perf_counter() - t0) * 1000.0
                    ),
                    **fatal.attrs,
                )
            raise
        return self._finish(stage, state, ctx, outcome, elapsed=(time.perf_counter() - t0) * 1000.0)

    def _finish(
        self,
        stage: RetrievalStage,
        state: RetrievalState,
        ctx: StageContext,
        outcome: StageOutcome,
        *,
        elapsed: float,
    ) -> RetrievalState:
        next_state = outcome.state if outcome.state is not None else state
        if outcome.span is not None:
            add_span(outcome.span[0], outcome.span[1], next_state.traces)

        if outcome.failed is not None and stage.node_id is not None:
            failure = outcome.failed
            fail_node(
                stage.node_id,
                error_type=failure.error_type,
                reason=failure.reason,
                recoverable=failure.recoverable,
                duration_ms=(
                    failure.duration_ms if failure.duration_ms is not None else elapsed
                ),
                **failure.attrs,
            )
            # 降级必须可见：可恢复失败**必然**产出 degraded，阶段想漏也漏不掉。
            if failure.recoverable:
                degrade = failure.degrade or Degrade(failure.reason)
                degraded_node(stage.node_id, degrade.reason, **degrade.attrs)
        elif outcome.skipped is not None and stage.node_id is not None:
            skip_node(stage.node_id, outcome.skipped.reason, **outcome.skipped.attrs)
        elif outcome.completed is not None and stage.node_id is not None:
            duration_ms, attrs = outcome.completed
            complete_node(stage.node_id, duration_ms, **attrs)

        if outcome.degraded is not None and stage.node_id is not None:
            degraded_node(stage.node_id, outcome.degraded.reason, **outcome.degraded.attrs)
        if outcome.announced is not None:
            announce_route(
                outcome.announced.node_id,
                outcome.announced.route,
                effective=outcome.announced.effective,
            )
        return next_state


# --------------------------------------------------------------------------- #
# 注册表（复用 core.providers.ProviderRegistry，与 chunk writer 同一手法）
# --------------------------------------------------------------------------- #
RETRIEVAL_STAGES: ProviderRegistry[None, RetrievalStage] = ProviderRegistry("retrieval stage")


#: 一个阶段必须给全的成员。``node_id`` / ``span_name`` 等允许取 None，但**必须有这个名字**——
#: ``stage_node_ids()``、``StageRunner`` 与 ``engaged()`` 都是直接取属性，不是 getattr 兜底。
_REQUIRED_STAGE_MEMBERS: tuple[str, ...] = (
    "name",
    "order",
    "node_id",
    "span_name",
    "prefetches",
    "requires_flag",
    "requires_component",
    "eligible",
    "fatal_error",
    "run",
)

#: 协议里必须可调用的成员（其余是声明性属性）。
_CALLABLE_STAGE_MEMBERS: tuple[str, str, str] = ("eligible", "fatal_error", "run")


def register_retrieval_stage(stage: RetrievalStage, *, replace: bool = False) -> None:
    """Register one retrieval stage under its own ``name`` key.

    形状在这里判死，而不是等到那次真跑到它的检索：缺成员的阶段若被静默收下，
    ``retrieval_stage_order()`` 只按 ``order`` 排序所以照样通过，异常要等到
    ``StageRunner`` 真去调 ``eligible`` / ``run`` 时才炸——装配错误被推迟到线上。
    可调用成员还要**真是可调用**：``run = "x"`` 这类非可调用赋值同样推迟爆炸
    （S-RS 评审 P2-2）。
    """
    missing = [m for m in _REQUIRED_STAGE_MEMBERS if not hasattr(stage, m)]
    _require(
        not missing,
        f"检索阶段 {getattr(stage, 'name', '?')!r} 不符合 RetrievalStage 协议，"
        f"缺少成员：{missing}",
    )
    not_callable = [
        m
        for m in _REQUIRED_STAGE_MEMBERS
        if m in _CALLABLE_STAGE_MEMBERS and not callable(getattr(stage, m))
    ]
    _require(
        not not_callable,
        f"检索阶段 {getattr(stage, 'name', '?')!r} 不符合 RetrievalStage 协议，"
        f"成员不是可调用：{not_callable}",
    )
    RETRIEVAL_STAGES.register(
        stage.name, lambda _config, _s=stage: _s, replace=replace
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def retrieval_stage_order() -> tuple[RetrievalStage, ...]:
    """按 ``order`` 排好、并**校验作用域收口没有被注册顺序侥幸绕过**。

    三条硬序（S2.4.3）不靠"大家记得写对数字"，而是注册期就判：

    * 图谱分支、sentence_window 都在按 id 直取、绕过 ``filter_expr``；
      ``dataset_scope`` 是唯一的兜底收口，因此必须在它们之后。
    * 收口必须在裁剪之前，否则越界 chunk 会进最终 top_k。
    """
    stages = sorted(
        (RETRIEVAL_STAGES.create(name, None) for name in RETRIEVAL_STAGES.names()),
        key=lambda stage: stage.order,
    )
    orders = [stage.order for stage in stages]
    _require(
        len(set(orders)) == len(orders),
        f"检索阶段 order 不可重复：{[(stage.name, stage.order) for stage in stages]}",
    )
    by_name = {stage.name: stage for stage in stages}
    scope = by_name.get(SCOPE_STAGE_NAME)
    if scope is not None:
        for earlier_name in (GRAPH_STAGE_NAME, SENTENCE_WINDOW_STAGE_NAME):
            earlier = by_name.get(earlier_name)
            if earlier is not None:
                _require(
                    earlier.order < scope.order,
                    f"{earlier_name} 必须排在 {SCOPE_STAGE_NAME} 之前（作用域收口不得被绕过）",
                )
        truncate = by_name.get(TRUNCATE_STAGE_NAME)
        if truncate is not None:
            _require(
                scope.order < truncate.order,
                f"{SCOPE_STAGE_NAME} 必须排在 {TRUNCATE_STAGE_NAME} 之前",
            )
    return tuple(stages)


def stage_node_ids() -> tuple[str, ...]:
    """已注册阶段申报的 run-event 节点 id（不含 None）。

    这些名字是契约：``frontend/src/run/serverRunProjection.ts`` 按节点 id 认策略，
    ``rag_topology.py`` 按节点 id 画拓扑，run-events 落库后也按名读。
    """
    return tuple(stage.node_id for stage in retrieval_stage_order() if stage.node_id)


def span_names() -> tuple[str, ...]:
    """已注册阶段申报的 span 名 —— ``traces`` 里 ``name:xx.xxms`` 的那个 name。

    ``frontend/src/strategy/traceParse.ts`` 的 ``STAGE_LABELS`` 按这个名字查中文标签，
    没登记的名字会在前端阶段面板里退化成裸英文键。
    """
    return tuple(stage.span_name for stage in retrieval_stage_order() if stage.span_name)


# --------------------------------------------------------------------------- #
# 阶段实现：每个阶段一个类，只申报"这次算成功 / 跳过 / 降级"，不发事件。
# 节点 id、span 名与 trace 文案逐字沿用重构前 ``run()`` 的写法——它们是前端
# 阶段面板与 run-events 消费方的解析契约。
# --------------------------------------------------------------------------- #
class _Stage:
    """阶段基类。

    默认值即"重构前那些没有仪式的段落"：无节点、不参与预取、异常不记账
    （原样上抛）。迁移时只覆写需要的方法，不新增仪式。
    """

    name: str = ""
    node_id: Optional[str] = None
    order: int = 0
    #: 本阶段往 ``traces`` 里记的 span 名（前端耗时条的键，与 node_id 可以不同：
    #: 门控的阶段节点叫 ``complexity_gate``，span 叫 ``gate``）。None = 无 span。
    span_name: Optional[str] = None
    prefetches: bool = False
    requires_component: Optional[str] = None
    requires_flag: Optional[str] = None

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate:
        return Gate.silent()

    def fatal_error(
        self, exc: BaseException, state: RetrievalState, ctx: StageContext
    ) -> Optional[Failure]:
        return None

    # -- 声明数据的读法（开关与组件都按名字取，装配点与阶段同源） ------------- #
    def component(self, ctx: StageContext) -> Any:
        return ctx.component(str(self.requires_component))

    def flag_on(self, ctx: StageContext) -> bool:
        return bool(getattr(ctx.p, str(self.requires_flag), False))

    def engaged(self, state: RetrievalState, ctx: StageContext) -> bool:
        """``开关开着且组件注入了吗``——全链路唯一一份这道闸的写法。"""
        if self.requires_flag is not None and not self.flag_on(ctx):
            return False
        if self.requires_component is not None and self.component(ctx) is None:
            return False
        return True

    def skip_reason(self, state: RetrievalState, ctx: StageContext) -> Optional[str]:
        """可选阶段的停用原因（与重构前的 ``disabled`` / ``unavailable`` 一致）。"""
        if self.requires_flag is not None and not self.flag_on(ctx):
            return "disabled"
        if self.requires_component is not None and self.component(ctx) is None:
            return "unavailable"
        return None


class ComplexityGateStage(_Stage):
    """1. 复杂度门控：查询已具体明确时跳过改写与路由（零 LLM 成本）。

    关掉时节点按 ``disabled`` 记一条 skip，但**主体照跑**：重构前那个
    ``gate`` span 是无条件记录的（关闭时记 ~0ms），前端耗时条因此总能看到它。
    ``gate_skipped`` 是它给下游的声明——``rewrite`` / ``route`` 的启用条件不是
    各自独立的布尔，而是"门控有没有判成简单查询"，所以 :class:`Gate` 读状态。
    """

    name = "complexity_gate"
    node_id = "complexity_gate"
    span_name = "gate"
    order = 10
    requires_flag = "complexity_gate_on"

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate:
        if self.flag_on(ctx):
            return Gate.running()
        return Gate.skipped("disabled")

    def fatal_error(
        self, exc: BaseException, state: RetrievalState, ctx: StageContext
    ) -> Optional[Failure]:
        return Failure(
            error_type=type(exc).__name__, reason="evaluation_failed", recoverable=False
        )

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        t0 = time.perf_counter()
        rewritten, changed = state.query, False
        gate_skipped = False
        if self.flag_on(ctx):
            rewritten, changed = ctx.services.rewriter.rewrite(state.query)
            gate_skipped = not changed  # 具体查询：跳过改写与路由
        gate_ms = (time.perf_counter() - t0) * 1000.0
        outcome = StageOutcome().set_span("gate", gate_ms)
        if self.flag_on(ctx):
            outcome.complete(gate_ms, rewrite_required=bool(changed))
        return outcome.derive(
            state.with_gate(rewritten=rewritten, changed=changed, gate_skipped=gate_skipped)
        )


class RewriteStage(_Stage):
    """2. 查询改写：复杂查询先 LLM 改写（失败静默降级为原文）。

    两条来源共用一个节点（重构前就是两条 ``_start_node("rewrite")``）：门控关闭
    时在这里真跑一次改写，门控开启且判为复杂时改写已在门控那一步完成、这里只补
    一条 0ms 的 span。门控判成简单查询则整体跳过 —— 这道闸读的是**状态**而不是
    自己的开关字段，所以 :meth:`eligible` 不复用 ``skip_reason``。
    """

    name = "rewrite"
    node_id = "rewrite"
    span_name = "rewrite"
    order = 20

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate:
        if state.gate_skipped:
            return Gate.skipped("gate_simple")
        return Gate.running()

    def fatal_error(
        self, exc: BaseException, state: RetrievalState, ctx: StageContext
    ) -> Optional[Failure]:
        return Failure(
            error_type=type(exc).__name__, reason="rewrite_failed", recoverable=False
        )

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        outcome = StageOutcome()
        if state.gate_skipped:
            return outcome.derive(state).set_span("rewrite", 0.0)
        if not ctx.p.complexity_gate_on:
            # 门控关闭：此处显式执行改写
            t0 = time.perf_counter()
            rewritten, changed = ctx.services.rewriter.rewrite(state.query)
            rewrite_ms = (time.perf_counter() - t0) * 1000.0
            state = state.with_rewrite(rewritten=rewritten, changed=changed)
            outcome.set_span("rewrite", rewrite_ms).complete(rewrite_ms, changed=bool(changed))
        else:
            # 门控开启且查询复杂：改写已在门控阶段完成
            outcome.set_span("rewrite", 0.0).complete(0.0, changed=bool(state.rewrite_changed))
        search_query = (state.rewritten or "").strip() or state.query
        return outcome.derive(state.with_search_query(search_query))


class RouteStage(_Stage):
    """3. 意图路由：改写结果参与路由，低置信度自动降级，全程不抛错。

    ``route.selected`` 在两条分支之后**各播报一次**（重构前那句
    ``_selected_route("route", route)`` 就在 if/else 外面），所以它挂在 outcome 上
    而不是某条分支里。
    """

    name = "route"
    node_id = "route"
    span_name = "route"
    order = 30

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate:
        if state.gate_skipped:
            return Gate.skipped("gate_simple")
        return Gate.running()

    def fatal_error(
        self, exc: BaseException, state: RetrievalState, ctx: StageContext
    ) -> Optional[Failure]:
        return Failure(
            error_type=type(exc).__name__, reason="route_failed", recoverable=False
        )

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        outcome = StageOutcome()
        if state.gate_skipped:
            outcome.set_span("route", 0.0)
        else:
            t0 = time.perf_counter()
            route = ctx.services.router.route(state.search_query)
            route_ms = (time.perf_counter() - t0) * 1000.0
            state = state.with_route(route)
            outcome.set_span("route", route_ms).complete(route_ms)
        outcome.announce_route("route", state.route)
        return outcome.derive(state)


class _EnhancerStage(_Stage):
    """查询端增强阶段的共同形状：由预取组提前发起生成，主体里再取回。

    成员资格是**声明**出来的（``prefetches = True`` + ``enhancer_name``），不是
    写死在三处的一份名单：预取组按注册表顺序问每个成员"这轮启用了吗"，启用即
    提前发 ``node.started`` 并把 ``generate`` 丢进生成池。
    """

    prefetches = True
    #: 预取袋里的键（也是并发 trace 文案里出现的顺序名）
    enhancer_name: str = ""

    def prefetch_engaged(self, state: RetrievalState, ctx: StageContext) -> bool:
        return self.engaged(state, ctx)

    def submit_prefetch(self, executor: ThreadPoolExecutor, ctx: StageContext, query: str) -> Future:
        """把这一次 LLM 生成丢进生成池（耗时由任务自己量，见 :class:`EnhancerPrefetch`）。"""
        return executor.submit(
            ENHANCER_PREFETCH.timed_generate, self.component(ctx), query
        )

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate:
        reason = self.skip_reason(state, ctx)
        if reason is not None:
            return Gate.skipped(reason)
        # 启用时 node.started 已由预取发起点发过（早于 embed），这里不能再发一遍
        return Gate.silent()


class HydeStage(_EnhancerStage):
    """4. HyDE 假设文档（嵌入前）：稠密检索用假设文档向量，BM25 仍用查询文本。

    生成失败 / 无产出时静默降级为原查询嵌入（span 记 0ms），绝不中断管线。
    """

    name = "plugin.hyde.expand"
    node_id = "plugin.hyde.expand"
    span_name = "hyde"
    order = 40
    enhancer_name = "hyde"
    requires_flag = "hyde_on"
    requires_component = "hyde"

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        outcome = StageOutcome()
        if not self.engaged(state, ctx):
            return outcome.derive(state)
        traces = state.traces
        component = self.component(ctx)
        hyde_doc, hyde_ms, hyde_exc = ENHANCER_PREFETCH.take(
            state, self.enhancer_name, component, state.search_query
        )
        hyde_work_ms = hyde_ms
        if hyde_exc is not None:
            traces.append(f"hyde 生成失败，降级为普通嵌入: {hyde_exc}")
            hyde_doc = None
        if not (isinstance(hyde_doc, str) and hyde_doc.strip()):
            hyde_ms = 0.0  # 未产出假设文档：不改变嵌入
            hyde_doc = None
            traces.append("hyde 未产出假设文档，使用原查询嵌入")
        outcome.set_span("hyde", hyde_ms)
        if hyde_exc is not None:
            outcome.fail(
                type(hyde_exc).__name__,
                "generation_failed",
                recoverable=True,
                duration_ms=hyde_work_ms,
            )
        elif hyde_doc is None:
            outcome.fail(
                "EmptyResult", "no_output", recoverable=True, duration_ms=hyde_ms
            )
        else:
            outcome.complete(hyde_ms, output_present=True)
        return outcome.derive(state.with_hyde_doc(hyde_doc))


# --------------------------------------------------------------------------- #
# 预取组：查询端增强是并发发起，不是一个一个排队（S2.3）
# --------------------------------------------------------------------------- #
class PrefetchableStage(Protocol):
    """声明了 ``prefetches = True`` 的阶段额外的能力（生成与主检索并行）。"""

    name: str
    node_id: Optional[str]
    enhancer_name: str

    def prefetch_engaged(self, state: RetrievalState, ctx: StageContext) -> bool: ...

    def component(self, ctx: StageContext) -> Any: ...

    def submit_prefetch(
        self, executor: ThreadPoolExecutor, ctx: StageContext, query: str
    ) -> Future: ...


class EnhancerPrefetch:
    """把已启用的查询端增强的 LLM 生成**同时**发出去。

    三者都只吃 ``search_query``，彼此没有任何数据依赖，从前却是一次 HyDE、等
    它回来做完检索、再一次子查询、再一次后退提问——默认全开时这是三次完整的
    LLM 往返，一条接一条地摞在用户等待里。它们唯一的时序约束是 HyDE 必须早于
    ``embed_query``，而那个约束在下面取回结果的地方自然满足（用到谁才等谁）。

    成员清单来自阶段自己的声明（``prefetches`` + ``enhancer_name``），因此新增一
    路增强只要多注册一个阶段，不必再改这里。两个线程池分开是刻意的
    （见 ``RetrievalPipeline.__init__`` 的注释），这里只用生成池。
    """

    #: 生成任务自己收尾：异常不穿过 ``Future``，三者互不影响是原有语义。
    @staticmethod
    def timed_generate(component: Any, query: str) -> tuple[Any, float, Optional[Exception]]:
        t0 = time.perf_counter()
        try:
            return component.generate(query), (time.perf_counter() - t0) * 1000.0, None
        except Exception as exc:  # noqa: BLE001 - 由调用方按各自语义降级
            return None, (time.perf_counter() - t0) * 1000.0, exc

    def launch(
        self, members: Sequence[PrefetchableStage], state: RetrievalState, ctx: StageContext
    ) -> dict[str, Future]:
        """返回 ``{增强名: Future}``；空字典即表示"就地同步生成"。"""
        query = state.search_query
        enabled = [stage for stage in members if stage.prefetch_engaged(state, ctx)]
        for stage in enabled:
            if stage.node_id is not None:
                start_node(stage.node_id)
        if len(enabled) < 2:
            return {}
        try:
            executor = ctx.services._ensure_enhance_executor()
            jobs = {
                stage.enhancer_name: stage.submit_prefetch(executor, ctx, query)
                for stage in enabled
            }
        except Exception as exc:  # noqa: BLE001 - 线程池不可用就退回串行
            state.traces.append(f"查询端增强并发生成不可用，退回串行: {exc}")
            return {}
        state.traces.append(
            "查询端增强并发生成："
            + " / ".join(stage.enhancer_name for stage in enabled)
            + " 的 LLM 调用已同时发出（各自的 span 因此相互重叠）"
        )
        return jobs

    def take(
        self,
        state: RetrievalState,
        name: str,
        component: Any,
        query: str,
    ) -> tuple[Any, float, Optional[Exception]]:
        """取回预先发出的生成结果（袋子在状态里）。"""
        return self.take_jobs(state.enhance_jobs, name, component, query)

    def take_jobs(
        self,
        jobs: dict[str, Future],
        name: str,
        component: Any,
        query: str,
    ) -> tuple[Any, float, Optional[Exception]]:
        """取回预先发出的生成结果；没预发过就地同步生成（语义完全一致）。"""
        job = jobs.pop(name, None)
        if job is None:
            return self.timed_generate(component, query)
        try:
            return job.result()
        except Exception as exc:  # noqa: BLE001 - 线程池自身出问题（已关闭等）
            return None, 0.0, exc


ENHANCER_PREFETCH = EnhancerPrefetch()


# --------------------------------------------------------------------------- #
# 主检索段阶段（预取发起 -> 嵌入 -> 过滤表达式 -> 混合检索 -> 两路增强合并）
# --------------------------------------------------------------------------- #
class EnhancerPrefetchStage(_Stage):
    """预取发起点：把已启用的查询端增强的 LLM 生成**同时**发出去。

    它是一个阶段而不是驱动器里的特例，因为它有自己的时序位置：必须晚于 route
    （吃的是 ``search_query``，改写后的），又必须早于 embed（HyDE 的向量要参与
    嵌入）。把它放在 ``route`` 与 ``plugin.hyde.expand`` 之间，这个位置就由
    ``order`` 声明而不是由某个方法的书写顺序决定。

    成员清单来自注册表里 ``prefetches`` 的那几个阶段，所以新增一路增强不必改
    这里；三个都未启用时它什么都不做（``launch`` 返回空袋 = 就地同步生成）。
    """

    name = "enhancers.prefetch"
    node_id = None
    order = 35

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        members = [stage for stage in retrieval_stage_order() if stage.prefetches]
        jobs = ENHANCER_PREFETCH.launch(members, state, ctx)
        return StageOutcome().derive(state.with_enhance_jobs(jobs))


class EmbedStage(_Stage):
    """5. 嵌入：BGE-M3 稠密嵌入；HyDE 生效时嵌的是假设文档（span 恒记 ``embed``）。"""

    name = "embed"
    node_id = "embed"
    span_name = "embed"
    order = 50

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate:
        return Gate.running(source="hyde" if state.hyde_doc else "query")

    def fatal_error(
        self, exc: BaseException, state: RetrievalState, ctx: StageContext
    ) -> Optional[Failure]:
        return Failure(
            error_type=type(exc).__name__, reason="embed_failed", recoverable=False
        )

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        t0 = time.perf_counter()
        query_vec = ctx.services.embedder.embed_query(
            state.hyde_doc if state.hyde_doc else state.search_query
        )
        embed_ms = (time.perf_counter() - t0) * 1000.0
        outcome = StageOutcome().set_span("embed", embed_ms)
        outcome.complete(embed_ms, source="hyde" if state.hyde_doc else "query")
        return outcome.derive(state.with_query_vec(query_vec))


class SearchFilterStage(_Stage):
    """混合检索的过滤表达式拼装（无自有节点）。

    租户 + 知识库 + ACL 的组合统一收敛到 ``milvus.build_filters``，自动元数据
    过滤再 AND 上去；``want_cosine`` 也在这里定——它是 rerank 三条"事前就知道
    的失效路径"的预判，HyDE 开着时不索取余弦（那把尺子没校准过）。
    """

    name = "search.filter_expr"
    node_id = None
    order = 55

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        p, services = ctx.p, ctx.services
        traces = state.traces
        group_by_field: Optional[str] = None
        group_size: Optional[int] = None
        if p.source_diversity in ("group_only", "group_mmr"):
            group_by_field = p.group_by_field
            group_size = p.group_size

        # 组合逻辑统一收敛到 RagMilvusClient.build_filters，避免此处与客户端
        # 各写一份而漏掉维度（dataset_id 此前就是这样被漏掉的）。
        filter_expr = services.milvus.build_filters(
            tenant_id=ctx.tenant,
            acl=ctx.acl,
            acl_filter_on=p.acl_filter_on,
            dataset_id=ctx.dataset,
        )

        # 自动元数据过滤（可选增强）：LLM 按元数据 schema 生成过滤表达式，与
        # 租户/ACL/dataset 表达式 AND 组合。任何失败（LLM 不可用、schema 读取
        # 失败、校验失败）都静默降级为不过滤——过滤是优化而非正确性约束。
        if services.auto_filter is not None:
            auto_expr = services._auto_filter_expr(state.query, ctx.dataset, traces)
            if auto_expr:
                if filter_expr:
                    filter_expr = f"({filter_expr}) and ({auto_expr})"
                else:
                    filter_expr = auto_expr
                traces.append(f"auto_filter: {auto_expr}")

        # rerank 一旦不生效，score 就退化成 RRF 融合分，弃权闸那道"知识库有没有
        # 相关内容"的检查会整个失效（见 verify.abstention 的说明）。稠密余弦是
        # 那种情况下唯一还能用的信号，但取回向量要额外传 dim×4 字节/条，所以
        # **只在确实用得上时才要**。这里只能预判三条"事前就知道"的失效路径；
        # 调用到一半抛错那第四条事前无从得知，那种情况仍旧如实承认不可评估。
        want_cosine = state.hyde_doc is None and (
            not p.rerank_on
            or services.reranker is None
            or (services.reranker_cb is not None and not services.reranker_cb.allow())
        )
        return StageOutcome().derive(
            state.with_search_prep(
                filter_expr=filter_expr,
                group_by_field=group_by_field,
                group_size=group_size,
                want_cosine=want_cosine,
            )
        )


def candidate_request_count(p: Any) -> int:
    """混合检索向 Milvus 请求的候选条数（``top_k × 放大系数``）。

    系数来自 ``pipeline.search_candidate_factor``，默认 2 —— 也就是这次配置化
    之前的硬编码值，所以**不配置时行为与改造前完全一致**。

    取值做兜底而不是让它炸在检索路径上：这个字段只影响"候选取多少"，是纯粹的
    质量/成本旋钮，取不到（旧配置对象 / 测试里的替身）时退回默认值最安全，
    不该因为一个旋钮让整条检索失败。非法值（0、负数、非整数）同样退回默认。
    """
    factor = getattr(p, "search_candidate_factor", 2)
    try:
        value = int(factor)
    except (TypeError, ValueError):
        return int(getattr(p, "top_k", 0) or 0) * 2
    if value < 1:
        value = 2
    return int(getattr(p, "top_k", 0) or 0) * value


class SearchStage(_Stage):
    """6. 混合检索：候选按 ``search_candidate_factor`` 放大，给重排留余量。失败是致命的。"""

    name = "search"
    node_id = "search"
    span_name = "search"
    order = 60

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate:
        return Gate.running(
            mode="hybrid" if ctx.p.hybrid_search_on else "dense",
            requested_count=candidate_request_count(ctx.p),
        )

    def fatal_error(
        self, exc: BaseException, state: RetrievalState, ctx: StageContext
    ) -> Optional[Failure]:
        return Failure(
            error_type=type(exc).__name__,
            reason="search_failed",
            recoverable=False,
            attrs={"mode": "hybrid" if ctx.p.hybrid_search_on else "dense"},
        )

    @staticmethod
    def mode(ctx: StageContext) -> str:
        return "hybrid" if ctx.p.hybrid_search_on else "dense"

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        p, services = ctx.p, ctx.services
        search_mode = self.mode(ctx)
        t0 = time.perf_counter()
        candidates = services.milvus.hybrid_search(
            query_dense=state.query_vec,
            top_k=candidate_request_count(p),
            query_text=services._bm25_text(state.search_query),
            group_by_field=state.group_by_field,
            group_size=state.group_size,
            filter_expr=state.filter_expr,
            with_cosine=state.want_cosine,
        )
        if not p.hybrid_search_on:
            state.traces.append("hybrid_search 关闭：仅执行稠密语义检索（无 BM25 分支）")
        search_ms = (time.perf_counter() - t0) * 1000.0
        outcome = StageOutcome().set_span("search", search_ms)
        outcome.complete(search_ms, mode=search_mode, candidate_count=len(candidates))
        return outcome.derive(state.with_candidates(candidates))


class ServingFenceStage(_Stage):
    """伺服栅栏（无自有节点）：把已删除 / 不可检索文档的证据剔除。

    三个位置各自对应一批候选的形成时刻，``stage`` 标签进 trace 文案，所以位置
    就是语义 —— 它必须紧跟在它过滤的那批检索之后。
    """

    node_id = None

    def __init__(self, name: str, label: str, order: int) -> None:
        self.name = name
        self.label = label
        self.order = order

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        kept = ctx.services._filter_serving_candidates(
            state.candidates, ctx.serving_context, state.traces, stage=self.label
        )
        return StageOutcome().derive(state.with_candidates(kept))


class SubQueriesStage(_EnhancerStage):
    """7. 子查询拆解：一次批量嵌入 + N 路并发检索，整批共用一个 deadline。

    扇出用的是**检索池**（``_ensure_fanout_executor``）而不是生成池，两个池分开
    是刻意的（见 ``RetrievalPipeline.__init__``）。单路失败逐路捕获、跳过；超时
    不取消已发出的检索，但照常带着主检索结果返回。耗时口径：起点在取回生成
    结果之后，生成耗时由线程内自测后加回来。
    """

    name = "plugin.subqueries.expand"
    node_id = "plugin.subqueries.expand"
    span_name = "subqueries"
    order = 70
    enhancer_name = "subqueries"
    requires_flag = "subqueries_on"
    requires_component = "subqueries"

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        outcome = StageOutcome()
        if not self.engaged(state, ctx):
            return outcome.derive(state)
        p, services = ctx.p, ctx.services
        traces = state.traces
        search_query = state.search_query
        candidates = state.candidates
        subs: list[str] = []
        raw, sub_gen_ms, sub_exc = ENHANCER_PREFETCH.take(
            state, self.enhancer_name, self.component(ctx), search_query
        )
        sub_failure_reason: Optional[str] = None
        sub_error_type = "SubqueryFailure"
        sub_failure_count = 0
        sub_success_count = 0
        sub_added_count = 0
        # 计时器起点在取回生成结果**之后**：这一段要报的是"子查询这一步花了
        # 多少"，而不是"主线程在这里坐了多久"（理由见类 docstring）。
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
            # hybrid_search」，总延迟随子查询数线性叠加。改为一次 embed_texts
            # 批量嵌入 + 线程池并发发起 N 次 hybrid_search；单路失败仍逐路
            # 捕获、跳过（语义与原实现一致），批量嵌入整体失败按「全部子查询
            # 检索失败」降级。
            searched_any = False
            try:
                sub_vecs = services.embedder.embed_texts(subs)
            except Exception as exc:  # noqa: BLE001 - 降级：跳过全部子查询
                traces.append(f"subqueries 批量嵌入失败，跳过全部子查询: {exc}")
                sub_vecs = []
                sub_failure_reason = "batch_embed_failed"
                sub_error_type = type(exc).__name__
                sub_failure_count = len(subs)
            if sub_vecs and len(sub_vecs) == len(subs):
                executor = services._ensure_fanout_executor()
                futures = [
                    (
                        sub,
                        executor.submit(
                            services.milvus.hybrid_search,
                            query_dense=sub_vec,
                            top_k=p.enhance_candidate_k,
                            query_text=services._bm25_text(sub),
                            group_by_field=state.group_by_field,
                            group_size=state.group_size,
                            filter_expr=state.filter_expr,
                        ),
                    )
                    for sub, sub_vec in zip(subs, sub_vecs)
                ]
                # 整批共用一个截止时刻，而不是每路各给一份超时：后者会累加成
                # N × timeout。超时本身是保险丝而非调优项。
                budget = float(getattr(p, "subquery_fanout_timeout_s", 0.0) or 0.0)
                deadline = (time.perf_counter() + budget) if budget > 0 else None
                for sub, future in futures:
                    try:
                        if deadline is None:
                            extra = future.result()
                        else:
                            # 剩余预算可能已经是负数，留一点点让最后一路也有
                            # 机会立刻交出已完成的结果。
                            extra = future.result(
                                timeout=max(0.001, deadline - time.perf_counter())
                            )
                    except FuturesTimeoutError:
                        sub_failure_reason = "search_timeout"
                        sub_error_type = "TimeoutError"
                        sub_failure_count += 1
                        # 超时不取消已发出的检索（线程还占着），但本次查询照常
                        # 带着主检索结果返回，不陪着一起卡死。
                        traces.append(
                            f"subqueries 子查询检索超时（{budget:.1f}s 预算耗尽），"
                            f"放弃剩余路（{sub!r}）"
                        )
                        break
                    except Exception as exc:  # noqa: BLE001 - 单路失败跳过该路
                        sub_failure_reason = "search_failed"
                        sub_error_type = type(exc).__name__
                        sub_failure_count += 1
                        traces.append(f"subqueries 子查询检索失败，跳过（{sub!r}）: {exc}")
                        continue
                    sub_added_count += services._merge_extra(candidates, extra)
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
        outcome.set_span("subqueries", sub_ms)
        if sub_failure_count and sub_success_count:
            sub_failure_reason = "partial_search_failure"
        if sub_failure_reason is not None:
            outcome.fail(
                sub_error_type,
                sub_failure_reason,
                recoverable=True,
                duration_ms=sub_ms,
                degrade_attrs={
                    "successful_count": sub_success_count,
                    "failure_count": sub_failure_count,
                },
                subquery_count=len(subs),
                successful_count=sub_success_count,
                failure_count=sub_failure_count,
                added_count=sub_added_count,
            )
        else:
            outcome.complete(
                sub_ms,
                subquery_count=len(subs),
                successful_count=sub_success_count,
                added_count=sub_added_count,
            )
        return outcome.derive(state)


class StepbackStage(_EnhancerStage):
    """8. Stepback 后退式提问：抽象问题补一次检索，按 chunk_id 去重合并。"""

    name = "plugin.stepback.expand"
    node_id = "plugin.stepback.expand"
    span_name = "stepback"
    order = 80
    enhancer_name = "stepback"
    requires_flag = "stepback_on"
    requires_component = "stepback"

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        outcome = StageOutcome()
        if not self.engaged(state, ctx):
            return outcome.derive(state)
        p, services = ctx.p, ctx.services
        traces = state.traces
        search_query = state.search_query
        candidates = state.candidates
        sb_query, sb_gen_ms, sb_exc = ENHANCER_PREFETCH.take(
            state, self.enhancer_name, self.component(ctx), search_query
        )
        sb_failure_reason: Optional[str] = None
        sb_error_type = "StepbackFailure"
        sb_added_count = 0
        t0 = time.perf_counter()  # 起点在取回之后，理由同子查询
        if sb_exc is not None:
            traces.append(f"stepback 生成失败，跳过: {sb_exc}")
            sb_failure_reason = "generation_failed"
            sb_error_type = type(sb_exc).__name__
            sb_query = None
        if isinstance(sb_query, str) and sb_query.strip() and sb_query != search_query:
            try:
                sb_vec = services.embedder.embed_query(sb_query)
                extra = services.milvus.hybrid_search(
                    query_dense=sb_vec,
                    top_k=p.enhance_candidate_k,
                    query_text=services._bm25_text(sb_query),
                    group_by_field=state.group_by_field,
                    group_size=state.group_size,
                    filter_expr=state.filter_expr,
                )
                sb_added_count = services._merge_extra(candidates, extra)
            except Exception as exc:  # noqa: BLE001 - 降级：跳过这一步增强
                traces.append(f"stepback 检索失败，跳过: {exc}")
                sb_failure_reason = "search_failed"
                sb_error_type = type(exc).__name__
        elif sb_exc is None:
            sb_failure_reason = "no_output"
            sb_error_type = "EmptyResult"
        sb_ms = sb_gen_ms + (time.perf_counter() - t0) * 1000.0
        outcome.set_span("stepback", sb_ms)
        if sb_failure_reason is not None:
            outcome.fail(
                sb_error_type,
                sb_failure_reason,
                recoverable=True,
                duration_ms=sb_ms,
                added_count=sb_added_count,
            )
        else:
            outcome.complete(sb_ms, added_count=sb_added_count)
        return outcome.derive(state)


# --------------------------------------------------------------------------- #
# 检索后段阶段（多样性 -> 图谱分支 -> 重排 -> 父块回取 -> 作用域收口 -> 裁剪）
# --------------------------------------------------------------------------- #
class DiversityStage(_Stage):
    """9. 来源多样性：``group_mmr`` 在分组检索之后再做一次文档感知 MMR。

    ``off`` 时节点按 ``disabled`` 跳过，但 span 照记（重构前就是无条件记的），
    前端耗时条因此总能看见它。
    """

    name = "diversity"
    node_id = "diversity"
    span_name = "diversity"
    order = 90

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate:
        mode = ctx.p.source_diversity
        if mode == "off":
            return Gate.skipped("disabled", mode="off")
        return Gate.running(mode=mode)

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        p = ctx.p
        before = len(state.candidates)
        t0 = time.perf_counter()
        candidates = state.candidates
        if p.source_diversity == "group_mmr":
            candidates = mmr_select(candidates, lambda_=p.mmr_lambda, k=p.top_k * 2)
        diversity_ms = (time.perf_counter() - t0) * 1000.0
        outcome = StageOutcome().set_span("diversity", diversity_ms)
        if p.source_diversity != "off":
            outcome.complete(
                diversity_ms,
                mode=p.source_diversity,
                input_count=before,
                output_count=len(candidates),
            )
        return outcome.derive(state.with_candidates(candidates))


class GraphStage(_Stage):
    """10. 图谱分支：``vector_graph_rag`` / ``full`` 路由命中时走一次图检索。

    三条降级路径（开关关闭 / 未注入图检索器 / 检索失败或无命中或命中被过滤）
    都降级为纯 hybrid 结果并标 ``degraded=True``。图回取按 id 直取、绕过
    ``filter_expr``，所以 :class:`DatasetScopeStage` 必须排在它之后。

    "跳过"事件在主体**之后**才发（重构前如此：要先问图分支拿到什么事实才知道
    跳过原因），所以它挂在 outcome 上而不是 :meth:`eligible` 里。
    """

    name = "graph"
    node_id = "graph.retrieve"
    span_name = "graph"
    order = 100
    requires_flag = "graph_retrieval_on"
    requires_component = "graph_retriever"

    @staticmethod
    def selected(state: RetrievalState) -> bool:
        return state.route.target in GRAPH_TARGETS

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate:
        if self.selected(state) and self.engaged(state, ctx):
            return Gate.running(configured_route=state.route.target)
        return Gate.silent()

    def fatal_error(
        self, exc: BaseException, state: RetrievalState, ctx: StageContext
    ) -> Optional[Failure]:
        if not self.selected(state):
            return None
        return Failure(
            error_type=type(exc).__name__, reason="graph_failed", recoverable=False
        )

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        services = ctx.services
        route = state.route
        graph_selected = self.selected(state)
        t0 = time.perf_counter()
        if graph_selected:
            route, graph_fact = services._run_graph_branch(
                route=route,
                search_query=state.search_query,
                candidates=state.candidates,
                acl=ctx.acl,
                tenant=ctx.tenant,
                traces=state.traces,
                serving_context=ctx.serving_context,
            )
        else:
            graph_fact = {"status": "skipped", "reason": "route_not_selected"}
        graph_ms = (time.perf_counter() - t0) * 1000.0
        outcome = StageOutcome().set_span("graph", graph_ms)
        status = graph_fact["status"]
        reason = str(graph_fact.get("reason") or "")
        added_count = int(graph_fact.get("added_count") or 0)
        if status == "skipped":
            outcome.skip(reason)
        elif status == "failed":
            # 可恢复失败 -> 驱动器随之发 degraded；effective_route 说的是"这次
            # 改用 hybrid 了"，是降级事实的一部分，所以跟着失败一起申报。
            outcome.fail(
                str(graph_fact["error_type"]),
                reason,
                recoverable=True,
                duration_ms=graph_ms,
                degrade_attrs={"effective_route": "hybrid"},
                added_count=added_count,
            )
        else:
            outcome.complete(graph_ms, added_count=added_count)
        if status != "failed" and graph_selected and route.degraded:
            outcome.degrade(reason, effective_route="hybrid")
        if graph_selected:
            outcome.announce_route(
                "graph.retrieve",
                route,
                effective="hybrid" if route.degraded else route.target,
            )
        return outcome.derive(state.with_route(route))


class RerankStage(_Stage):
    """11. 重排序：**这个阶段的产出是"分数量纲"，不只是顺序**。

    ``reranked`` 只在"真的调用了 reranker 且返回的分数数量与候选数一致"时为
    True；其余每一条路（``disabled`` / ``unavailable`` / ``circuit_open`` 三种
    停用，加上"分数数量不符"与"调用抛 RerankError"两种失败）留在
    ``item.score`` 上的都是 Milvus 的 RRF 融合分——只由名次决定，不含相似度
    信息。弃权闸门读的就是这个标记（``verify/abstention.py``），所以这里**不得
    靠猜**：标记由本阶段独占产出。

    三条停用路径里 ``unavailable`` 与 ``circuit_open`` 还要各发一次
    ``degraded``（``disabled`` 是用户的选择，不算降级）；两条失败路径走
    "可恢复失败"，由驱动器随之发 ``degraded``。指标与告警日志由
    :func:`report_rerank_degraded` 负责——降级可以，降得无声无息不行。
    """

    name = "rerank"
    node_id = "rerank"
    span_name = "rerank"
    order = 110
    requires_flag = "rerank_on"
    requires_component = "reranker"

    def skip_reason(self, state: RetrievalState, ctx: StageContext) -> Optional[str]:
        services = ctx.services
        if not ctx.p.rerank_on:
            return "disabled"
        if services.reranker is None:
            return "unavailable"
        if services.reranker_cb is not None and not services.reranker_cb.allow():
            return "circuit_open"
        return None

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate:
        reason = self.skip_reason(state, ctx)
        if reason is not None:
            return Gate.skipped(reason)
        return Gate.running(candidate_count=len(state.candidates))

    def fatal_error(
        self, exc: BaseException, state: RetrievalState, ctx: StageContext
    ) -> Optional[Failure]:
        return Failure(
            error_type=type(exc).__name__, reason="rerank_failed", recoverable=False
        )

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        services = ctx.services
        traces = state.traces
        candidates = state.candidates
        skip = self.skip_reason(state, ctx)
        t0 = time.perf_counter()
        # 只有走到「分数数量对得上」那一支才算重排真正生效——其余每一条路留在
        # item.score 里的都是 RRF 融合分，量纲和 reranker 相关度完全不同。
        reranked = False
        failure_reason: Optional[str] = None
        error_type = "RerankFailure"
        if skip == "disabled":
            traces.append("rerank 关闭：保留召回顺序（RRF 融合序）")
        elif skip == "unavailable":
            report_rerank_degraded("未注入 reranker", "rerank_on=True 但 reranker 为 None")
            traces.append("rerank 未注入：保留召回顺序（RRF 融合序）")
        elif skip == "circuit_open":
            report_rerank_degraded("熔断打开", f"{services.reranker_cb.name} 冷却中")
            traces.append("rerank 熔断打开：快速跳过，使用召回顺序")
        else:
            try:
                scores = services.reranker.rerank(
                    state.search_query, [c.chunk for c in candidates]
                )
                if len(scores) != len(candidates):
                    if services.reranker_cb is not None:
                        services.reranker_cb.record_failure()
                    report_rerank_degraded(
                        "分数数量不符",
                        f"期望 {len(candidates)} 个，实际 {len(scores)} 个",
                    )
                    traces.append("rerank 失败：分数数量不符，使用原始顺序")
                    failure_reason = "invalid_score_count"
                    error_type = "InvalidRerankScoreCount"
                else:
                    if services.reranker_cb is not None:
                        services.reranker_cb.record_success()
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
                if services.reranker_cb is not None:
                    services.reranker_cb.record_failure()
                report_rerank_degraded("调用失败", f"{type(exc).__name__}: {exc}")
                traces.append("rerank 失败，使用原始顺序")
                failure_reason = "rerank_failed"
                error_type = type(exc).__name__
        if not reranked:
            # 说清楚代价：这不只是"顺序没优化"，而是**检索分数这把尺子作废了**。
            # 有稠密余弦时换那把尺子接着量；没有时才真正让这道闸空着。
            if any(rc.dense_cosine is not None for rc in candidates):
                traces.append("rerank 未生效：分数为 RRF 融合序，改用稠密余弦参与弃权判定")
            else:
                traces.append("rerank 未生效：分数为 RRF 融合序，检索阈值本轮不参与弃权判定")
        rerank_ms = (time.perf_counter() - t0) * 1000.0
        outcome = StageOutcome().set_span("rerank", rerank_ms)
        if skip is None:
            if failure_reason is not None:
                outcome.fail(
                    error_type,
                    failure_reason,
                    recoverable=True,
                    duration_ms=rerank_ms,
                    candidate_count=len(candidates),
                )
            else:
                outcome.complete(rerank_ms, candidate_count=len(candidates))
        elif skip != "disabled":
            outcome.degrade(skip)
        return outcome.derive(
            state.with_candidates(candidates).with_reranked(reranked)
        )


class SentenceWindowStage(_Stage):
    """12. SentenceWindow 父块回取（small-to-big）：命中子块展开为父块。

    位置在重排之后、裁剪之前：展开依赖命中项的 rank / score 继承，重排后展开
    才能让父块以完整上下文参与最终 top_k 裁剪。展开会 ``get_chunks_by_ids`` 回
    取父块——**按 id 直取、绕过 filter_expr**，所以这里保留一道防御性租户二次
    过滤（能拿到 tenant 的唯一位置），且 :class:`DatasetScopeStage` 必须排在后面。
    """

    name = "sentence_window"
    node_id = "sentence_window"
    span_name = "sentence_window"
    order = 120
    requires_flag = "sentence_window_on"
    requires_component = "sentence_window"

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate:
        reason = self.skip_reason(state, ctx)
        if reason is not None:
            return Gate.skipped(reason)
        return Gate.running(input_count=len(state.candidates))

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        outcome = StageOutcome()
        if not self.engaged(state, ctx):
            return outcome.derive(state)
        traces = state.traces
        candidates = state.candidates
        failure_reason: Optional[str] = None
        error_type = "SentenceWindowFailure"
        t0 = time.perf_counter()
        try:
            expanded = self.component(ctx).expand(candidates)
            if isinstance(expanded, list):
                # 防御性租户二次过滤：展开可能回取父块（get_chunks_by_ids），
                # 父块必须归属当前租户（空 tenant_id 视为存量 / 桩数据放行）
                candidates = [
                    rc
                    for rc in expanded
                    if not ctx.tenant
                    or not rc.chunk.tenant_id
                    or rc.chunk.tenant_id == ctx.tenant
                ]
            else:
                traces.append("sentence_window 展开返回异常类型，保留原结果")
                failure_reason = "invalid_output"
                error_type = "InvalidSentenceWindowOutput"
        except Exception as exc:  # noqa: BLE001 - 降级：保留原结果
            traces.append(f"sentence_window 展开失败，保留原结果: {exc}")
            failure_reason = "expand_failed"
            error_type = type(exc).__name__
        sentence_ms = (time.perf_counter() - t0) * 1000.0
        outcome.set_span("sentence_window", sentence_ms)
        if failure_reason is not None:
            outcome.fail(
                error_type,
                failure_reason,
                recoverable=True,
                duration_ms=sentence_ms,
                output_count=len(candidates),
            )
        else:
            outcome.complete(sentence_ms, output_count=len(candidates))
        return outcome.derive(state.with_candidates(candidates))


class DatasetScopeStage(_Stage):
    """13. 知识库范围收口：**唯一天花板式作用域闸门**。

    图谱分支（``get_chunks_by_ids``）与 sentence_window 父块回取都按 id 直取，
    绕过了向量检索的 ``filter_expr``，可能带回别的知识库的 chunk；这里是唯一的
    兜底收口，所以顺序上必须在它们之后、在裁剪之前 —— ``retrieval_stage_order``
    把这条钉成注册期校验，不靠注册顺序侥幸。

    语义与 ``build_dataset_filter`` 严格一致（``dataset_id == x``），因此空
    ``dataset_id`` 的存量行在指定知识库时同样被排除——两条路径判定必须一致，
    否则主路径过滤掉的行会从旁路漏回来。
    """

    name = "dataset_scope"
    node_id = "dataset_scope"
    order = 130

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate:
        if ctx.dataset:
            return Gate.running(scoped=True, input_count=len(state.candidates))
        return Gate.skipped("unscoped", scoped=False)

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        outcome = StageOutcome()
        if not ctx.dataset:
            return outcome.derive(state)
        traces = state.traces
        before = len(state.candidates)
        candidates = [rc for rc in state.candidates if rc.chunk.dataset_id == ctx.dataset]
        if len(candidates) != before:
            traces.append(
                f"知识库收口：{before - len(candidates)} 条越界 chunk 被剔除"
                f"（dataset_id != {ctx.dataset!r}）"
            )
        outcome.complete(
            None,
            scoped=True,
            input_count=before,
            output_count=len(candidates),
            removed_count=before - len(candidates),
        )
        return outcome.derive(state.with_candidates(candidates))


class TruncateStage(_Stage):
    """14. 裁剪：取前 ``top_k`` 条。恒开，无降级路径。"""

    name = "truncate"
    node_id = "truncate"
    order = 140

    def eligible(self, state: RetrievalState, ctx: StageContext) -> Gate:
        return Gate.running(input_count=len(state.candidates), top_k=ctx.p.top_k)

    def run(self, state: RetrievalState, ctx: StageContext) -> StageOutcome:
        chunks = state.candidates[: ctx.p.top_k]
        outcome = StageOutcome()
        outcome.complete(
            None,
            input_count=len(state.candidates),
            output_count=len(chunks),
            top_k=ctx.p.top_k,
        )
        return outcome.derive(state.with_chunks(chunks))


# --------------------------------------------------------------------------- #
# 可选策略装配注册表（rag.py 的装配点与阶段闸门同源）
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ComponentDeps:
    """构造可选组件时的环境（构造本身不联网、不加载模型）。"""

    settings: Any
    embedder: Any
    milvus: Any


@dataclass(frozen=True)
class OptionalStrategy:
    """一个可选策略的装配声明：叫什么、看哪个开关、怎么造。"""

    name: str
    flag_section: str
    flag: str
    build: Callable[[ComponentDeps], Any]


OPTIONAL_STRATEGIES: ProviderRegistry[None, OptionalStrategy] = ProviderRegistry(
    "optional retrieval strategy"
)


def register_optional_strategy(strategy: OptionalStrategy, *, replace: bool = False) -> None:
    OPTIONAL_STRATEGIES.register(
        strategy.name, lambda _config, _s=strategy: _s, replace=replace
    )


def is_strategy_enabled(name: str, settings: Any) -> bool:
    strategy = OPTIONAL_STRATEGIES.create(name, None)
    section = getattr(settings, strategy.flag_section, None)
    return bool(getattr(section, strategy.flag, False))


def build_optional_components(deps: ComponentDeps, log_warning: Callable[[str, Any], None]) -> dict[str, Any]:
    """按注册表逐个构造可选组件；任一失败只让该策略退化为不可用。

    每个组件只在对应开关打开时构造：关闭时返回 None，阶段里的
    ``if 开关 and 组件 is not None`` 判断自然短路，零开销。构造本身不联网、
    不加载模型（LLM 客户端与模板均为惰性），但仍逐个包在 try 里——任一组件
    构造失败（模板缺失、可选依赖未装等）只让该策略不可用，绝不影响其余策略与
    主链路可用性。
    """
    components: dict[str, Any] = {}
    for name in OPTIONAL_STRATEGIES.names():
        strategy = OPTIONAL_STRATEGIES.create(name, None)
        if not is_strategy_enabled(name, deps.settings):
            components[name] = None
            continue
        try:
            components[name] = strategy.build(deps)
        except Exception as exc:  # noqa: BLE001 - 单个策略不可用不影响主链路
            log_warning(name, exc)
            components[name] = None
    return components


# --------------------------------------------------------------------------- #
# 内置可选策略（装配声明与阶段申报的组件名同源）
# --------------------------------------------------------------------------- #
_PROMPTS_ROOT = Path(__file__).resolve().parent.parent / "prompts"


def _build_hyde(deps: ComponentDeps) -> Any:
    from core.llm import create_client
    from retrieval.query_enhance import HydeGenerator

    return HydeGenerator(
        llm_client=create_client(deps.settings.llm.hyde, slot="hyde"),
        template_path=_PROMPTS_ROOT / "hyde_v1.txt",
        enabled=True,
    )


def _build_subqueries(deps: ComponentDeps) -> Any:
    from core.llm import create_client
    from retrieval.query_enhance import SubQueryGenerator

    return SubQueryGenerator(
        llm_client=create_client(deps.settings.llm.subqueries, slot="subqueries"),
        template_path=_PROMPTS_ROOT / "subqueries_v1.txt",
        enabled=True,
    )


def _build_stepback(deps: ComponentDeps) -> Any:
    from core.llm import create_client
    from retrieval.query_enhance import StepbackGenerator

    return StepbackGenerator(
        llm_client=create_client(deps.settings.llm.stepback, slot="stepback"),
        template_path=_PROMPTS_ROOT / "stepback_v1.txt",
        enabled=True,
    )


def _build_sentence_window(deps: ComponentDeps) -> Any:
    from retrieval.sentence_window import SentenceWindowExpander

    # 依赖入库期的父子块切分：只有 parent_child 模式会写 parent_chunk_id，
    # 用 recursive / qa 切分入库的文档展开时取不到子块，组件内部会原样返回。
    return SentenceWindowExpander(milvus=deps.milvus, replace=True, enabled=True)


def _build_graph_retriever(deps: ComponentDeps) -> Any:
    from core.graph_store_registry import create_graph_store
    from core.llm import create_client
    from retrieval.graph_retriever import GraphRetriever

    return GraphRetriever(
        store=create_graph_store(deps.settings),
        embedder=deps.embedder,
        # 图谱关系重排复用 judge 槽位（温度 0，确定性输出）；
        # graph.use_llm_rerank 关闭时不传 LLM，按检索分数排序。
        llm=(
            create_client(deps.settings.llm.judge, slot="judge")
            if deps.settings.graph.use_llm_rerank
            else None
        ),
    )


def _build_auto_filter(deps: ComponentDeps) -> Any:
    from retrieval.auto_filter import create_auto_filter

    return create_auto_filter(deps.settings)


BUILTIN_OPTIONAL_STRATEGIES: tuple[OptionalStrategy, ...] = (
    OptionalStrategy("hyde", "pipeline", "hyde_on", _build_hyde),
    OptionalStrategy("subqueries", "pipeline", "subqueries_on", _build_subqueries),
    OptionalStrategy("stepback", "pipeline", "stepback_on", _build_stepback),
    OptionalStrategy(
        "sentence_window", "pipeline", "sentence_window_on", _build_sentence_window
    ),
    OptionalStrategy(
        "graph_retriever", "pipeline", "graph_retrieval_on", _build_graph_retriever
    ),
    # auto_filter 的开关不在 pipeline 段而在 catalog 段——这个差异是声明数据，
    # 不再是装配点里多出来的一条 if。
    OptionalStrategy("auto_filter", "catalog", "auto_filter_on", _build_auto_filter),
)


def register_builtin_optional_strategies() -> None:
    for strategy in BUILTIN_OPTIONAL_STRATEGIES:
        register_optional_strategy(strategy, replace=True)


register_builtin_optional_strategies()


# --------------------------------------------------------------------------- #
# 内置阶段清单（阶段类都定义完才登记；import 本模块即生效）
# --------------------------------------------------------------------------- #
#: 已迁入注册表的阶段。未列出的阶段仍在 ``RetrievalPipeline.run()`` 里内联。
BUILTIN_RETRIEVAL_STAGES: tuple[RetrievalStage, ...] = (
    ComplexityGateStage(),
    RewriteStage(),
    RouteStage(),
    EnhancerPrefetchStage(),
    HydeStage(),
    EmbedStage(),
    SearchFilterStage(),
    SearchStage(),
    ServingFenceStage("serving_fence.initial", "milvus.initial", 65),
    SubQueriesStage(),
    StepbackStage(),
    ServingFenceStage("serving_fence.enhanced", "milvus.enhanced", 85),
    DiversityStage(),
    GraphStage(),
    RerankStage(),
    SentenceWindowStage(),
    ServingFenceStage("serving_fence.rerank_final", "rerank.final", 125),
    DatasetScopeStage(),
    TruncateStage(),
)


def register_builtin_retrieval_stages() -> None:
    """(Re)register the built-in stages; safe to call more than once."""
    for stage in BUILTIN_RETRIEVAL_STAGES:
        register_retrieval_stage(stage, replace=True)


register_builtin_retrieval_stages()
