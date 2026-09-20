from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pydantic import SecretStr

import pytest

import rag_common
from rag_topology import build_cache_replay_topology, topology_dict

from config.settings import RunHistorySettings
from core.metrics import get_metrics
from core.run_events import MemoryRunEventSink, RunEvent
from models.schemas import QueryResult
from server import app as server_app
from server.run_ops import RunOpsRuntime, create_run_ops_router


@pytest.fixture(autouse=True)
def _disable_serving_fence_for_run_registry_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setattr(
        server_app, "_serving_snapshot", lambda *_args, **_kwargs: (None, None)
    )
    yield


def _settings(
    tmp_path: Path,
    *,
    enabled: bool = True,
    persistence_enabled: bool | None = None,
    fingerprint_secret: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        run_history=RunHistorySettings(
            enabled=enabled,
            persistence_enabled=(enabled if persistence_enabled is None else persistence_enabled),
            fingerprint_secret=(
                None if fingerprint_secret is None else SecretStr(fingerprint_secret)
            ),
            sqlite_path=str(tmp_path / "run-history.sqlite3"),
            writer_queue_capacity=4,
            writer_batch_size=1,
            writer_flush_ms=1,
        ),
        tenant=SimpleNamespace(enforced=True, default_tenant="default"),
        pipeline=SimpleNamespace(
            hyde_on=False, subqueries_on=False, stepback_on=False,
            graph_retrieval_on=False, rerank_on=False, sentence_window_on=False,
        ),
    )


def test_runtime_disabled_and_healthy_bootstrap(tmp_path: Path) -> None:
    disabled = RunOpsRuntime.disabled(_settings(tmp_path / "off", enabled=False))
    healthy = RunOpsRuntime.bootstrap(_settings(tmp_path / "on"))

    assert disabled.store is None
    assert disabled.service.registry is disabled.registry
    assert disabled.identity.tenant_scope_stability == "boot"
    assert disabled.registry.health_snapshot().status == "disabled"
    assert healthy.store is not None
    assert healthy.identity.tenant_scope_stability == "installation"
    assert healthy.store.health_snapshot().state == "ready"
    disabled.close()
    healthy.close()
    healthy.close()


