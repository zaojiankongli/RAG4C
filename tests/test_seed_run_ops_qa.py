from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_run_ops_qa.py"
PREFIX = "qa-route-b-"
EXPECTED = {
    "active": "running",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "interrupted": "interrupted",
    "stuck": "running",
    "slow": "completed",
    "retry-degraded": "completed",
    "gap": "completed",
    "events-1000": "completed",
}
FORBIDDEN_KEYS = {
    "query",
    "question",
    "answer",
    "response",
    "text",
    "prompt",
    "acl",
    "tenant",
    "dataset",
    "document",
    "chunk",
    "embedding",
    "vector",
    "api_key",
    "authorization",
    "bearer",
    "cookie",
    "token",
    "message",
    "traceback",
    "stack",
    "path",
}


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=SCRIPT.parents[1],
        text=True,
        capture_output=True,
        check=False,
    )


def _walk_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = {str(key).casefold() for key in value}
        for item in value.values():
            keys.update(_walk_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_walk_keys(item))
        return keys
    return set()


def test_cli_seeds_safe_route_b_visual_matrix_and_checks_it(tmp_path: Path) -> None:
    database = tmp_path / "qa-run-history.sqlite3"

    result = _run("--sqlite-path", str(database))

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    reported_event_count = report.pop("event_count")
    scope_prefix = report.pop("scope_prefix")
    assert len(scope_prefix) == 8
    assert report == {
        "adopted_worker_id": None,
        "database": str(database.resolve()),
        "gap_earliest_available_seq": 5,
        "privacy_check": "clean",
        "run_count": 10,
        "status_counts": {
            "cancelled": 1,
            "completed": 5,
            "failed": 1,
            "interrupted": 1,
            "running": 2,
        },
    }

    with sqlite3.connect(database) as connection:
        scope_key = connection.execute(
            "SELECT value FROM registry_meta WHERE key=\'scope_key\'"
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT run_id,status,query_fingerprint FROM runs WHERE run_id LIKE ? ORDER BY run_id",
            (PREFIX + "%",),
        ).fetchall()
        events = connection.execute(
            "SELECT run_id,seq,event_json FROM run_events WHERE run_id LIKE ? ORDER BY run_id,seq",
            (PREFIX + "%",),
        ).fetchall()

    from config.settings import get_settings, resolve_tenant
    from core.run_registry import derive_tenant_scope

    expected_scope = derive_tenant_scope(resolve_tenant(None, get_settings()), bytes(scope_key))
    assert scope_prefix == expected_scope[:8]
    assert {run_id.removeprefix(PREFIX): status for run_id, status, _ in rows} == EXPECTED
    assert all(fingerprint is None for _, _, fingerprint in rows)
    assert reported_event_count == len(events)
    assert sum(run_id == PREFIX + "events-1000" for run_id, _, _ in events) == 1000
    gap_seqs = [seq for run_id, seq, _ in events if run_id == PREFIX + "gap"]
    assert gap_seqs == [5, 6, 7, 8]
    assert all(not (_walk_keys(json.loads(event_json)) & FORBIDDEN_KEYS) for _, _, event_json in events)
    database_bytes = database.read_bytes()
    for sentinel in (b"SENTINEL_QUERY", b"SENTINEL_ANSWER", b"SENTINEL_TOKEN", b"SENTINEL_DOCUMENT"):
        assert sentinel not in database_bytes



