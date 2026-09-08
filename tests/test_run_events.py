from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from core.run_events import (
    EventError,
    MemoryRunEventSink,
    RunEvent,
    RunEventSequencer,
    run_event_dict,
    run_event_json,
)
from core.tracing import (
    current_run_observer,
    current_trace,
    is_tracing_enabled,
    set_tracing_enabled,
    trace_session,
)


FIXED_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def _event_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": "run-1",
        "seq": 1,
        "occurred_at": FIXED_NOW,
        "elapsed_ms": 0.0,
        "topology_id": "rag.query",
        "topology_revision": "rev-1",
        "type": "run.started",
    }
    payload.update(overrides)
    return payload


def _sequencer(
    run_id: str = "run-1",
    *,
    sinks: tuple[Any, ...] | None = None,
) -> tuple[RunEventSequencer, MemoryRunEventSink]:
    monotonic_values = iter([10.0 + (index / 10) for index in range(100)])
    memory_sink = MemoryRunEventSink()
    sequencer = RunEventSequencer(
        run_id,
        topology_id="rag.query",
        topology_revision="rev-1",
        monotonic=lambda: next(monotonic_values),
        utcnow=lambda: FIXED_NOW,
        sinks=sinks if sinks is not None else (memory_sink,),
    )
    return sequencer, memory_sink


def test_run_started_is_seq_one_and_terminal_is_last() -> None:
    sequencer, sink = _sequencer()

    sequencer.start_run(executor_requested="sequential_stream", retry=True)
    sequencer.start_node("search", attempt=1)
    sequencer.complete_node("search", attempt=1, attributes={"chunks": 3})
    sequencer.complete_run(outcome="answered", executor_used="sequential_stream")

    assert [event.seq for event in sink.events] == [1, 2, 3, 4]
    assert [event.type for event in sink.events] == [
        "run.started",
        "node.started",
        "node.completed",
        "run.completed",
    ]
    assert sink.events[0].elapsed_ms == pytest.approx(100.0)
    assert sink.events[-1].type == "run.completed"
    assert sequencer.observer().start_node("generate") is None
    assert [event.seq for event in sink.events] == [1, 2, 3, 4]


def test_event_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="unexpected"):
        RunEvent.model_validate(_event_payload(unexpected=True))

    with pytest.raises(ValidationError, match="message"):
        EventError(type="TimeoutError", recoverable=False, message="secret")


def test_event_json_rejects_nan_and_non_json_attributes() -> None:
    invalid_values = [
        math.nan,
        math.inf,
        -math.inf,
        {"not", "json"},
        RuntimeError("not json"),
        FIXED_NOW,
        EventError(type="TypeError", recoverable=False),
        {1: "non-string-key"},
        {"nested": [1, {"bad": object()}]},
    ]

    for value in invalid_values:
        with pytest.raises(ValidationError):
            RunEvent.model_validate(_event_payload(attributes={"value": value}))


def test_event_requires_timezone_aware_occurred_at() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        RunEvent.model_validate(_event_payload(occurred_at=datetime(2026, 8, 22, 12, 0)))


def test_event_is_frozen_and_serializes_as_compact_json() -> None:
    event = RunEvent.model_validate(
        _event_payload(
            attributes={"route": "混合检索", "counts": [1, 2], "nested": {"ok": True}}
        )
    )

    with pytest.raises(ValidationError):
        event.seq = 2  # type: ignore[misc]

    as_dict = run_event_dict(event)
    serialized = run_event_json(event)

    assert as_dict["occurred_at"] == "2026-08-22T12:00:00Z"
    assert json.loads(serialized) == as_dict
    assert "混合检索" in serialized
    assert ": " not in serialized
    assert ", " not in serialized


def test_published_nested_attributes_are_detached_and_deeply_immutable() -> None:
    original: dict[str, Any] = {
        "nested": {"values": [1, 2], "metadata": {"route": "hybrid"}}
    }
    sequencer, sink = _sequencer()

    published = sequencer.start_run(attributes=original)
    original["nested"]["values"].append(99)
    original["nested"]["metadata"]["route"] = "mutated"

    expected = {
        "nested": {"values": [1, 2], "metadata": {"route": "hybrid"}}
    }
    assert run_event_dict(published)["attributes"] == expected

    retained: Any = sink.events[0].attributes
    with pytest.raises(TypeError):
        retained["nested"]["values"].append(3)
    with pytest.raises(TypeError):
        retained["nested"]["metadata"]["route"] = "changed"
    with pytest.raises(TypeError):
        retained["new"] = True

    serialized_snapshot: Any = run_event_dict(sink.events[0])
    serialized_snapshot["attributes"]["nested"]["values"].append(4)
    assert run_event_dict(sink.events[0])["attributes"] == expected
    assert json.loads(run_event_json(sink.events[0]))["attributes"] == expected


