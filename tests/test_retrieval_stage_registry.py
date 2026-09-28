"""检索阶段注册表的可扩展性判据 —— 与 chunk writer / 切分模式 / 存储 provider 同一把尺子。

判据是"新增一个实现，不改任何既有分支"。这里把它钉四件事：

1. 注册一个新阶段后，它真的出现在执行次序里，而 ``retrieval/pipeline.py`` 一个字节都没变。
2. 残缺的"阶段"在注册时就被拒 —— 否则它要等到真跑到那次检索才炸。
3. ``StageRunner`` 之外不再有一份按阶段名分支的暗副本。
4. trace 节点号与 spec 表一致，前端耗时条不会多出或少掉节点。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import retrieval.pipeline as pipeline_module
import retrieval.stages as stages
from retrieval.stages import (
    RETRIEVAL_STAGES,
    register_retrieval_stage,
    retrieval_stage_order,
    stage_node_ids,
)


class _ProbeStage:
    """一个完整但什么也不做的阶段。"""

    name = "probe.extra"
    node_id: str | None = None
    span_name: str | None = None
    order = 10_000
    prefetches = False
    requires_flag: str | None = None
    requires_component: str | None = None

    def eligible(self, state, ctx):  # noqa: ANN001, ANN201
        return stages.Gate.silent()

    def fatal_error(self, exc, state, ctx):  # noqa: ANN001
        return None

    def run(self, state, ctx):  # noqa: ANN001, ANN201
        # S-RS 评审 P2-1：探针阶段必须真的可执行——返回驱动器认的记账单，
        # 而不是裸 Skip（那种残件一旦被 StageRunner 驱到就 AttributeError）。
        return stages.StageOutcome().complete()


def _pipeline_source() -> bytes:
    path = Path(inspect.getsourcefile(pipeline_module) or "")
    return path.read_bytes()


@pytest.fixture()
def probe_registered():
    before = _pipeline_source()
    register_retrieval_stage(_ProbeStage())
    try:
        yield before
    finally:
        RETRIEVAL_STAGES.unregister(_ProbeStage.name)


def test_new_stage_is_reached_without_editing_the_pipeline(probe_registered: bytes) -> None:
    order = [stage.name for stage in retrieval_stage_order()]

    assert "probe.extra" in order
    # 排在所有内置阶段之后：次序由 order 决定，不由注册先后决定
    assert order.index("probe.extra") > order.index(stages.TRUNCATE_STAGE_NAME)
    assert _pipeline_source() == probe_registered, (
        "注册新阶段却需要改动 retrieval/pipeline.py —— 判据不成立"
    )


def test_registration_rejects_a_stage_that_does_not_fit_the_protocol() -> None:
    class _Incomplete:
        name = "probe.incomplete"
        order = 9_999

    with pytest.raises(ValueError, match="RetrievalStage 协议") as exc:
        register_retrieval_stage(_Incomplete())  # type: ignore[arg-type]

    assert "eligible" in str(exc.value) and "run" in str(exc.value)
    assert "probe.incomplete" not in RETRIEVAL_STAGES.names()


def test_registration_rejects_a_non_callable_protocol_member() -> None:
    """S-RS 评审 P2-2：``run = "x"`` 这类非可调用赋值必须在注册期判死。"""
    broken = _ProbeStage()
    broken.name = "probe.broken-run"
    broken.run = "not callable"  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="不是可调用") as exc:
        register_retrieval_stage(broken)

    assert "run" in str(exc.value)
    assert "probe.broken-run" not in RETRIEVAL_STAGES.names()


def test_duplicate_stage_name_is_refused_unless_explicitly_replaced(
    probe_registered: bytes,
) -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_retrieval_stage(_ProbeStage())

    register_retrieval_stage(_ProbeStage(), replace=True)  # 显式替换必须可用


def test_pipeline_run_has_no_per_stage_branch_copy() -> None:
    """``run()`` 里不该再出现按阶段名判定的字面量：那意味着注册表之外还有第二份编排真相。"""
    source = inspect.getsource(pipeline_module.RetrievalPipeline.run)
    leaked = [
        name for name in RETRIEVAL_STAGES.names() if f'"{name}"' in source or f"'{name}'" in source
    ]

    assert leaked == [], f"RetrievalPipeline.run 仍在按阶段名分支：{leaked}"


def test_trace_nodes_stay_one_to_one_with_observable_stages() -> None:
    nodes = set(stage_node_ids())
    observable = {
        stage.node_id for stage in retrieval_stage_order() if stage.node_id
    }  # 内置阶段贡献的节点号

    assert nodes == observable
    # 内置阶段共 19 个，但其中 prefetch / filter_expr / serving_fence 三道是
    # 不单独出 trace 节点的次序守卫：前端耗时条只认 14 个节点。
    assert len(nodes) == 14, sorted(nodes)


def test_hard_ordering_invariants_are_enforced_not_polite() -> None:
    """收口必须在图谱与 sentence_window 之后、裁剪之前 —— 靠注册期判死，不靠自觉。"""
    scope = RETRIEVAL_STAGES.create(stages.SCOPE_STAGE_NAME, None)
    truncate = RETRIEVAL_STAGES.create(stages.TRUNCATE_STAGE_NAME, None)
    # 把收口挪到所有阶段之后 —— 正是"越界 chunk 会先进最终 top_k"那一条被破坏的形状。
    too_late = max(s.order for s in retrieval_stage_order()) + 100
    assert too_late > truncate.order
    broken = type("BrokenScopeStage", (type(scope),), {"order": too_late})()

    register_retrieval_stage(broken, replace=True)
    try:
        with pytest.raises(ValueError, match="dataset_scope"):
            retrieval_stage_order()
    finally:
        register_retrieval_stage(scope, replace=True)

    assert truncate.order < too_late
    retrieval_stage_order()  # 还原后必须重新可用


# --------------------------------------------------------------------------- #
# S-RS 评审 P1-2/P2-1：驱动器三态直测
#
# 此前 ok/skipped/degraded 只被完整管线的观测 parity 套件间接覆盖，spec R2 的
# 判据始终空着。这里构造 StageRunner 直驱一个带 node_id 的最小阶段，钉住
# 驱动器把记账单翻译成 run 事件的三种主态（外加可恢复失败必伴 degraded）。
# --------------------------------------------------------------------------- #


class _RecordingObserver:
    """把驱动器发出的事件按序收下来，供断言。

    事件方法名以 ``retrieval.stages`` 的发射侧为准：``start_node`` /
    ``complete_node`` / ``skip_node`` / ``fail_node`` / ``degraded``。
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def start_node(self, node_id, **kwargs):
        self.events.append(("start_node", {"node_id": node_id, **kwargs}))

    def complete_node(self, node_id, **kwargs):
        self.events.append(("complete_node", {"node_id": node_id, **kwargs}))

    def skip_node(self, node_id, **kwargs):
        self.events.append(("skip_node", {"node_id": node_id, **kwargs}))

    def fail_node(self, node_id, **kwargs):
        self.events.append(("fail_node", {"node_id": node_id, **kwargs}))

    def degraded(self, node_id, **kwargs):
        self.events.append(("degraded_node", {"node_id": node_id, **kwargs}))

    def __getattr__(self, _name):
        return lambda *a, **k: None


