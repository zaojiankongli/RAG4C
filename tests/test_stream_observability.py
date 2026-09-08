from __future__ import annotations

import itertools
import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from config.settings import PipelineSettings
from core.reranker import RerankError
from core.run_events import MemoryRunEventSink, RunEvent, RunEventSequencer
from core.tracing import current_run_observer, trace_session
from models.schemas import Chunk, RetrievedChunk, RouteDecision
from retrieval.graph_retriever import GraphRetrieverError
from retrieval.pipeline import RetrievalPipeline, RetrievalResult
from verify.abstention import AbstentionGate
from verify.verifier import VerificationResult


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _chunk(
    chunk_id: str,
    *,
    score: float = 0.5,
    dataset_id: str = "",
    doc_id: str = "doc-1",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            text=f"正文 {chunk_id}",
            text_hash=f"hash-{chunk_id}",
            created_at=_NOW,
            updated_at=_NOW,
            dataset_id=dataset_id,
        ),
        score=score,
        rank=0,
        branch="hybrid",
    )


class _Embedder:
    def embed_query(self, _text: str) -> list[float]:
        return [1.0, 0.0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


class _Milvus:
    def __init__(
        self,
        *,
        main: list[RetrievedChunk] | None = None,
        by_query: dict[str, list[RetrievedChunk] | BaseException] | None = None,
        graph_chunks: list[Chunk] | None = None,
    ) -> None:
        self.main = main or [_chunk("main", score=0.8)]
        self.by_query = by_query or {}
        self.graph_chunks = graph_chunks or []

    @staticmethod
    def build_filters(**_kwargs: Any) -> None:
        return None

    def hybrid_search(
        self,
        query_dense: list[float] | None = None,
        top_k: int | None = None,
        **kwargs: Any,
    ) -> list[RetrievedChunk]:
        del query_dense, top_k
        query_text = kwargs.get("query_text")
        scripted = self.by_query.get(query_text)
        if isinstance(scripted, BaseException):
            raise scripted
        source = scripted if scripted is not None else self.main
        return [item.model_copy(deep=True) for item in source]

    def get_chunks_by_ids(self, _ids: list[str]) -> list[Chunk]:
        return [chunk.model_copy(deep=True) for chunk in self.graph_chunks]


class _Rewriter:
    def __init__(self, rewritten: str = "rewritten", changed: bool = True) -> None:
        self.rewritten = rewritten
        self.changed = changed

    def rewrite(self, query: str) -> tuple[str, bool]:
        return (self.rewritten if self.changed else query), self.changed


class _Router:
    def __init__(self, target: str = "hybrid") -> None:
        self.target = target

    def route(self, _query: str) -> RouteDecision:
        return RouteDecision(target=self.target, confidence=0.75)


class _Reranker:
    def rerank(self, _query: str, chunks: list[Chunk]) -> list[float]:
        return [0.9 - index * 0.1 for index, _ in enumerate(chunks)]


class _RerankerRaises:
    def rerank(self, _query: str, _chunks: list[Chunk]) -> list[float]:
        raise RerankError("reranker unavailable")


class _Generator:
    def __init__(self, value: Any = None, error: BaseException | None = None) -> None:
        self.value = value
        self.error = error

    def generate(self, _query: str) -> Any:
        if self.error is not None:
            raise self.error
        return self.value


class _GraphRetriever:
    def __init__(
        self,
        *,
        passage_ids: list[str] | None = None,
        degraded: bool = False,
        error: BaseException | None = None,
    ) -> None:
        self.passage_ids = passage_ids or []
        self.degraded = degraded
        self.error = error

    def retrieve(self, _query: str, *, tenant_id: str) -> Any:
        del tenant_id
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            passage_ids=list(self.passage_ids),
            degraded=self.degraded,
        )


class _SentenceWindowRaises:
    def expand(self, _items: list[RetrievedChunk]) -> list[RetrievedChunk]:
        raise RuntimeError("parent lookup failed")