def test_retry_degraded_fixture_has_parallel_retry_fallback_and_verification_facts(
    tmp_path: Path,
) -> None:
    database = tmp_path / "qa-run-history.sqlite3"
    result = _run("--sqlite-path", str(database))
    assert result.returncode == 0, result.stderr

    with sqlite3.connect(database) as connection:
        run = connection.execute(
            """SELECT last_seq,event_count,degraded_count,retry_count,failed_node_ids_json
            FROM runs WHERE run_id=?""",
            (PREFIX + "retry-degraded",),
        ).fetchone()
        rows = connection.execute(
            """SELECT seq,event_type,node_id,attempt,elapsed_ms,duration_ms,event_json
            FROM run_events WHERE run_id=? ORDER BY seq""",
            (PREFIX + "retry-degraded",),
        ).fetchall()

    assert run == (22, 22, 1, 1, '["graph.retrieve"]')
    assert [row[0] for row in rows] == list(range(1, 23))
    events = [json.loads(row[6]) for row in rows]
    assert [(event["type"], event["node_id"], event["attempt"]) for event in events[1:7]] == [
        ("node.started", "retrieve", 1),
        ("node.started", "graph.retrieve", 1),
        ("node.failed", "retrieve", 1),
        ("retry.started", "retrieve", 2),
        ("node.failed", "graph.retrieve", 1),
        ("degraded", "graph.retrieve", 1),
    ]
    assert events[1]["elapsed_ms"] < events[2]["elapsed_ms"] < events[3]["elapsed_ms"]
    fallback = events[6]["attributes"]
    assert fallback == {
        "effective_route": "hybrid",
        "fallback_route": "vector",
        "reason": "graph_retrieval_failed",
    }
    assert any(event["node_id"] == "retrieve" and event["attempt"] == 2 and event["type"] == "node.completed" for event in events)
    assert any(event["node_id"] == "rerank" and event["type"] == "node.completed" for event in events)
    assert any(event["node_id"] == "sentence_window" and event["type"] == "node.completed" for event in events)
    for layer in ("verify.l1", "verify.l2", "verify.l3"):
        layer_events = [event for event in events if event["node_id"] == layer]
        assert [event["type"] for event in layer_events] == ["node.started", "node.completed"]
        completed = layer_events[-1]
        assert completed["duration_ms"] is not None
        assert completed["attributes"].get("reason")
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["elapsed_ms"] == 10_000.0
    graph_failed = next(event for event in events if event["node_id"] == "graph.retrieve" and event["type"] == "node.failed")
    retry_second = next(event for event in events if event["node_id"] == "retrieve" and event["attempt"] == 2 and event["type"] == "node.started")
    assert retry_second["elapsed_ms"] - graph_failed["elapsed_ms"] == 500.0
    readable_durations = [
        event["duration_ms"]
        for event in events
        if event["type"] == "node.completed" and event["node_id"] in {
            "retrieve", "rerank", "sentence_window", "verify.l1", "verify.l2", "verify.l3"
        }
    ] + [graph_failed["duration_ms"]]
    assert all(800.0 <= duration <= 2500.0 for duration in readable_durations)


def test_adopt_worker_reassigns_only_active_and_stuck_to_existing_alive_worker(
    tmp_path: Path,
) -> None:
    database = tmp_path / "qa-run-history.sqlite3"
    first = _run("--sqlite-path", str(database))
    assert first.returncode == 0, first.stderr
    now_us = int(datetime.now(timezone.utc).timestamp() * 1_000_000)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO boots (boot_id,started_at_us,last_heartbeat_at_us,stopped_at_us,state) "
            "VALUES (?,?,?,?,?)",
            ("live-boot", now_us, now_us, None, "alive"),
        )
        connection.execute(
            "INSERT INTO workers (worker_id,boot_id,started_at_us,last_heartbeat_at_us,stopped_at_us,state) "
            "VALUES (?,?,?,?,?,?)",
            ("live-worker", "live-boot", now_us, now_us, None, "alive"),
        )

    adopted = _run(
        "--sqlite-path",
        str(database),
        "--adopt-worker-id",
        "live-worker",
        "--allow-live-qa-worker-adoption",
    )

    assert adopted.returncode == 0, adopted.stderr
    assert json.loads(adopted.stdout)["adopted_worker_id"] == "live-worker"
    with sqlite3.connect(database) as connection:
        assignments = dict(
            connection.execute(
                "SELECT run_id,worker_id FROM runs WHERE run_id IN (?,?)",
                (PREFIX + "active", PREFIX + "stuck"),
            ).fetchall()
        )
        worker = connection.execute(
            "SELECT boot_id,state,stopped_at_us FROM workers WHERE worker_id='live-worker'"
        ).fetchone()
    assert assignments == {
        PREFIX + "active": "live-worker",
        PREFIX + "stuck": "live-worker",
    }
    assert worker == ("live-boot", "alive", None)


