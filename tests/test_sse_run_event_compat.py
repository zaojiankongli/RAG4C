from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from core.metrics import get_metrics
from core.run_events import MemoryRunEventSink, RunEventSequencer
from server import app as server_app


def _inner_result(query: str = "报销流程", *, abstained: bool = False) -> dict[str, Any]:
    return {
        "query": query,
        "answer": "请先提交申请。" if not abstained else "",
        "route": "hybrid",
        "citations": [],
        "traces": [],
        "verdict": {},
        "abstained": abstained,
    }


def _cached_payload(
    query: str = "报销流程", *, abstained: bool = False
) -> dict[str, Any]:
    return {
        "result": _inner_result(query, abstained=abstained),
        "using_mock": False,
        "duration_ms": 12.5,
    }


def _fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        pipeline=SimpleNamespace(
            hyde_on=False,
            subqueries_on=False,
            stepback_on=False,
            graph_retrieval_on=False,
            rerank_on=False,
            sentence_window_on=False,
        )
    )


class _Request:
    async def is_disconnected(self) -> bool:
        return False


def _parse_chunk(chunk: str | bytes) -> list[dict[str, Any]]:
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    return [
        json.loads(block.removeprefix("data: "))
        for block in text.split("\n\n")
        if block.startswith("data: ")
    ]


async def _collect(response: Any) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    async for chunk in response.body_iterator:
        frames.extend(_parse_chunk(chunk))
    return frames


def _legacy(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [frame for frame in frames if frame["type"] != "run_event"]


def _run_events(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [frame["event"] for frame in frames if frame["type"] == "run_event"]


@pytest.fixture(autouse=True)
def _isolate_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    server_app._shutdown_query_executor()
    server_app._aio_flights.clear()
    server_app._pending = 0
    server_app._query_slots = asyncio.Semaphore(32)
    get_metrics().reset()
    monkeypatch.setattr(server_app, "_cache_key", lambda query, *_args: f"key:{query}")
    monkeypatch.setattr(server_app, "_serving_snapshot", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(server_app, "_cache_release", lambda *_args: None)
    monkeypatch.setattr(server_app, "_record_query", lambda *_args: None)
    monkeypatch.setattr(server_app, "get_settings", _fake_settings, raising=False)
    monkeypatch.setattr(
        server_app,
        "get_pipeline",
        lambda _settings=None: {"retrieval": SimpleNamespace()},
        raising=False,
    )
    yield
    server_app._aio_flights.clear()
    server_app._pending = 0
    server_app._shutdown_query_executor()


@pytest.fixture
def captured_runs(monkeypatch: pytest.MonkeyPatch) -> list[MemoryRunEventSink]:
    captured: list[MemoryRunEventSink] = []

    def factory(*args: Any, **kwargs: Any) -> RunEventSequencer:
        sink = MemoryRunEventSink()
        captured.append(sink)
        kwargs["sinks"] = (*tuple(kwargs.get("sinks", ())), sink)
        return RunEventSequencer(*args, **kwargs)

    monkeypatch.setattr(server_app, "RunEventSequencer", factory, raising=False)
    return captured


def _assert_one_terminal(events: list[dict[str, Any]], expected: str) -> None:
    terminals = [
        event
        for event in events
        if event["type"] in {"run.completed", "run.failed", "run.cancelled"}
    ]
    assert [event["type"] for event in terminals] == [expected]


def test_normal_stream_adds_gapless_run_events_without_changing_legacy_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app,
        "_cache_reserve",
        lambda _key: (None, threading.Event(), True),
    )
    cache_writes: list[dict[str, Any]] = []
    recent_records: list[dict[str, Any]] = []
    monkeypatch.setattr(server_app, "_cache_put", lambda _key, value: cache_writes.append(value))
    monkeypatch.setattr(server_app, "_record_query", recent_records.append)
    calls: list[dict[str, Any]] = []

    def stream(query: str, **kwargs: Any) -> Iterator[dict[str, Any]]:
        calls.append({"query": query, **kwargs})
        yield {"type": "phase", "phase": "retrieving"}
        yield {"type": "token", "text": "请先提交申请。"}
        yield {"type": "done", "result": _inner_result(query)}

    monkeypatch.setattr(server_app, "answer_query_stream", stream)

    async def scenario() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="报销流程"), _Request()
        )
        return await _collect(response)

    frames = asyncio.run(scenario())
    events = _run_events(frames)
    legacy = _legacy(frames)

    assert legacy[:2] == [
        {"type": "phase", "phase": "retrieving"},
        {"type": "token", "text": "请先提交申请。"},
    ]
    assert legacy[2]["type"] == "done"
    assert legacy[2]["result"]["result"] == _inner_result()
    assert legacy[2]["result"]["using_mock"] is False
    assert isinstance(legacy[2]["result"]["duration_ms"], float)
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert len({event["run_id"] for event in events}) == 1
    assert events[0]["type"] == "run.started"
    assert events[0]["attributes"]["topology"]["id"] == "rag.query"
    _assert_one_terminal(events, "run.completed")
    done_index = next(i for i, frame in enumerate(frames) if frame["type"] == "done")
    terminal_index = next(
        i
        for i, frame in enumerate(frames)
        if frame["type"] == "run_event" and frame["event"]["type"] == "run.completed"
    )
    assert terminal_index > done_index
    assert events[-1]["attributes"]["cache_write"] is True
    assert events[-1]["attributes"]["flight_released"] is True
    assert events[-1]["attributes"]["delivery"] == "completed"
    assert events[-1]["attributes"]["queue_admitted"] is True
    assert events[-1]["attributes"]["slot_wait_ms"] >= 0
    assert calls[0]["query_id"] == events[0]["run_id"]
    assert calls[0]["observer"] is not None
    assert len(cache_writes) == 1
    assert cache_writes[0]["result"] == _inner_result()
    assert len(recent_records) == 1
    assert recent_records[0]["query"] == "报销流程"
    metrics = get_metrics().snapshot()
    assert metrics["query.requests"]["count"] == 1
    assert metrics["query.completed"]["count"] == 1
    assert metrics["query.total"]["count"] == 1