def _pipeline(**overrides: Any) -> RetrievalPipeline:
    settings_overrides = overrides.pop("settings", {})
    settings_values = {
        "complexity_gate_on": False,
        "rerank_on": True,
        "source_diversity": "off",
        "top_k": 2,
    }
    settings_values.update(settings_overrides)
    settings = PipelineSettings(**settings_values)
    return RetrievalPipeline(
        embedder=overrides.pop("embedder", _Embedder()),
        milvus=overrides.pop("milvus", _Milvus()),
        reranker=overrides.pop("reranker", _Reranker()),
        rewriter=overrides.pop("rewriter", _Rewriter()),
        router=overrides.pop("router", _Router()),
        settings=settings,
        graph_retriever=overrides.pop("graph_retriever", None),
        hyde=overrides.pop("hyde", None),
        subqueries=overrides.pop("subqueries", None),
        stepback=overrides.pop("stepback", None),
        sentence_window=overrides.pop("sentence_window", None),
        **overrides,
    )


def _run_observed(
    pipeline: RetrievalPipeline,
    *,
    extra_sink: Any = None,
    dataset_id: str | None = None,
) -> tuple[Any, list[RunEvent]]:
    memory = MemoryRunEventSink()
    sinks = (extra_sink, memory) if extra_sink is not None else (memory,)
    sequencer = RunEventSequencer(
        "run-retrieval",
        topology_id="rag.query",
        topology_revision="rev-test",
        sinks=sinks,
    )
    observer = sequencer.observer()
    observer.start_run(source="test")
    with trace_session("query-test", observer=observer):
        result = pipeline.run("question", dataset_id=dataset_id)
    return result, memory.events


def _events(events: list[RunEvent], node_id: str) -> list[RunEvent]:
    return [event for event in events if event.node_id == node_id]


def _types(events: list[RunEvent], node_id: str) -> list[str]:
    return [event.type for event in _events(events, node_id)]


def test_observer_and_throwing_sink_do_not_change_retrieval_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline = _pipeline()

    def reset_clock() -> None:
        ticks = itertools.count(1)
        monkeypatch.setattr("retrieval.pipeline.time.perf_counter", lambda: next(ticks) / 1000.0)

    reset_clock()
    baseline = pipeline.run("question")
    reset_clock()
    recorded, events = _run_observed(pipeline)

    def throwing_sink(_event: RunEvent) -> None:
        raise RuntimeError("sink exploded")

    reset_clock()
    throwing, _ = _run_observed(pipeline, extra_sink=throwing_sink)

    assert recorded == baseline
    assert throwing == baseline
    assert events


def test_gate_skips_rewrite_and_route_but_reports_effective_route() -> None:
    pipeline = _pipeline(
        settings={"complexity_gate_on": True},
        rewriter=_Rewriter(changed=False),
    )

    result, events = _run_observed(pipeline)

    assert result.route == RouteDecision(target="hybrid", confidence=1.0)
    assert _types(events, "complexity_gate") == ["node.started", "node.completed"]
    assert _types(events, "rewrite") == ["node.skipped"]
    assert _types(events, "route") == ["node.skipped", "route.selected"]
    assert _events(events, "route")[-1].attributes["route"] == "hybrid"


def test_router_path_reports_rewrite_route_and_selected_target() -> None:
    pipeline = _pipeline(router=_Router("full"))

    _result, events = _run_observed(pipeline)

    assert _types(events, "complexity_gate") == ["node.skipped"]
    assert _types(events, "rewrite") == ["node.started", "node.completed"]
    assert _types(events, "route")[:2] == ["node.started", "node.completed"]
    selected = [event for event in _events(events, "route") if event.type == "route.selected"]
    assert selected[0].attributes == {"confidence": 0.75, "degraded": False, "route": "full"}


def test_hyde_configured_without_component_is_skipped() -> None:
    pipeline = _pipeline(settings={"hyde_on": True})

    _result, events = _run_observed(pipeline)

    assert _types(events, "plugin.hyde.expand") == ["node.skipped"]
    assert _events(events, "plugin.hyde.expand")[0].attributes["reason"] == "unavailable"


