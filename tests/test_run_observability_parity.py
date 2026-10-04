from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Literal

import pytest

import rag
import rag_stream
from config.settings import PipelineSettings
from core.metrics import get_metrics
from core.run_events import MemoryRunEventSink, RunEvent, RunEventSequencer
from models.schemas import Chunk, QueryResult, RetrievedChunk, RouteDecision
from retrieval.pipeline import RetrievalPipeline
from server import app as server_app
from verify.abstention import AbstentionGate
from verify.verifier import VerificationResult


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_TERMINALS = {"run.completed", "run.failed", "run.cancelled"}
_RAW_QUERY = "QUERY-SENTINEL-84217"
_RAW_ANSWER = "ANSWER-SENTINEL-84217"
_RAW_ACL = "ACL-SENTINEL-84217"
_RAW_CHUNK = "CHUNK-SENTINEL-84217"


class _Request:
    async def is_disconnected(self) -> bool:
        return False


class _Embedder:
    def embed_query(self, _text: str) -> list[float]:
        return [1.0, 0.0]


class _Milvus:
    def __init__(self, chunk: RetrievedChunk) -> None:
        self._chunk = chunk

    @staticmethod
    def build_filters(**_kwargs: Any) -> None:
        return None

    def hybrid_search(self, **_kwargs: Any) -> list[RetrievedChunk]:
        return [self._chunk.model_copy(deep=True)]


class _Rewriter:
    @staticmethod
    def rewrite(query: str) -> tuple[str, bool]:
        return query, False


class _Router:
    @staticmethod
    def route(_query: str) -> RouteDecision:
        return RouteDecision(target="hybrid", confidence=0.9)


class _Reranker:
    @staticmethod
    def rerank(_query: str, chunks: list[Chunk]) -> list[float]:
        return [0.95 for _chunk in chunks]


class _Generated:
    def __init__(self, answer: str) -> None:
        self.answer = answer


class _StreamGenerator:
    @staticmethod
    def generate_stream(_query: str, _chunks: list[RetrievedChunk]) -> Iterator[str]:
        yield _RAW_ANSWER

    @staticmethod
    def _dedup_chunks(chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
        return chunks

    @staticmethod
    def postprocess_stream(text: str, _evidence_count: int) -> _Generated:
        return _Generated(text)


class _Verifier:
    @staticmethod
    def verify(
        _answer: str,
        _chunks: list[RetrievedChunk],
        strict: bool | None = None,
        question: str = "",
    ) -> VerificationResult:
        # strict/question 是 CitationVerifier 的既有契约参数，桩必须一并接受
        del strict, question
        return VerificationResult(
            supported=True,
            entailment_scores={"claim": 1.0},
            entailment_evaluated=True,
        )


def _components() -> dict[str, Any]:
    chunk = RetrievedChunk(
        chunk=Chunk(
            chunk_id="private-chunk",
            doc_id="private-doc",
            text=_RAW_CHUNK,
            text_hash="hash-private",
            created_at=_NOW,
            updated_at=_NOW,
            tenant_id="default",
        ),
        score=0.8,
        rank=1,
        branch="hybrid",
        dense_cosine=0.95,
    )
    retrieval = RetrievalPipeline(
        embedder=_Embedder(),
        milvus=_Milvus(chunk),
        reranker=_Reranker(),
        rewriter=_Rewriter(),
        router=_Router(),
        settings=PipelineSettings(
            complexity_gate_on=False,
            rerank_on=True,
            source_diversity="off",
            top_k=2,
        ),
    )
    return {
        "retrieval": retrieval,
        "generator": _StreamGenerator(),
        "verifier": _Verifier(),
        "gate": AbstentionGate(retrieval_threshold=0.3, entailment_threshold=0.6),
    }


def _fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        pipeline=SimpleNamespace(
            hyde_on=False,
            subqueries_on=False,
            stepback_on=False,
            graph_retrieval_on=False,
            rerank_on=True,
            sentence_window_on=False,
        )
    )