def test_l1_replay_owns_a_run_before_lookup_and_uses_fresh_cache_topology(
    monkeypatch: pytest.MonkeyPatch,
    captured_runs: list[MemoryRunEventSink],
) -> None:
    payload = _cached_payload()
    constructed_before_lookup: list[int] = []

    def peek(_key: str) -> dict[str, Any]:
        constructed_before_lookup.append(len(captured_runs))
        return payload

    monkeypatch.setattr(server_app, "_cache_peek", peek)
    monkeypatch.setattr(
        server_app,
        "answer_query_stream",
        lambda *_args, **_kwargs: pytest.fail("L1 replay entered retrieval"),
    )

    async def one() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="报销流程"), _Request()
        )
        return await _collect(response)

    async def scenario() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        first, second = await asyncio.gather(one(), one())
        return first, second

    first, second = asyncio.run(scenario())
    first_events = _run_events(first)
    second_events = _run_events(second)

    assert constructed_before_lookup == [1, 3]
    assert _legacy(first) == server_app._replay_events(payload)
    assert _legacy(second) == server_app._replay_events(payload)
    assert first_events[0]["topology_id"] == "rag.cache_replay"
    assert second_events[0]["topology_id"] == "rag.cache_replay"
    assert first_events[0]["run_id"] != second_events[0]["run_id"]
    _assert_one_terminal(first_events, "run.completed")
    _assert_one_terminal(second_events, "run.completed")
    metrics = get_metrics().snapshot()
    assert metrics["query.cache_hits"]["count"] == 2
    assert metrics["query.completed"]["count"] == 2


def test_l1_abstained_replay_counts_delivery_without_actual_compute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        server_app,
        "_cache_peek",
        lambda _key: _cached_payload(abstained=True),
    )

    async def scenario() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="报销流程"), _Request()
        )
        return await _collect(response)

    frames = asyncio.run(scenario())
    metrics = get_metrics().snapshot()

    assert _legacy(frames)[-1]["type"] == "done"
    assert metrics["query.completed"]["count"] == 1
    assert metrics["query.abstained"]["count"] == 1
    assert "query.total" not in metrics


