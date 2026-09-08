from __future__ import annotations

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import Session

from config.settings import PipelineSettings
from core.document_serving import (
    DocumentServingGuard,
    KnowledgeChanged,
    bind_document_serving,
)
from core.query_cache import make_cache_key
from models.orm import Base, Dataset, Document, Tenant
from models.schemas import Chunk, QueryResult, RetrievedChunk, RouteDecision
from retrieval.pipeline import RetrievalPipeline


_NOW = datetime(2026, 8, 25, 8, 0, 0)
_CHUNK_NOW = datetime(2026, 8, 25, 8, 0, 0, tzinfo=UTC)


def _engine(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'serving.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id="tenant-1", name="Tenant"))
        session.add(
            Dataset(
                id="dataset-1",
                tenant_id="tenant-1",
                name="Dataset",
                serving_generation=7,
            )
        )
        session.commit()
    return engine


def _document(
    document_id: str,
    *,
    lifecycle_state: str = "active",
    retrieval_enabled: bool = True,
    effective_from: datetime | None = None,
    expires_at: datetime | None = None,
) -> Document:
    return Document(
        id=document_id,
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        name=document_id,
        status="completed",
        lifecycle_state=lifecycle_state,
        retrieval_enabled=retrieval_enabled,
        effective_from=effective_from,
        expires_at=expires_at,
    )


def _retrieved(document_id: str, chunk_id: str, *, branch: str = "hybrid") -> RetrievedChunk:
    return RetrievedChunk(
        chunk=Chunk(
            chunk_id=chunk_id,
            doc_id=document_id,
            text=f"text {chunk_id}",
            text_hash=f"hash-{chunk_id}",
            created_at=_CHUNK_NOW,
            updated_at=_CHUNK_NOW,
            tenant_id="tenant-1",
            dataset_id="dataset-1",
        ),
        score=0.8,
        rank=0,
        branch=branch,
    )


class _Embedder:
    def embed_query(self, _text: str) -> list[float]:
        return [1.0, 0.0]


class _Milvus:
    def __init__(
        self,
        main: list[RetrievedChunk],
        graph_chunks: list[Chunk] | None = None,
    ) -> None:
        self.main = main
        self.graph_chunks = graph_chunks or []

    @staticmethod
    def build_filters(**_kwargs: Any) -> None:
        return None

    def hybrid_search(self, **_kwargs: Any) -> list[RetrievedChunk]:
        return [item.model_copy(deep=True) for item in self.main]

    def get_chunks_by_ids(self, _ids: list[str]) -> list[Chunk]:
        return [chunk.model_copy(deep=True) for chunk in self.graph_chunks]


class _Rewriter:
    @staticmethod
    def rewrite(query: str) -> tuple[str, bool]:
        return query, False


class _Router:
    @staticmethod
    def route(_query: str) -> RouteDecision:
        return RouteDecision(target="vector_graph_rag", confidence=1.0)


class _GraphRetriever:
    @staticmethod
    def retrieve(_query: str, *, tenant_id: str) -> Any:
        assert tenant_id == "tenant-1"
        return SimpleNamespace(passage_ids=["graph-deleted"], degraded=False)


def test_guard_filters_lifecycle_and_effective_window_in_one_document_query(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add_all(
            [
                _document("active"),
                _document("future", effective_from=_NOW + timedelta(seconds=1)),
                _document("window-expired", expires_at=_NOW),
                _document("expired", lifecycle_state="expired", retrieval_enabled=False),
                _document(
                    "delete-requested",
                    lifecycle_state="delete_requested",
                    retrieval_enabled=False,
                ),
                _document("deleting", lifecycle_state="deleting", retrieval_enabled=False),
                _document(
                    "delete-failed",
                    lifecycle_state="delete_failed",
                    retrieval_enabled=False,
                ),
                _document("deleted", lifecycle_state="deleted", retrieval_enabled=False),
            ]
        )
        session.commit()

    guard = DocumentServingGuard(engine)
    snapshot = guard.snapshot("tenant-1", "dataset-1")
    document_selects = 0

    def count_document_selects(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        nonlocal document_selects
        if statement.lstrip().lower().startswith("select") and " from documents " in (
            " " + " ".join(statement.lower().split()) + " "
        ):
            document_selects += 1

    event.listen(engine, "before_cursor_execute", count_document_selects)
    try:
        allowed = guard.filter_document_ids(
            snapshot,
            [
                "active",
                "future",
                "window-expired",
                "expired",
                "delete-requested",
                "deleting",
                "delete-failed",
                "deleted",
            ],
            now=_NOW,
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_document_selects)

    assert allowed == {"active"}
    assert document_selects == 1


def test_pipeline_filters_milvus_and_graph_evidence_before_return(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(_document("active"))
        session.add(_document("deleted", lifecycle_state="deleted", retrieval_enabled=False))
        session.commit()

    active = _retrieved("active", "milvus-active")
    deleted = _retrieved("deleted", "milvus-deleted")
    graph_deleted = _retrieved("deleted", "graph-deleted", branch="graph").chunk
    pipeline = RetrievalPipeline(
        embedder=_Embedder(),
        milvus=_Milvus([active, deleted], [graph_deleted]),
        reranker=None,
        rewriter=_Rewriter(),
        router=_Router(),
        settings=PipelineSettings(
            complexity_gate_on=False,
            rerank_on=False,
            graph_retrieval_on=True,
            source_diversity="off",
            top_k=10,
        ),
        graph_retriever=_GraphRetriever(),
    )
    guard = DocumentServingGuard(engine)
    snapshot = guard.snapshot("tenant-1", "dataset-1")

    with bind_document_serving(guard, snapshot):
        result = pipeline.run(
            "question",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
        )

    assert [item.chunk.doc_id for item in result.chunks] == ["active"]
    assert all(item.chunk.chunk_id != "graph-deleted" for item in result.chunks)


def test_assert_current_detects_mid_query_delete_generation(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    guard = DocumentServingGuard(engine)
    snapshot = guard.snapshot("tenant-1", "dataset-1")

    with engine.begin() as connection:
        connection.execute(
            update(Dataset)
            .where(Dataset.id == "dataset-1", Dataset.tenant_id == "tenant-1")
            .values(serving_generation=Dataset.serving_generation + 1)
        )

    with pytest.raises(KnowledgeChanged):
        guard.assert_current(snapshot)


def test_cache_key_includes_database_serving_generation_even_if_redis_epoch_is_stale() -> None:
    old = make_cache_key(
        "question",
        ["public"],
        False,
        "tenant-1",
        "dataset-1",
        epoch="redis-epoch-stuck",
        serving_generation="7",
    )
    new = make_cache_key(
        "question",
        ["public"],
        False,
        "tenant-1",
        "dataset-1",
        epoch="redis-epoch-stuck",
        serving_generation="8",
    )

    assert old != new


def test_non_stream_query_abstains_and_does_not_cache_when_generation_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server import app as server_app

    engine = _engine(tmp_path)
    guard = DocumentServingGuard(engine)
    puts: list[dict[str, Any]] = []

    monkeypatch.setattr(server_app, "_get_document_serving_guard", lambda: guard)
    monkeypatch.setattr(
        server_app,
        "_cache_reserve",
        lambda _key: (None, SimpleNamespace(wait=lambda **_kwargs: True), True),
    )
    monkeypatch.setattr(server_app, "_cache_release", lambda *_args: None)
    monkeypatch.setattr(server_app, "_cache_put", lambda _key, payload: puts.append(payload))
    monkeypatch.setattr(server_app, "_record_query", lambda _payload: None)

    def answer(*_args: Any, **_kwargs: Any) -> QueryResult:
        with engine.begin() as connection:
            connection.execute(
                update(Dataset)
                .where(Dataset.id == "dataset-1", Dataset.tenant_id == "tenant-1")
                .values(serving_generation=Dataset.serving_generation + 1)
            )
        return QueryResult(query="question", answer="stale answer", abstained=False)

    monkeypatch.setattr(server_app, "answer_query", answer)

    payload = server_app._run_query(
        server_app.QueryRequest(
            query="question",
            tenant_id="tenant-1",
            dataset_id="dataset-1",
        )
    )

    assert payload["result"]["abstained"] is True
    assert payload["result"]["answer"] == ""
    assert payload["result"]["knowledge_changed"] is True
    assert puts == []


def test_stream_abstains_before_first_token_and_skips_cache_on_generation_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server import app as server_app

    engine = _engine(tmp_path)
    guard = DocumentServingGuard(engine)
    snapshot = guard.snapshot("tenant-1", "dataset-1")
    cache_writes: list[dict[str, Any]] = []

    settings = SimpleNamespace(
        pipeline=SimpleNamespace(
            hyde_on=False,
            subqueries_on=False,
            stepback_on=False,
            graph_retrieval_on=False,
            rerank_on=False,
            sentence_window_on=False,
        )
    )
    monkeypatch.setattr(server_app, "get_settings", lambda: settings)
    monkeypatch.setattr(
        server_app,
        "get_pipeline",
        lambda _settings=None: {"retrieval": SimpleNamespace()},
    )
    monkeypatch.setattr(
        server_app, "_serving_snapshot", lambda *_args, **_kwargs: (guard, snapshot)
    )
    monkeypatch.setattr(server_app, "_cache_key", lambda query, *_args: f"key:{query}")
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app,
        "_cache_reserve",
        lambda _key: (None, threading.Event(), True),
    )
    monkeypatch.setattr(server_app, "_cache_release", lambda *_args: None)
    monkeypatch.setattr(
        server_app, "_cache_put", lambda _key, payload: cache_writes.append(payload)
    )
    monkeypatch.setattr(server_app, "_record_query", lambda _payload: None)
    server_app._shutdown_query_executor()
    server_app._aio_flights.clear()
    server_app._pending = 0
    server_app._query_slots = asyncio.Semaphore(4)

    def stream(*_args: Any, **_kwargs: Any):
        yield {"type": "phase", "phase": "retrieving"}
        yield {"type": "phase", "phase": "generating"}
        # Race: generation changes after the phase frame was accepted but
        # before the first answer token is emitted.
        with engine.begin() as connection:
            connection.execute(
                update(Dataset)
                .where(Dataset.id == "dataset-1", Dataset.tenant_id == "tenant-1")
                .values(serving_generation=Dataset.serving_generation + 1)
            )
        yield {"type": "token", "text": "must-not-leak"}
        yield {
            "type": "done",
            "result": {
                "query": "question",
                "answer": "must-not-leak",
                "citations": [],
                "verdict": {},
                "abstained": False,
                "route": "hybrid",
                "traces": [],
            },
        }

    monkeypatch.setattr(server_app, "answer_query_stream", stream)

    class Request:
        async def is_disconnected(self) -> bool:
            return False

    async def scenario() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(
                query="question",
                tenant_id="tenant-1",
                dataset_id="dataset-1",
            ),
            Request(),
        )
        frames: list[dict[str, Any]] = []
        async for chunk in response.body_iterator:
            value = chunk.decode() if isinstance(chunk, bytes) else chunk
            frames.extend(
                json.loads(block.removeprefix("data: "))
                for block in value.split("\n\n")
                if block.startswith("data: ")
            )
        return frames

    try:
        frames = asyncio.run(scenario())
    finally:
        server_app._aio_flights.clear()
        server_app._shutdown_query_executor()

    legacy = [frame for frame in frames if frame.get("type") != "run_event"]
    assert not any(frame.get("type") == "token" for frame in legacy)
    done = next(frame for frame in legacy if frame.get("type") == "done")
    assert done["result"]["result"]["abstained"] is True
    assert done["result"]["result"]["knowledge_changed"] is True
    assert cache_writes == []


def _cached_answer_payload() -> dict[str, Any]:
    return {
        "result": {
            "query": "question",
            "answer": "stale cached answer",
            "citations": [],
            "verdict": {},
            "abstained": False,
            "route": "hybrid",
            "traces": [],
        },
        "using_mock": False,
        "duration_ms": 1.0,
    }


def test_stream_checks_every_answer_token_not_only_the_generation_phase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server import app as server_app

    engine = _engine(tmp_path)
    guard = DocumentServingGuard(engine)
    snapshot = guard.snapshot("tenant-1", "dataset-1")
    settings = SimpleNamespace(
        pipeline=SimpleNamespace(
            hyde_on=False,
            subqueries_on=False,
            stepback_on=False,
            graph_retrieval_on=False,
            rerank_on=False,
            sentence_window_on=False,
        )
    )
    monkeypatch.setattr(server_app, "get_settings", lambda: settings)
    monkeypatch.setattr(
        server_app,
        "get_pipeline",
        lambda _settings=None: {"retrieval": SimpleNamespace()},
    )
    monkeypatch.setattr(
        server_app, "_serving_snapshot", lambda *_args, **_kwargs: (guard, snapshot)
    )
    monkeypatch.setattr(server_app, "_cache_key", lambda query, *_args: f"key:{query}")
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(server_app, "_cache_reserve", lambda _key: (None, threading.Event(), True))
    monkeypatch.setattr(server_app, "_cache_release", lambda *_args: None)
    monkeypatch.setattr(server_app, "_cache_put", lambda *_args: None)
    monkeypatch.setattr(server_app, "_record_query", lambda *_args: None)
    server_app._shutdown_query_executor()
    server_app._aio_flights.clear()
    server_app._pending = 0
    server_app._query_slots = asyncio.Semaphore(4)

    first_token_consumed = threading.Event()

    def stream(*_args: Any, **_kwargs: Any):
        yield {"type": "phase", "phase": "generating"}
        yield {"type": "token", "text": "first-token"}
        assert first_token_consumed.wait(timeout=10)
        with engine.begin() as connection:
            connection.execute(
                update(Dataset)
                .where(Dataset.id == "dataset-1", Dataset.tenant_id == "tenant-1")
                .values(serving_generation=Dataset.serving_generation + 1)
            )
        yield {"type": "token", "text": "second-token-must-not-leak"}
        yield {"type": "done", "result": _cached_answer_payload()["result"]}

    monkeypatch.setattr(server_app, "answer_query_stream", stream)

    class Request:
        async def is_disconnected(self) -> bool:
            return False

    async def scenario() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="question", tenant_id="tenant-1", dataset_id="dataset-1"),
            Request(),
        )
        frames: list[dict[str, Any]] = []
        async for chunk in response.body_iterator:
            value = chunk.decode() if isinstance(chunk, bytes) else chunk
            parsed = [
                json.loads(block.removeprefix("data: "))
                for block in value.split("\n\n")
                if block.startswith("data: ")
            ]
            frames.extend(parsed)
            if any(
                frame.get("type") == "token" and frame.get("text") == "first-token"
                for frame in parsed
            ):
                first_token_consumed.set()
        return frames

    try:
        frames = asyncio.run(scenario())
    finally:
        server_app._aio_flights.clear()
        server_app._shutdown_query_executor()

    legacy = [frame for frame in frames if frame.get("type") != "run_event"]
    assert [frame["text"] for frame in legacy if frame.get("type") == "token"] == ["first-token"]
    done = next(frame for frame in legacy if frame.get("type") == "done")
    assert done["result"]["result"]["knowledge_changed"] is True


@pytest.mark.parametrize("replay_path", ["l1", "l2", "singleflight"])
def test_delayed_cache_replay_rechecks_generation_before_token_and_done(
    replay_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server import app as server_app

    engine = _engine(tmp_path)
    guard = DocumentServingGuard(engine)
    snapshot = guard.snapshot("tenant-1", "dataset-1")
    settings = SimpleNamespace(
        pipeline=SimpleNamespace(
            hyde_on=False,
            subqueries_on=False,
            stepback_on=False,
            graph_retrieval_on=False,
            rerank_on=False,
            sentence_window_on=False,
        )
    )
    monkeypatch.setattr(server_app, "get_settings", lambda: settings)
    monkeypatch.setattr(
        server_app,
        "get_pipeline",
        lambda _settings=None: {"retrieval": SimpleNamespace()},
    )
    monkeypatch.setattr(
        server_app, "_serving_snapshot", lambda *_args, **_kwargs: (guard, snapshot)
    )
    monkeypatch.setattr(server_app, "_cache_key", lambda query, *_args: f"key:{query}")
    monkeypatch.setattr(server_app, "_cache_release", lambda *_args: None)
    monkeypatch.setattr(server_app, "_record_query", lambda *_args: None)
    server_app._shutdown_query_executor()
    server_app._aio_flights.clear()
    server_app._pending = 0
    server_app._query_slots = asyncio.Semaphore(4)
    payload = _cached_answer_payload()

    class Request:
        async def is_disconnected(self) -> bool:
            return False

    async def scenario() -> list[dict[str, Any]]:
        if replay_path == "l1":
            monkeypatch.setattr(server_app, "_cache_peek", lambda _key: payload)
        else:
            monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
        if replay_path == "l2":
            monkeypatch.setattr(
                server_app,
                "_cache_reserve",
                lambda _key: (payload, threading.Event(), False),
            )
        else:
            monkeypatch.setattr(
                server_app,
                "_cache_reserve",
                lambda _key: (None, threading.Event(), True),
            )
        if replay_path == "singleflight":
            future = asyncio.get_running_loop().create_future()
            future.set_result(payload)
            server_app._aio_flights["key:question"] = future

        response = await server_app.query_stream(
            server_app.QueryRequest(query="question", tenant_id="tenant-1", dataset_id="dataset-1"),
            Request(),
        )
        # The response object exists, but replay has not been consumed yet.
        with engine.begin() as connection:
            connection.execute(
                update(Dataset)
                .where(Dataset.id == "dataset-1", Dataset.tenant_id == "tenant-1")
                .values(serving_generation=Dataset.serving_generation + 1)
            )
        frames: list[dict[str, Any]] = []
        async for chunk in response.body_iterator:
            value = chunk.decode() if isinstance(chunk, bytes) else chunk
            frames.extend(
                json.loads(block.removeprefix("data: "))
                for block in value.split("\n\n")
                if block.startswith("data: ")
            )
        return frames

    try:
        frames = asyncio.run(scenario())
    finally:
        server_app._aio_flights.clear()
        server_app._shutdown_query_executor()

    legacy = [frame for frame in frames if frame.get("type") != "run_event"]
    assert not any(
        frame.get("type") == "token" and frame.get("text") == "stale cached answer"
        for frame in legacy
    )
    done = next(frame for frame in legacy if frame.get("type") == "done")
    assert done["result"]["result"]["knowledge_changed"] is True


def test_catalog_unavailable_fails_closed_for_scoped_and_unscoped_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from server import app as server_app

    calls: list[str] = []
    monkeypatch.setattr(
        server_app,
        "_get_document_serving_guard",
        lambda: (_ for _ in ()).throw(RuntimeError("catalog unavailable")),
    )
    monkeypatch.setattr(
        server_app,
        "answer_query",
        lambda *_args, **_kwargs: calls.append("rag") or QueryResult(query="q", answer="bad"),
    )
    monkeypatch.setattr(server_app, "_cache_release", lambda *_args: None)

    scoped = server_app._run_query(
        server_app.QueryRequest(query="q", tenant_id="tenant-1", dataset_id="dataset-1")
    )
    unscoped = server_app._run_query(
        server_app.QueryRequest(query="q", tenant_id="tenant-1", dataset_id=None)
    )

    assert scoped["result"]["knowledge_changed"] is True
    assert unscoped["result"]["knowledge_changed"] is True

    settings = SimpleNamespace(
        pipeline=SimpleNamespace(
            hyde_on=False,
            subqueries_on=False,
            stepback_on=False,
            graph_retrieval_on=False,
            rerank_on=False,
            sentence_window_on=False,
        )
    )
    monkeypatch.setattr(server_app, "get_settings", lambda: settings)
    monkeypatch.setattr(
        server_app,
        "get_pipeline",
        lambda _settings=None: {"retrieval": SimpleNamespace()},
    )
    monkeypatch.setattr(
        server_app,
        "answer_query_stream",
        lambda *_args, **_kwargs: calls.append("stream-rag") or iter(()),
    )

    class Request:
        async def is_disconnected(self) -> bool:
            return False

    async def stream_scenario() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="q", tenant_id="tenant-1"),
            Request(),
        )
        frames: list[dict[str, Any]] = []
        async for chunk in response.body_iterator:
            value = chunk.decode() if isinstance(chunk, bytes) else chunk
            frames.extend(
                json.loads(block.removeprefix("data: "))
                for block in value.split("\n\n")
                if block.startswith("data: ")
            )
        return frames

    frames = asyncio.run(stream_scenario())
    legacy = [frame for frame in frames if frame.get("type") != "run_event"]
    assert not any(frame.get("type") == "token" for frame in legacy)
    done = next(frame for frame in legacy if frame.get("type") == "done")
    assert done["result"]["result"]["knowledge_changed"] is True
    assert calls == []