def test_runtime_store_failure_is_boot_scoped_without_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    def fail_open(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise OSError("disk unavailable")

    monkeypatch.setattr("server.run_ops.RunHistoryStore.open", fail_open)
    runtime = RunOpsRuntime.bootstrap(_settings(tmp_path))
    scope = runtime.scope_for("default")

    assert calls == 1
    assert runtime.store is None
    assert runtime.identity.tenant_scope_stability == "boot"
    assert runtime.scope_for("default") == scope
    runtime.close()


def test_router_once_and_lifespan_closes_after_query_settle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    included = [
        route.original_router
        for route in server_app.app.routes
        if hasattr(route, "original_router")
    ]
    run_paths = [
        route.path
        for router in included
        for route in router.routes
        if route.path.startswith("/api/runs")
    ]
    assert sorted(run_paths) == [
        "/api/runs", "/api/runs/health", "/api/runs/{run_id}",
        "/api/runs/{run_id}/events",
    ]

    order: list[str] = []
    runtime = SimpleNamespace(close=lambda: order.append("runtime.close"))

    class RuntimeFactory:
        @classmethod
        def bootstrap(cls, _settings: Any) -> Any:
            order.append("runtime.bootstrap")
            return runtime

    monkeypatch.setattr(server_app, "RunOpsRuntime", RuntimeFactory)
    monkeypatch.setattr(server_app, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        server_app.app.state,
        "knowledge_source_dispatcher_test_override",
        None,
        raising=False,
    )
    monkeypatch.setattr(server_app, "_start_query_executor", lambda: order.append("query.start"))
    monkeypatch.setattr(server_app.documents_api, "start_ingest_executor", lambda: None)
    monkeypatch.setattr(server_app, "_prime_cache_epoch", lambda: None)
    monkeypatch.setattr(server_app, "_prewarm_router", lambda: None)
    monkeypatch.setattr(server_app, "_metrics_persist_loop", lambda: None)
    monkeypatch.setattr(server_app, "_persist_metrics_once", lambda: None)
    monkeypatch.setattr(
        server_app, "_shutdown_query_executor", lambda: order.append("query.settled")
    )
    monkeypatch.setattr(server_app.documents_api, "shutdown_ingest_executor", lambda: None)

    async def scenario() -> None:
        async with server_app.lifespan(server_app.app):
            assert server_app.app.state.run_ops_runtime is runtime
            assert order.index("runtime.bootstrap") < order.index("query.start")

    asyncio.run(scenario())
    assert order.index("query.settled") < order.index("runtime.close")


class _StreamRequest:
    app = server_app.app

    async def is_disconnected(self) -> bool:
        return False


def _parse_frames(chunks: list[str | bytes]) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for chunk in chunks:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        frames.extend(
            json.loads(block.removeprefix("data: "))
            for block in text.split("\n\n")
            if block.startswith("data: ")
        )
    return frames


@pytest.mark.parametrize("secret, fingerprint_present", [(None, False), ("secret", True)])
def test_stream_stores_same_typed_events_with_optional_fingerprint(
    secret: str | None,
    fingerprint_present: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path, persistence_enabled=False, fingerprint_secret=secret
    )
    runtime = RunOpsRuntime.bootstrap(settings)
    # 裸赋值会把 runtime 永久留在进程级 app.state 上污染后续用例；monkeypatch 会还原。
    monkeypatch.setattr(server_app.app.state, "run_ops_runtime", runtime, raising=False)
    monkeypatch.setattr(server_app, "get_settings", lambda: settings)
    monkeypatch.setattr(
        server_app, "get_pipeline", lambda _settings=None: {"retrieval": SimpleNamespace()}
    )
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(
        server_app, "_cache_reserve", lambda _key: (None, threading.Event(), True)
    )
    monkeypatch.setattr(server_app, "_cache_put", lambda *_args: None)
    monkeypatch.setattr(server_app, "_cache_release", lambda *_args: None)
    monkeypatch.setattr(server_app, "_record_query", lambda *_args: None)
    resolved: list[str | None] = []
    contexts: list[tuple[str, str | None]] = []
    original_bound_sink = runtime.registry.bound_sink

    def resolve_once(tenant_id: str | None, _settings: Any) -> str:
        resolved.append(tenant_id)
        return "tenant-private"

    def bound_sink(scope: str, fingerprint: str | None = None) -> Any:
        contexts.append((scope, fingerprint))
        return original_bound_sink(scope, fingerprint)

    def stream(query: str, **_kwargs: Any) -> Iterator[dict[str, Any]]:
        yield {"type": "phase", "phase": "retrieving"}
        yield {
            "type": "done",
            "result": QueryResult(query=query, answer="answer").model_dump(mode="json"),
        }

    monkeypatch.setattr(server_app, "resolve_tenant", resolve_once)
    monkeypatch.setattr(rag_common, "resolve_tenant", resolve_once)
    monkeypatch.setattr(runtime.registry, "bound_sink", bound_sink)
    monkeypatch.setattr(server_app, "answer_query_stream", stream)

    async def scenario() -> list[dict[str, Any]]:
        response = await server_app.query_stream(
            server_app.QueryRequest(query="private question", tenant_id="tenant-private"),
            _StreamRequest(),
        )
        chunks = [chunk async for chunk in response.body_iterator]
        return _parse_frames(chunks)

    frames = asyncio.run(scenario())
    typed = [frame["event"] for frame in frames if frame["type"] == "run_event"]
    stored = runtime.registry.get_events(contexts[0][0], typed[0]["run_id"], 0, 500)
    runtime.close()

    assert resolved == ["tenant-private"]
    assert contexts[0][0] != "tenant-private"
    assert (contexts[0][1] is not None) is fingerprint_present
    assert stored is not None
    assert json.loads(
        json.dumps(stored.events, ensure_ascii=False, default=dict)
    ) == typed
    assert "private question" not in json.dumps(
        stored.events, ensure_ascii=False, default=dict
    )


def test_rest_query_does_not_bind_registry_sink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, persistence_enabled=False)
    runtime = RunOpsRuntime.bootstrap(settings)
    # 裸赋值会把 runtime 永久留在进程级 app.state 上污染后续用例；monkeypatch 会还原。
    monkeypatch.setattr(server_app.app.state, "run_ops_runtime", runtime, raising=False)
    calls = 0

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError("REST must not bind registry")

    monkeypatch.setattr(runtime.registry, "bound_sink", forbidden)
    monkeypatch.setattr(
        server_app,
        "_cache_peek",
        lambda _key: {"result": {"query": "q"}, "duration_ms": 0.0},
    )
    monkeypatch.setattr(server_app, "_cache_key", lambda *_args: "key:q")

    result = asyncio.run(server_app.query(server_app.QueryRequest(query="q")))
    runtime.close()
    assert result["cached"] is True
    assert calls == 0


