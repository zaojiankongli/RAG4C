# ruff: noqa: E402
"""弹性 / 可观测 / 图编排 / 回退 离线冒烟测试。

完全离线：无网络、无模型、无 Milvus、无真实 LLM（全部使用确定性内存桩，
仅依赖 langgraph（已安装）；未安装时图相关断言自动跳过）。

覆盖断言：
1. 重试（core/retry.py）：成功重试 / 耗尽抛最后异常 / retry_on 过滤 /
   @retryable 装饰器 / on_retry 回调（含回调异常被吞）。
2. 指标（core/metrics.py）：count/mean/p50/p95/p99 秩插值、error_rate、
   快照字段完整。
3. 追踪（core/tracing.py）：span observer 挂钩、observer 异常被吞。
4. 可观测性（core/observability.py）：setup_logging 幂等、bind_query_id
   注入与恢复、setup_observability 联动 span -> metrics。
5. 图编排（rag_graph.py）：完整流程 / 检索失败弃权 / 检索弃权门 /
   二轮检索循环 / 无新增证据停止 / 节点异常回退 None / 验证弃权门。
6. 回退集成（rag.answer_query dispatcher）：图优先 / 图不可用回退 /
   图异常回退 + fallback 指标 / 开关关闭直接顺序。
7. 图/顺序一致性差分（run_graph_answer vs _answer_sequential）：7 个
   确定性场景逐字段差分断言（query/answer/citations/verdict/abstained/
   route + 业务 traces + 检索调用次数），验证两条编排路径行为完全一致。
8. 多租户强制隔离：resolve_tenant 解析规则（enforced=True 空/None 回退
   default_tenant、enforced=False 原样返回、无 tenant 段按强制处理）、
   answer_query 将 tenant_id 透传给图/顺序检索管线、未指定回退 default、
   关闭强制开关时空串透传（不过滤）。

运行：
    python scripts/smoke_resilience.py

退出码：全部通过为 0，任一断言失败为 1。
"""
from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

# 保证从任意工作目录运行都能找到 config / models / core / rag / rag_graph
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import rag
import rag_graph
from config.settings import (
    ObservabilitySettings,
    PipelineSettings,
    Settings,
    TenantSettings,
    resolve_tenant,
)
from core.metrics import get_metrics
from core.observability import (
    bind_query_id,
    current_query_id,
    setup_logging,
    setup_observability,
)
from core.retry import RetryPolicy, retry_call, retryable, reset_retry_stats, retry_stats
from core.tracing import clear_span_observers, register_span_observer, trace_session
from generation.generator import GeneratedAnswer
from models.schemas import Chunk, QueryResult, RetrievedChunk, RouteDecision
from retrieval.pipeline import RetrievalResult
from verify.abstention import AbstentionGate
from verify.verifier import VerificationResult

_NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 确定性桩（图编排）
# ---------------------------------------------------------------------------

def make_chunk(cid: str, doc: str = "d") -> Chunk:
    return Chunk(
        chunk_id=cid, doc_id=doc, text=f"text {cid}", text_hash=f"h{cid}",
        created_at=_NOW, updated_at=_NOW,
    )


def make_rc(cid: str, score: float, rank: int = 1) -> RetrievedChunk:
    return RetrievedChunk(chunk=make_chunk(cid), score=score, rank=rank)


def ret_result(chunks, route_target: str = "hybrid") -> RetrievalResult:
    return RetrievalResult(
        query="q",
        chunks=chunks,
        route=RouteDecision(target=route_target, confidence=0.9),
        traces=[f"retrieve:{len(chunks)}"],
    )


class FakeRetrieval:
    """可配置行为的桩检索器（记录调用、可注入异常）。"""

    def __init__(self, result, calls=None, raise_on_call=None):
        self.result = result
        self.calls = calls if calls is not None else []
        self.raise_on_call = raise_on_call
        self.tenant_calls: list[str] = []
        self.dataset_calls: list[str] = []

    def run(self, query, acl=None, tenant_id=None, dataset_id=None):
        self.calls.append(query)
        self.tenant_calls.append(tenant_id or "")
        self.dataset_calls.append(dataset_id or "")
        if self.raise_on_call is not None:
            raise self.raise_on_call
        return self.result


class _GrowRetrieval(FakeRetrieval):
    """首次返回 [a]，二次返回 [a, b]（新增证据），触发真正的二轮补救。"""

    def __init__(self, first, second):
        super().__init__(None, calls=[])
        self.first = first
        self.second = second

    def run(self, query, acl=None, tenant_id=None, dataset_id=None):
        super().run(query, acl, tenant_id=tenant_id, dataset_id=dataset_id)
        return self.first if len(self.calls) == 1 else self.second


class FakeGenerator:
    def generate(self, query, chunks):
        return GeneratedAnswer(
            answer="答案[1]", citation_ids=[1], evidence=[c.chunk for c in chunks]
        )


class FakeVerifier:
    def __init__(self, supported=True, missing=False, entailment=0.9):
        self.supported = supported
        self.missing = missing
        self.entailment = entailment

    def verify(self, answer, evidence_chunks, strict=None):
        return VerificationResult(
            citations=[],
            supported=self.supported,
            missing_evidence=self.missing,
            entailment_scores={
                f"c{i}": self.entailment for i in range(len(evidence_chunks))
            },
        )


class _BoomGate:
    """decide 抛异常：模拟节点未捕获异常（图执行失败 -> 回退信号）。"""

    def decide(self, *args, **kwargs):
        raise RuntimeError("gate boom")


class _EnhancedPipelineSettings:
    """最小 settings 桩：只提供 ``second_round_can_help`` 要看的三个开关。

    二轮补救现在只在带 LLM 的查询增强（HyDE / 子查询 / Stepback）打开时才跑
    ——三者全关时检索是确定性的，重跑必然原样返回，那一轮纯属白花钱。
    测二轮循环本身就得把开关打开，否则测的是"跳过"而不是"循环"。
    """

    class pipeline:  # noqa: N801 - 仅作命名空间，模仿 Settings.pipeline
        hyde_on = True
        subqueries_on = False
        stepback_on = False