def test_node_cannot_complete_without_start() -> None:
    sequencer, sink = _sequencer()
    sequencer.start_run()

    with pytest.raises(RuntimeError, match="not active"):
        sequencer.complete_node("search")

    assert [event.type for event in sink.events] == ["run.started"]


def test_node_attempt_cannot_start_twice() -> None:
    sequencer, sink = _sequencer()
    sequencer.start_run()
    sequencer.start_node("search")

    with pytest.raises(RuntimeError, match="already started"):
        sequencer.start_node("search")

    assert [event.type for event in sink.events] == ["run.started", "node.started"]


def test_skipped_node_was_not_started() -> None:
    sequencer, sink = _sequencer()
    sequencer.start_run()
    sequencer.start_node("rerank")

    with pytest.raises(RuntimeError, match="started node"):
        sequencer.skip_node("rerank", reason="disabled")

    sequencer.cancel_node("rerank")
    sequencer.skip_node("sentence_window", reason="disabled")
    assert sink.events[-1].type == "node.skipped"


def test_repeatable_attempts_are_contiguous() -> None:
    sequencer, sink = _sequencer()
    sequencer.start_run()

    with pytest.raises(RuntimeError, match="attempt 1"):
        sequencer.start_node("retrieve", attempt=2, repeatable=True)

    sequencer.start_node("retrieve", attempt=1, repeatable=True)
    with pytest.raises(RuntimeError, match="active"):
        sequencer.start_node("retrieve", attempt=2, repeatable=True)

    sequencer.fail_node("retrieve", attempt=1, error_type="TimeoutError", recoverable=True)
    with pytest.raises(RuntimeError, match="contiguous"):
        sequencer.start_node("retrieve", attempt=3, repeatable=True)

    sequencer.start_node("retrieve", attempt=2, repeatable=True)
    sequencer.complete_node("retrieve", attempt=2)

    assert [event.attempt for event in sink.events if event.node_id == "retrieve"] == [1, 1, 2, 2]
    assert sink.events[2].error == EventError(type="TimeoutError", recoverable=True)


def test_skipped_non_repeatable_node_requires_attempt_one() -> None:
    sequencer, _ = _sequencer()
    sequencer.start_run()

    with pytest.raises(RuntimeError, match="attempt 1"):
        sequencer.skip_node("rerank", attempt=2, reason="disabled")


def test_invalid_start_attributes_do_not_reserve_the_attempt() -> None:
    sequencer, sink = _sequencer()
    sequencer.start_run()

    with pytest.raises(ValueError, match="non-JSON"):
        sequencer.start_node("search", attributes={"bad": object()})

    sequencer.start_node("search")
    assert [event.type for event in sink.events] == ["run.started", "node.started"]


def test_non_repeatable_node_rejects_second_attempt() -> None:
    sequencer, _ = _sequencer()
    sequencer.start_run()
    sequencer.start_node("search")
    sequencer.complete_node("search")

    with pytest.raises(RuntimeError, match="not repeatable"):
        sequencer.start_node("search", attempt=2, repeatable=True)


def test_route_retry_degraded_and_cancel_events_follow_lifecycle() -> None:
    sequencer, sink = _sequencer()
    sequencer.start_run()
    sequencer.route_selected("retrieve", route="hybrid")
    sequencer.degraded("rerank", reason="provider_unavailable")

    with pytest.raises(RuntimeError, match="attempt 1"):
        sequencer.retry_skipped("second_round", attempt=2, reason="disabled")

    sequencer.retry_started("second_round", attempt=1)
    sequencer.retry_failed("second_round", attempt=1, error_type="TimeoutError")
    sequencer.retry_started("second_round", attempt=2)
    sequencer.retry_completed("second_round", attempt=2)
    sequencer.retry_skipped("second_round", attempt=3, reason="exhausted")
    sequencer.cancel_run(reason="client_disconnected")

    assert [event.type for event in sink.events] == [
        "run.started",
        "route.selected",
        "degraded",
        "retry.started",
        "retry.failed",
        "retry.started",
        "retry.completed",
        "retry.skipped",
        "run.cancelled",
    ]
    assert sink.events[1].attributes == {"route": "hybrid"}
    assert sink.events[-1].attributes == {"reason": "client_disconnected"}


def test_started_nodes_must_close_before_terminal() -> None:
    sequencer, sink = _sequencer()
    sequencer.start_run()
    sequencer.start_node("generate")

    with pytest.raises(RuntimeError, match="active node"):
        sequencer.complete_run(outcome="answered")

    sequencer.complete_node("generate")
    sequencer.complete_run(outcome="answered")
    assert sink.events[-1].type == "run.completed"


def test_terminal_is_exactly_once() -> None:
    sequencer, sink = _sequencer()
    sequencer.start_run()
    sequencer.fail_run(error_type="TimeoutError")

    with pytest.raises(RuntimeError, match="terminal"):
        sequencer.cancel_run(reason="client_disconnected")

    assert [event.type for event in sink.events] == ["run.started", "run.failed"]