@pytest.mark.parametrize(
    "mode",
    ["disabled", "healthy", "throwing_sink", "permanent_sqlite_failure", "queue_full"],
)
def test_five_registry_modes_preserve_canonical_fanout_parity(
    mode: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    enabled = mode != "disabled"
    settings = _settings(
        tmp_path / mode, enabled=enabled, persistence_enabled=enabled
    )
    if mode == "permanent_sqlite_failure":
        monkeypatch.setattr(
            "server.run_ops.RunHistoryStore.open",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
        )
    runtime = (
        RunOpsRuntime.disabled(settings)
        if mode == "disabled"
        else RunOpsRuntime.bootstrap(settings)
    )
    scope = runtime.scope_for("default")
    if mode == "throwing_sink":
        def registry_sink(_event: RunEvent) -> None:
            raise RuntimeError("registry unavailable")
    else:
        if mode == "queue_full":
            runtime.registry.set_persistence_offer(lambda _mutation: False)
        registry_sink = runtime.registry.bound_sink(scope)

    baseline_sink = MemoryRunEventSink()
    candidate_sink = MemoryRunEventSink()
    baseline = server_app._HttpStreamRun(
        settings=settings,
        pipeline={"retrieval": SimpleNamespace()},
        sink=baseline_sink,
        retry_enabled=True,
    )
    candidate = server_app._HttpStreamRun(
        settings=settings,
        pipeline={"retrieval": SimpleNamespace()},
        sink=candidate_sink,
        retry_enabled=True,
        extra_sinks=(registry_sink,),
    )
    for run in (baseline, candidate):
        run.activate(mode="query", path="l2_owner")
        run.complete(abstained=False)
    runtime.close()

    def stable(events: list[RunEvent]) -> list[dict[str, Any]]:
        values = [event.model_dump(mode="json") for event in events]
        for value in values:
            value["run_id"] = "<run>"
            value["occurred_at"] = "<time>"
            value["elapsed_ms"] = 0.0
            if value.get("duration_ms") is not None:
                value["duration_ms"] = 0.0
        return values

    assert stable(candidate_sink.events) == stable(baseline_sink.events)


def test_bootstrap_background_start_failure_closes_store_and_freezes_memory_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, int | None]] = []
    opened_identity: dict[str, str] = {}

    class FailingStartStore:
        scope_key = b"s" * 32
        cursor_key = b"c" * 32
        write_enabled = True

        @staticmethod
        def health_snapshot() -> Any:
            return SimpleNamespace(state="ready")

        @staticmethod
        def enqueue(_mutation: Any) -> bool:
            return True

        @staticmethod
        def start_background_tasks() -> None:
            calls.append(("start", None))
            raise RuntimeError("background failed")

        @staticmethod
        def close(grace_ms: int | None = None) -> None:
            calls.append(("close", grace_ms))

    def open_store(*_args: Any, **kwargs: Any) -> FailingStartStore:
        opened_identity.update(
            boot_id=kwargs["boot_id"], worker_id=kwargs["worker_id"]
        )
        return FailingStartStore()

    monkeypatch.setattr("server.run_ops.RunHistoryStore.open", open_store)
    runtime = RunOpsRuntime.bootstrap(_settings(tmp_path))
    scope = runtime.scope_for("default")

    assert calls == [("start", None), ("close", 2000)]
    assert runtime.store is None
    assert runtime.identity.tenant_scope_stability == "boot"
    assert runtime.scope_for("default") == scope
    health = asyncio.run(runtime.service.health())
    assert health.status == "degraded"
    assert health.persistence.state == "memory_only"