def make_comp(retrieval, verifier=None, gate=None, *, second_round=False) -> dict:
    comp = {
        "retrieval": retrieval,
        "generator": FakeGenerator(),
        "verifier": verifier or FakeVerifier(),
        "gate": gate or AbstentionGate(retrieval_threshold=0.3, entailment_threshold=0.6),
    }
    if second_round:
        comp["settings"] = _EnhancedPipelineSettings()
    return comp


# ---------------------------------------------------------------------------
# 断言工具
# ---------------------------------------------------------------------------

class Checker:
    """逐步断言：失败立即抛出，由 main 捕获并置退出码 1。"""

    def __init__(self) -> None:
        self.steps = 0

    def ok(self, name: str) -> None:
        self.steps += 1
        print(f"[PASS] {name}")

    def check(self, cond: bool, name: str, detail: str = "") -> None:
        if not cond:
            raise AssertionError(f"{name} 失败：{detail}")
        self.ok(name)


# ---------------------------------------------------------------------------
# 差分一致性工具（分区 7：图编排 vs 顺序编排）
# ---------------------------------------------------------------------------

#: span 计时项格式："name:xx.xxms"（如 "retrieval:1.50ms" / "graph.invoke:2.30ms"）。
#: 两条路径的 span 是真实耗时（天然不同），差分断言只比较过滤后的业务项。
_SPAN_RE = re.compile(r"^[\w.]+:\d+\.\d+ms$")


def biz_traces(traces: list[str]) -> list[str]:
    """过滤 span 计时项，只保留业务 traces（顺序与数量都须与另一路径一致）。"""
    return [t for t in traces if not _SPAN_RE.match(t)]


def diff_pair(
    retrieval_factory,
    verifier_factory=None,
    gate=None,
    retry: bool = True,
    query: str = "q",
    second_round: bool = False,
):
    """同一场景跑图 / 顺序两条路径，各用一份全新桩（避免有状态桩串扰）。

    Args:
        retrieval_factory: 无参工厂，每次调用生成一份全新检索桩（独立计数）。
        verifier_factory: 无参工厂，每次调用生成一份全新验证桩（None 用默认）。
        gate: 弃权门（无状态，可共享；None 用默认 AbstentionGate）。
        retry: 是否允许二轮检索补救。
        query: 查询文本。
        second_round: 是否打开查询增强开关，使二轮补救真的会执行
            （见 :class:`_EnhancedPipelineSettings`）。默认 False，
            与生产默认配置一致。

    Returns:
        (graph_result, seq_result, graph_retrieval, seq_retrieval)：
        两条路径的 QueryResult 与各自独立计数过的检索桩。
    """
    comp_graph = make_comp(
        retrieval_factory(), verifier_factory() if verifier_factory else None, gate,
        second_round=second_round,
    )
    comp_seq = make_comp(
        retrieval_factory(), verifier_factory() if verifier_factory else None, gate,
        second_round=second_round,
    )
    rg = rag_graph.run_graph_answer(comp_graph, query, None, retry, "diff-graph")
    rs = rag._answer_sequential(comp_seq, query, None, retry, "diff-seq")
    return rg, rs, comp_graph["retrieval"], comp_seq["retrieval"]


def check_diff_equal(ck: Checker, label: str, rg, rs, g_ret, s_ret) -> None:
    """差分核心断言：非 None + 逐字段一致 + 业务 traces 一致 + 检索调用次数一致。

    Args:
        ck: Checker 实例。
        label: 断言名前缀（场景标识）。
        rg: 图路径的 QueryResult（run_graph_answer 返回）。
        rs: 顺序路径的 QueryResult（_answer_sequential 返回）。
        g_ret: 图路径的独立检索桩（记录自身调用次数）。
        s_ret: 顺序路径的独立检索桩。
    """
    ck.check(
        rg is not None and rs is not None,
        f"{label}: 两条路径均返回 QueryResult（非 None）",
        f"graph={rg!r}, seq={rs!r}",
    )
    # QueryResult 逐字段对比（traces 单独比过滤后的业务项，span 计时天然不同）
    ck.check(
        (
            rg.query == rs.query
            and rg.answer == rs.answer
            and rg.citations == rs.citations
            and rg.verdict == rs.verdict
            and rg.abstained == rs.abstained
            and rg.route == rs.route
        ),
        f"{label}: 字段逐项一致（query/answer/citations/verdict/abstained/route）",
        f"graph=({rg.query}, {rg.answer}, {rg.citations}, {rg.verdict}, "
        f"{rg.abstained}, {rg.route}) | seq=({rs.query}, {rs.answer}, "
        f"{rs.citations}, {rs.verdict}, {rs.abstained}, {rs.route})",
    )
    g_traces = biz_traces(rg.traces)
    s_traces = biz_traces(rs.traces)
    ck.check(
        g_traces == s_traces,
        f"{label}: 业务 traces 一致（已滤 span 计时项）",
        f"graph={g_traces} vs seq={s_traces}",
    )
    ck.check(
        len(g_ret.calls) == len(s_ret.calls),
        f"{label}: 检索调用次数一致",
        f"graph={len(g_ret.calls)} vs seq={len(s_ret.calls)}",
    )