def test_document_expiry_changes_cache_generation_without_redis_epoch_bump(
    tmp_path: Path,
) -> None:
    from core.knowledge_content import KnowledgeContentRepository
    from core.knowledge_governance import AuditContext

    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(
            _document(
                "expiring-document",
                expires_at=_NOW - timedelta(seconds=1),
            )
        )
        session.commit()

    guard = DocumentServingGuard(engine)
    before = guard.snapshot("tenant-1", "dataset-1")
    old_key = make_cache_key(
        "question",
        ["public"],
        False,
        "tenant-1",
        "dataset-1",
        epoch="redis-bump-failed",
        serving_generation=before.cache_token,
    )
    result = KnowledgeContentRepository(engine).expire_due(
        "tenant-1",
        "dataset-1",
        now=_NOW,
        audit=AuditContext(
            actor_id="expiry-worker",
            request_id="expiry-request",
            request_ip="127.0.0.1",
        ),
    )
    after = guard.snapshot("tenant-1", "dataset-1")
    new_key = make_cache_key(
        "question",
        ["public"],
        False,
        "tenant-1",
        "dataset-1",
        epoch="redis-bump-failed",
        serving_generation=after.cache_token,
    )

    assert result.documents_expired == 1
    assert after.generation == before.generation + 1
    assert new_key != old_key