def test_sink_exception_does_not_escape_observer() -> None:
    memory_sink = MemoryRunEventSink()

    def throwing_sink(_event: RunEvent) -> None:
        raise OSError("sink unavailable")

    sequencer, _ = _sequencer(sinks=(throwing_sink, memory_sink))
    observer = sequencer.observer()

    assert observer.start_run(source="test") is not None
    assert observer.start_node("search") is not None
    assert observer.complete_node("search") is not None
    assert observer.complete_run(outcome="answered") is not None
    assert observer.start_node("too-late") is None
    assert [event.seq for event in memory_sink.events] == [1, 2, 3, 4]


def test_memory_sink_returns_a_copy() -> None:
    sequencer, sink = _sequencer()
    sequencer.start_run()

    copied = sink.events
    copied.clear()

    assert [event.type for event in sink.events] == ["run.started"]


def test_elapsed_time_is_monotonic_despite_wall_clock_rollback() -> None:
    monotonic_values = iter([5.0, 5.2, 5.4, 5.6])
    wall_values = iter(
        [
            FIXED_NOW,
            FIXED_NOW - timedelta(hours=1),
            FIXED_NOW - timedelta(hours=2),
        ]
    )
    sink = MemoryRunEventSink()
    sequencer = RunEventSequencer(
        "run-clock",
        topology_id="rag.query",
        topology_revision="rev-1",
        monotonic=lambda: next(monotonic_values),
        utcnow=lambda: next(wall_values),
        sinks=(sink,),
    )

    sequencer.start_run()
    sequencer.skip_node("cache", reason="miss")
    sequencer.complete_run(outcome="answered")

    assert [event.elapsed_ms for event in sink.events] == pytest.approx([200.0, 400.0, 600.0])
    assert all(event.occurred_at.tzinfo is not None for event in sink.events)


def test_shared_sequencer_is_thread_safe_and_sink_order_matches_seq() -> None:
    sink = MemoryRunEventSink()
    sequencer = RunEventSequencer(
        "run-shared",
        topology_id="rag.query",
        topology_revision="rev-1",
        sinks=(sink,),
    )
    sequencer.start_run()

    def emit_node(index: int) -> None:
        node_id = f"plugin-{index}"
        sequencer.start_node(node_id)
        sequencer.complete_node(node_id)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(emit_node, range(32)))

    sequencer.complete_run(outcome="answered")

    assert [event.seq for event in sink.events] == list(range(1, 67))
    assert sink.events[-1].type == "run.completed"


def test_twenty_runs_have_independent_gapless_sequences() -> None:
    def execute_run(index: int) -> list[int]:
        sink = MemoryRunEventSink()
        sequencer = RunEventSequencer(
            f"run-{index}",
            topology_id="rag.query",
            topology_revision="rev-1",
            sinks=(sink,),
        )
        sequencer.start_run()
        sequencer.start_node("search")
        sequencer.complete_node("search")
        sequencer.complete_run(outcome="answered")
        return [event.seq for event in sink.events]

    with ThreadPoolExecutor(max_workers=8) as executor:
        sequences = list(executor.map(execute_run, range(20)))

    assert sequences == [[1, 2, 3, 4]] * 20


def test_trace_as_list_is_identical_with_observer() -> None:
    sequencer, _ = _sequencer()
    observer = sequencer.observer()

    with trace_session("q-1", observer=observer) as trace:
        assert current_run_observer() is observer
        trace.add_span("search", 12.5)

    assert trace.as_list() == ["search:12.50ms"]
    assert current_run_observer() is None


def test_tracing_disabled_does_not_disable_run_observer() -> None:
    sequencer, _ = _sequencer()
    observer = sequencer.observer()
    previous_enabled = is_tracing_enabled()
    set_tracing_enabled(False)

    try:
        with trace_session("q-1", observer=observer):
            assert current_trace() is None
            assert current_run_observer() is observer
    finally:
        set_tracing_enabled(previous_enabled)

    assert current_run_observer() is None


def test_nested_trace_sessions_restore_observer_bindings() -> None:
    outer_sequencer, _ = _sequencer("run-outer")
    inner_sequencer, _ = _sequencer("run-inner")
    outer_observer = outer_sequencer.observer()
    inner_observer = inner_sequencer.observer()

    with trace_session("q-outer", observer=outer_observer) as outer_trace:
        assert current_trace() is outer_trace
        assert current_run_observer() is outer_observer

        with trace_session("q-inner", observer=inner_observer) as inner_trace:
            assert current_trace() is inner_trace
            assert current_run_observer() is inner_observer

        assert current_trace() is outer_trace
        assert current_run_observer() is outer_observer

    assert current_trace() is None
    assert current_run_observer() is None


def test_trace_session_cleans_observer_after_exception() -> None:
    sequencer, _ = _sequencer()
    observer = sequencer.observer()

    with pytest.raises(RuntimeError, match="query failed"):
        with trace_session("q-1", observer=observer):
            raise RuntimeError("query failed")

    assert current_trace() is None
    assert current_run_observer() is None
