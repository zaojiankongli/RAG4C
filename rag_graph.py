"""LangGraph StateGraph 图编排（``rag.answer_query`` 的图优先替代路径）。

与 :mod:`rag` 的顺序编排（``rag._answer_sequential``）**行为完全一致**：
同 8 个检索 span、同双重阈值弃权门、同二轮检索循环，QueryResult 结构 /
route / traces 语义逐字段对齐。区别仅在内部编排方式：本模块用有向图 +
条件边显式建模「检索 -> 弃权门 -> 生成验证 -> 二轮补救 -> 弃权门 -> 收尾」。

关键设计（图异常兜底）：

- 模块级惰性导入 langgraph：未安装时 :data:`_LANGGRAPH_AVAILABLE` 为 False，
  模块本身仍可离线 import（仅 :func:`build_graph` 返回 None）。
- 每个节点返回 dict 做状态增量更新（纯函数风格，便于单测）；节点内的
  **业务失败**（如检索抛异常、弃权门命中）一律产出弃权 QueryResult 存入
  ``state["final"]`` 后经条件边短路到 END，绝不向图外抛错。
- 节点**未捕获**的异常（结构缺陷 / 意外编码错误）会让 ``graph.invoke``
  抛出，由 :func:`run_graph_answer` 统一捕获：记录 ``graph.invoke.errors``
  指标 + ``exception`` 日志并返回 None，触发 ``rag.answer_query`` 回退
  顺序编排——绝不向调用方抛异常。
- 二轮检索用 LangGraph 原生环（``generate_verify -> retrieve_again ->
  generate_verify`` 条件边回环）实现，``rounds`` 计数由 retrieve_again
  累加，``MAX_ROUNDS`` 从 :mod:`rag_common` 导入，与顺序版至多补一轮一致。

本模块依赖 ``core.metrics`` / ``core.observability`` / ``core.tracing`` 埋点；
共享纯函数（``MAX_ROUNDS`` / ``merge_chunks`` / ``evidence_chunks`` /
``generate_and_verify``）从 :mod:`rag_common` 导入，数据契约来自
``models.schemas``，不 import :mod:`rag`（避免跨模块私有耦合与依赖链拉起）。
可观测性整体注册（``core.observability.setup_observability``）由
``rag.answer_query`` 装配时调用，本模块只负责自身埋点，不重复注册。
"""
from __future__ import annotations

import threading
from typing import Any, TypedDict

from config.settings import get_settings
from core.metrics import get_metrics
from core.observability import get_logger
from core.tracing import trace_session
from models.schemas import QueryResult
from rag_common import (
    MAX_ROUNDS,
    evidence_chunks,
    generate_and_verify,
    merge_chunks,
    second_round_can_help,
)
from retrieval.pipeline import dense_cosines_of

try:
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - langgraph 未安装的离线场景
    END = START = None
    StateGraph = None  # type: ignore[assignment,misc]

#: langgraph 是否可用（False 时 build_graph / run_graph_answer 返回 None）
_LANGGRAPH_AVAILABLE: bool = StateGraph is not None

# ---------------------------------------------------------------------------
# 编译图缓存（性能优化）
#
# comp 组件是 ``rag.get_pipeline`` 的 lru_cache 结果，同一进程内不变；
# 而 build_graph 只按拓扑构图（节点经 state 读取 comp，构图本身不捕获
# comp 引用），因此编译结果与具体 comp 内容无关，可按 id(comp) 缓存。
#
# 缓存安全理由（可重入）：CompiledStateGraph.invoke 每次调用都会用传入的
# initial state 新建一次独立执行（状态只在单次 invoke 内部流转，节点返回
# 的增量 dict 不落任何模块级共享变量），故同一编译图可被多个线程并发
# invoke，互不串扰——缓存命中直接返回同一图对象不会引入跨请求状态污染。
# ---------------------------------------------------------------------------

#: 已编译的 StateGraph（None 表示尚未编译）
_compiled_graph: Any | None = None
#: 编译时对应的组件 id（id(comp) 变化则重新构图）
_compiled_comp_id: int | None = None
_compiled_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 状态定义
# ---------------------------------------------------------------------------


