"""Permanent guard for the run ``event_type`` partition (inventory B-2) and the
``RagExecutor`` vocabulary fix (inventory D.5).

The acceptance criterion is the repo's usual one: adding an event type costs **zero
edits to existing branches**, and that has to be measured, not asserted in prose. So
this file does four things:

1. Scans the two reducers and the envelope validator for hand-written event-type
   membership sets. The partition was written three times before; it must not come back.
2. Proves the reducers classify *from the table* by replaying every declared member of
   each partition cell through both of them.
3. Registers a probe event type, asserts both reducers classify it correctly, and
   asserts all five host files stay **byte-identical** — the technique from
   ``tests/test_retrieval_stage_registry.py``.
4. Records what an unknown event type does today — nothing in the reducers, a pydantic
   rejection at the envelope, a ``TypeError`` for an unhashable ``type`` — so nobody
   "improves" it into raising, or into passing by accident, later.

The reducers are driven at their own boundary (the event payload mapping they consume)
rather than through ``RunEvent``, because ``RunEvent.type`` is a closed ``Literal`` by
design: widening it for a probe would itself be an edit to a host file.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import ValidationError

import core.run_events as run_events
import core.run_registry as run_registry
import rag_topology
import server.run_ops as run_ops
from config.settings import RunHistorySettings
from core.run_event_taxonomy import (
    BUILTIN_RUN_EVENT_SPECS,
    RUN_EVENT_TYPE_SPECS,
    RunEventTypeSpec,
    register_run_event_type,
    unregister_run_event_type,
    resolve_run_event_type,
    run_event_types_in_family,
    run_event_types_with,
    run_terminal_status,
)

HOST_FILES = {
    "core/run_events.py": Path(inspect.getsourcefile(run_events) or ""),
    "core/run_event_taxonomy.py": Path(inspect.getsourcefile(resolve_run_event_type) or ""),
    "core/run_registry.py": Path(inspect.getsourcefile(run_registry) or ""),
    "server/run_ops.py": Path(inspect.getsourcefile(run_ops) or ""),
    "rag_topology.py": Path(inspect.getsourcefile(rag_topology) or ""),
}

WIRE_EVENT_TYPES: tuple[str, ...] = get_args(run_events.EventType)
FAMILIES = ("run", "node", "retry", "annotation")
PHASES = ("start", "terminal", "standalone")
_NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)


def _host_bytes() -> dict[str, bytes]:
    return {name: path.read_bytes() for name, path in HOST_FILES.items()}


def _hosts_unchanged(before: dict[str, bytes]) -> bool:
    after = _host_bytes()
    changed = sorted(name for name in before if before[name] != after[name])
    if changed:
        print(f"host files changed by registration alone: {changed}")
        return False
    return True


def _spec(event_type: str) -> RunEventTypeSpec:
    spec = resolve_run_event_type(event_type)
    assert spec is not None, f"{event_type} is not declared"
    return spec


def _of(family: str, phase: str) -> list[str]:
    return [
        name
        for name in WIRE_EVENT_TYPES
        if (_spec(name).family, _spec(name).phase) == (family, phase)
    ]


def _identity() -> Any:
    return run_registry.RegistryIdentity(
        boot_id="boot",
        worker_id="worker",
        scope_key=b"0" * 32,
        cursor_key=b"1" * 32,
        tenant_scope_stability="boot",
    )


def _record() -> Any:
    return run_registry._MutableRun(  # noqa: SLF001
        tenant_scope="scope",
        query_fingerprint=None,
        run_id="run-taxonomy",
        status="running",
        outcome="unknown",
        started_at=_NOW,
        updated_at=_NOW,
        finished_at=None,
        elapsed_ms=0.0,
        boot_id="boot",
        worker_id="worker",
        topology_id="rag.query",
        topology_revision="rev",
        executor="sequential_stream",
        last_seq=0,
        event_count=0,
        integrity="complete",
        persistence_status="memory",
        topology={},
    )


def _payload(
    event_type: Any,
    *,
    node_id: Any = "search",
    attempt: Any = 1,
    elapsed: float = 10.0,
    duration: float | None = 4.0,
    attributes: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": "run-taxonomy",
        "seq": 1,
        "occurred_at": _NOW.isoformat(),
        "elapsed_ms": elapsed,
        "topology_id": "rag.query",
        "topology_revision": "rev",
        "type": event_type,
        "attributes": dict(attributes or {}),
    }
    if node_id is not None:
        payload["node_id"] = node_id
    if attempt is not None:
        payload["attempt"] = attempt
    if duration is not None:
        payload["duration_ms"] = duration
    if error is not None:
        payload["error"] = dict(error)
    return payload


def _reduce(payloads: list[dict[str, Any]], **kwargs: Any) -> tuple[Any, Any]:
    registry = run_registry.RunRegistry(
        RunHistorySettings(persistence_enabled=False), _identity()
    )
    record = _record()
    for payload in payloads:
        registry._reduce_event_locked(record, payload, **kwargs)  # noqa: SLF001
    return registry, record


def _rollups(payloads: list[dict[str, Any]]) -> list[str]:
    return [row.status for row in run_ops._rollups_from_events(tuple(payloads))]  # noqa: SLF001


def _registry_reducer_source() -> str:
    return "".join(
        inspect.getsource(step)
        for step in (
            run_registry.RunRegistry._reduce_event_locked,  # noqa: SLF001
            run_registry.RunRegistry._open_slot_locked,  # noqa: SLF001
            run_registry.RunRegistry._close_slot_locked,  # noqa: SLF001
            run_registry._active_slots,
        )
    )


# --------------------------------------------------------------------------- #
# 1. the partition is declared once, and it is the same partition the wire has
# --------------------------------------------------------------------------- #
def test_partition_is_declared_once_and_covers_the_wire_vocabulary() -> None:
    declared = frozenset(RUN_EVENT_TYPE_SPECS.names())
    # The pydantic Literal is the wire contract the frontend mirrors; this table is what
    # each of those names means. Neither may hold a member the other lacks.
    assert declared == frozenset(WIRE_EVENT_TYPES)

    cells = {
        (family, phase): run_event_types_with(family, phase)
        for family in FAMILIES
        for phase in PHASES
    }
    assert sum(len(cell) for cell in cells.values()) == len(declared)
    assert set().union(*cells.values()) == declared
    for family in FAMILIES:
        assert run_event_types_in_family(family) == set().union(
            *(cell for (cell_family, _), cell in cells.items() if cell_family == family)
        )
    # Every member resolves, and resolves to exactly one family.
    for event_type in declared:
        assert _spec(event_type).event_type == event_type


def test_terminal_and_start_cells_match_the_reducer_semantics() -> None:
    assert _of("node", "start") == ["node.started"]
    assert _of("retry", "start") == ["retry.started"]
    assert set(_of("node", "terminal")) == {
        "node.completed",
        "node.failed",
        "node.skipped",
        "node.cancelled",
    }
    assert set(_of("retry", "terminal")) == {
        "retry.completed",
        "retry.failed",
        "retry.skipped",
    }
    assert _of("run", "start") == ["run.started"]
    assert set(_of("run", "terminal")) == {"run.completed", "run.failed", "run.cancelled"}
    assert _of("annotation", "standalone") == ["route.selected", "degraded"]


def test_run_terminal_family_is_the_only_source_of_the_status_map() -> None:
    for event_type, status in (
        ("run.completed", "completed"),
        ("run.failed", "failed"),
        ("run.cancelled", "cancelled"),
    ):
        assert run_terminal_status(event_type) == status
    for event_type in WIRE_EVENT_TYPES:
        if not event_type.startswith("run."):
            assert run_terminal_status(event_type) is None
    assert run_terminal_status("bogus.type") is None


def test_lifecycle_field_rule_comes_from_the_table() -> None:
    """``run_events`` kept ``_NODE_TYPES`` / ``_RETRY_TYPES`` for exactly this rule."""
    for event_type in WIRE_EVENT_TYPES:
        expects_slot_fields = event_type.startswith(("node.", "retry."))
        assert _spec(event_type).lifecycle_fields_required is expects_slot_fields

    with pytest.raises(ValidationError, match="requires node_id"):
        _envelope("node.completed", node_id=None)
    with pytest.raises(ValidationError, match="requires attempt"):
        _envelope("retry.started", node_id="search", attempt=None)
    # An annotation event may omit both, exactly as before.
    _envelope("route.selected", node_id=None, attempt=None)


def _envelope(event_type: str, **overrides: Any) -> Any:
    node_event = event_type.startswith(("node.", "retry."))
    fields: dict[str, Any] = {
        "run_id": "run-envelope",
        "seq": 1,
        "occurred_at": _NOW + timedelta(seconds=1),
        "elapsed_ms": 1.0,
        "topology_id": "rag.query",
        "topology_revision": "rev",
        "type": event_type,
        "node_id": "search" if node_event else None,
        "attempt": 1 if node_event else None,
    }
    fields.update(overrides)
    return run_events.RunEvent(**fields)


# --------------------------------------------------------------------------- #
# 2. source-level guard: no second copy of the partition inside a reducer
# --------------------------------------------------------------------------- #
def test_reducers_hold_no_hand_written_event_type_membership() -> None:
    reducer_sources = {
        "core/run_registry.py": _registry_reducer_source(),
        "server/run_ops.py": inspect.getsource(run_ops._rollups_from_events),  # noqa: SLF001
    }
    for event_type in WIRE_EVENT_TYPES:
        for literal in (f'"{event_type}"', f"'{event_type}'"):
            for name, source in reducer_sources.items():
                assert literal not in source, f"{name} 的 reducer 又手写了一次 {event_type}"
    for needle in ("event_type in {", 'payload["type"] in {', "in frozenset({"):
        for name in ("core/run_registry.py", "server/run_ops.py"):
            assert needle not in HOST_FILES[name].read_text(encoding="utf-8"), needle
    assert "terminal_status: dict" not in reducer_sources["core/run_registry.py"]


def test_reducers_hold_no_event_type_literal_at_all_not_just_declared_ones() -> None:
    """上一条只拦"已声明的名字"，所以一条 "if event_type == "node.timed_out":" 的新分支
    （用了个还没声明的拼写）能躲过它 —— 而那正是这张表要消灭的东西。这里不看清单，
    只看形状：reducer 源码里任何 "<family>.<name>" 形状的字面量都不许出现。
    """
    import re

    dq = chr(34)
    sq = chr(39)
    families = ("run", "node", "retry", "annotation")
    sources = {
        "core/run_registry.py": _registry_reducer_source(),
        "server/run_ops.py": inspect.getsource(run_ops._rollups_from_events),  # noqa: SLF001
    }
    for quote in (dq, sq):
        each = re.compile(quote + r"([a-z_]+\.[a-z_]+)" + quote)
        for name, source in sources.items():
            offenders = sorted({
                m.group(1) for m in each.finditer(source) if m.group(1).split(".")[0] in families
            })
            assert not offenders, f"{name} 的 reducer 里又按字面量判事件：{offenders}"
def test_the_two_stores_of_the_partition_never_disagree() -> None:
    """热路径那份精确索引与内核注册表必须同步：两边都是同一批注册函数写的。"""
    from core.run_event_taxonomy import _BY_NAME  # noqa: SLF001

    assert set(_BY_NAME) == set(RUN_EVENT_TYPE_SPECS.names())
    register_run_event_type(_probe("node.consistency", "node"))
    assert set(_BY_NAME) == set(RUN_EVENT_TYPE_SPECS.names())
    assert resolve_run_event_type("node.consistency") is _BY_NAME["node.consistency"]
    unregister_run_event_type("node.consistency")
    assert set(_BY_NAME) == set(RUN_EVENT_TYPE_SPECS.names())
    assert "node.consistency" not in _BY_NAME


def test_withdrawing_with_a_non_canonical_spelling_moves_both_stores() -> None:
    """内核按 ``strip().lower()`` 存键，精确索引按原样存：撤回时少归一一次就只清空一边。

    留下的那份仍然被 ``resolve_run_event_type`` 命中，于是同一个事件类型"在枚举里
    不存在、在查表里活着"——两个 reducer 会各按一半事实投影。
    """
    from core.run_event_taxonomy import _BY_NAME  # noqa: SLF001

    register_run_event_type(_probe("node.padded", "node"))
    unregister_run_event_type("  NODE.PADDED  ")
    assert set(_BY_NAME) == set(RUN_EVENT_TYPE_SPECS.names())
    assert resolve_run_event_type("node.padded") is None
    assert "node.padded" not in run_event_types_in_family("node")


def test_the_rollup_words_are_the_ones_the_console_shows() -> None:
    """装饰词按字面钉住，不能拿被测方法自己比自己。

    node 与 retry 的终端事件**后缀是同一批词**（completed/failed/skipped），全靠
    ``rollup_prefix`` 装饰才不撞键。原有用例断言的是 ``status == spec.rollup_status(t)``，
    把 ``rollup_status`` 改成忽略前缀也照样全绿。
    """
    assert _spec("node.completed").rollup_status("node.completed") == "completed"
    assert _spec("node.failed").rollup_status("node.failed") == "failed"
    assert _spec("retry.completed").rollup_status("retry.completed") == "retry_completed"
    assert _spec("retry.failed").rollup_status("retry.failed") == "retry_failed"
    assert _spec("node.started").running_rollup_status == "running"
    assert _spec("retry.started").running_rollup_status == "retry_running"
    assert _spec("node.skipped").unknown_rollup_status == "unknown"
    assert _spec("retry.skipped").unknown_rollup_status == "retry_unknown"
    assert (
        _spec("node.completed").rollup_status("node.completed")
        != _spec("retry.completed").rollup_status("retry.completed")
    ), "两族终端事件装饰出同一个词，两个 rollup 会互相盖"


def test_the_hot_path_looks_up_in_its_own_index_not_the_kernel_menu(monkeypatch) -> None:
    """23 倍热路径回归是**行为等价**的，任何用例都挡不住，只能钉实现形状。

    走 ``RUN_EVENT_TYPE_SPECS.names()`` 每次要排两遍序，而这条查表每个 SSE 事件、每
    条存库事件的 reducer 都要跑一次。这里如实记下：本条不是判据 §3.1 的行为等价证据，
    是给一个可测不出来的性质上的形状守卫。
    """
    from core.providers import ProviderRegistry

    calls: list[str] = []
    real = ProviderRegistry.names

    def counted(self: ProviderRegistry) -> tuple[str, ...]:
        calls.append("names")
        return real(self)

    monkeypatch.setattr(ProviderRegistry, "names", counted)
    assert RUN_EVENT_TYPE_SPECS.names()
    assert calls == ["names"], "计数桩没接到内核菜单上，下面几条断言会是空跑"
    calls.clear()
    assert resolve_run_event_type("run.completed") is not None
    assert resolve_run_event_type("run.not-a-type") is None
    assert resolve_run_event_type("RUN.COMPLETED") is None
    assert run_event_types_in_family("node")
    assert calls == [], "热路径又去排内核那份菜单了"


def test_the_builtin_declaration_list_has_no_duplicate() -> None:
    """清单里写重一次过去是静默后者覆盖前者；现在注册期就拒，所以清单本身也要钉住。"""
    names = [spec.event_type for spec in BUILTIN_RUN_EVENT_SPECS]
    assert len(set(names)) == len(names), names


def test_run_terminal_statuses_stay_a_subset_of_the_persisted_column() -> None:
    """``run_status`` 是要写进 ``RunRecord.status`` 那列的值，列的合法集在别处定义。

    ``_RUN_TERMINAL_STATUSES`` 现在从 ``RunTerminalStatus`` 派生（不再手抄第三遍），
    这条把它与列的枚举拴在一起：列少了这个值，注册表就不能声明它。
    """
    from core.run_event_taxonomy import RunTerminalStatus

    assert set(get_args(RunTerminalStatus)) <= set(get_args(run_registry.RunStatus))
    declared = {
        spec.run_status
        for spec in BUILTIN_RUN_EVENT_SPECS
        if spec.run_status is not None
    }
    assert declared == set(get_args(RunTerminalStatus))


def test_rollup_prefix_has_to_match_the_family_it_declares() -> None:
    """投影里的状态词由 prefix 拼出来；写错了要注册期炸，不是跑起来才对不上。"""
    for family, prefix, reason in (
        ("retry", "", "rollup_prefix"),
        ("node", "retry_", "rollup_prefix"),
        ("node", "", None),
    ):
        spec = RunEventTypeSpec(
            event_type=f"{family}.prefixed",
            family=family,  # type: ignore[arg-type]
            phase="terminal",
            rollup_namespace=f"{family}.",
            rollup_prefix=prefix,
            lifecycle_fields_required=True,
        )
        if reason is None:
            register_run_event_type(spec)
            unregister_run_event_type(spec.event_type)
            continue
        with pytest.raises(ValueError, match=reason):
            register_run_event_type(spec)


def test_run_events_no_longer_keeps_its_own_membership_sets() -> None:
    source = HOST_FILES["core/run_events.py"].read_text(encoding="utf-8")
    assert "_NODE_TYPES" not in source
    assert "_RETRY_TYPES" not in source
    validator = inspect.getsource(run_events.RunEvent._lifecycle_fields_match_type)  # noqa: SLF001
    assert "resolve_run_event_type" in validator
    for event_type in WIRE_EVENT_TYPES:
        assert f'"{event_type}"' not in validator


def test_the_executor_value_set_is_declared_in_exactly_one_file() -> None:
    assert "RAG_EXECUTORS: tuple[str, ...]" in HOST_FILES[
        "core/run_events.py"
    ].read_text(encoding="utf-8")
    assert "Literal[tuple(RAG_EXECUTORS)]" in HOST_FILES[
        "rag_topology.py"
    ].read_text(encoding="utf-8")
    # run_registry and run_ops each used to retype the same four values as their own list.
    for name in ("core/run_registry.py", "server/run_ops.py"):
        source = HOST_FILES[name].read_text(encoding="utf-8")
        for executor in ("sequential_stream", "sequential", "langgraph", "cache_replay"):
            assert f'"{executor}"' not in source, f"{name} 仍自带一份 executor 值集"


# --------------------------------------------------------------------------- #
# 3. behavioural proof: the reducers follow the table, whatever it declares
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("family", ["node", "retry"])
def test_every_declared_start_opens_and_every_terminal_closes_a_slot(family: str) -> None:
    starts, closes = _of(family, "start"), _of(family, "terminal")
    assert starts and closes
    spec = _spec(starts[0])
    for start in starts:
        _, record = _reduce([_payload(start)])
        active = record.active_nodes if family == "node" else record.active_retries
        assert active == {"search": 1}, start
        assert record.rollups[(family, "search", 1)].status == spec.running_rollup_status
    for terminal in closes:
        _, record = _reduce([_payload(starts[0]), _payload(terminal)])
        active = record.active_nodes if family == "node" else record.active_retries
        assert active == {}, terminal
        assert record.rollups[(family, "search", 1)].status == _spec(terminal).rollup_status(
            terminal
        )
        assert _rollups([_payload(starts[0]), _payload(terminal)]) == [
            _spec(terminal).rollup_status(terminal)
        ], terminal


def test_summary_effects_belong_to_the_live_projection_only() -> None:
    """The B-2 divergence, pinned as tested fact rather than left as a rumour.

    ``node.failed`` -> ``failed_nodes`` and ``route.selected`` -> ``route`` are applied by
    the registry reducer only. ``run_ops`` has no return channel for them: it yields
    ``tuple[NodeRollup, ...]``, and the durable summary row already stores
    ``failed_node_ids`` / ``route`` as its own columns.
    """
    payloads = [
        _payload("node.started"),
        _payload("node.failed", error={"type": "E", "code": "C", "recoverable": True}),
        _payload("route.selected", node_id=None, attributes={"route": "hybrid"}),
        _payload("retry.started", attributes={"reason": "backoff"}),
        _payload("degraded", attributes={"reason": "slow"}),
    ]
    _, record = _reduce(payloads)
    assert tuple(record.failed_nodes) == ("search",)
    assert record.route == "hybrid"
    assert record.retry_count == 1
    assert record.degraded_count == 1

    assert _rollups(payloads) == ["failed", "retry_running"]
    # The same ledger replayed for rollups reports neither the failure nor the route:
    # _rollups_from_events returns rollups and nothing else.
    rows = run_ops._rollups_from_events(tuple(payloads))  # noqa: SLF001
    assert [(row.node_id, row.attempt) for row in rows] == [("search", 1), ("search", 1)]
    assert {row.status for row in rows} == {"failed", "retry_running"}


def test_counters_and_rollups_follow_declared_effects_over_the_whole_vocabulary() -> None:
    payloads = [
        _payload(
            event_type,
            attributes={"route": "hybrid"} if event_type == "route.selected" else None,
        )
        for event_type in WIRE_EVENT_TYPES
    ]
    _, record = _reduce(payloads)
    last_node_terminal = _of("node", "terminal")[-1]
    last_retry_terminal = _of("retry", "terminal")[-1]

    assert record.retry_count == sum(1 for name in WIRE_EVENT_TYPES if _spec(name).counts_retry)
    assert record.degraded_count == sum(
        1 for name in WIRE_EVENT_TYPES if _spec(name).counts_degraded
    )
    assert record.status == run_terminal_status(WIRE_EVENT_TYPES[-1])
    assert record.rollups[("node", "search", 1)].status == _spec(
        last_node_terminal
    ).rollup_status(last_node_terminal)
    assert record.rollups[("retry", "search", 1)].status == _spec(
        last_retry_terminal
    ).rollup_status(last_retry_terminal)
    assert _rollups(payloads) == [
        _spec(last_node_terminal).rollup_status(last_node_terminal),
        _spec(last_retry_terminal).rollup_status(last_retry_terminal),
    ]


# --------------------------------------------------------------------------- #
# 4. the criterion: a new event type costs zero edits to existing branches
# --------------------------------------------------------------------------- #
def _probe(event_type: str, family: str) -> RunEventTypeSpec:
    return RunEventTypeSpec(
        event_type=event_type,
        family=family,  # type: ignore[arg-type]
        phase="terminal",
        rollup_namespace=f"{family}.",
        rollup_prefix="" if family == "node" else "retry_",
        lifecycle_fields_required=True,
    )


@pytest.mark.parametrize(
    ("event_type", "family", "expected_status"),
    [
        ("node.timed_out", "node", "timed_out"),
        ("retry.aborted", "retry", "retry_aborted"),
    ],
)
def test_a_new_event_type_needs_no_edit_to_either_reducer(
    event_type: str,
    family: str,
    expected_status: str,
) -> None:
    hosts_before = _host_bytes()
    start = "node.started" if family == "node" else "retry.started"
    register_run_event_type(_probe(event_type, family))
    try:
        assert _spec(event_type).rollup_status(event_type) == expected_status
        assert event_type in run_event_types_with(family, "terminal")  # type: ignore[arg-type]

        _, record = _reduce([_payload(start), _payload(event_type)])
        active = record.active_nodes if family == "node" else record.active_retries
        assert active == {}, "registry did not close the slot for a newly declared event"
        rollup = record.rollups[(family, "search", 1)]
        assert rollup.status == expected_status
        assert rollup.finished_elapsed_ms == 10.0
        assert rollup.duration_ms == 4.0

        assert _rollups([_payload(start), _payload(event_type)]) == [expected_status], (
            "run_ops did not classify the newly declared event"
        )
    finally:
        unregister_run_event_type(event_type)

    assert _hosts_unchanged(hosts_before), (
        "注册一个新 event type 竟要改动宿主文件 —— 判据不成立"
    )
    assert resolve_run_event_type(event_type) is None


def test_a_probe_without_slot_semantics_stays_inert() -> None:
    """A new annotation event must not be mistaken for a node/retry event."""
    hosts_before = _host_bytes()
    register_run_event_type(
        RunEventTypeSpec("annotation.probe", "annotation", "standalone", records_route=True)
    )
    try:
        _, record = _reduce([_payload("node.started"), _payload("annotation.probe")])
        assert record.active_nodes == {"search": 1}
        assert record.route is None  # no route attribute supplied, so nothing is recorded
        assert len(record.rollups) == 1
        assert _rollups([_payload("node.started"), _payload("annotation.probe")]) == ["running"]
    finally:
        unregister_run_event_type("annotation.probe")
    assert _hosts_unchanged(hosts_before)


# --------------------------------------------------------------------------- #
# 5. registration dies on shape, not at run time
# --------------------------------------------------------------------------- #
def test_duplicate_registration_is_refused_unless_replaced() -> None:
    register_run_event_type(_probe("node.dupe", "node"))
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_run_event_type(_probe("node.dupe", "node"))
        register_run_event_type(_probe("node.dupe", "node"), replace=True)
        assert _spec("node.dupe").closes_active_slot
    finally:
        unregister_run_event_type("node.dupe")
    assert resolve_run_event_type("node.dupe") is None


@pytest.mark.parametrize(
    ("spec", "reason"),
    [
        (RunEventTypeSpec("", "annotation", "standalone"), "non-empty"),
        (RunEventTypeSpec("Node.Loose", "annotation", "standalone"), "lower-case"),
        (RunEventTypeSpec("node.loose", "node", "standalone"), "start or a terminal"),
        (
            RunEventTypeSpec(
                "node.loose", "node", "terminal", rollup_namespace="retry.",
                rollup_prefix="retry_", lifecycle_fields_required=True,
            ),
            "rollup_namespace",
        ),
        (
            RunEventTypeSpec(
                "node.loose", "node", "terminal", rollup_namespace="node.",
            ),
            "require node_id",
        ),
        (
            RunEventTypeSpec("annotation.loose", "annotation", "start", rollup_prefix="x_"),
            "only node and retry events own rollups",
        ),
        (
            RunEventTypeSpec(
                "node.loose", "node", "terminal", rollup_namespace="node.",
                lifecycle_fields_required=True, run_status="completed",
            ),
            "run_status requires a run terminal",
        ),
        (
            RunEventTypeSpec(
                "node.loose", "node", "start", rollup_namespace="node.",
                lifecycle_fields_required=True, counts_retry=True,
            ),
            "counts_retry requires a retry start",
        ),
        (
            RunEventTypeSpec(
                "node.loose", "node", "start", rollup_namespace="node.",
                lifecycle_fields_required=True, records_failed_node=True,
            ),
            "records_failed_node requires a node terminal",
        ),
        (
            RunEventTypeSpec("run.loose", "run", "terminal", run_status="expired"),  # type: ignore[arg-type]
            "unknown run status",
        ),
    ],
    ids=[
        "empty",
        "upper-case",
        "standalone-node",
        "wrong-namespace",
        "missing-lifecycle",
        "annotation-rollup",
        "status-on-node",
        "retry-count-on-node-start",
        "failed-node-on-node-start",
        "inventable-run-status",
    ],
)
def test_a_half_declared_event_type_dies_at_registration(
    spec: RunEventTypeSpec,
    reason: str,
) -> None:
    with pytest.raises(ValueError, match=reason):
        register_run_event_type(spec)
    assert resolve_run_event_type(spec.event_type) is None


def test_the_run_status_vocabulary_is_not_widenable_from_the_table() -> None:
    """``RunStatus`` is a persisted column, so inventing a fourth value is a migration.

    The table refuses it: a new run terminal cannot half-exist by making the projection
    report a status the history row cannot store.
    """
    with pytest.raises(ValueError, match="unknown run status"):
        register_run_event_type(
            RunEventTypeSpec("run.expired", "run", "terminal", run_status="expired")  # type: ignore[arg-type]
        )
    assert resolve_run_event_type("run.expired") is None


# --------------------------------------------------------------------------- #
# 6. what an unknown event type does today, recorded so it stays that way
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "unknown", ["bogus.type", "node.timed_out", "", "RUN.STARTED", "run.completed "]
)
def test_unknown_event_type_is_a_no_op_in_both_reducers(unknown: str) -> None:
    _, record = _reduce([_payload("node.started"), _payload(unknown)])
    assert record.rollups[("node", "search", 1)].status == "running", unknown
    assert record.active_nodes == {"search": 1}, unknown
    assert record.status == "running", unknown
    assert record.retry_count == record.degraded_count == 0, unknown
    assert record.route is None and tuple(record.failed_nodes) == (), unknown
    assert _rollups([_payload("node.started"), _payload(unknown)]) == ["running"], unknown


@pytest.mark.parametrize("weird_hashable", [None, 7, 3.5], ids=["none", "int", "float"])
def test_non_string_event_type_is_not_a_membership_hit(weird_hashable: Any) -> None:
    _, record = _reduce([_payload("node.started"), _payload(weird_hashable)])
    assert record.rollups[("node", "search", 1)].status == "running"
    assert _rollups([_payload("node.started"), _payload(weird_hashable)]) == ["running"]


def test_unknown_event_type_is_still_rejected_by_the_envelope() -> None:
    with pytest.raises(ValidationError):
        _envelope("bogus.type")


def test_unhashable_event_type_still_raises_type_error_as_the_sets_did() -> None:
    """Accidental-but-real behaviour of ``event_type in {...}``, kept on purpose."""
    with pytest.raises(TypeError, match="unhashable"):
        _reduce([_payload(["node.started"])])
    with pytest.raises(TypeError, match="unhashable"):
        _rollups([_payload(["node.started"])])


def test_project_only_still_suppresses_the_run_terminal() -> None:
    _, record = _reduce([_payload("run.completed", node_id=None, attempt=None)], project_only=True)
    assert record.status == "running"
    assert run_terminal_status("run.completed") == "completed"


# --------------------------------------------------------------------------- #
# 7. inventory D.5 — the RagExecutor gap
# --------------------------------------------------------------------------- #
def test_rag_executor_is_declared_once_and_includes_cache_replay() -> None:
    assert "cache_replay" in run_events.RAG_EXECUTORS
    assert run_events.RAG_EXECUTORS == get_args(run_events.RagExecutor)
    assert get_args(rag_topology.RagExecutor) == run_events.RAG_EXECUTORS
    assert get_args(run_ops.RunTopologyResponse.model_fields["executor"].annotation) == (
        run_events.RAG_EXECUTORS
    )
    assert run_registry._TOPOLOGY_EXECUTORS == frozenset(run_events.RAG_EXECUTORS)  # noqa: SLF001
    assert len(set(run_events.RAG_EXECUTORS)) == len(run_events.RAG_EXECUTORS)
    # sequential_stream stays first: it is the enum order the OpenAPI contract carries.
    assert run_events.RAG_EXECUTORS[0] == "sequential_stream"


def test_cache_replay_is_a_real_executor_everywhere_it_is_emitted() -> None:
    topology = rag_topology.build_cache_replay_topology()
    assert topology.executor == "cache_replay"
    assert topology.executor in run_events.RAG_EXECUTORS
    response = run_ops.RunTopologyResponse.model_validate(
        {
            "id": topology.id,
            "revision": topology.revision,
            "executor": topology.executor,
            "nodes": [],
            "edges": [],
        }
    )
    assert response.executor == "cache_replay"
    # The registry sanitizer already accepted it, which is what made the gap silent.
    sanitized = run_registry._sanitize_topology(  # noqa: SLF001
        {
            "revision": topology.revision,
            "id": topology.id,
            "executor": topology.executor,
            "nodes": [],
            "edges": [],
        },
        topology_revision=topology.revision,
        topology_id=topology.id,
        settings=RunHistorySettings(persistence_enabled=False),
    )
    assert sanitized["executor"] == "cache_replay"


def test_a_rejected_executor_is_still_reported_by_reason() -> None:
    topology = rag_topology.build_rag_topology(
        executor="sequential_stream",
        enabled_components=frozenset(),
        available_components=frozenset(),
    )
    with pytest.raises(run_registry._RejectedValue) as exc_info:  # noqa: SLF001
        run_registry._sanitize_topology(  # noqa: SLF001
            {
                "revision": topology.revision,
                "id": topology.id,
                "executor": "cache_replay_v3",
                "nodes": [],
                "edges": [],
            },
            topology_revision=topology.revision,
            topology_id=topology.id,
            settings=RunHistorySettings(persistence_enabled=False),
        )
    assert exc_info.value.reason == "topology_invalid_executor"