def _parse_chunk(chunk: str | bytes) -> list[dict[str, Any]]:
    text = chunk.decode() if isinstance(chunk, bytes) else chunk
    return [
        json.loads(block.removeprefix("data: "))
        for block in text.split("\n\n")
        if block.startswith("data: ")
    ]


async def _collect(response: Any) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    iterator: AsyncIterator[Any] = response.body_iterator
    async for chunk in iterator:
        frames.extend(_parse_chunk(chunk))
    return frames


def _legacy_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    typed_transport = {"run_event", "run_event_desync"}
    return [frame for frame in frames if frame["type"] not in typed_transport]


def _run_events(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [frame["event"] for frame in frames if frame["type"] == "run_event"]


def _query_result(frames: list[dict[str, Any]]) -> QueryResult:
    done = next(frame for frame in _legacy_frames(frames) if frame["type"] == "done")
    return QueryResult.model_validate(done["result"]["result"])


def _assert_terminal_is_last(events: list[dict[str, Any]]) -> None:
    terminal_indexes = [index for index, event in enumerate(events) if event["type"] in _TERMINALS]
    assert terminal_indexes == [len(events) - 1]


@dataclass
class _RunCapture:
    frames: list[dict[str, Any]]
    cache_payload: dict[str, Any]
    sink_events: list[RunEvent]


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
    monkeypatch.setattr(server_app.time, "perf_counter", lambda: 100.0)
    monkeypatch.setattr(rag_stream.time, "perf_counter", lambda: 100.0)
    # 计时发生在阶段驱动器里（retrieval/stages.py 的 time 就是标准库 time
    # 模块本身，patch 它对管线与阶段同样生效）。
    monkeypatch.setattr("retrieval.stages.time.perf_counter", lambda: 100.0)
    yield
    server_app._aio_flights.clear()
    server_app._pending = 0
    server_app._shutdown_query_executor()


async def _run_mode(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: Literal["none", "recording", "throwing"],
    query: str = _RAW_QUERY,
) -> _RunCapture:
    components = _components()
    monkeypatch.setattr(rag, "get_pipeline", lambda settings=None: components)
    monkeypatch.setattr(server_app, "get_pipeline", lambda _settings=None: components)
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app,
        "_cache_reserve",
        lambda _key: (None, threading.Event(), True),
    )
    cache_writes: list[dict[str, Any]] = []
    monkeypatch.setattr(server_app, "_cache_put", lambda _key, value: cache_writes.append(value))

    actual_stream = rag_stream.answer_query_stream

    def stream(*args: Any, observer: Any = None, **kwargs: Any) -> Iterator[dict[str, Any]]:
        selected_observer = None if mode == "none" else observer
        yield from actual_stream(*args, observer=selected_observer, **kwargs)

    monkeypatch.setattr(server_app, "answer_query_stream", stream)

    captured: list[MemoryRunEventSink] = []
    original_sequencer = RunEventSequencer

    def sequencer_factory(*args: Any, **kwargs: Any) -> RunEventSequencer:
        memory = MemoryRunEventSink()
        captured.append(memory)
        sinks = tuple(kwargs.get("sinks", ()))

        def throwing_sink(_event: RunEvent) -> None:
            raise RuntimeError("audit sink exploded")

        extras = (throwing_sink, memory) if mode == "throwing" else (memory,)
        kwargs["sinks"] = (*sinks, *extras)
        return original_sequencer(*args, **kwargs)

    monkeypatch.setattr(server_app, "RunEventSequencer", sequencer_factory)
    response = await server_app.query_stream(
        server_app.QueryRequest(query=query, acl=[_RAW_ACL], retry=False),
        _Request(),
    )
    frames = await _collect(response)
    assert len(cache_writes) == 1
    assert len(captured) == 1
    return _RunCapture(frames, cache_writes[0], captured[0].events)