class _State(TypedDict):
    """StateGraph 全量状态（每次 invoke 独立初始化，节点返回增量更新）。

    Attributes:
        query: 用户原始查询。
        acl: 访问控制列表（None 不过滤）。
        retry: 是否允许二轮检索补救。
        tenant_id: 已解析的租户标识（透传给检索管线，全链路强制隔离）。
        dataset_id: 知识库标识（空串 = 不限知识库），透传给检索管线。
        comp: 组件引用（:func:`rag.get_pipeline` 返回的字典）。
        retrieval: 第一轮检索结果（:class:`retrieval.pipeline.RetrievalResult`）。
        chunks: 当前累计证据（list[RetrievedChunk]，含二轮合并）。
        retrieval_scores: 当前证据的检索分数列表。
        scores_comparable: ``retrieval_scores`` 是否与 ``retrieval_score_threshold``
            同尺。rerank 未生效时残留的是 RRF 融合分（只由名次决定），不可比。
        dense_cosines: 当前证据的真实稠密余弦（查询向量 × 命中向量）。**另一把
            尺子**，只在 ``scores_comparable`` 为 False 时顶上。跟
            ``retrieval_scores`` 一样做成 state 字段而不是在闸门节点现算，是为了
            保证两把尺子量的永远是同一批 chunk——它们必须在同一个节点里、由同一
            个 ``chunks`` 一起算出来，否则会出现"分数取自合并后的证据、余弦取自
            合并前"这种错位。空列表 = 这一路信号本次没有。
        rounds: 已执行的检索轮数（从 1 起，二轮补救后为 2）。
        generated: 最近一轮生成结果（GeneratedAnswer | None）。
        verification: 最近一轮验证结果（VerificationResult | None）。
        traces: 业务流水（检索 / 弃权 / 二轮补救说明）。
        route: 最终路由目标（首轮 retrieval.route.target）。
        added_evidence: 二轮检索是否新增证据（内部条件路由标记）。
        final: 已产出的 QueryResult（非 None 即短路返回，后置节点不再执行）。
    """

    query: str
    acl: list[str] | None
    retry: bool
    tenant_id: str
    dataset_id: str
    comp: dict[str, Any]
    retrieval: Any | None
    chunks: list[Any]
    retrieval_scores: list[float]
    scores_comparable: bool
    dense_cosines: list[float]
    rounds: int
    generated: Any | None
    verification: Any | None
    traces: list[str]
    route: str
    added_evidence: bool
    final: QueryResult | None


# ---------------------------------------------------------------------------
# 节点（返回 dict 做状态增量更新；业务失败产出弃权 final 而非抛错）
# ---------------------------------------------------------------------------


def _node_retrieve(state: _State) -> dict[str, Any]:
    """第一轮检索；失败产出弃权结果并短路（route 固定 hybrid，与顺序版一致）。"""
    comp = state["comp"]
    query = state["query"]
    acl = state["acl"]
    try:
        retrieval = comp["retrieval"].run(
            query, acl, tenant_id=state["tenant_id"], dataset_id=state["dataset_id"]
        )
    except Exception as exc:  # noqa: BLE001 - Milvus 等不可用时降级弃权
        return {
            "final": QueryResult(
                query=query,
                answer="",
                abstained=True,
                route="hybrid",
                traces=[f"检索失败（弃权）: {exc}"],
            )
        }
    traces = list(retrieval.traces)
    chunks = list(retrieval.chunks)
    return {
        "retrieval": retrieval,
        "chunks": chunks,
        "retrieval_scores": [rc.score for rc in chunks],
        "scores_comparable": retrieval.reranked,
        "dense_cosines": dense_cosines_of(chunks),
        "traces": traces,
        "route": retrieval.route.target,
    }


def _node_abstain_retrieval(state: _State) -> dict[str, Any]:
    """检索阶段弃权门；命中则产出弃权 final 短路，否则原样放行。"""
    abstain, reason = state["comp"]["gate"].decide(
        state["retrieval_scores"],
        None,
        retrieval_scores_comparable=state["scores_comparable"],
        dense_cosines=state["dense_cosines"],
    )
    if not abstain:
        return {}
    traces = state["traces"] + [f"弃权: {reason}"]
    return {
        "traces": traces,
        "final": QueryResult(
            query=state["query"],
            answer="",
            abstained=True,
            route=state["route"],
            traces=traces,
        ),
    }


def _node_generate_verify(state: _State) -> dict[str, Any]:
    """一轮生成 + 三层验证，验证备注追加进 traces（与顺序版一致）。"""
    generated, verification = generate_and_verify(
        state["comp"], state["query"], state["chunks"]
    )
    return {
        "generated": generated,
        "verification": verification,
        "traces": state["traces"] + list(verification.notes),
    }


