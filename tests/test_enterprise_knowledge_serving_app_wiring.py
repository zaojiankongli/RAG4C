from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import APIRouter


SERVING_PATHS = {
    "/api/enterprise/knowledge-bases/{dataset_id}/serving/summary",
    "/api/enterprise/knowledge-bases/{dataset_id}/serving/profile",
    "/api/enterprise/knowledge-bases/{dataset_id}/serving/profile/revisions",
    "/api/enterprise/knowledge-bases/{dataset_id}/serving/profile/activate",
    "/api/enterprise/knowledge-bases/{dataset_id}/serving/snapshots",
    "/api/enterprise/knowledge-bases/{dataset_id}/serving/snapshots/{snapshot_id}",
    "/api/enterprise/knowledge-bases/{dataset_id}/serving/stage-facts",
    "/api/enterprise/knowledge-bases/{dataset_id}/serving/events",
    "/api/enterprise/knowledge-bases/{dataset_id}/serving/preview",
}


def _route_paths(router: Any) -> set[str]:
    paths: set[str] = set()
    for route in router.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)
        included = getattr(route, "original_router", None)
        if included is not None:
            paths.update(_route_paths(included))
    return paths


def _load_app_with_mocked_serving_module(monkeypatch: Any) -> tuple[Any, Any, dict[str, Any]]:
    calls: dict[str, Any] = {}
    fake_module = ModuleType("server.enterprise_knowledge_serving_api")

    def build_enterprise_knowledge_serving_router(
        *, read_engine_provider: Any, mutation_engine_provider: Any, readiness_provider: Any
    ) -> APIRouter:
        calls["read_engine_provider"] = read_engine_provider
        calls["mutation_engine_provider"] = mutation_engine_provider
        calls["readiness_provider"] = readiness_provider
        router = APIRouter()
        for path in sorted(SERVING_PATHS):
            router.add_api_route(path, lambda: {"ok": True}, methods=["GET"])
        return router

    fake_module.build_enterprise_knowledge_serving_router = (  # type: ignore[attr-defined]
        build_enterprise_knowledge_serving_router
    )
    monkeypatch.setitem(sys.modules, "server.enterprise_knowledge_serving_api", fake_module)

    probe_name = "server._stage26_app_wiring_probe"
    app_path = Path(__file__).resolve().parents[1] / "server" / "app.py"
    spec = importlib.util.spec_from_file_location(probe_name, app_path)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load server.app wiring probe")
    app_module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, probe_name, app_module)
    spec.loader.exec_module(app_module)
    return app_module.app, app_module, calls


def test_main_app_mounts_knowledge_serving_routes_with_separate_engines(monkeypatch: Any) -> None:
    app, app_module, calls = _load_app_with_mocked_serving_module(monkeypatch)

    assert SERVING_PATHS <= _route_paths(app)
    assert callable(calls["read_engine_provider"])
    assert callable(calls["mutation_engine_provider"])
    assert callable(calls["readiness_provider"])
    assert calls["read_engine_provider"] is not calls["mutation_engine_provider"]

    read_engine = object()
    mutation_engine = object()
    app.state.knowledge_auth_engine = read_engine
    monkeypatch.setattr(app_module.catalog, "get_engine", lambda: mutation_engine)
    assert calls["read_engine_provider"]() is read_engine
    assert calls["mutation_engine_provider"]() is mutation_engine
    assert mutation_engine is not read_engine


def test_enterprise_context_exposes_stage22_through_stage26_ready_capabilities(tmp_path) -> None:
    from core.enterprise_directory import enterprise_capabilities
    from tests.test_enterprise_knowledge_serving_readiness import _engine

    engine = _engine(tmp_path, "stage26-context.db")
    try:
        capabilities = enterprise_capabilities(engine)
        for key in (
            "enterprise_notification_center",
            "enterprise_content_recovery",
            "enterprise_task_operations",
            "enterprise_automation_workflows",
            "enterprise_knowledge_serving_reliability",
        ):
            assert capabilities[key]["state"] == "ready", capabilities[key]
    finally:
        engine.dispose()
