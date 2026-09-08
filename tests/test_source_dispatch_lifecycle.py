from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import server.app as server_app


def test_lifespan_starts_and_stops_source_dispatcher(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    class Runtime:
        def stop(self) -> None:
            order.append("source.stop")

    source_runtime = Runtime()
    run_runtime = SimpleNamespace(close=lambda: order.append("runs.stop"))

    class RunFactory:
        @classmethod
        def bootstrap(cls, _settings: Any) -> Any:
            return run_runtime

    monkeypatch.setattr(server_app, "RunOpsRuntime", RunFactory)
    monkeypatch.setattr(server_app, "get_settings", lambda: SimpleNamespace(sources=SimpleNamespace()))
    monkeypatch.setattr(server_app, "_start_query_executor", lambda: None)
    monkeypatch.setattr(server_app.documents_api, "start_ingest_executor", lambda: None)
    monkeypatch.setattr(server_app, "_prime_cache_epoch", lambda: None)
    monkeypatch.setattr(server_app, "_prewarm_router", lambda: None)
    monkeypatch.setattr(server_app, "_metrics_persist_loop", lambda: None)
    monkeypatch.setattr(server_app, "_persist_metrics_once", lambda: None)
    monkeypatch.setattr(server_app, "_shutdown_query_executor", lambda: None)
    monkeypatch.setattr(server_app.documents_api, "shutdown_ingest_executor", lambda: None)
    monkeypatch.setattr(server_app.documents_api, "shutdown_index_operation_worker", lambda: None)
    monkeypatch.setattr(
        server_app,
        "start_source_dispatch_runtime",
        lambda application, settings: order.append("source.start") or source_runtime,
    )

    async def scenario() -> None:
        async with server_app.lifespan(server_app.app):
            assert server_app.app.state.knowledge_source_dispatcher is source_runtime
            assert order == ["source.start"]

    asyncio.run(scenario())
    assert order.index("source.stop") < order.index("runs.stop")



def test_lifespan_fails_closed_when_source_dispatcher_authority_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = SimpleNamespace(close=lambda: None)

    class RunFactory:
        @classmethod
        def bootstrap(cls, _settings: Any) -> Any:
            return runtime

    monkeypatch.setattr(server_app, "RunOpsRuntime", RunFactory)
    monkeypatch.setattr(server_app, "get_settings", lambda: SimpleNamespace(sources=SimpleNamespace()))
    monkeypatch.setattr(server_app, "_start_query_executor", lambda: None)
    monkeypatch.setattr(server_app.documents_api, "start_ingest_executor", lambda: None)
    monkeypatch.setattr(
        server_app,
        "start_source_dispatch_runtime",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("catalog unavailable")),
    )

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="catalog unavailable"):
            async with server_app.lifespan(server_app.app):
                raise AssertionError("lifespan must not become ready")

    asyncio.run(scenario())