def test_runtime_close_is_silent_when_grace_settings_access_fails(tmp_path: Path) -> None:
    runtime = RunOpsRuntime.bootstrap(_settings(tmp_path, persistence_enabled=False))
    calls: list[tuple[str, int]] = []

    class BrokenSettings:
        @property
        def run_history(self) -> Any:
            raise RuntimeError("settings unavailable")

    class Store:
        @staticmethod
        def mark_clean_shutdown(_now: Any, grace_ms: int) -> None:
            calls.append(("mark", grace_ms))

        @staticmethod
        def close(grace_ms: int) -> None:
            calls.append(("close", grace_ms))

    runtime.settings = BrokenSettings()
    runtime.store = Store()  # type: ignore[assignment]

    runtime.close()
    assert calls == [("mark", 0), ("close", 0)]


@pytest.mark.parametrize("failing_shutdown", ["query", "ingest"])
def test_lifespan_shutdown_failures_still_close_runtime_without_leaking(
    failing_shutdown: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []

    class Runtime:
        @staticmethod
        def close() -> None:
            order.append("runtime.close")
            raise RuntimeError("runtime close failed")

    class RuntimeFactory:
        @classmethod
        def bootstrap(cls, _settings: Any) -> Any:
            return Runtime()

    def shutdown(name: str) -> None:
        order.append(name)
        if name == failing_shutdown:
            raise RuntimeError(f"{name} shutdown failed")

    monkeypatch.setattr(server_app, "RunOpsRuntime", RuntimeFactory)
    monkeypatch.setattr(server_app, "get_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(
        server_app.app.state,
        "knowledge_source_dispatcher_test_override",
        None,
        raising=False,
    )
    monkeypatch.setattr(server_app, "_start_query_executor", lambda: None)
    monkeypatch.setattr(server_app.documents_api, "start_ingest_executor", lambda: None)
    monkeypatch.setattr(server_app, "_prime_cache_epoch", lambda: None)
    monkeypatch.setattr(server_app, "_prewarm_router", lambda: None)
    monkeypatch.setattr(server_app, "_metrics_persist_loop", lambda: None)
    monkeypatch.setattr(server_app, "_persist_metrics_once", lambda: None)
    monkeypatch.setattr(server_app, "_shutdown_query_executor", lambda: shutdown("query"))
    monkeypatch.setattr(
        server_app.documents_api, "shutdown_ingest_executor", lambda: shutdown("ingest")
    )

    async def scenario() -> None:
        async with server_app.lifespan(server_app.app):
            pass

    asyncio.run(scenario())
    assert set(order[:2]) == {"query", "ingest"}
    assert order[2:] == ["runtime.close"]


@pytest.mark.parametrize(
    "mode",
    [
        "disabled",
        "persistence_disabled",
        "healthy",
        "throwing_sink",
        "permanent_sqlite_failure",
        "unwritable",
        "corrupt",
        "schema_too_new",
        "locked",
        "disk_full",
        "queue_full",
    ],
)
def test_registry_failure_modes_preserve_query_sse_and_cache_parity(
    mode: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    get_metrics().reset()
    enabled = mode != "disabled"
    persistence_enabled = enabled and mode != "persistence_disabled"
    data_dir = tmp_path / mode
    settings = _settings(
        data_dir, enabled=enabled, persistence_enabled=persistence_enabled
    )
    if mode == "unwritable":
        data_dir.write_text("occupied", encoding="utf-8")
    elif mode == "corrupt":
        data_dir.mkdir(parents=True)
        Path(settings.run_history.sqlite_path).write_bytes(b"not-a-sqlite-database")
    elif mode == "schema_too_new":
        seed_runtime = RunOpsRuntime.bootstrap(settings)
        seed_runtime.close()
        with sqlite3.connect(settings.run_history.sqlite_path) as connection:
            connection.execute("PRAGMA user_version=2")
    if mode == "queue_full":
        settings.run_history = settings.run_history.model_copy(
            update={"writer_queue_capacity": 1, "writer_batch_size": 1}
        )
    elif mode in {"locked", "disk_full"}:
        settings.run_history = settings.run_history.model_copy(
            update={"writer_queue_capacity": 64, "writer_batch_size": 8}
        )
    if mode == "permanent_sqlite_failure":
        with monkeypatch.context() as scoped:
            scoped.setattr(
                "server.run_ops.RunHistoryStore.open",
                lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    OSError("disk unavailable")
                ),
            )
            runtime = RunOpsRuntime.bootstrap(settings)
    else:
        runtime = (
            RunOpsRuntime.disabled(settings)
            if mode == "disabled"
            else RunOpsRuntime.bootstrap(settings)
        )
    if mode == "throwing_sink":
        monkeypatch.setattr(
            runtime.registry,
            "bound_sink",
            lambda *_args, **_kwargs: (
                lambda _event: (_ for _ in ()).throw(RuntimeError("sink failed"))
            ),
        )
    if mode == "queue_full":
        assert runtime.store is not None
        assert runtime.store._capacity.acquire(blocking=False)  # noqa: SLF001

    database_lock: sqlite3.Connection | None = None
    if mode == "locked":
        assert runtime.store is not None
        database_lock = sqlite3.connect(runtime.store.database_path, timeout=0, isolation_level=None)
        database_lock.execute("BEGIN IMMEDIATE")
    if mode == "disk_full":
        assert runtime.store is not None
        original_write = runtime.store._write_batch_once  # noqa: SLF001
        failed_once = False

        def fail_disk_once(*args: Any, **kwargs: Any) -> Any:
            nonlocal failed_once
            if not failed_once:
                failed_once = True
                raise sqlite3.OperationalError("database or disk is full")
            return original_write(*args, **kwargs)

        monkeypatch.setattr(runtime.store, "_write_batch_once", fail_disk_once)

    if mode in {"locked", "disk_full"}:
        probe = server_app._HttpStreamRun(
            settings=settings,
            pipeline={"retrieval": SimpleNamespace()},
            sink=MemoryRunEventSink(),
            retry_enabled=True,
            extra_sinks=(
                runtime.registry.bound_sink(runtime.scope_for("default")),
            ),
        )
        probe.activate(mode="query", path="l2_owner")
        probe.complete(abstained=False)

    monkeypatch.setattr(server_app, "get_settings", lambda: settings)
    monkeypatch.setattr(
        server_app, "get_pipeline", lambda _settings=None: {"retrieval": SimpleNamespace()}
    )
    monkeypatch.setattr(server_app, "_cache_peek", lambda _key: None)
    monkeypatch.setattr(server_app, "_cache_release", lambda *_args: None)
    monkeypatch.setattr(server_app, "_record_query", lambda *_args: None)

    def stream(query: str, **_kwargs: Any) -> Iterator[dict[str, Any]]:
        yield {"type": "phase", "phase": "retrieving"}
        yield {"type": "token", "text": "answer"}
        yield {
            "type": "done",
            "result": QueryResult(query=query, answer="answer").model_dump(mode="json"),
        }

    monkeypatch.setattr(server_app, "answer_query_stream", stream)

    async def capture(selected_runtime: Any) -> dict[str, Any]:
        server_app._aio_flights.clear()
        server_app._query_slots = asyncio.Semaphore(32)
        # 同上：裸赋值会把本次选中的 runtime 永久留在进程级 app.state 上。
        monkeypatch.setattr(
            server_app.app.state, "run_ops_runtime", selected_runtime, raising=False
        )
        cache_writes: list[dict[str, Any]] = []
        monkeypatch.setattr(
            server_app, "_cache_reserve", lambda _key: (None, threading.Event(), True)
        )
        monkeypatch.setattr(
            server_app, "_cache_put", lambda _key, value: cache_writes.append(value)
        )
        response = await server_app.query_stream(
            server_app.QueryRequest(query="same fixture", retry=True), _StreamRequest()
        )
        chunks = [chunk async for chunk in response.body_iterator]
        frames = _parse_frames(chunks)
        legacy = [
            frame for frame in frames if frame["type"] not in {"run_event", "run_event_desync"}
        ]
        typed = [frame["event"] for frame in frames if frame["type"] == "run_event"]
        done = next(frame["result"] for frame in legacy if frame["type"] == "done")
        errors = [frame.get("code") for frame in legacy if frame["type"] == "error"]
        return {
            "status": response.status_code,
            "legacy": legacy,
            "typed": typed,
            "done": done,
            "cache": cache_writes,
            "errors": errors,
        }

    baseline = asyncio.run(capture(SimpleNamespace(registry=None, identity=None)))
    candidate = asyncio.run(capture(runtime))
    if mode == "disk_full":
        assert runtime.store is not None
        assert runtime.store.flush_for_test(timeout_s=3)
        assert runtime.store.health_snapshot().reason == "write_failed"
    if mode == "locked":
        assert runtime.store is not None
        assert runtime.store.health_snapshot().writer_queue_depth > 0
    if database_lock is not None:
        database_lock.rollback()
        database_lock.close()
        assert runtime.store is not None
        assert runtime.store.flush_for_test(timeout_s=6)
    runtime.close()
    server_app._shutdown_query_executor()

    def stable(value: Any) -> Any:
        normalized = json.loads(json.dumps(value, ensure_ascii=False))
        def visit(item: Any) -> None:
            if isinstance(item, dict):
                if "run_id" in item:
                    item["run_id"] = "<run>"
                if "occurred_at" in item:
                    item["occurred_at"] = "<time>"
                if "elapsed_ms" in item:
                    item["elapsed_ms"] = 0.0
                for timing_key in (
                    "elapsed_ms", "duration_ms", "slot_wait_ms", "queue_wait_ms"
                ):
                    if timing_key in item:
                        item[timing_key] = 0.0
                for child in item.values():
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)
        visit(normalized)
        return normalized

    assert stable(candidate) == stable(baseline)
    metrics = get_metrics().snapshot()
    run_metrics = {key for key in metrics if key.startswith(("run_registry.", "run_history.", "run_ops."))}
    if mode not in {"disabled", "throwing_sink"}:
        assert run_metrics
    forbidden = {"run_id", "tenant", "fingerprint", "route", "error", "message"}
    for key in run_metrics:
        labels = key.partition("|")[2].split(",") if "|" in key else []
        assert not any(label.partition("=")[0] in forbidden for label in labels)


