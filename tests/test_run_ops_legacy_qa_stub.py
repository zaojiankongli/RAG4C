from __future__ import annotations

import importlib
import importlib.util
import json

from fastapi.testclient import TestClient


def _module():
    spec = importlib.util.find_spec("scripts.run_ops_legacy_qa_stub")
    assert spec is not None, "development-only legacy QA stub module is missing"
    return importlib.import_module("scripts.run_ops_legacy_qa_stub")


def test_legacy_stub_exposes_old_backend_metrics_health_and_cors() -> None:
    module = _module()
    client = TestClient(module.app, client=("127.0.0.1", 50000))

    runs = client.get("/api/runs/health")
    metrics = client.get("/api/metrics", headers={"Origin": "http://localhost:1420"})
    health = client.get("/api/health")
    preflight = client.options(
        "/api/metrics",
        headers={
            "Origin": "http://localhost:1420",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert runs.status_code == 404
    assert runs.json() == {
        "detail": {
            "code": "legacy_backend",
            "message": "development-only QA stub intentionally omits the Runs API",
        }
    }
    assert metrics.status_code == 200
    assert metrics.headers["access-control-allow-origin"] == "http://localhost:1420"
    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "milvus_uri": "qa-stub://not-connected",
        "embedding_model": "qa-stub",
        "reranker_model": "qa-stub",
        "generation_model": "qa-stub",
        "graph_engine_on": False,
        "qa_stub": True,
        "development_only": True,
    }
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:1420"


def test_legacy_metrics_snapshot_is_minimal_safe_and_frontend_compatible() -> None:
    payload = TestClient(_module().app).get("/api/metrics").json()

    assert payload["qa_stub"] is True
    assert payload["development_only"] is True
    assert payload["recent_queries"] == [
        {
            "query": "合成问题 A",
            "route": "hybrid",
            "abstained": False,
            "duration_ms": 1280,
            "citations": 3,
            "traces": [],
            "ts": "2026-08-24 09:00:00",
        },
        {
            "query": "合成问题 B",
            "route": "vector",
            "abstained": True,
            "duration_ms": 1840,
            "citations": 0,
            "traces": [],
            "ts": "2026-08-24 09:01:00",
        },
    ]
    required_stats = {"count", "sum", "mean", "min", "max", "p50", "p95", "p99"}
    assert {"query.total", "query.completed", "query.abstained"} <= payload["metrics"].keys()
    assert all(required_stats <= stat.keys() for stat in payload["metrics"].values())
    assert payload["queue"] == {"pending": 0, "max_concurrent": 4, "queue_max": 20}
    assert payload["cache"] == {
        "size": 2,
        "max": 64,
        "hits": 1,
        "misses": 1,
        "hit_rate": 0.5,
        "ttl_s": 600,
    }
    blob = json.dumps(payload, ensure_ascii=False).lower()
    for sentinel in (
        "sentinel_query",
        "sentinel_answer",
        "sentinel_token",
        "authorization",
        "tenant_scope",
        "document content",
    ):
        assert sentinel not in blob


def test_legacy_stub_cli_is_loopback_development_only_with_default_port_8000() -> None:
    module = _module()

    args = module.build_parser().parse_args([])

    assert args.port == 8000
    assert module.app.title == "RAG4C Legacy Ops QA Stub — development only"
    assert module.HOST == "127.0.0.1"