def test_real_pipeline_observer_modes_preserve_result_legacy_sse_and_cache_privacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> dict[str, _RunCapture]:
        captures: dict[str, _RunCapture] = {}
        for mode in ("none", "recording", "throwing"):
            server_app._aio_flights.clear()
            server_app._query_slots = asyncio.Semaphore(32)
            captures[mode] = await _run_mode(monkeypatch, mode=mode)
        return captures

    captures = asyncio.run(scenario())
    baseline = captures["none"]
    baseline_result = _query_result(baseline.frames).model_dump(mode="json")
    baseline_legacy = _legacy_frames(baseline.frames)

    for mode in ("recording", "throwing"):
        capture = captures[mode]
        assert _query_result(capture.frames).model_dump(mode="json") == baseline_result
        assert _legacy_frames(capture.frames) == baseline_legacy
        assert capture.cache_payload == baseline.cache_payload
        assert any(event.node_id == "embed" for event in capture.sink_events)
        events = [event.model_dump(mode="json") for event in capture.sink_events]
        assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
        _assert_terminal_is_last(events)
        serialized = "\n".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True) for event in events
        )
        for secret in (_RAW_QUERY, _RAW_ANSWER, _RAW_ACL, _RAW_CHUNK):
            assert secret not in serialized


def test_concurrent_real_pipeline_runs_keep_events_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    components = _components()
    monkeypatch.setattr(rag, "get_pipeline", lambda settings=None: components)
    monkeypatch.setattr(server_app, "get_pipeline", lambda _settings=None: components)
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app,
        "_cache_reserve",
        lambda _key: (None, threading.Event(), True),
    )
    monkeypatch.setattr(server_app, "_cache_put", lambda *_args: None)
    monkeypatch.setattr(server_app, "answer_query_stream", rag_stream.answer_query_stream)

    captured: list[MemoryRunEventSink] = []
    original_sequencer = RunEventSequencer

    def sequencer_factory(*args: Any, **kwargs: Any) -> RunEventSequencer:
        memory = MemoryRunEventSink()
        captured.append(memory)
        kwargs["sinks"] = (*tuple(kwargs.get("sinks", ())), memory)
        return original_sequencer(*args, **kwargs)

    monkeypatch.setattr(server_app, "RunEventSequencer", sequencer_factory)

    async def one(index: int) -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query=f"concurrent-query-{index}", retry=False),
            _Request(),
        )
        return await _collect(response)

    async def scenario() -> list[list[dict[str, Any]]]:
        return await asyncio.gather(*(one(index) for index in range(8)))

    streams = asyncio.run(scenario())
    assert len(captured) == 8
    frame_run_ids: set[str] = set()
    for frames in streams:
        events = _run_events(frames)
        run_ids = {event["run_id"] for event in events}
        assert len(run_ids) == 1
        frame_run_ids.update(run_ids)
        assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
        _assert_terminal_is_last(events)

    sink_run_ids = {event.run_id for sink in captured for event in sink.events}
    assert len(frame_run_ids) == 8
    assert sink_run_ids == frame_run_ids
    for sink in captured:
        events = sink.events
        assert len({event.run_id for event in events}) == 1
        assert [event.seq for event in events] == list(range(1, len(events) + 1))
        _assert_terminal_is_last([event.model_dump(mode="json") for event in events])


def test_unconsumed_saturated_sse_queue_does_not_pin_query_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app,
        "_cache_reserve",
        lambda _key: (None, threading.Event(), True),
    )
    monkeypatch.setattr(server_app, "_cache_put", lambda *_args: None)
    monkeypatch.setattr(
        server_app,
        "get_pipeline",
        lambda _settings=None: {"retrieval": SimpleNamespace()},
    )

    def saturated_stream(*_args: Any, **_kwargs: Any) -> Iterator[dict[str, Any]]:
        for index in range(400):
            yield {"type": "token", "text": str(index)}
        yield {
            "type": "done",
            "result": QueryResult(query="saturation", answer="done").model_dump(mode="json"),
        }

    monkeypatch.setattr(server_app, "answer_query_stream", saturated_stream)

    async def scenario() -> int:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="saturation", retry=False),
            _Request(),
        )
        await asyncio.sleep(0.1)
        pending_before_cleanup = server_app._pending
        iterator: AsyncIterator[Any] = response.body_iterator
        await anext(iterator)
        await iterator.aclose()
        for _ in range(100):
            if server_app._pending == 0:
                break
            await asyncio.sleep(0.01)
        return pending_before_cleanup

    assert asyncio.run(scenario()) == 0