def test_background_start_failure_reuses_boot_identity_and_stops_sqlite_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)

    def fail_start(_store: Any) -> None:
        raise RuntimeError("background start failed")

    monkeypatch.setattr(
        "server.run_ops.RunHistoryStore.start_background_tasks", fail_start
    )
    runtime = RunOpsRuntime.bootstrap(settings)

    with sqlite3.connect(settings.run_history.sqlite_path) as connection:
        boots = connection.execute(
            "SELECT boot_id,state FROM boots ORDER BY boot_id"
        ).fetchall()
        workers = connection.execute(
            "SELECT worker_id,boot_id,state FROM workers ORDER BY worker_id"
        ).fetchall()

    assert runtime.store is None
    assert runtime.identity.tenant_scope_stability == "boot"
    assert boots == [(runtime.identity.boot_id, "stopped")]
    assert workers == [
        (runtime.identity.worker_id, runtime.identity.boot_id, "stopped")
    ]


def test_sensitive_corpus_is_absent_across_registry_queue_sqlite_api_and_logs(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    hostile_fields: tuple[tuple[str, Any], ...] = (
        ("query", "SENTINEL_QUERY_8F31"),
        ("answer", "SENTINEL_ANSWER_49C2"),
        ("acl", "SENTINEL_ACL_1A77"),
        ("chunk_ids", ["SENTINEL_CHUNK_65D0"]),
        ("api_key", "SENTINEL_API_KEY_D288"),
        ("authorization", "Bearer SENTINEL_BEARER_443E"),
        ("cookie", "SENTINEL_COOKIE_C901"),
        ("prompt", "SENTINEL_PROMPT_30AA"),
        ("traceback", "SENTINEL_STACK_B70C"),
    )
    sentinels = {
        "SENTINEL_QUERY_8F31",
        "SENTINEL_ANSWER_49C2",
        "SENTINEL_ACL_1A77",
        "SENTINEL_CHUNK_65D0",
        "SENTINEL_API_KEY_D288",
        "SENTINEL_BEARER_443E",
        "SENTINEL_COOKIE_C901",
        "SENTINEL_PROMPT_30AA",
        "SENTINEL_STACK_B70C",
    }
    settings = _settings(tmp_path)
    runtime = RunOpsRuntime.bootstrap(settings)
    assert runtime.store is not None
    scope = runtime.scope_for("default")
    sink = runtime.registry.bound_sink(scope)
    now = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)
    topology = topology_dict(build_cache_replay_topology())
    started = RunEvent(
        run_id="run-release-privacy",
        seq=1,
        occurred_at=now,
        elapsed_ms=0.0,
        topology_id=topology["id"],
        topology_revision=topology["revision"],
        type="run.started",
        attributes={"executor": topology["executor"], "topology": topology},
    )
    sink(started)
    for key, value in hostile_fields:
        sink(
            started.model_copy(
                update={
                    "seq": 2,
                    "elapsed_ms": 2.0,
                    "type": "degraded",
                    "attributes": {key: value},
                }
            )
        )
    sink(
        started.model_copy(
            update={
                "seq": 2,
                "elapsed_ms": 5.0,
                "type": "degraded",
                "attributes": {"reason": "redaction_probe"},
            }
        )
    )
    sink(
        started.model_copy(
            update={
                "seq": 3,
                "elapsed_ms": 10.0,
                "type": "run.completed",
                "attributes": {"outcome": "answered"},
            }
        )
    )

    detail = runtime.registry.get_detail(scope, started.run_id)
    events = runtime.registry.get_events(scope, started.run_id, 0, 20)
    assert detail is not None and events is not None
    assert detail.summary.event_integrity == "partial"
    assert [event["seq"] for event in events.events] == [1, 2, 3]
    assert runtime.store.flush_for_test(timeout_s=5)
    stored_detail = runtime.store.get_run(scope, started.run_id)
    stored_events = runtime.store.get_events(scope, started.run_id, 0, 20)
    assert stored_detail is not None and stored_events is not None
    assert [event["seq"] for event in stored_events.events] == [1, 2, 3]
    with sqlite3.connect(runtime.store.database_path) as connection:
        run_count = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE run_id=?", (started.run_id,)
        ).fetchone()[0]
        event_count = connection.execute(
            "SELECT COUNT(*) FROM run_events WHERE run_id=?", (started.run_id,)
        ).fetchone()[0]
    assert run_count == 1
    assert event_count == 3

    caplog.set_level("WARNING", logger="core.run_registry")
    original_accept = runtime.registry._accept
    monkeypatch.setattr(
        runtime.registry,
        "_accept",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("SENTINEL_STACK_B70C")
        ),
    )
    sink(started.model_copy(update={"run_id": "run-log-probe"}))
    monkeypatch.setattr(runtime.registry, "_accept", original_accept)

    app = FastAPI()
    app.state.run_ops_runtime = runtime
    app.include_router(create_run_ops_router())
    client = TestClient(app, client=("127.0.0.1", 43120))
    list_response = client.get("/api/runs")
    detail_response = client.get(f"/api/runs/{started.run_id}")
    events_response = client.get(f"/api/runs/{started.run_id}/events")
    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert events_response.status_code == 200
    assert any(item["run_id"] == started.run_id for item in list_response.json()["items"])
    assert detail_response.json()["summary"]["run_id"] == started.run_id
    assert events_response.json()["run_id"] == started.run_id
    assert [event["seq"] for event in events_response.json()["events"]] == [1, 2, 3]
    responses = [list_response.json(), detail_response.json(), events_response.json()]
    client.close()
    runtime.close()

    database_path = Path(settings.run_history.sqlite_path)
    disk_bytes = b"".join(
        path.read_bytes()
        for path in (
            database_path,
            Path(f"{database_path}-wal"),
            Path(f"{database_path}-shm"),
        )
        if path.exists()
    )
    exposed = "\n".join(
        [
            json.dumps(responses, ensure_ascii=False),
            repr(detail),
            repr(events),
            repr(stored_detail),
            repr(stored_events),
            caplog.text,
        ]
        + [repr(record.__dict__) for record in caplog.records]
    )
    for sentinel in sentinels:
        assert sentinel not in exposed
        assert sentinel.encode() not in disk_bytes