def test_l2_and_cross_process_replays_do_not_enter_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _cached_payload()
    calls = 0
    released = threading.Event()

    def reserve(_key: str) -> tuple[dict[str, Any] | None, threading.Event, bool]:
        nonlocal calls
        calls += 1
        if calls == 1:
            threading.Timer(0.01, released.set).start()
            return None, released, False
        return payload, released, False

    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(server_app, "_cache_reserve", reserve)
    monkeypatch.setattr(
        server_app,
        "answer_query_stream",
        lambda *_args, **_kwargs: pytest.fail("L2 replay entered retrieval"),
    )

    async def scenario() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="报销流程"), _Request()
        )
        return await _collect(response)

    frames = asyncio.run(scenario())
    events = _run_events(frames)

    assert _legacy(frames) == server_app._replay_events(payload)
    assert events[0]["topology_id"] == "rag.cache_replay"
    _assert_one_terminal(events, "run.completed")
    assert events[-1]["attributes"]["cache_level"] == "l2"
    assert events[-1]["attributes"]["singleflight"] == "cross_process"
    metrics = get_metrics().snapshot()
    assert metrics["query.cache_hits"]["count"] == 1
    assert metrics["query.singleflight_joined"]["count"] == 1


def test_same_process_waiter_gets_an_independent_replay_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _cached_payload()
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)

    async def scenario() -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        future.set_result(payload)
        server_app._aio_flights["key:报销流程"] = future
        response = await server_app.query_stream(
            server_app.QueryRequest(query="报销流程"), _Request()
        )
        return await _collect(response)

    frames = asyncio.run(scenario())
    events = _run_events(frames)

    assert _legacy(frames) == server_app._replay_events(payload)
    assert events[0]["topology_id"] == "rag.cache_replay"
    _assert_one_terminal(events, "run.completed")
    assert events[-1]["attributes"]["singleflight"] == "same_process"
    metrics = get_metrics().snapshot()
    assert metrics["query.singleflight_joined"]["count"] == 1
    assert metrics["query.completed"]["count"] == 1


def test_same_process_wait_timeout_does_not_claim_cache_replay_topology(
    monkeypatch: pytest.MonkeyPatch,
    captured_runs: list[MemoryRunEventSink],
) -> None:
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(server_app, "QUERY_TIMEOUT_S", 0.01)

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        server_app._aio_flights["key:报销流程"] = loop.create_future()
        with pytest.raises(HTTPException) as exc_info:
            await server_app.query_stream(server_app.QueryRequest(query="报销流程"), _Request())
        assert exc_info.value.status_code == 503

    asyncio.run(scenario())
    published = [event.model_dump(mode="json") for sink in captured_runs for event in sink.events]
    started = next(event for event in published if event["type"] == "run.started")

    assert started["topology_id"] == "rag.query"
    assert started["attributes"]["topology"]["id"] == "rag.query"
    assert started["attributes"]["executor_used"] == "sequential_stream"
    assert all(event["node_id"] != "cache.replay" for event in published)
    _assert_one_terminal(published, "run.failed")
    assert published[-1]["error"]["code"] == "singleflight_timeout"


def test_queue_rejection_has_one_cancelled_terminal_and_existing_429(
    monkeypatch: pytest.MonkeyPatch,
    captured_runs: list[MemoryRunEventSink],
) -> None:
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(server_app, "_inc_pending", lambda _tenant=None: False)

    async def scenario() -> None:
        with pytest.raises(HTTPException) as exc_info:
            await server_app.query_stream(server_app.QueryRequest(query="报销流程"), _Request())
        assert exc_info.value.status_code == 429

    asyncio.run(scenario())
    published = [event.model_dump(mode="json") for sink in captured_runs for event in sink.events]

    _assert_one_terminal(published, "run.cancelled")
    assert published[-1]["attributes"]["reason"] == "queue_rejected"
    metrics = get_metrics().snapshot()
    assert metrics["query.requests"]["count"] == 1
    assert metrics["query.rejected"]["count"] == 1
    assert "query.cancelled" not in metrics