def test_default_run_ops_api_scope_lists_seeded_runs(tmp_path: Path) -> None:
    import asyncio
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from config.settings import get_settings, resolve_tenant
    from core.run_history_store import RunHistoryStore
    from core.run_registry import RegistryIdentity, RunRegistry, derive_tenant_scope
    from server.run_ops import RunListFilters, RunOpsService, create_run_ops_router

    database = tmp_path / "qa-run-history.sqlite3"
    seeded = _run("--sqlite-path", str(database))
    assert seeded.returncode == 0, seeded.stderr

    settings = get_settings()
    history_settings = settings.run_history.model_copy(update={"sqlite_path": str(database)})
    store = RunHistoryStore.open(
        history_settings,
        "api-boot",
        "api-worker",
        now=lambda: datetime.now(timezone.utc),
    )
    identity = RegistryIdentity(
        "api-boot",
        "api-worker",
        store.scope_key,
        store.cursor_key,
        "installation",
    )
    registry = RunRegistry(history_settings, identity)
    service = RunOpsService(
        registry,
        store,
        identity,
        settings,
        lambda: datetime.now(timezone.utc),
    )
    default_scope = derive_tenant_scope(resolve_tenant(None, settings), store.scope_key)
    try:
        direct = asyncio.run(service.list_runs(default_scope, RunListFilters(limit=50)))
        app = FastAPI()
        app.state.run_ops_runtime = SimpleNamespace(service=service)
        app.include_router(create_run_ops_router())
        client = TestClient(app, client=("127.0.0.1", 50000))
        response = client.get("/api/runs?limit=50")
        rich_detail = client.get("/api/runs/" + PREFIX + "retry-degraded")
        rich_events = client.get("/api/runs/" + PREFIX + "retry-degraded/events?limit=200")
    finally:
        store.close(2000)

    expected_ids = {PREFIX + name for name in EXPECTED}
    assert {item.run_id for item in direct.items} == expected_ids
    assert response.status_code == 200
    assert {item["run_id"] for item in response.json()["items"]} == expected_ids
    assert rich_detail.status_code == 200
    rollups = rich_detail.json()["node_rollup"]
    assert {(item["node_id"], item["attempt"], item["status"]) for item in rollups} >= {
        ("retrieve", 1, "failed"),
        ("retrieve", 2, "completed"),
        ("graph.retrieve", 1, "failed"),
    }
    graph_rollup = next(item for item in rollups if item["node_id"] == "graph.retrieve")
    assert graph_rollup["degraded_reason"] == "graph_retrieval_failed"
    assert rich_events.status_code == 200
    assert len(rich_events.json()["events"]) == 22



def test_rejects_the_resolved_production_default_database_path() -> None:
    import importlib

    module = importlib.import_module("scripts.seed_run_ops_qa")
    production = (SCRIPT.parents[1] / "data" / "run-history.sqlite3").resolve()

    with pytest.raises(RuntimeError, match="production default"):
        module.validate_qa_database_path(production)


def test_rejects_existing_database_with_any_non_qa_run(tmp_path: Path) -> None:
    database = tmp_path / "qa-run-history.sqlite3"
    first = _run("--sqlite-path", str(database))
    assert first.returncode == 0, first.stderr
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM run_events WHERE run_id=?",
            (PREFIX + "completed",),
        )
        connection.execute(
            "UPDATE runs SET run_id='real-run' WHERE run_id=?",
            (PREFIX + "completed",),
        )

    rejected = _run("--sqlite-path", str(database))

    assert rejected.returncode == 2
    assert "non-QA run" in rejected.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs WHERE run_id='real-run'").fetchone()[0] == 1


def test_live_worker_adoption_requires_explicit_ack(tmp_path: Path) -> None:
    database = tmp_path / "qa-run-history.sqlite3"
    first = _run("--sqlite-path", str(database))
    assert first.returncode == 0, first.stderr

    rejected = _run(
        "--sqlite-path",
        str(database),
        "--adopt-worker-id",
        PREFIX + "worker",
    )

    assert rejected.returncode == 2
    assert "--allow-live-qa-worker-adoption" in rejected.stderr

def test_explicit_tenant_id_derives_one_opaque_scope_without_echoing_raw_tenant(
    tmp_path: Path,
) -> None:
    from core.run_registry import derive_tenant_scope

    database = tmp_path / "qa-run-history.sqlite3"
    result = _run(
        "--sqlite-path",
        str(database),
        "--tenant-id",
        "visual-qa-tenant",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert "visual-qa-tenant" not in result.stdout
    with sqlite3.connect(database) as connection:
        scope_key = bytes(
            connection.execute(
                "SELECT value FROM registry_meta WHERE key='scope_key'"
            ).fetchone()[0]
        )
        scopes = connection.execute(
            "SELECT DISTINCT tenant_scope FROM runs WHERE run_id LIKE ?",
            (PREFIX + "%",),
        ).fetchall()
    expected_scope = derive_tenant_scope("visual-qa-tenant", scope_key)
    assert scopes == [(expected_scope,)]
    assert report["scope_prefix"] == expected_scope[:8]