def _node_retrieve_again(state: _State) -> dict[str, Any]:
    """二轮检索补救；失败 / 无新增证据时直接转最终弃权检查（不重复生成）。"""
    comp = state["comp"]
    query = state["query"]
    acl = state["acl"]
    rounds = state["rounds"] + 1
    # 判定放在节点里而不是条件边上：条件边只能返回下一站的名字，改不了
    # traces——那样这一轮就被**静默**省掉了，和 ⑨ 里批评过的静默降级同一个
    # 毛病。走一趟空节点不花钱（没有任何 I/O），换来流水里说得清楚。
    if not second_round_can_help(comp.get("settings") or get_settings()):
        get_metrics().incr("query.round2.skipped")
        return {
            "rounds": state["rounds"],
            "traces": state["traces"]
            + ["二轮检索已跳过：查询增强（HyDE/子查询/Stepback）全关，重跑结果必然相同"],
            "added_evidence": False,
        }
    try:
        retrieval2 = comp["retrieval"].run(
            query, acl, tenant_id=state["tenant_id"], dataset_id=state["dataset_id"]
        )
    except Exception as exc:  # noqa: BLE001 - 补救失败沿用第一轮结果
        return {
            "rounds": rounds,
            "traces": state["traces"] + [f"二轮检索失败（沿用第一轮结果）: {exc}"],
            "added_evidence": False,
        }
    merged = merge_chunks(state["chunks"], retrieval2.chunks)
    if len(merged) == len(state["chunks"]):
        # 无新增证据：再跑也无益，避免死循环
        return {
            "rounds": rounds,
            "traces": state["traces"] + ["二轮检索无新增证据，停止补救"],
            "added_evidence": False,
        }
    return {
        "rounds": rounds,
        "chunks": merged,
        "retrieval_scores": [rc.score for rc in merged],
        # 合并后混了两轮的分数，任一轮没重排则整体不可比。
        "scores_comparable": state["scores_comparable"] and retrieval2.reranked,
        # 余弦必须跟上面那行的分数量的是同一批 chunk，所以同样对 merged 现算。
        "dense_cosines": dense_cosines_of(merged),
        "traces": state["traces"]
        + list(retrieval2.traces)
        + [f"二轮检索已执行（第 {rounds} 轮，合并 {len(merged)} 条证据）"],
        "added_evidence": True,
    }


def _node_abstain_final(state: _State) -> dict[str, Any]:
    """验证阶段弃权门（检索分数 + 蕴含分数）；命中则产出弃权 final 短路。"""
    verification = state["verification"]
    if verification is None:
        return {}
    entailment_scores = verification.gate_entailment_scores()
    if state["generated"] is None:
        # 生成整个失败了。没有答案就没什么可"支撑"的——弃权门问的是"证据够不够"，
        # 回答不了"有没有答案"。从前这条路径被掩盖着：生成失败 -> 蕴含分为空 ->
        # 编排器无条件传 [] -> 门顺手弃权。阶段 2 把 [] 改成 None 之后遮蔽消失，
        # 真面目是 **abstained=False + answer=""**：看起来成功、实际空白的响应。
        abstain, reason = True, "生成失败"
    else:
        abstain, reason = state["comp"]["gate"].decide(
            state["retrieval_scores"],
            entailment_scores,
            retrieval_scores_comparable=state["scores_comparable"],
            dense_cosines=state["dense_cosines"],
        )
    if not abstain:
        return {}
    traces = state["traces"] + [f"弃权: {reason}"]
    return {
        "traces": traces,
        "final": QueryResult(
            query=state["query"],
            answer="",
            abstained=True,
            route=state["route"],
            traces=traces,
        ),
    }


def _node_finalize(state: _State) -> dict[str, Any]:
    """构造最终结果（引用 / 弃权关闭 / 证据 verdict），与顺序版逐字段一致。"""
    verification = state["verification"]
    generated = state["generated"]
    return {
        "final": QueryResult(
            query=state["query"],
            answer=generated.answer if generated is not None else "",
            citations=verification.citations,
            verdict={"evidence_chunks": evidence_chunks(state["chunks"])},
            abstained=False,
            route=state["route"],
            traces=state["traces"],
        )
    }


# ---------------------------------------------------------------------------
# 条件路由（返回目标节点名；final 已产出 -> END 短路）
# ---------------------------------------------------------------------------


def _route_after_retrieve(state: _State) -> str:
    return END if state["final"] is not None else "abstain_retrieval"


def _route_after_abstain_retrieval(state: _State) -> str:
    return END if state["final"] is not None else "generate_verify"


def _route_after_generate_verify(state: _State) -> str:
    verification = state["verification"]
    should_retry = (
        state["retry"]
        and state["rounds"] < MAX_ROUNDS
        and (verification.missing_evidence or not verification.supported)
    )
    return "retrieve_again" if should_retry else "abstain_final"


def _route_after_retrieve_again(state: _State) -> str:
    return "generate_verify" if state["added_evidence"] else "abstain_final"


def _route_after_abstain_final(state: _State) -> str:
    return END if state["final"] is not None else "finalize"


# ---------------------------------------------------------------------------
# 构图与执行
# ---------------------------------------------------------------------------