def test_subquery_partial_failure_reports_degraded_and_keeps_successful_results() -> None:
    milvus = _Milvus(
        by_query={
            "good": [_chunk("extra", score=0.4)],
            "bad": RuntimeError("one branch failed"),
        }
    )
    pipeline = _pipeline(
        settings={"subqueries_on": True, "rerank_on": False},
        milvus=milvus,
        subqueries=_Generator(["good", "bad"]),
        reranker=None,
    )
    try:
        result, events = _run_observed(pipeline)
    finally:
        pipeline.shutdown()

    assert [item.chunk.chunk_id for item in result.chunks] == ["main", "extra"]
    assert _types(events, "plugin.subqueries.expand") == [
        "node.started",
        "node.failed",
        "degraded",
    ]
    failed = _events(events, "plugin.subqueries.expand")[1]
    assert failed.error is not None and failed.error.recoverable is True
    assert failed.attributes["reason"] == "partial_search_failure"


def test_empty_subquery_embeddings_report_degraded_fallback() -> None:
    class _EmptyBatchEmbedder(_Embedder):
        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            del texts
            return []

    pipeline = _pipeline(
        settings={"subqueries_on": True, "rerank_on": False},
        embedder=_EmptyBatchEmbedder(),
        subqueries=_Generator(["good"]),
        reranker=None,
    )
    try:
        result, events = _run_observed(pipeline)
    finally:
        pipeline.shutdown()

    assert [item.chunk.chunk_id for item in result.chunks] == ["main"]
    assert _types(events, "plugin.subqueries.expand") == [
        "node.started",
        "node.failed",
        "degraded",
    ]
    assert _events(events, "plugin.subqueries.expand")[1].attributes["reason"] == "empty_embeddings"


def test_stepback_failure_reports_recoverable_degradation() -> None:
    pipeline = _pipeline(
        settings={"stepback_on": True},
        stepback=_Generator(error=RuntimeError("generation failed")),
    )

    result, events = _run_observed(pipeline)

    assert [item.chunk.chunk_id for item in result.chunks] == ["main"]
    assert _types(events, "plugin.stepback.expand") == [
        "node.started",
        "node.failed",
        "degraded",
    ]


@pytest.mark.parametrize(
    "graph_retriever,reason,error_type",
    [
        (_GraphRetriever(), "no_match", "GraphNoMatch"),
        (
            _GraphRetriever(error=GraphRetrieverError("graph down")),
            "retrieval_failed",
            "GraphRetrieverError",
        ),
    ],
)
def test_graph_failure_or_no_match_reports_degraded_effective_hybrid_route(
    graph_retriever: _GraphRetriever,
    reason: str,
    error_type: str,
) -> None:
    pipeline = _pipeline(
        settings={"graph_retrieval_on": True},
        router=_Router("vector_graph_rag"),
        graph_retriever=graph_retriever,
    )

    result, events = _run_observed(pipeline)

    assert result.route.degraded is True
    assert _types(events, "graph.retrieve") == [
        "node.started",
        "node.failed",
        "degraded",
        "route.selected",
    ]
    failed = _events(events, "graph.retrieve")[1]
    assert failed.attributes["reason"] == reason
    assert failed.error is not None and failed.error.type == error_type
    assert _events(events, "graph.retrieve")[-1].attributes["route"] == "hybrid"


def test_rerank_disabled_is_skipped() -> None:
    pipeline = _pipeline(settings={"rerank_on": False}, reranker=None)

    result, events = _run_observed(pipeline)

    assert result.reranked is False
    assert _types(events, "rerank") == ["node.skipped"]
    assert _events(events, "rerank")[0].attributes["reason"] == "disabled"


def test_rerank_failure_reports_degraded_and_keeps_rrf_order() -> None:
    milvus = _Milvus(main=[_chunk("first", score=0.8), _chunk("second", score=0.7)])
    pipeline = _pipeline(milvus=milvus, reranker=_RerankerRaises())

    result, events = _run_observed(pipeline)

    assert result.reranked is False
    assert [item.chunk.chunk_id for item in result.chunks] == ["first", "second"]
    assert _types(events, "rerank") == ["node.started", "node.failed", "degraded"]
    failed = _events(events, "rerank")[1]
    assert failed.error is not None and failed.error.recoverable is True


def test_sentence_window_failure_reports_degraded_and_keeps_candidates() -> None:
    pipeline = _pipeline(
        settings={"sentence_window_on": True},
        sentence_window=_SentenceWindowRaises(),
    )

    result, events = _run_observed(pipeline)

    assert [item.chunk.chunk_id for item in result.chunks] == ["main"]
    assert _types(events, "sentence_window") == [
        "node.started",
        "node.failed",
        "degraded",
    ]