def test_slot_timeout_has_one_failed_terminal_and_existing_503(
    monkeypatch: pytest.MonkeyPatch,
    captured_runs: list[MemoryRunEventSink],
) -> None:
    class NeverAvailable:
        async def acquire(self) -> None:
            await asyncio.Future()

    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(server_app, "_query_slots", NeverAvailable())
    monkeypatch.setattr(server_app, "QUERY_TIMEOUT_S", 0.01)

    async def scenario() -> None:
        with pytest.raises(HTTPException) as exc_info:
            await server_app.query_stream(server_app.QueryRequest(query="报销流程"), _Request())
        assert exc_info.value.status_code == 503

    asyncio.run(scenario())
    published = [event.model_dump(mode="json") for sink in captured_runs for event in sink.events]

    _assert_one_terminal(published, "run.failed")
    assert published[-1]["error"]["code"] == "queue_timeout"
    metrics = get_metrics().snapshot()
    assert metrics["query.timeouts"]["count"] == 1
    assert metrics["query.errors"]["count"] == 1


def test_producer_failure_preserves_legacy_error_and_emits_safe_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app,
        "_cache_reserve",
        lambda _key: (None, threading.Event(), True),
    )

    def broken(*_args: Any, **_kwargs: Any) -> Iterator[dict[str, Any]]:
        raise RuntimeError("private prompt contents")
        yield

    monkeypatch.setattr(server_app, "answer_query_stream", broken)

    async def scenario() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="报销流程"), _Request()
        )
        return await _collect(response)

    frames = asyncio.run(scenario())
    events = _run_events(frames)

    assert _legacy(frames) == [
        {
            "type": "error",
            "code": "stream_error",
            "message": "private prompt contents",
        }
    ]
    _assert_one_terminal(events, "run.failed")
    assert events[-1]["error"] == {
        "type": "RuntimeError",
        "code": "stream_error",
        "recoverable": False,
    }
    assert "private prompt contents" not in json.dumps(events, ensure_ascii=False)
    metrics = get_metrics().snapshot()
    assert metrics["query.errors"]["count"] == 1
    assert metrics["query.total.errors"]["count"] == 1


def test_client_abandonment_cancels_run_and_never_writes_cache(
    monkeypatch: pytest.MonkeyPatch,
    captured_runs: list[MemoryRunEventSink],
) -> None:
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app,
        "_cache_reserve",
        lambda _key: (None, threading.Event(), True),
    )
    cache_writes: list[dict[str, Any]] = []
    monkeypatch.setattr(server_app, "_cache_put", lambda _key, value: cache_writes.append(value))

    def slow_stream(*_args: Any, **_kwargs: Any) -> Iterator[dict[str, Any]]:
        yield {"type": "phase", "phase": "generating"}
        for _ in range(1_000):
            time.sleep(0.001)
            yield {"type": "token", "text": "x"}
        yield {"type": "done", "result": _inner_result()}

    monkeypatch.setattr(server_app, "answer_query_stream", slow_stream)

    async def scenario() -> None:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="报销流程"), _Request()
        )
        iterator: AsyncIterator[Any] = response.body_iterator
        await anext(iterator)
        await iterator.aclose()
        await asyncio.sleep(0.6)

    asyncio.run(scenario())
    published = [event.model_dump(mode="json") for sink in captured_runs for event in sink.events]

    assert cache_writes == []
    _assert_one_terminal(published, "run.cancelled")
    assert published[-1]["attributes"]["reason"] == "client_cancelled"
    metrics = get_metrics().snapshot()
    assert metrics["query.cancelled"]["count"] == 1
    assert "query.completed" not in metrics