def build_graph(comp: dict[str, Any]):
    """构建并编译 StateGraph（编译结果按 id(comp) 缓存，命中直接复用）。

    Args:
        comp: :func:`rag.get_pipeline` 返回的组件字典（节点经 state 读取，
            本函数仅按拓扑构图，不捕获 comp 引用）。

    Returns:
        langgraph 已编译图（``CompiledStateGraph``）；langgraph 未安装时
        返回 None（调用方回退顺序编排）。
    """
    global _compiled_graph, _compiled_comp_id
    if not _LANGGRAPH_AVAILABLE:
        return None
    if _compiled_graph is not None and _compiled_comp_id == id(comp):
        # 缓存命中：同一组件反复构图无意义（构图不捕获 comp 引用，
        # 且 CompiledStateGraph.invoke 可重入），直接复用编译结果。
        return _compiled_graph
    with _compiled_lock:
        if _compiled_graph is not None and _compiled_comp_id == id(comp):
            return _compiled_graph
        builder = StateGraph(_State)
        builder.add_node("retrieve", _node_retrieve)
        builder.add_node("abstain_retrieval", _node_abstain_retrieval)
        builder.add_node("generate_verify", _node_generate_verify)
        builder.add_node("retrieve_again", _node_retrieve_again)
        builder.add_node("abstain_final", _node_abstain_final)
        builder.add_node("finalize", _node_finalize)

        builder.add_edge(START, "retrieve")
        builder.add_conditional_edges("retrieve", _route_after_retrieve)
        builder.add_conditional_edges("abstain_retrieval", _route_after_abstain_retrieval)
        builder.add_conditional_edges("generate_verify", _route_after_generate_verify)
        builder.add_conditional_edges("retrieve_again", _route_after_retrieve_again)
        builder.add_conditional_edges("abstain_final", _route_after_abstain_final)
        builder.add_edge("finalize", END)
        _compiled_graph = builder.compile()
        _compiled_comp_id = id(comp)
        return _compiled_graph


def _reset_graph_cache() -> None:
    """测试钩子：清空编译图缓存（切换不同 comp 前调用，避免 id 复用误命中）。

    build_graph 的缓存以 ``id(comp)`` 为键；Python 对象被回收后 id 可能被
    复用，测试中用不同 comp 轮流构图时建议先清缓存保证每次真实重编译。
    """
    global _compiled_graph, _compiled_comp_id
    with _compiled_lock:
        _compiled_graph = None
        _compiled_comp_id = None


def run_graph_answer(
    comp: dict[str, Any],
    query: str,
    acl: list[str] | None,
    retry: bool,
    query_id: str,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
) -> QueryResult | None:
    """以图编排执行一次完整问答；任何异常均返回 None 触发调用方回退。

    Args:
        comp: :func:`rag.get_pipeline` 返回的组件字典。
        query: 用户原始查询。
        acl: 访问控制列表（None 不过滤）。
        retry: 是否允许二轮检索补救。
        query_id: 查询标识（贯穿 traces / 指标）。
        tenant_id: 已解析的租户标识（由 ``rag.answer_query`` 调用
            :func:`rag_common.resolve_tenant` 后传入；None 时节点透传
            None 给检索管线，由其按配置解析回退租户）。
        dataset_id: 知识库标识（None / 空串 = 不限知识库），透传给检索管线。

    Returns:
        最终 :class:`models.schemas.QueryResult`（节点级业务失败已产出弃权
        结果，不会返回 None）；langgraph 未安装 / 图构建失败 / 图执行异常
        / 图未产出 final（结构缺陷）时返回 None，交由 ``rag.answer_query``
        回退顺序编排。
    """
    try:
        graph = build_graph(comp)
        if graph is None:
            return None
        initial: _State = {
            "query": query,
            "acl": acl,
            "retry": retry,
            "tenant_id": tenant_id or "",
            "dataset_id": (dataset_id or "").strip(),
            "comp": comp,
            "retrieval": None,
            "chunks": [],
            "retrieval_scores": [],
            "scores_comparable": False,
            "dense_cosines": [],
            "rounds": 1,
            "generated": None,
            "verification": None,
            "traces": [],
            "route": "hybrid",
            "added_evidence": False,
            "final": None,
        }
        with trace_session(query_id) as trace:
            with get_metrics().timing("graph.invoke"):
                state = graph.invoke(initial)
            final = state.get("final")
            if final is None:
                get_logger(__name__).warning(
                    "图编排未产出最终结果（final is None），回退顺序管线"
                )
                return None
            # 与顺序版一致：业务 traces 之后追加 span 列表
            final.traces.extend(trace.as_list())
            return final
    except Exception:  # noqa: BLE001 - 图执行异常一律回退顺序，绝不向调用方抛
        get_metrics().incr("graph.invoke.errors")
        get_logger(__name__).exception("图编排执行异常，回退顺序管线")
        return None


__all__ = ["build_graph", "run_graph_answer", "_reset_graph_cache", "_LANGGRAPH_AVAILABLE"]