def test_diversity_dataset_scope_and_truncate_report_counts_without_dataset_value() -> None:
    milvus = _Milvus(
        main=[
            _chunk("keep-1", dataset_id="private-dataset", doc_id="a"),
            _chunk("drop", dataset_id="other", doc_id="b"),
            _chunk("keep-2", dataset_id="private-dataset", doc_id="c"),
        ]
    )
    pipeline = _pipeline(
        settings={"source_diversity": "group_mmr", "top_k": 1},
        milvus=milvus,
    )

    result, events = _run_observed(pipeline, dataset_id="private-dataset")

    assert len(result.chunks) == 1
    assert _types(events, "diversity") == ["node.started", "node.completed"]
    assert _types(events, "dataset_scope") == ["node.started", "node.completed"]
    assert _types(events, "truncate") == ["node.started", "node.completed"]
    serialized = "\n".join(event.model_dump_json() for event in events)
    assert "private-dataset" not in serialized
    assert _events(events, "dataset_scope")[-1].attributes["removed_count"] == 1
    assert _events(events, "truncate")[-1].attributes["output_count"] == 1


def test_fatal_embed_exception_remains_fatal_and_is_reported_nonrecoverable() -> None:
    class _FatalEmbedder(_Embedder):
        def embed_query(self, _text: str) -> list[float]:
            raise ValueError("invalid embedding response")

    pipeline = _pipeline(embedder=_FatalEmbedder())
    memory = MemoryRunEventSink()
    sequencer = RunEventSequencer(
        "run-fatal",
        topology_id="rag.query",
        topology_revision="rev-test",
        sinks=(memory,),
    )
    observer = sequencer.observer()
    observer.start_run(source="test")

    with pytest.raises(ValueError, match="invalid embedding response"):
        with trace_session("query-test", observer=observer):
            pipeline.run("question")

    assert _types(memory.events, "embed") == ["node.started", "node.failed"]
    failed = _events(memory.events, "embed")[-1]
    assert failed.error is not None and failed.error.recoverable is False


def test_enhancer_started_is_emitted_before_parent_waits_for_future() -> None:
    release = threading.Event()
    hyde_entered = threading.Event()
    stepback_entered = threading.Event()
    captured: list[tuple[RunEvent, str]] = []
    captured_lock = threading.Lock()
    errors: list[BaseException] = []

    class _BlockingGenerator:
        def __init__(self, entered: threading.Event, value: str) -> None:
            self.entered = entered
            self.value = value

        def generate(self, _query: str) -> str:
            self.entered.set()
            if not release.wait(timeout=5.0):
                raise TimeoutError("test did not release enhancer")
            return self.value

    def recording_sink(event: RunEvent) -> None:
        with captured_lock:
            captured.append((event, threading.current_thread().name))

    pipeline = _pipeline(
        settings={"hyde_on": True, "stepback_on": True, "rerank_on": False},
        hyde=_BlockingGenerator(hyde_entered, "hypothetical document"),
        stepback=_BlockingGenerator(stepback_entered, "abstract"),
        reranker=None,
    )
    sequencer = RunEventSequencer(
        "run-blocking-enhancers",
        topology_id="rag.query",
        topology_revision="rev-test",
        sinks=(recording_sink,),
    )
    observer = sequencer.observer()

    def run_pipeline() -> None:
        try:
            observer.start_run(source="test")
            with trace_session("query-blocking", observer=observer):
                pipeline.run("question")
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    request_thread = threading.Thread(target=run_pipeline, name="request-parent")
    request_thread.start()
    try:
        assert hyde_entered.wait(timeout=2.0)
        assert stepback_entered.wait(timeout=2.0)
        with captured_lock:
            before_release = list(captured)

        for node_id in ("plugin.hyde.expand", "plugin.stepback.expand"):
            node_events = [event for event, _thread in before_release if event.node_id == node_id]
            assert [event.type for event in node_events] == ["node.started"]
            assert {
                thread_name for event, thread_name in before_release if event.node_id == node_id
            } == {"request-parent"}
    finally:
        release.set()
        request_thread.join(timeout=5.0)
        pipeline.shutdown()

    assert not request_thread.is_alive()
    assert errors == []
    with captured_lock:
        after_acceptance = list(captured)
    assert [
        event.type for event, _thread in after_acceptance if event.node_id == "plugin.hyde.expand"
    ] == ["node.started", "node.completed"]
    assert [
        event.type
        for event, _thread in after_acceptance
        if event.node_id == "plugin.stepback.expand"
    ] == ["node.started", "node.completed"]