def _make_node_stage(outcome_factory):
    """构造一个带 node_id、run 行为由 outcome_factory 决定的最小阶段。"""

    class _NodeStage:
        name = "probe.node"
        node_id = "probe.node"
        span_name = None
        order = 10_001
        prefetches = False
        requires_flag = None
        requires_component = None

        def eligible(self, state, ctx):
            return stages.Gate.running()

        def fatal_error(self, exc, state, ctx):
            return None

        def run(self, state, ctx):
            return outcome_factory()

    return _NodeStage()


def _drive(stage) -> list[tuple[str, dict]]:
    from types import SimpleNamespace

    import core.tracing as tracing
    from models.schemas import RouteDecision
    from retrieval.stages import RetrievalState

    observer = _RecordingObserver()
    state = RetrievalState(
        query="q",
        search_query="q",
        rewritten="q",
        rewrite_changed=False,
        gate_skipped=False,
        route=RouteDecision(target="hybrid"),
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
    ctx = SimpleNamespace(tenant="tenant-1", dataset="dataset-1", acl=None)
    with tracing.trace_session("driver-test", observer=observer):
        stages.StageRunner([stage]).run(state, ctx)
    return observer.events


def test_stage_runner_drives_the_three_primary_outcomes() -> None:
    # ok：complete 记账单 → start + complete 两个事件
    ok_events = _drive(_make_node_stage(lambda: stages.StageOutcome().complete(1.0)))
    names = [name for name, _ in ok_events]
    assert names == ["start_node", "complete_node"], ok_events

    # skipped：Skip 记账单 → start + skip，绝无 complete
    skip_events = _drive(
        _make_node_stage(lambda: stages.StageOutcome().skip("not needed"))
    )
    names = [name for name, _ in skip_events]
    assert names == ["start_node", "skip_node"], skip_events

    # degraded：显式 Degrade 记账单 → start + degraded
    degrade_events = _drive(
        _make_node_stage(
            lambda: stages.StageOutcome().degrade("partial failure")
        )
    )
    names = [name for name, _ in degrade_events]
    assert names == ["start_node", "degraded_node"], degrade_events


def test_recoverable_failure_always_announces_degraded() -> None:
    """驱动器规定可恢复失败必伴 degraded——阶段想漏也漏不掉。"""
    events = _drive(
        _make_node_stage(
            lambda: stages.StageOutcome().fail(
                "RuntimeError",
                "boom",
                recoverable=True,
            )
        )
    )
    names = [name for name, _ in events]
    assert names == ["start_node", "fail_node", "degraded_node"], events
    fail_payload = dict(events[1][1])
    assert fail_payload["recoverable"] is True

    # 不可恢复失败只发 fail，不降级
    fatal_events = _drive(
        _make_node_stage(
            lambda: stages.StageOutcome().fail(
                "RuntimeError", "boom", recoverable=False
            )
        )
    )
    names = [name for name, _ in fatal_events]
    assert names == ["start_node", "fail_node"], fatal_events