def main() -> int:
    ck = Checker()
    try:
        # ------------------------------------------------------------------ #
        # 1. 重试（core.retry）
        # ------------------------------------------------------------------ #
        reset_retry_stats()
        policy = RetryPolicy(
            max_attempts=5, base_delay=0.001, max_delay=0.01,
            jitter=0.0, backoff_factor=2.0,
        )

        # (a) 成功重试
        calls: list[int] = []

        def flaky(x):
            calls.append(x)
            if len(calls) < 3:
                raise ValueError("boom")
            return x * 10

        r = retry_call(flaky, 7, policy=policy)
        ck.check(r == 70 and len(calls) == 3, "(a) 成功重试：3 次尝试后返回 70", str((r, calls)))
        stats = retry_stats()
        ck.check(
            stats["total_attempts"] == 3 and stats["total_retries"] == 2,
            "(a) 重试统计：attempts=3, retries=2",
            str(stats),
        )

        # (b) 耗尽抛最后异常
        calls2: list[int] = []

        def always_fail():
            calls2.append(1)
            raise TimeoutError("exhaust")

        try:
            retry_call(
                always_fail,
                policy=RetryPolicy(max_attempts=3, base_delay=0.001, jitter=0.0),
            )
            raise AssertionError("应抛出 TimeoutError")
        except TimeoutError:
            ck.check(len(calls2) == 3, "(b) 耗尽：3 次后原样抛 TimeoutError", str(len(calls2)))

        # (c) retry_on 过滤
        calls3: list[int] = []

        def wrong_exc():
            calls3.append(1)
            raise KeyError("nope")

        try:
            retry_call(
                wrong_exc,
                policy=RetryPolicy(max_attempts=3, base_delay=0.001, retry_on=(ValueError,)),
            )
            raise AssertionError("应抛出 KeyError")
        except KeyError:
            ck.check(len(calls3) == 1, "(c) retry_on 过滤：KeyError 不重试（1 次）", str(len(calls3)))

        # (d) @retryable 装饰器
        n = [0]

        @retryable(RetryPolicy(max_attempts=4, base_delay=0.001, jitter=0.0))
        def decorated():
            n[0] += 1
            if n[0] < 2:
                raise RuntimeError("retry me")
            return "done"

        ck.check(
            decorated() == "done" and n[0] == 2,
            "(d) @retryable 装饰器：第 2 次成功",
            f"n={n[0]}",
        )

        # (e) on_retry 回调
        events: list[tuple[int, str, float]] = []
        calls4: list[int] = []

        def flaky_cb():
            calls4.append(1)
            if len(calls4) < 2:
                raise ValueError("x")
            return "ok"

        retry_call(
            flaky_cb,
            policy=RetryPolicy(max_attempts=3, base_delay=0.001, jitter=0.0),
            on_retry=lambda attempt, exc, delay: events.append(
                (attempt, type(exc).__name__, delay)
            ),
        )
        ck.check(
            len(events) == 1 and events[0][0] == 1 and events[0][1] == "ValueError",
            "(e) on_retry 回调收到 (attempt=1, ValueError, delay)",
            str(events),
        )

        def bad_cb(attempt, exc, delay):
            raise RuntimeError("callback bug")

        retry_call(
            flaky_cb,
            policy=RetryPolicy(max_attempts=3, base_delay=0.001, jitter=0.0),
            on_retry=bad_cb,
        )
        ck.check(
            len(calls4) >= 2,
            "(e) 回调异常被吞，重试流程不受影响",
            f"calls4={len(calls4)}",
        )

        # ------------------------------------------------------------------ #
        # 2. 指标（core.metrics）
        # ------------------------------------------------------------------ #
        m = get_metrics()
        m.reset()
        for v in range(1, 101):
            m.observe("retrieval.total", float(v))
        for _ in range(10):
            m.incr("graph.invoke.total")
        for _ in range(2):
            m.incr("graph.invoke.errors")
        snap = m.snapshot()
        rt = snap["retrieval.total"]
        ck.check(
            rt["count"] == 100 and abs(rt["mean"] - 50.5) < 1e-6,
            "(f) 指标：count=100, mean=50.5",
            str({k: rt[k] for k in ("count", "mean")}),
        )
        ck.check(
            abs(rt["p50"] - 50.5) < 1e-6 and 94 <= rt["p95"] <= 96 and 98 <= rt["p99"] <= 100,
            "(f) 指标：p50/p95/p99 秩插值",
            f"p50={rt['p50']}, p95={rt['p95']}, p99={rt['p99']}",
        )
        ck.check(
            abs(snap["graph.invoke.errors"]["error_rate"] - 0.2) < 1e-9,
            "(f) 指标：error_rate=0.2",
            str(snap["graph.invoke.errors"]["error_rate"]),
        )
        ck.check(
            all(k in rt for k in ("sum", "min", "max")),
            "(g) 快照含 sum/min/max",
            str(sorted(rt.keys())),
        )

        # ------------------------------------------------------------------ #
        # 3. 追踪 observer（core.tracing）
        # ------------------------------------------------------------------ #
        clear_span_observers()
        collected: list[tuple[str, float]] = []
        register_span_observer(lambda name, ms: collected.append((name, ms)))
        with trace_session("t-1") as trace:
            trace.add_span("retrieval", 1.5)
            trace.add_span("generate", 2.5)
        ck.check(
            [n for n, _ in collected] == ["retrieval", "generate"],
            "(h) span observer 挂钩收到按序 (name, ms)",
            str(collected),
        )
        ck.check(
            trace.as_list() == ["retrieval:1.50ms", "generate:2.50ms"],
            "(h) trace.as_list 格式不变",
            str(trace.as_list()),
        )

        clear_span_observers()

        def bad_observer(name, ms):
            raise RuntimeError("observer bug")

        register_span_observer(bad_observer)
        with trace_session("t-2") as trace:
            trace.add_span("x", 1.0)  # 不应抛异常
        ck.check(
            len(trace.spans) == 1,
            "(i) observer 异常被吞，主流程不受影响",
            str(len(trace.spans)),
        )
        clear_span_observers()

        # ------------------------------------------------------------------ #
        # 4. 可观测性（core.observability）
        # ------------------------------------------------------------------ #
        obs = ObservabilitySettings(log_level="INFO", metrics_enabled=True)
        setup_logging(obs)
        setup_logging(obs)  # 第二次调用
        handlers = [
            h for h in logging.getLogger().handlers
            if h.__class__.__name__ == "_QueryIdHandler"
        ]
        ck.check(
            len(handlers) <= 1,
            "(j) setup_logging 幂等：不叠加 Handler",
            f"query_id_handlers={len(handlers)}",
        )
        ck.check(
            current_query_id() == "-",
            "(k) 无绑定会话时 current_query_id() == '-'",
            current_query_id(),
        )
        with bind_query_id("query-abc"):
            bound = current_query_id()
        ck.check(
            bound == "query-abc" and current_query_id() == "-",
            "(k) bind_query_id 注入与退出恢复",
            f"bound={bound}, after={current_query_id()}",
        )

        # (l) setup_observability 联动：span -> metrics（span.<name>）
        setup_observability(obs)
        m.reset()
        with trace_session("t-3") as trace:
            trace.add_span("retrieval", 3.0)
        span_snap = m.snapshot().get("span.retrieval")
        ck.check(
            span_snap is not None and span_snap["count"] == 1 and span_snap["sum"] == 3.0,
            "(l) setup_observability：span 记录同步进 metrics",
            str(span_snap),
        )

        # (l2) setup_observability **重复调用**不得叠加观察者
        #
        # 补这条是因为 (l) 只调了一次，而真实调用方 rag.answer_query 是
        # 每次问答都调一遍的。曾经这里的回调是个闭包，身份每次都不同，
        # 去重完全失效——观察者按问答次数无限增长，每个 span 的开销随之
        # 线性上升，实测 920 次问答后吞吐从 21.8 QPS 衰减到 6.1 QPS。
        # 只有"调 N 次仍然只记 1 条"这个断言才能拦住它。
        for _ in range(5):
            setup_observability(obs)
        m.reset()
        with trace_session("t-3b") as trace:
            trace.add_span("retrieval", 3.0)
        span_snap2 = m.snapshot().get("span.retrieval")
        ck.check(
            span_snap2 is not None and span_snap2["count"] == 1,
            "(l2) setup_observability 调 6 次：span 仍只记 1 条（观察者不叠加）",
            f"count={span_snap2['count'] if span_snap2 else None}（期望 1）",
        )

        # ------------------------------------------------------------------ #
        # 5. 图编排（rag_graph，确定性桩）
        # ------------------------------------------------------------------ #
        if not rag_graph._LANGGRAPH_AVAILABLE:
            print("[SKIP] langgraph 未安装，跳过图编排断言（分区 5/6）")
        else:
            comp_ok = make_comp(
                FakeRetrieval(ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]))
            )

            # (m) 完整流程成功
            r = rag_graph.run_graph_answer(comp_ok, "q", None, True, "t-m")
            ck.check(
                isinstance(r, QueryResult) and r.abstained is False and r.answer == "答案[1]",
                "(m) 完整流程：非弃权 + 答案正确",
                str(r.abstained),
            )
            ck.check(
                r.route == "hybrid" and any(s.startswith("retrieve:") for s in r.traces),
                "(m) route=hybrid + 检索 trace 保留",
                str(r.traces),
            )

            # (n) 检索失败 -> 弃权
            comp = make_comp(
                FakeRetrieval(None, raise_on_call=RuntimeError("milvus down"))
            )
            r = rag_graph.run_graph_answer(comp, "q", None, True, "t-n")
            ck.check(
                isinstance(r, QueryResult) and r.abstained is True and r.answer == "",
                "(n) 检索失败 -> 弃权结果（不崩溃）",
                str(r.abstained),
            )
            ck.check(
                "检索失败（弃权）" in r.traces[0],
                "(n) 弃权 trace 含失败原因",
                str(r.traces),
            )

            # (o) 检索阶段弃权门（低分）
            comp = make_comp(FakeRetrieval(ret_result([make_rc("a", 0.1)])))
            r = rag_graph.run_graph_answer(comp, "q", None, True, "t-o")
            ck.check(
                r.abstained is True and "弃权" in " ".join(r.traces),
                "(o) 检索阶段弃权门命中",
                str(r.traces),
            )

            # (p) 二轮检索循环：新增证据 -> rounds=2
            retrieval = _GrowRetrieval(
                ret_result([make_rc("a", 0.8)]),
                ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]),
            )
            comp = make_comp(
                retrieval,
                verifier=FakeVerifier(supported=False, missing=True),
                second_round=True,
            )
            r = rag_graph.run_graph_answer(comp, "q", None, True, "t-p")
            ck.check(
                len(retrieval.calls) == 2,
                "(p) 二轮检索：检索器被调用 2 次",
                str(len(retrieval.calls)),
            )
            ck.check(
                any("二轮检索已执行" in s for s in r.traces),
                "(p) trace 记录二轮检索",
                str(r.traces),
            )

            # (p2) 查询增强全关 -> 二轮**不跑**，且在 trace 里说明为什么
            # 这一条守的是一笔实打实的浪费：重跑同一个 query 走同一条确定性
            # 链路，结果必然一样，但一次嵌入 + Milvus + 重排的钱已经花掉了，
            # 而且专挑「答案本来就没被证据支撑」的慢请求上加码。
            retrieval = _GrowRetrieval(
                ret_result([make_rc("a", 0.8)]),
                ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]),
            )
            comp = make_comp(retrieval, verifier=FakeVerifier(supported=False, missing=True))
            r = rag_graph.run_graph_answer(comp, "q", None, True, "t-p2")
            ck.check(
                len(retrieval.calls) == 1,
                "(p2) 增强全关：二轮检索被跳过，检索器只调 1 次",
                str(len(retrieval.calls)),
            )
            ck.check(
                any("二轮检索已跳过" in s for s in r.traces),
                "(p2) 跳过原因写进 trace，而不是静默少跑一轮",
                str(r.traces),
            )

            # (q) 二轮无新增证据 -> 停止补救
            retrieval = FakeRetrieval(
                ret_result([make_rc("a", 0.8)]),
                calls=[],
            )
            comp = make_comp(
                retrieval,
                verifier=FakeVerifier(supported=False, missing=True),
                second_round=True,
            )
            r = rag_graph.run_graph_answer(comp, "q", None, True, "t-q")
            ck.check(
                len(retrieval.calls) == 2
                and any("无新增证据" in s for s in r.traces),
                "(q) 无新增证据 -> 停止补救",
                f"calls={len(retrieval.calls)}, traces={r.traces}",
            )

            # (r) 节点未捕获异常 -> 返回 None（回退信号）
            comp = make_comp(
                FakeRetrieval(ret_result([make_rc("a", 0.8)])),
                gate=_BoomGate(),
            )
            r = rag_graph.run_graph_answer(comp, "q", None, True, "t-r")
            ck.check(
                r is None,
                "(r) 节点未捕获异常 -> run_graph_answer 返回 None",
                str(r),
            )

            # (s) 验证阶段弃权门（蕴含低分）
            comp = make_comp(
                FakeRetrieval(ret_result([make_rc("a", 0.8)])),
                verifier=FakeVerifier(supported=False, missing=False, entailment=0.1),
            )
            r = rag_graph.run_graph_answer(comp, "q", None, True, "t-s")
            ck.check(
                r.abstained is True and "证据不足以支撑" in " ".join(r.traces),
                "(s) 验证阶段弃权门命中",
                str(r.traces),
            )

        # ------------------------------------------------------------------ #
        # 6. 回退集成（rag.answer_query dispatcher）
        # ------------------------------------------------------------------ #
        graph_calls = {"n": 0}
        seq_calls = {"n": 0}
        # 保存真实引用（避免 patch 后递归调用 mock 自身）
        _real_run_graph = rag_graph.run_graph_answer
        _real_seq = rag._answer_sequential

        def _wrap_graph(*a, **k):
            graph_calls["n"] += 1
            return _real_run_graph(*a, **k)

        def _wrap_seq(*a, **k):
            seq_calls["n"] += 1
            return _real_seq(*a, **k)

        settings_on = Settings(pipeline=PipelineSettings(graph_engine_on=True))
        settings_off = Settings(pipeline=PipelineSettings(graph_engine_on=False))
        comp_ok = make_comp(
            FakeRetrieval(ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]))
        )

        if not rag_graph._LANGGRAPH_AVAILABLE:
            print("[SKIP] langgraph 未安装，跳过回退集成断言（分区 6）")
        else:
            # (t) 图优先：图成功 -> 零顺序调用
            graph_calls["n"] = seq_calls["n"] = 0
            with mock.patch("rag.get_pipeline", return_value=comp_ok):
                with mock.patch("rag_graph.run_graph_answer", side_effect=_wrap_graph):
                    with mock.patch("rag._answer_sequential", side_effect=_wrap_seq):
                        r = rag.answer_query("q", settings=settings_on)
            ck.check(
                graph_calls["n"] == 1 and seq_calls["n"] == 0,
                "(t) graph_engine_on=True + 图成功：走图路径，零顺序调用",
                f"graph={graph_calls['n']}, seq={seq_calls['n']}",
            )
            ck.check(
                r.abstained is False and r.answer == "答案[1]",
                "(t) 图路径返回正常结果",
                str(r.abstained),
            )

            # (u) 图返回 None（不可用）-> 回退顺序
            graph_calls["n"] = seq_calls["n"] = 0
            with mock.patch("rag.get_pipeline", return_value=comp_ok):
                with mock.patch("rag_graph.run_graph_answer", return_value=None):
                    with mock.patch("rag._answer_sequential", side_effect=_wrap_seq):
                        r = rag.answer_query("q", settings=settings_on)
            ck.check(
                seq_calls["n"] == 1 and r.abstained is False,
                "(u) 图不可用（None）-> 回退顺序编排，结果正常",
                f"seq={seq_calls['n']}, abstained={r.abstained}",
            )

            # (v) 图抛异常 -> 回退顺序 + 累计 graph.fallback
            m.reset()
            graph_calls["n"] = seq_calls["n"] = 0
            with mock.patch("rag.get_pipeline", return_value=comp_ok):
                with mock.patch(
                    "rag_graph.run_graph_answer", side_effect=RuntimeError("graph dead")
                ):
                    with mock.patch("rag._answer_sequential", side_effect=_wrap_seq):
                        r = rag.answer_query("q", settings=settings_on)
            ck.check(
                seq_calls["n"] == 1 and r.abstained is False,
                "(v) 图抛异常 -> 回退顺序编排，结果正常",
                f"seq={seq_calls['n']}, abstained={r.abstained}",
            )
            ck.check(
                m.snapshot().get("graph.fallback", {}).get("count", 0) >= 1,
                "(v) graph.fallback 指标已累计",
                str(m.snapshot().get("graph.fallback")),
            )

            # (w) 开关关闭 -> 直接顺序，图零调用
            graph_calls["n"] = seq_calls["n"] = 0
            with mock.patch("rag.get_pipeline", return_value=comp_ok):
                with mock.patch("rag_graph.run_graph_answer", side_effect=_wrap_graph):
                    with mock.patch("rag._answer_sequential", side_effect=_wrap_seq):
                        r = rag.answer_query("q", settings=settings_off)
            ck.check(
                graph_calls["n"] == 0 and seq_calls["n"] == 1 and r.abstained is False,
                "(w) graph_engine_on=False：直接顺序编排，图零调用",
                f"graph={graph_calls['n']}, seq={seq_calls['n']}",
            )

        # ------------------------------------------------------------------ #
        # 7. 图/顺序行为一致性差分（run_graph_answer vs _answer_sequential）
        #    每个场景用独立桩：图路径与顺序路径各一份相同配置的全新 comp，
        #    避免有状态桩（calls 计数）串扰。两条路径行为完全一致是 PR 核心
        #    承诺——任一字段 / 业务 traces / 调用次数不一致即为 bug（断言
        #    失败暴露，而非记录为已知差异）。
        # ------------------------------------------------------------------ #
        if not rag_graph._LANGGRAPH_AVAILABLE:
            print("[SKIP] langgraph 未安装，跳过差分一致性断言（分区 7）")
        else:
            diff_report: list[tuple[str, list[str], list[str], int]] = []

            def run_diff_scenario(label: str, fn) -> None:
                """执行一个差分场景并记入汇总（fn 内做全部 ck.check 断言）。"""
                steps0 = ck.steps
                g_traces, s_traces = fn()
                diff_report.append((label, g_traces, s_traces, ck.steps - steps0))

            # (x) 场景 1：快乐路径（2 个高分组块，verifier 全支持）
            def sc1():
                def _r():
                    return FakeRetrieval(ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]))

                rg, rs, g_ret, s_ret = diff_pair(_r)
                check_diff_equal(ck, "(x) 场景1 快乐路径", rg, rs, g_ret, s_ret)
                ck.check(
                    rg.abstained is False and rs.abstained is False,
                    "(x) 场景1 快乐路径：两条路径均 abstained=False",
                    f"graph={rg.abstained}, seq={rs.abstained}",
                )
                ck.check(
                    rg.route == "hybrid" and rs.route == "hybrid",
                    "(x) 场景1 快乐路径：两条路径均 route=hybrid",
                    f"graph={rg.route}, seq={rs.route}",
                )
                ck.check(
                    len(rg.verdict["evidence_chunks"]) == 2
                    and len(rs.verdict["evidence_chunks"]) == 2,
                    "(x) 场景1 快乐路径：两条路径证据块数一致且为 2",
                    f"graph={len(rg.verdict['evidence_chunks'])}, "
                    f"seq={len(rs.verdict['evidence_chunks'])}",
                )
                return biz_traces(rg.traces), biz_traces(rs.traces)

            run_diff_scenario("场景1 快乐路径", sc1)

            # (y) 场景 2：检索失败（RuntimeError -> 弃权，异常文本两条路径一致）
            def sc2():
                def _r():
                    return FakeRetrieval(None, raise_on_call=RuntimeError("milvus down"))

                rg, rs, g_ret, s_ret = diff_pair(_r)
                check_diff_equal(ck, "(y) 场景2 检索失败", rg, rs, g_ret, s_ret)
                ck.check(
                    rg.abstained is True and rs.abstained is True,
                    "(y) 场景2 检索失败：两条路径均 abstained=True",
                    f"graph={rg.abstained}, seq={rs.abstained}",
                )
                ck.check(
                    rg.answer == "" and rs.answer == "",
                    "(y) 场景2 检索失败：两条路径均空答案",
                    f"graph={rg.answer!r}, seq={rs.answer!r}",
                )
                ck.check(
                    any("检索失败（弃权）" in t for t in biz_traces(rg.traces))
                    and any("检索失败（弃权）" in t for t in biz_traces(rs.traces)),
                    "(y) 场景2 检索失败：两条路径 trace 均含失败弃权",
                    f"graph={rg.traces}, seq={rs.traces}",
                )
                return biz_traces(rg.traces), biz_traces(rs.traces)

            run_diff_scenario("场景2 检索失败", sc2)

            # (z) 场景 3：检索弃权门（1 个低分组块 score=0.1 < 0.3）
            def sc3():
                def _r():
                    return FakeRetrieval(ret_result([make_rc("a", 0.1)]))

                rg, rs, g_ret, s_ret = diff_pair(_r)
                check_diff_equal(ck, "(z) 场景3 检索弃权门", rg, rs, g_ret, s_ret)
                ck.check(
                    rg.abstained is True and rs.abstained is True,
                    "(z) 场景3 检索弃权门：两条路径均 abstained=True",
                    f"graph={rg.abstained}, seq={rs.abstained}",
                )
                ck.check(
                    any("弃权" in t for t in biz_traces(rg.traces))
                    and any("弃权" in t for t in biz_traces(rs.traces)),
                    "(z) 场景3 检索弃权门：两条路径 trace 均含弃权",
                    f"graph={rg.traces}, seq={rs.traces}",
                )
                return biz_traces(rg.traces), biz_traces(rs.traces)

            run_diff_scenario("场景3 检索弃权门", sc3)

            # (aa) 场景 4：二轮检索循环（首轮 [a]，二轮 [a,b]，verifier 证据不足）
            def sc4():
                def _r():
                    return _GrowRetrieval(
                        ret_result([make_rc("a", 0.8)]),
                        ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]),
                    )

                def _v():
                    return FakeVerifier(supported=False, missing=True)

                rg, rs, g_ret, s_ret = diff_pair(
                    _r, verifier_factory=_v, second_round=True
                )
                check_diff_equal(ck, "(aa) 场景4 二轮检索循环", rg, rs, g_ret, s_ret)
                ck.check(
                    len(g_ret.calls) == 2 and len(s_ret.calls) == 2,
                    "(aa) 场景4 二轮检索循环：两条路径均触发 2 次检索调用",
                    f"graph={len(g_ret.calls)}, seq={len(s_ret.calls)}",
                )
                ck.check(
                    any("二轮检索已执行" in t for t in biz_traces(rg.traces))
                    and any("二轮检索已执行" in t for t in biz_traces(rs.traces)),
                    "(aa) 场景4 二轮检索循环：两条路径 trace 均记录二轮检索",
                    f"graph={rg.traces}, seq={rs.traces}",
                )
                ck.check(
                    rg.answer == "答案[1]" and rs.answer == "答案[1]",
                    "(aa) 场景4 二轮检索循环：两条路径答案一致",
                    f"graph={rg.answer!r}, seq={rs.answer!r}",
                )
                return biz_traces(rg.traces), biz_traces(rs.traces)

            run_diff_scenario("场景4 二轮检索循环", sc4)

            # (ab) 场景 5：二轮无新增证据（恒返回 [a]，verifier 证据不足 -> 停止补救）
            def sc5():
                def _r():
                    return FakeRetrieval(ret_result([make_rc("a", 0.8)]), calls=[])

                def _v():
                    return FakeVerifier(supported=False, missing=True)

                rg, rs, g_ret, s_ret = diff_pair(
                    _r, verifier_factory=_v, second_round=True
                )
                check_diff_equal(ck, "(ab) 场景5 二轮无新增证据", rg, rs, g_ret, s_ret)
                ck.check(
                    len(g_ret.calls) == 2 and len(s_ret.calls) == 2,
                    "(ab) 场景5 二轮无新增证据：两条路径均 2 次检索调用",
                    f"graph={len(g_ret.calls)}, seq={len(s_ret.calls)}",
                )
                ck.check(
                    any("无新增证据" in t for t in biz_traces(rg.traces))
                    and any("无新增证据" in t for t in biz_traces(rs.traces)),
                    "(ab) 场景5 二轮无新增证据：两条路径 trace 均记录停止补救",
                    f"graph={rg.traces}, seq={rs.traces}",
                )
                return biz_traces(rg.traces), biz_traces(rs.traces)

            run_diff_scenario("场景5 二轮无新增证据", sc5)

            # (ac) 场景 6：验证弃权门（高分组块 + 蕴含低分 0.1 < 0.6；
            #     retry=False 隔离单轮，专注验证阶段弃权差分）
            def sc6():
                def _r():
                    return FakeRetrieval(ret_result([make_rc("a", 0.8)]))

                def _v():
                    return FakeVerifier(supported=False, missing=False, entailment=0.1)

                rg, rs, g_ret, s_ret = diff_pair(_r, verifier_factory=_v, retry=False)
                check_diff_equal(ck, "(ac) 场景6 验证弃权门", rg, rs, g_ret, s_ret)
                ck.check(
                    rg.abstained is True and rs.abstained is True,
                    "(ac) 场景6 验证弃权门：两条路径均 abstained=True",
                    f"graph={rg.abstained}, seq={rs.abstained}",
                )
                ck.check(
                    any("弃权" in t for t in biz_traces(rg.traces))
                    and any("弃权" in t for t in biz_traces(rs.traces)),
                    "(ac) 场景6 验证弃权门：两条路径 trace 均含弃权",
                    f"graph={rg.traces}, seq={rs.traces}",
                )
                ck.check(
                    len(g_ret.calls) == 1 and len(s_ret.calls) == 1,
                    "(ac) 场景6 验证弃权门：retry=False 下两条路径均只 1 次检索调用",
                    f"graph={len(g_ret.calls)}, seq={len(s_ret.calls)}",
                )
                return biz_traces(rg.traces), biz_traces(rs.traces)

            run_diff_scenario("场景6 验证弃权门", sc6)

            # (ad) 场景 7：retry=False（快乐路径但 verifier missing=True，禁止二轮）
            def sc7():
                def _r():
                    return FakeRetrieval(ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]))

                def _v():
                    return FakeVerifier(supported=True, missing=True)

                rg, rs, g_ret, s_ret = diff_pair(_r, verifier_factory=_v, retry=False)
                check_diff_equal(ck, "(ad) 场景7 retry=False", rg, rs, g_ret, s_ret)
                ck.check(
                    len(g_ret.calls) == 1 and len(s_ret.calls) == 1,
                    "(ad) 场景7 retry=False：两条路径均只 1 次检索调用",
                    f"graph={len(g_ret.calls)}, seq={len(s_ret.calls)}",
                )
                ck.check(
                    rg.abstained is False and rs.abstained is False,
                    "(ad) 场景7 retry=False：两条路径弃权状态一致（均不弃权）",
                    f"graph={rg.abstained}, seq={rs.abstained}",
                )
                return biz_traces(rg.traces), biz_traces(rs.traces)

            run_diff_scenario("场景7 retry=False", sc7)

            # 差分汇总：逐场景对比两条路径的实际业务 traces（已滤 span 计时项）
            print()
            print("差分一致性汇总（业务 traces，已滤 span 计时项）：")
            for label, g_traces, s_traces, n in diff_report:
                mark = "一致" if g_traces == s_traces else "不一致"
                print(f"  [{mark}] {label}: {n} 项断言 | graph={g_traces} | seq={s_traces}")

        # ------------------------------------------------------------------ #
        # 8. 多租户强制隔离（resolve_tenant + 管线透传 + 跨租户不可见）
        # ------------------------------------------------------------------ #
        # (ae) resolve_tenant 强制模式：空 / None -> default_tenant；非空原样返回
        enforced_settings = Settings(tenant=TenantSettings(enforced=True, default_tenant="corp"))
        ck.check(
            resolve_tenant(None, enforced_settings) == "corp",
            "(ae) enforced=True + None -> 回退 default_tenant",
            repr(resolve_tenant(None, enforced_settings)),
        )
        ck.check(
            resolve_tenant("", enforced_settings) == "corp",
            "(ae) enforced=True + 空串 -> 回退 default_tenant",
            repr(resolve_tenant("", enforced_settings)),
        )
        ck.check(
            resolve_tenant("acme", enforced_settings) == "acme",
            "(ae) enforced=True + 显式租户 -> 原样返回",
            repr(resolve_tenant("acme", enforced_settings)),
        )

        # (af) resolve_tenant 关闭开关：空 / None -> 空串（不过滤）
        off_settings = Settings(tenant=TenantSettings(enforced=False, default_tenant="corp"))
        ck.check(
            resolve_tenant(None, off_settings) == "",
            "(af) enforced=False + None -> 空串（不过滤）",
            repr(resolve_tenant(None, off_settings)),
        )
        ck.check(
            resolve_tenant("acme", off_settings) == "acme",
            "(af) enforced=False + 显式租户 -> 原样返回",
            repr(resolve_tenant("acme", off_settings)),
        )

        # (ag) settings 缺 tenant 段（桩场景）按 enforced 处理
        ck.check(
            resolve_tenant("", PipelineSettings()) == "default",
            "(ag) 无 tenant 段 -> 按强制模式回退 default",
            repr(resolve_tenant("", PipelineSettings())),
        )

        # (ah) answer_query 透传 tenant_id 到检索管线（图 / 顺序两条路径）
        tenant_ret = FakeRetrieval(
            ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]), calls=[]
        )
        comp_tenant = make_comp(tenant_ret)
        with mock.patch("rag.get_pipeline", return_value=comp_tenant):
            with mock.patch("rag_graph.run_graph_answer", side_effect=lambda *a, **k: None):
                r = rag.answer_query("q", settings=settings_off, tenant_id="acme")
        ck.check(
            r.abstained is False and tenant_ret.tenant_calls == ["acme"],
            "(ah) answer_query 将 tenant_id 透传给顺序路径检索管线",
            f"tenant_calls={tenant_ret.tenant_calls}, abstained={r.abstained}",
        )

        # (ai) 图路径同样透传 tenant_id
        if not rag_graph._LANGGRAPH_AVAILABLE:
            print("[SKIP] langgraph 未安装，跳过图路径租户透传断言（ai）")
        else:
            tenant_ret2 = FakeRetrieval(
                ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]), calls=[]
            )
            comp_tenant2 = make_comp(tenant_ret2)
            with mock.patch("rag.get_pipeline", return_value=comp_tenant2):
                with mock.patch("rag._answer_sequential", side_effect=lambda *a, **k: None):
                    r = rag.answer_query("q", settings=settings_on, tenant_id="acme")
            ck.check(
                r.abstained is False and tenant_ret2.tenant_calls == ["acme"],
                "(ai) answer_query 将 tenant_id 透传给图路径检索管线",
                f"tenant_calls={tenant_ret2.tenant_calls}, abstained={r.abstained}",
            )

        # (aj) 未显式指定 tenant_id -> 强制回退 default_tenant 并透传
        tenant_ret3 = FakeRetrieval(
            ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]), calls=[]
        )
        comp_tenant3 = make_comp(tenant_ret3)
        with mock.patch("rag.get_pipeline", return_value=comp_tenant3):
            with mock.patch("rag_graph.run_graph_answer", side_effect=lambda *a, **k: None):
                rag.answer_query("q", settings=settings_off)
        ck.check(
            tenant_ret3.tenant_calls == ["default"],
            "(aj) 未指定 tenant_id -> 强制回退 default 并透传",
            f"tenant_calls={tenant_ret3.tenant_calls}",
        )

        # (ak) 关闭强制开关：未指定 tenant_id -> 空串（不过滤）并透传
        settings_free = Settings(
            pipeline=PipelineSettings(graph_engine_on=False),
            tenant=TenantSettings(enforced=False),
        )
        tenant_ret4 = FakeRetrieval(
            ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]), calls=[]
        )
        comp_tenant4 = make_comp(tenant_ret4)
        with mock.patch("rag.get_pipeline", return_value=comp_tenant4):
            rag.answer_query("q", settings=settings_free)
        ck.check(
            tenant_ret4.tenant_calls == [""],
            "(ak) enforced=False + 未指定 -> 空串透传（不过滤）",
            f"tenant_calls={tenant_ret4.tenant_calls}",
        )

        # (al) answer_query 透传 dataset_id 到顺序路径检索管线
        ds_ret = FakeRetrieval(
            ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]), calls=[]
        )
        comp_ds = make_comp(ds_ret)
        with mock.patch("rag.get_pipeline", return_value=comp_ds):
            with mock.patch("rag_graph.run_graph_answer", side_effect=lambda *a, **k: None):
                r = rag.answer_query("q", settings=settings_off, dataset_id="milvus")
        ck.check(
            r.abstained is False and ds_ret.dataset_calls == ["milvus"],
            "(al) answer_query 将 dataset_id 透传给顺序路径检索管线",
            f"dataset_calls={ds_ret.dataset_calls}, abstained={r.abstained}",
        )

        # (am) 图路径同样透传 dataset_id
        if not rag_graph._LANGGRAPH_AVAILABLE:
            print("[SKIP] langgraph 未安装，跳过图路径知识库透传断言（am）")
        else:
            ds_ret2 = FakeRetrieval(
                ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]), calls=[]
            )
            comp_ds2 = make_comp(ds_ret2)
            with mock.patch("rag.get_pipeline", return_value=comp_ds2):
                with mock.patch("rag._answer_sequential", side_effect=lambda *a, **k: None):
                    r = rag.answer_query("q", settings=settings_on, dataset_id="milvus")
            ck.check(
                r.abstained is False and ds_ret2.dataset_calls == ["milvus"],
                "(am) answer_query 将 dataset_id 透传给图路径检索管线",
                f"dataset_calls={ds_ret2.dataset_calls}, abstained={r.abstained}",
            )

        # (an) 未指定 dataset_id -> 空串透传（全域检索，不做强制回退）
        #      与 tenant 的强制回退语义刻意不同：知识库是检索范围而非安全边界。
        ds_ret3 = FakeRetrieval(
            ret_result([make_rc("a", 0.8), make_rc("b", 0.7)]), calls=[]
        )
        comp_ds3 = make_comp(ds_ret3)
        with mock.patch("rag.get_pipeline", return_value=comp_ds3):
            with mock.patch("rag_graph.run_graph_answer", side_effect=lambda *a, **k: None):
                rag.answer_query("q", settings=settings_off)
        ck.check(
            ds_ret3.dataset_calls == [""],
            "(an) 未指定 dataset_id -> 空串透传（全域检索）",
            f"dataset_calls={ds_ret3.dataset_calls}",
        )

        print()
        print(f"SMOKE RESILIENCE PASSED (exit 0)  --  {ck.steps} 项断言全部通过")
        return 0

    except AssertionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("SMOKE RESILIENCE FAILED (exit 1)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