def test_parallel_enhancer_and_subquery_workers_never_emit_observer_events() -> None:
    event_threads: list[str] = []

    def thread_recording_sink(_event: RunEvent) -> None:
        event_threads.append(threading.current_thread().name)

    pipeline = _pipeline(
        settings={
            "hyde_on": True,
            "subqueries_on": True,
            "stepback_on": True,
            "rerank_on": False,
        },
        hyde=_Generator("hypothetical document"),
        subqueries=_Generator(["good"]),
        stepback=_Generator("abstract"),
        milvus=_Milvus(
            by_query={
                "good": [_chunk("sub")],
                "abstract": [_chunk("step")],
            }
        ),
        reranker=None,
    )
    try:
        _result, events = _run_observed(pipeline, extra_sink=thread_recording_sink)
    finally:
        pipeline.shutdown()

    assert events
    assert set(event_threads) == {threading.current_thread().name}


class _StreamRecordingObserver:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __getattr__(self, method: str):
        def record(*args: Any, **kwargs: Any) -> None:
            node_id = args[0] if args else None
            self.calls.append({"method": method, "node_id": node_id, **kwargs})

        return record


class _StreamRetrieval:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.attempt = 0

    def run(self, query, acl=None, tenant_id=None, dataset_id=None):
        del acl, tenant_id, dataset_id
        self.attempt += 1
        observer = current_run_observer()
        if observer is not None:
            observer.start_node("search", attempt=self.attempt, repeatable=True)
        outcome = self.outcomes[min(self.attempt - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            if observer is not None:
                observer.fail_node(
                    "search",
                    attempt=self.attempt,
                    error_type=type(outcome).__name__,
                    error_code="search_failed",
                    recoverable=True,
                )
            raise outcome
        chunks = list(outcome)
        if observer is not None:
            observer.complete_node(
                "search",
                attempt=self.attempt,
                attributes={"chunk_count": len(chunks)},
            )
        return RetrievalResult(
            query=query,
            chunks=chunks,
            route=RouteDecision(target="hybrid", confidence=1.0),
            traces=[f"fake retrieval {self.attempt}"],
            reranked=True,
        )


class _SilentFailingStreamRetrieval:
    def run(self, query, acl=None, tenant_id=None, dataset_id=None):
        del query, acl, tenant_id, dataset_id
        raise RuntimeError("raw retrieval secret")


class _StreamGenerated:
    def __init__(self, answer: str) -> None:
        self.answer = answer


class _StreamGenerator:
    def __init__(self, tokens: list[str], *, error: BaseException | None = None) -> None:
        self.tokens = tokens
        self.error = error

    def generate_stream(self, query, chunks):
        del query, chunks
        for token in self.tokens:
            yield token
        if self.error is not None:
            raise self.error

    @staticmethod
    def _dedup_chunks(chunks):
        return chunks

    @staticmethod
    def postprocess_stream(text, evidence_count):
        del evidence_count
        return _StreamGenerated(text)


class _StreamVerifier:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.attempt = 0

    def verify(self, answer, chunks):
        del answer, chunks
        outcome = self.outcomes[min(self.attempt, len(self.outcomes) - 1)]
        self.attempt += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _supported_verification() -> VerificationResult:
    return VerificationResult(
        supported=True,
        entailment_scores={"claim": 1.0},
        entailment_evaluated=True,
    )


def _unsupported_verification() -> VerificationResult:
    return VerificationResult(
        supported=False,
        missing_evidence=True,
        entailment_scores={"claim": 0.0},
        entailment_evaluated=True,
    )


def _run_stream_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    *,
    retrieval_outcomes: list[Any] | None = None,
    generator: _StreamGenerator | None = None,
    verifier_outcomes: list[Any] | None = None,
    retry: bool = True,
    query: str = "question",
) -> tuple[list[dict[str, Any]], _StreamRecordingObserver]:
    import rag
    from rag_stream import answer_query_stream

    observer = _StreamRecordingObserver()
    comp = {
        "retrieval": _StreamRetrieval(retrieval_outcomes or [[_chunk("first", score=0.9)]]),
        "generator": generator or _StreamGenerator(["answer"]),
        "verifier": _StreamVerifier(verifier_outcomes or [_supported_verification()]),
        "gate": AbstentionGate(retrieval_threshold=0.3, entailment_threshold=0.6),
    }
    monkeypatch.setattr(rag, "get_pipeline", lambda settings=None: comp)
    events = list(
        answer_query_stream(
            query,
            retry=retry,
            query_id="query-stream-observed",
            observer=observer,
        )
    )
    return events, observer


def _stream_calls(
    observer: _StreamRecordingObserver,
    *,
    method: str | None = None,
    node_id: str | None = None,
) -> list[dict[str, Any]]:
    return [
        call
        for call in observer.calls
        if (method is None or call["method"] == method)
        and (node_id is None or call["node_id"] == node_id)
    ]


def test_stream_reports_business_lifecycle_without_token_or_run_terminal_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, observer = _run_stream_lifecycle(monkeypatch)

    lifecycle = [(call["method"], call["node_id"], call.get("attempt")) for call in observer.calls]
    assert lifecycle == [
        ("start_node", "search", 1),
        ("complete_node", "search", 1),
        ("start_node", "gate.retrieval", 1),
        ("complete_node", "gate.retrieval", 1),
        ("start_node", "generate", 1),
        ("complete_node", "generate", 1),
        ("start_node", "verify", 1),
        ("complete_node", "verify", 1),
        ("start_node", "gate.final", 1),
        ("complete_node", "gate.final", 1),
        ("start_node", "finalize", 1),
        ("complete_node", "finalize", 1),
    ]
    assert [event["type"] for event in events] == [
        "phase",
        "phase",
        "phase",
        "token",
        "phase",
        "done",
    ]
    assert not any(
        call["method"] in {"complete_run", "fail_run", "cancel_run"} for call in observer.calls
    )
    assert not any(call["node_id"] == "token" for call in observer.calls)


def test_stream_reports_retrieval_gate_abstention_and_finalize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, observer = _run_stream_lifecycle(monkeypatch, retrieval_outcomes=[[]])

    gate = _stream_calls(observer, method="complete_node", node_id="gate.retrieval")[0]
    finalized = _stream_calls(observer, method="complete_node", node_id="finalize")[0]
    assert gate["attributes"] == {"abstained": True, "reason": "no_retrieval_results"}
    assert finalized["attributes"] == {"outcome": "abstained"}
    assert events[-1]["result"]["abstained"] is True
    assert not _stream_calls(observer, node_id="generate")


def test_stream_reports_silent_first_round_retrieval_failure_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rag
    from rag_stream import answer_query_stream

    observer = _StreamRecordingObserver()
    comp = {
        "retrieval": _SilentFailingStreamRetrieval(),
        "generator": _StreamGenerator(["unused"]),
        "verifier": _StreamVerifier([_supported_verification()]),
        "gate": AbstentionGate(retrieval_threshold=0.3, entailment_threshold=0.6),
    }
    monkeypatch.setattr(rag, "get_pipeline", lambda settings=None: comp)

    events = list(
        answer_query_stream(
            "question",
            query_id="query-silent-retrieval-failure",
            observer=observer,
        )
    )

    assert [
        (call["method"], call["node_id"], call.get("attempt")) for call in observer.calls
    ] == [
        ("start_node", "search", 1),
        ("fail_node", "search", 1),
        ("degraded", "search", 1),
        ("skip_node", "gate.retrieval", 1),
        ("start_node", "finalize", 1),
        ("complete_node", "finalize", 1),
    ]
    started = _stream_calls(observer, method="start_node", node_id="search")[0]
    failed = _stream_calls(observer, method="fail_node", node_id="search")[0]
    degraded = _stream_calls(observer, method="degraded", node_id="search")[0]
    assert started["repeatable"] is True
    assert failed["error_type"] == "RuntimeError"
    assert failed["error_code"] == "retrieval_failed"
    assert failed["recoverable"] is True
    assert failed["attributes"] == {"reason": "retrieval_failed"}
    assert degraded["attributes"] == {"reason": "retrieval_failed"}
    assert [event["type"] for event in events] == ["phase", "done"]
    assert events[-1]["result"]["abstained"] is True
    assert any("检索失败" in trace for trace in events[-1]["result"]["traces"])


def test_stream_reports_generation_and_verifier_failures_as_recoverable_degradation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _events, generation_observer = _run_stream_lifecycle(
        monkeypatch,
        generator=_StreamGenerator(["partial"], error=RuntimeError("raw generation secret")),
        retry=False,
    )
    failed_generation = _stream_calls(generation_observer, method="fail_node", node_id="generate")[
        0
    ]
    assert failed_generation["error_type"] == "RuntimeError"
    assert failed_generation["error_code"] == "generation_failed"
    assert failed_generation["recoverable"] is True
    assert _stream_calls(generation_observer, method="degraded", node_id="generate")[0][
        "attributes"
    ] == {"reason": "generation_failed"}

    _events, verifier_observer = _run_stream_lifecycle(
        monkeypatch,
        verifier_outcomes=[RuntimeError("raw verifier secret")],
        retry=False,
    )
    failed_verifier = _stream_calls(verifier_observer, method="fail_node", node_id="verify")[0]
    assert failed_verifier["error_type"] == "RuntimeError"
    assert failed_verifier["error_code"] == "verification_failed"
    assert failed_verifier["recoverable"] is True
    assert _stream_calls(verifier_observer, method="degraded", node_id="verify")[0][
        "attributes"
    ] == {"reason": "verification_failed"}


def test_stream_generator_exit_cancels_generation_fact_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rag
    from rag_stream import answer_query_stream

    observer = _StreamRecordingObserver()
    comp = {
        "retrieval": _StreamRetrieval([[_chunk("first", score=0.9)]]),
        "generator": _StreamGenerator(["one", "two"]),
        "verifier": _StreamVerifier([_supported_verification()]),
        "gate": AbstentionGate(retrieval_threshold=0.3, entailment_threshold=0.6),
    }
    monkeypatch.setattr(rag, "get_pipeline", lambda settings=None: comp)
    stream = answer_query_stream(
        "question",
        query_id="query-cancelled",
        observer=observer,
    )
    assert [next(stream)["type"] for _ in range(4)] == ["phase", "phase", "phase", "token"]

    with pytest.raises(GeneratorExit):
        stream.throw(GeneratorExit)

    cancelled = _stream_calls(observer, method="cancel_node", node_id="generate")[0]
    assert cancelled["attributes"] == {"reason": "client_disconnected"}
    assert not _stream_calls(observer, method="cancel_run")


@pytest.mark.parametrize(
    ("case", "retry", "retrieval_outcomes", "verifier_outcomes", "method", "reason"),
    [
        ("disabled", False, None, [_unsupported_verification()], "retry_skipped", "disabled"),
        (
            "retrieval_failure",
            True,
            [[_chunk("first", score=0.9)], RuntimeError("raw retry error")],
            [_unsupported_verification()],
            "retry_failed",
            "retrieval_failed",
        ),
        (
            "no_new_evidence",
            True,
            [[_chunk("first", score=0.9)], [_chunk("first", score=0.9)]],
            [_unsupported_verification()],
            "retry_completed",
            "no_new_evidence",
        ),
        (
            "success",
            True,
            [[_chunk("first", score=0.9)], [_chunk("second", score=0.95)]],
            [_unsupported_verification(), _supported_verification()],
            "retry_completed",
            "recovered",
        ),
        (
            "exhausted",
            True,
            [[_chunk("first", score=0.9)], [_chunk("second", score=0.95)]],
            [_unsupported_verification(), _unsupported_verification()],
            "retry_skipped",
            "exhausted",
        ),
    ],
)
def test_stream_reports_retry_outcomes_with_normalized_reasons(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    retry: bool,
    retrieval_outcomes: list[Any] | None,
    verifier_outcomes: list[Any],
    method: str,
    reason: str,
) -> None:
    events, observer = _run_stream_lifecycle(
        monkeypatch,
        retrieval_outcomes=retrieval_outcomes,
        verifier_outcomes=verifier_outcomes,
        retry=retry,
    )

    matching = [
        call
        for call in _stream_calls(observer, method=method, node_id="search")
        if call.get("attributes", {}).get("reason") == reason
    ]
    assert matching, case
    if case != "disabled":
        started = _stream_calls(observer, method="retry_started", node_id="search")
        assert started[0]["attempt"] == 1
        assert started[0]["attributes"] == {"target_attempt": 2}
    if case in {"success", "exhausted"}:
        assert [call.get("attempt") for call in _stream_calls(observer, node_id="generate")] == [
            1,
            1,
            2,
            2,
        ]
        assert [call.get("attempt") for call in _stream_calls(observer, node_id="verify")] == [
            1,
            1,
            2,
            2,
        ]
    assert events[-1]["result"]["abstained"] is (case != "success")


def test_stream_observer_attributes_exclude_raw_query_answer_token_and_document_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_query = "QUERY-SENTINEL-7391"
    raw_answer = "ANSWER-SENTINEL-7391"
    raw_document = "DOCUMENT-SENTINEL-7391"
    chunk = _chunk("private", score=0.9)
    chunk.chunk.text = raw_document

    _events, observer = _run_stream_lifecycle(
        monkeypatch,
        retrieval_outcomes=[[chunk]],
        generator=_StreamGenerator([raw_answer]),
        query=raw_query,
    )

    serialized = json.dumps(observer.calls, ensure_ascii=False, default=str)
    assert raw_query not in serialized
    assert raw_answer not in serialized
    assert raw_document not in serialized


def test_stream_lifecycle_is_valid_for_caller_owned_sequencer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rag
    from rag_stream import answer_query_stream

    memory = MemoryRunEventSink()
    sequencer = RunEventSequencer(
        "run-stream",
        topology_id="rag.query",
        topology_revision="rev-test",
        sinks=(memory,),
    )
    observer = sequencer.observer()
    observer.start_run(source="test")
    comp = {
        "retrieval": _StreamRetrieval([[_chunk("first", score=0.9)]]),
        "generator": _StreamGenerator(["answer"]),
        "verifier": _StreamVerifier([_supported_verification()]),
        "gate": AbstentionGate(retrieval_threshold=0.3, entailment_threshold=0.6),
    }
    monkeypatch.setattr(rag, "get_pipeline", lambda settings=None: comp)

    events = list(
        answer_query_stream(
            "question",
            query_id="query-canonical",
            observer=observer,
        )
    )

    assert events[-1]["result"]["abstained"] is False
    assert [event.node_id for event in memory.events if event.type == "node.completed"] == [
        "search",
        "gate.retrieval",
        "generate",
        "verify",
        "gate.final",
        "finalize",
    ]
    assert [event.type for event in memory.events if event.type.startswith("run.")] == [
        "run.started"
    ]


def test_stream_real_pipeline_retrieval_failure_has_no_duplicate_search_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rag
    from rag_stream import answer_query_stream

    retrieval = _pipeline(
        milvus=_Milvus(by_query={"question": RuntimeError("milvus unavailable")}),
        rewriter=_Rewriter(changed=False),
    )
    memory = MemoryRunEventSink()
    sequencer = RunEventSequencer(
        "run-stream-retrieval-failure",
        topology_id="rag.query",
        topology_revision="rev-test",
        sinks=(memory,),
    )
    observer = sequencer.observer()
    observer.start_run(source="test")
    comp = {
        "retrieval": retrieval,
        "generator": _StreamGenerator(["unused"]),
        "verifier": _StreamVerifier([_supported_verification()]),
        "gate": AbstentionGate(retrieval_threshold=0.3, entailment_threshold=0.6),
    }
    monkeypatch.setattr(rag, "get_pipeline", lambda settings=None: comp)

    try:
        events = list(
            answer_query_stream(
                "question",
                query_id="query-real-retrieval-failure",
                observer=observer,
            )
        )
    finally:
        retrieval.shutdown()

    search_events = [event for event in memory.events if event.node_id == "search"]
    assert [event.type for event in search_events] == [
        "node.started",
        "node.failed",
        "degraded",
    ]
    failed = next(event for event in search_events if event.type == "node.failed")
    degraded = next(event for event in search_events if event.type == "degraded")
    assert failed.error is not None
    assert failed.error.type == "RuntimeError"
    assert degraded.attributes == {"reason": "retrieval_failed"}
    assert [event["type"] for event in events] == ["phase", "done"]
    assert events[-1]["result"]["abstained"] is True