def test_slow_consumer_preserves_legacy_frames_and_gapless_typed_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app,
        "_cache_reserve",
        lambda _key: (None, threading.Event(), True),
    )
    monkeypatch.setattr(server_app, "_cache_put", lambda *_args: None)
    monkeypatch.setattr(
        server_app,
        "get_pipeline",
        lambda _settings=None: {"retrieval": SimpleNamespace()},
    )

    def long_stream(*_args: Any, **_kwargs: Any) -> Iterator[dict[str, Any]]:
        yield {"type": "phase", "phase": "generating"}
        for index in range(320):
            yield {"type": "token", "text": str(index)}
        yield {
            "type": "done",
            "result": QueryResult(query="slow", answer="done").model_dump(mode="json"),
        }

    monkeypatch.setattr(server_app, "answer_query_stream", long_stream)

    async def scenario() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="slow", retry=False),
            _Request(),
        )
        frames: list[dict[str, Any]] = []
        async for chunk in response.body_iterator:
            frames.extend(_parse_chunk(chunk))
            await asyncio.sleep(0.001)
        return frames

    frames = asyncio.run(scenario())
    legacy = _legacy_frames(frames)
    assert legacy[0] == {"type": "phase", "phase": "generating"}
    assert [frame["text"] for frame in legacy[1:-1]] == [str(index) for index in range(320)]
    assert legacy[-1]["type"] == "done"
    events = _run_events(frames)
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    _assert_terminal_is_last(events)
    assert events[-1]["type"] == "run.completed"
    assert server_app._pending == 0


def test_event_loop_terminal_survives_full_legacy_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_app, "QUERY_TIMEOUT_S", 0.5)
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app,
        "_cache_reserve",
        lambda _key: (None, threading.Event(), True),
    )
    monkeypatch.setattr(server_app, "_cache_put", lambda *_args: None)
    monkeypatch.setattr(
        server_app,
        "get_pipeline",
        lambda _settings=None: {"retrieval": SimpleNamespace()},
    )

    def saturated_stream(*_args: Any, **_kwargs: Any) -> Iterator[dict[str, Any]]:
        for index in range(400):
            yield {"type": "token", "text": str(index)}
        yield {
            "type": "done",
            "result": QueryResult(query="late", answer="done").model_dump(mode="json"),
        }

    monkeypatch.setattr(server_app, "answer_query_stream", saturated_stream)

    async def scenario() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="late", retry=False),
            _Request(),
        )
        await asyncio.sleep(0.1)
        return await _collect(response)

    frames = asyncio.run(scenario())
    assert not any(frame.get("code") == "query_timeout" for frame in _legacy_frames(frames))
    events = _run_events(frames)
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    _assert_terminal_is_last(events)
    assert events[-1]["type"] == "run.cancelled"
    assert events[-1]["attributes"]["reason"] == "response_abandoned"
    assert server_app._pending == 0