def test_closing_immediately_after_done_does_not_commit_delivery(
    monkeypatch: pytest.MonkeyPatch,
    captured_runs: list[MemoryRunEventSink],
) -> None:
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app,
        "_cache_reserve",
        lambda _key: (None, threading.Event(), True),
    )
    cache_writes: list[dict[str, Any]] = []
    recent_records: list[dict[str, Any]] = []
    monkeypatch.setattr(server_app, "_cache_put", lambda _key, value: cache_writes.append(value))
    monkeypatch.setattr(server_app, "_record_query", recent_records.append)

    def stream(*_args: Any, **_kwargs: Any) -> Iterator[dict[str, Any]]:
        yield {"type": "phase", "phase": "verifying"}
        yield {"type": "done", "result": _inner_result(abstained=True)}

    monkeypatch.setattr(server_app, "answer_query_stream", stream)

    async def scenario() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="报销流程"), _Request()
        )
        iterator: AsyncIterator[Any] = response.body_iterator
        frames: list[dict[str, Any]] = []
        while True:
            chunk = await anext(iterator)
            parsed = _parse_chunk(chunk)
            frames.extend(parsed)
            if any(frame["type"] == "done" for frame in parsed):
                break
        await iterator.aclose()
        await asyncio.sleep(0.1)
        return frames

    frames = asyncio.run(scenario())
    published = [event.model_dump(mode="json") for sink in captured_runs for event in sink.events]
    metrics = get_metrics().snapshot()

    assert _legacy(frames)[-1]["type"] == "done"
    assert cache_writes == []
    assert recent_records == []
    _assert_one_terminal(published, "run.cancelled")
    assert "query.completed" not in metrics
    assert "query.abstained" not in metrics
    assert "query.total" not in metrics
    assert metrics["query.cancelled"]["count"] == 1


def test_unconsumed_cache_replay_is_cancelled_without_refreshing_cache(
    monkeypatch: pytest.MonkeyPatch,
    captured_runs: list[MemoryRunEventSink],
) -> None:
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: _cached_payload())
    cache_writes: list[dict[str, Any]] = []
    monkeypatch.setattr(server_app, "_cache_put", lambda _key, value: cache_writes.append(value))

    async def scenario() -> None:
        await server_app.query_stream(server_app.QueryRequest(query="报销流程"), _Request())
        await asyncio.sleep(0.1)

    asyncio.run(scenario())
    published = [event.model_dump(mode="json") for sink in captured_runs for event in sink.events]

    assert cache_writes == []
    _assert_one_terminal(published, "run.cancelled")
    assert published[-1]["attributes"]["reason"] == "response_abandoned"
    metrics = get_metrics().snapshot()
    assert metrics["query.cancelled"]["count"] == 1
    assert "query.completed" not in metrics


def test_twenty_concurrent_streams_never_mix_run_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app,
        "_cache_reserve",
        lambda _key: (None, threading.Event(), True),
    )
    monkeypatch.setattr(server_app, "_cache_put", lambda *_args: None)

    def stream(query: str, **_kwargs: Any) -> Iterator[dict[str, Any]]:
        yield {"type": "phase", "phase": "generating"}
        yield {"type": "token", "text": query}
        yield {"type": "done", "result": _inner_result(query)}

    monkeypatch.setattr(server_app, "answer_query_stream", stream)

    async def one(index: int) -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query=f"问题-{index}"), _Request()
        )
        return await _collect(response)

    async def scenario() -> list[list[dict[str, Any]]]:
        return await asyncio.gather(*(one(index) for index in range(20)))

    streams = asyncio.run(scenario())
    run_ids: list[str] = []
    for frames in streams:
        events = _run_events(frames)
        ids = {event["run_id"] for event in events}
        assert len(ids) == 1
        run_ids.append(ids.pop())
        assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
        _assert_one_terminal(events, "run.completed")
    assert len(set(run_ids)) == 20


def test_http_stream_run_extra_sink_receives_same_canonical_events() -> None:
    sse = MemoryRunEventSink()
    registry = MemoryRunEventSink()
    run = server_app._HttpStreamRun(
        settings=_fake_settings(),
        pipeline={"retrieval": SimpleNamespace()},
        sink=sse,
        retry_enabled=False,
        extra_sinks=(registry,),
    )

    run.activate(mode="query", path="l2_owner")
    run.complete(abstained=False)

    assert registry.events == sse.events