def test_typed_buffer_overflow_emits_one_desync_frame_without_seq_advance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server_app, "_TYPED_SSE_QUEUE_MAX", 3, raising=False)
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app,
        "_cache_reserve",
        lambda _key: (None, threading.Event(), True),
    )
    monkeypatch.setattr(server_app, "_cache_put", lambda *_args: None)
    monkeypatch.setattr(
        server_app,
        "get_pipeline",
        lambda _settings=None: {"retrieval": SimpleNamespace()},
    )

    def typed_burst(
        *_args: Any,
        observer: Any,
        **_kwargs: Any,
    ) -> Iterator[dict[str, Any]]:
        for index in range(4):
            node_id = f"burst-{index}"
            observer.start_node(node_id, attempt=1)
            observer.complete_node(node_id, attempt=1)
        yield {"type": "phase", "phase": "generating"}
        yield {
            "type": "done",
            "result": QueryResult(query="burst", answer="done").model_dump(mode="json"),
        }

    monkeypatch.setattr(server_app, "answer_query_stream", typed_burst)

    async def scenario() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="burst", retry=False),
            _Request(),
        )
        return await _collect(response)

    frames = asyncio.run(scenario())
    typed = _run_events(frames)
    desync = [frame for frame in frames if frame["type"] == "run_event_desync"]
    assert [event["seq"] for event in typed] == list(range(1, len(typed) + 1))
    assert len(desync) == 1
    assert desync[0]["run_id"] == typed[0]["run_id"]
    assert desync[0]["expected_seq"] == len(typed) + 1
    assert desync[0]["reason"] == "typed_buffer_overflow"
    assert _legacy_frames(frames) == [
        {"type": "phase", "phase": "generating"},
        {
            "type": "done",
            "result": {
                "result": QueryResult(query="burst", answer="done").model_dump(mode="json"),
                "using_mock": False,
                "duration_ms": 0.0,
            },
        },
    ]


def test_http_stream_throwing_extra_sink_preserves_canonical_frames() -> None:
    baseline = MemoryRunEventSink()
    candidate = MemoryRunEventSink()

    def throwing_sink(_event: RunEvent) -> None:
        raise RuntimeError("registry unavailable")

    baseline_run = server_app._HttpStreamRun(
        settings=_fake_settings(),
        pipeline={"retrieval": SimpleNamespace()},
        sink=baseline,
        retry_enabled=True,
    )
    candidate_run = server_app._HttpStreamRun(
        settings=_fake_settings(),
        pipeline={"retrieval": SimpleNamespace()},
        sink=candidate,
        retry_enabled=True,
        extra_sinks=(throwing_sink,),
    )
    for run in (baseline_run, candidate_run):
        run.activate(mode="query", path="l2_owner")
        run.complete(abstained=False)

    def stable(events: list[RunEvent]) -> list[dict[str, Any]]:
        values = [event.model_dump(mode="json") for event in events]
        for value in values:
            value["run_id"] = "<run>"
            value["occurred_at"] = "<time>"
            value["elapsed_ms"] = 0.0
            if value.get("duration_ms") is not None:
                value["duration_ms"] = 0.0
        return values

    assert stable(candidate.events) == stable(baseline.events)


def test_http_stream_real_rag_stream_resolves_tenant_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _fake_settings()
    settings.observability = SimpleNamespace()
    components = _components()
    calls: list[str | None] = []

    def resolve_probe(tenant_id: str | None, _settings: Any) -> str:
        calls.append(tenant_id)
        return "resolved-tenant"

    monkeypatch.setattr(server_app, "get_settings", lambda: settings)
    monkeypatch.setattr(server_app, "get_pipeline", lambda _settings=None: components)
    monkeypatch.setattr(server_app, "resolve_tenant", resolve_probe)
    monkeypatch.setattr(rag_stream, "resolve_tenant", resolve_probe)
    monkeypatch.setattr(rag, "get_pipeline", lambda _settings=None: components)
    monkeypatch.setattr(rag_stream, "setup_observability", lambda *_args: None)
    monkeypatch.setattr(server_app, "answer_query_stream", rag_stream.answer_query_stream)
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app, "_cache_reserve", lambda _key: (None, threading.Event(), True)
    )
    monkeypatch.setattr(server_app, "_cache_put", lambda *_args: None)

    async def scenario() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="tenant probe", tenant_id="raw-tenant"),
            _Request(),
        )
        return await _collect(response)

    frames = asyncio.run(scenario())
    assert any(frame["type"] == "done" for frame in _legacy_frames(frames))
    assert calls == ["raw-tenant"]
