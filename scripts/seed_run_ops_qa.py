"""Seed synthetic, privacy-safe Run Ops fixtures into an explicit QA SQLite file.

Development/visual-QA utility only. The SQLite path is required; no production path is
selected implicitly. Re-running replaces only deterministic qa-route-b fixture runs.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from config.settings import RunHistorySettings, get_settings, resolve_tenant
from core.run_history_store import RunHistoryStore
from core.run_registry import derive_tenant_scope


PREFIX = "qa-route-b-"
SYNTHETIC_BOOT_ID = PREFIX + "boot"
SYNTHETIC_WORKER_ID = PREFIX + "worker"
WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_DEFAULT = (WORKSPACE_ROOT / "data" / "run-history.sqlite3").resolve()
FORBIDDEN_KEYS = frozenset(
    {
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
)
SENTINELS = (
    b"SENTINEL_QUERY",
    b"SENTINEL_ANSWER",
    b"SENTINEL_TOKEN",
    b"SENTINEL_DOCUMENT",
)
TOPOLOGY = {
    "id": "rag.query",
    "revision": "qa-route-b-v2",
    "executor": "sequential_stream",
    "nodes": [
        {"id": "receive", "label": "接收请求", "group": "input", "description": "接收安全的合成运行信号。", "optional": False, "repeatable": False, "available": True, "plugin": None, "attributes": {}},
        {"id": "retrieve", "label": "检索阶段", "group": "retrieve", "description": "主检索路径。", "optional": False, "repeatable": True, "available": True, "plugin": None, "attributes": {}},
        {"id": "graph.retrieve", "label": "图检索", "group": "retrieve", "description": "并行图检索与安全降级。", "optional": True, "repeatable": True, "available": True, "plugin": None, "attributes": {}},
        {"id": "rerank", "label": "重排序", "group": "retrieve", "description": "候选计数重排序。", "optional": True, "repeatable": False, "available": True, "plugin": None, "attributes": {}},
        {"id": "sentence_window", "label": "句子窗口", "group": "retrieve", "description": "安全计数窗口扩展。", "optional": True, "repeatable": False, "available": True, "plugin": None, "attributes": {}},
        {"id": "verify.l1", "label": "L1 引用存在性", "group": "verify", "description": "安全计数验证。", "optional": False, "repeatable": False, "available": True, "plugin": None, "attributes": {}},
        {"id": "verify.l2", "label": "L2 哈希一致性", "group": "verify", "description": "安全状态验证。", "optional": False, "repeatable": False, "available": True, "plugin": None, "attributes": {}},
        {"id": "verify.l3", "label": "L3 蕴含验证", "group": "verify", "description": "安全聚合验证。", "optional": False, "repeatable": False, "available": True, "plugin": None, "attributes": {}},
        {"id": "generate", "label": "生成阶段", "group": "generate", "description": "仅展示安全的生命周期状态。", "optional": False, "repeatable": True, "available": True, "plugin": None, "attributes": {}},
    ],
    "edges": [
        {"id": "receive.retrieve", "source": "receive", "target": "retrieve", "kind": "dependency", "label": None},
        {"id": "receive.graph", "source": "receive", "target": "graph.retrieve", "kind": "dependency", "label": "并行"},
        {"id": "retrieve.rerank", "source": "retrieve", "target": "rerank", "kind": "dependency", "label": None},
        {"id": "graph.rerank", "source": "graph.retrieve", "target": "rerank", "kind": "conditional", "label": "可降级"},
        {"id": "rerank.window", "source": "rerank", "target": "sentence_window", "kind": "dependency", "label": None},
        {"id": "window.l1", "source": "sentence_window", "target": "verify.l1", "kind": "dependency", "label": None},
        {"id": "l1.l2", "source": "verify.l1", "target": "verify.l2", "kind": "dependency", "label": None},
        {"id": "l2.l3", "source": "verify.l2", "target": "verify.l3", "kind": "dependency", "label": None},
        {"id": "l3.generate", "source": "verify.l3", "target": "generate", "kind": "dependency", "label": None},
    ],
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_us(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000)


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _event(
    run_id: str,
    seq: int,
    event_type: str,
    started_at: datetime,
    elapsed_ms: float,
    *,
    node_id: str | None = None,
    attempt: int | None = None,
    duration_ms: float | None = None,
    attributes: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "seq": seq,
        "occurred_at": _rfc3339(started_at + timedelta(milliseconds=elapsed_ms)),
        "elapsed_ms": elapsed_ms,
        "topology_id": TOPOLOGY["id"],
        "topology_revision": TOPOLOGY["revision"],
        "type": event_type,
        "node_id": node_id,
        "attempt": attempt,
        "duration_ms": duration_ms,
        "attributes": attributes or {},
        "error": error,
    }


def _standard_events(
    name: str, started_at: datetime, terminal_type: str, *, outcome: str
) -> list[dict[str, Any]]:
    run_id = PREFIX + name
    node_type = {
        "run.completed": "node.completed",
        "run.failed": "node.failed",
        "run.cancelled": "node.cancelled",
    }[terminal_type]
    error = (
        {"type": "SyntheticFailure", "code": "qa_failure", "recoverable": False}
        if terminal_type == "run.failed"
        else None
    )
    return [
        _event(
            run_id,
            1,
            "run.started",
            started_at,
            0.0,
            attributes={"executor": "sequential_stream"},
        ),
        _event(
            run_id,
            2,
            node_type,
            started_at,
            900.0,
            node_id="generate",
            attempt=1,
            duration_ms=700.0,
            attributes={"output_count": 1 if terminal_type == "run.completed" else 0},
            error=error,
        ),
        _event(
            run_id,
            3,
            terminal_type,
            started_at,
            1200.0,
            attributes={"outcome": outcome},
            error=error,
        ),
    ]


def _events_1000(started_at: datetime) -> list[dict[str, Any]]:
    run_id = PREFIX + "events-1000"
    values = [
        _event(
            run_id,
            1,
            "run.started",
            started_at,
            0.0,
            attributes={"executor": "sequential_stream"},
        ),
        _event(
            run_id,
            2,
            "node.started",
            started_at,
            2.0,
            node_id="retrieve",
            attempt=1,
            attributes={"component": "qa_fixture"},
        ),
    ]
    for seq in range(3, 999):
        values.append(
            _event(
                run_id,
                seq,
                "degraded",
                started_at,
                float(seq),
                node_id="retrieve",
                attempt=1,
                attributes={"reason": "qa_density"},
            )
        )
    values.extend(
        [
            _event(
                run_id,
                999,
                "node.completed",
                started_at,
                999.0,
                node_id="retrieve",
                attempt=1,
                duration_ms=997.0,
                attributes={"output_count": 1000},
            ),
            _event(
                run_id,
                1000,
                "run.completed",
                started_at,
                1000.0,
                attributes={"outcome": "answered"},
            ),
        ]
    )
    return values


def _fixture_events(now: datetime) -> dict[str, list[dict[str, Any]]]:
    active_start = now - timedelta(seconds=8)
    stuck_start = now - timedelta(seconds=600)
    interrupted_start = now - timedelta(seconds=90)
    retry_start = now - timedelta(seconds=20)
    gap_start = now - timedelta(seconds=15)
    active = [
        _event(
            PREFIX + "active",
            1,
            "run.started",
            active_start,
            0.0,
            attributes={"executor": "sequential_stream"},
        ),
        _event(
            PREFIX + "active",
            2,
            "node.started",
            active_start,
            100.0,
            node_id="retrieve",
            attempt=1,
            attributes={"component": "qa_fixture"},
        ),
    ]
    stuck = [
        _event(
            PREFIX + "stuck",
            1,
            "run.started",
            stuck_start,
            0.0,
            attributes={"executor": "sequential_stream"},
        ),
        _event(
            PREFIX + "stuck",
            2,
            "node.started",
            stuck_start,
            50.0,
            node_id="retrieve",
            attempt=1,
            attributes={"component": "qa_fixture"},
        ),
    ]
    interrupted = [
        _event(
            PREFIX + "interrupted",
            1,
            "run.started",
            interrupted_start,
            0.0,
            attributes={"executor": "sequential_stream"},
        ),
        _event(
            PREFIX + "interrupted",
            2,
            "node.started",
            interrupted_start,
            200.0,
            node_id="generate",
            attempt=1,
            attributes={"component": "qa_fixture"},
        ),
    ]
    retry = [
        _event(PREFIX + "retry-degraded", 1, "run.started", retry_start, 0.0, attributes={"executor": "sequential_stream"}),
        _event(PREFIX + "retry-degraded", 2, "node.started", retry_start, 500.0, node_id="retrieve", attempt=1, attributes={"component": "qa_fixture"}),
        _event(PREFIX + "retry-degraded", 3, "node.started", retry_start, 800.0, node_id="graph.retrieve", attempt=1, attributes={"component": "qa_fixture"}),
        _event(PREFIX + "retry-degraded", 4, "node.failed", retry_start, 2500.0, node_id="retrieve", attempt=1, duration_ms=2000.0, attributes={"reason": "retrieval_timeout"}, error={"type": "SyntheticFailure", "code": "retrieval_timeout", "recoverable": True}),
        _event(PREFIX + "retry-degraded", 5, "retry.started", retry_start, 2600.0, node_id="retrieve", attempt=2, attributes={"reason": "retrieval_timeout", "target_attempt": 2}),
        _event(PREFIX + "retry-degraded", 6, "node.failed", retry_start, 3000.0, node_id="graph.retrieve", attempt=1, duration_ms=2200.0, attributes={"reason": "graph_retrieval_failed"}, error={"type": "SyntheticFailure", "code": "graph_retrieval_failed", "recoverable": True}),
        _event(PREFIX + "retry-degraded", 7, "degraded", retry_start, 3010.0, node_id="graph.retrieve", attempt=1, attributes={"reason": "graph_retrieval_failed", "effective_route": "hybrid", "fallback_route": "vector"}),
        _event(PREFIX + "retry-degraded", 8, "node.started", retry_start, 3500.0, node_id="retrieve", attempt=2, attributes={"component": "qa_fixture"}),
        _event(PREFIX + "retry-degraded", 9, "node.completed", retry_start, 5200.0, node_id="retrieve", attempt=2, duration_ms=1700.0, attributes={"candidate_count": 12, "chunk_count": 8, "source_count": 4}),
        _event(PREFIX + "retry-degraded", 10, "retry.completed", retry_start, 5250.0, node_id="retrieve", attempt=2, duration_ms=2650.0, attributes={"reason": "retrieval_recovered"}),
        _event(PREFIX + "retry-degraded", 11, "node.started", retry_start, 5300.0, node_id="rerank", attempt=1, attributes={"input_count": 8}),
        _event(PREFIX + "retry-degraded", 12, "node.completed", retry_start, 6300.0, node_id="rerank", attempt=1, duration_ms=1000.0, attributes={"input_count": 8, "output_count": 5}),
        _event(PREFIX + "retry-degraded", 13, "node.started", retry_start, 6400.0, node_id="sentence_window", attempt=1, attributes={"input_count": 5}),
        _event(PREFIX + "retry-degraded", 14, "node.completed", retry_start, 7200.0, node_id="sentence_window", attempt=1, duration_ms=800.0, attributes={"input_count": 5, "output_count": 5}),
        _event(PREFIX + "retry-degraded", 15, "node.started", retry_start, 7300.0, node_id="verify.l1", attempt=1, attributes={"component": "qa_fixture"}),
        _event(PREFIX + "retry-degraded", 16, "node.completed", retry_start, 8100.0, node_id="verify.l1", attempt=1, duration_ms=800.0, attributes={"citation_count": 5, "valid_count": 5, "reason": "citations_present"}),
        _event(PREFIX + "retry-degraded", 17, "node.started", retry_start, 8150.0, node_id="verify.l2", attempt=1, attributes={"component": "qa_fixture"}),
        _event(PREFIX + "retry-degraded", 18, "node.completed", retry_start, 8950.0, node_id="verify.l2", attempt=1, duration_ms=800.0, attributes={"valid_count": 4, "reason": "hash_match"}),
        _event(PREFIX + "retry-degraded", 19, "node.started", retry_start, 9000.0, node_id="verify.l3", attempt=1, attributes={"component": "qa_fixture"}),
        _event(PREFIX + "retry-degraded", 20, "node.completed", retry_start, 9800.0, node_id="verify.l3", attempt=1, duration_ms=800.0, attributes={"successful_count": 3, "supported": True, "reason": "entailed"}),
        _event(PREFIX + "retry-degraded", 21, "route.selected", retry_start, 9850.0, attributes={"route": "vector_graph_rag", "effective_route": "hybrid", "fallback_route": "vector", "reason": "graph_retrieval_failed"}),
        _event(PREFIX + "retry-degraded", 22, "run.completed", retry_start, 10000.0, attributes={"outcome": "answered"}),
    ]
    gap = [
        _event(
            PREFIX + "gap",
            5,
            "node.started",
            gap_start,
            500.0,
            node_id="retrieve",
            attempt=1,
            attributes={"component": "qa_fixture"},
        ),
        _event(
            PREFIX + "gap",
            6,
            "degraded",
            gap_start,
            700.0,
            node_id="retrieve",
            attempt=1,
            attributes={"reason": "retention_gap"},
        ),
        _event(
            PREFIX + "gap",
            7,
            "node.completed",
            gap_start,
            1000.0,
            node_id="retrieve",
            attempt=1,
            duration_ms=500.0,
            attributes={"output_count": 4},
        ),
        _event(
            PREFIX + "gap",
            8,
            "run.completed",
            gap_start,
            1200.0,
            attributes={"outcome": "answered"},
        ),
    ]
    return {
        "active": active,
        "completed": _standard_events(
            "completed", now - timedelta(seconds=40), "run.completed", outcome="answered"
        ),
        "failed": _standard_events(
            "failed", now - timedelta(seconds=35), "run.failed", outcome="unknown"
        ),
        "cancelled": _standard_events(
            "cancelled", now - timedelta(seconds=30), "run.cancelled", outcome="unknown"
        ),
        "interrupted": interrupted,
        "stuck": stuck,
        "slow": _standard_events(
            "slow", now - timedelta(seconds=70), "run.completed", outcome="answered"
        ),
        "retry-degraded": retry,
        "gap": gap,
        "events-1000": _events_1000(now - timedelta(seconds=25)),
    }


def validate_qa_database_path(database: Path) -> Path:
    resolved = database.expanduser().resolve()
    if resolved == PRODUCTION_DEFAULT:
        raise RuntimeError("refusing production default data/run-history.sqlite3")
    parts = tuple(part.casefold() for part in resolved.parts)
    qa_filename = resolved.name.casefold() == "qa-run-history.sqlite3"
    isolated_marker = ".testdata" in parts and any("qa" in part for part in parts)
    if not qa_filename and not isolated_marker:
        raise RuntimeError(
            "QA database must be named qa-run-history.sqlite3 or use a .testdata QA marker"
        )
    return resolved


def _reject_non_qa_runs(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT run_id FROM runs WHERE run_id NOT LIKE ? LIMIT 1",
            (PREFIX + "%",),
        ).fetchone()
    if row is not None:
        raise RuntimeError(f"refusing database containing non-QA run: {row[0]}")


def _ensure_schema(database: Path) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    if database.exists():
        with sqlite3.connect(database) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        required = {
            "schema_migrations",
            "registry_meta",
            "boots",
            "workers",
            "runs",
            "run_events",
        }
        if not required.issubset(tables):
            missing = ", ".join(sorted(required - tables))
            raise RuntimeError(f"SQLite file is not a RunHistory schema; missing: {missing}")
        return
    settings = RunHistorySettings(sqlite_path=str(database))
    store = RunHistoryStore.open(
        settings,
        boot_id=PREFIX + "init-boot-" + uuid.uuid4().hex,
        worker_id=PREFIX + "init-worker-" + uuid.uuid4().hex,
        now=_utcnow,
    )
    try:
        health = store.health_snapshot()
        if health.state not in {"ready", "read_only"}:
            raise RuntimeError(f"cannot initialize RunHistory schema: {health.reason}")
    finally:
        store.close(2000)


def _upsert_synthetic_identity(connection: sqlite3.Connection, now_us: int) -> None:
    connection.execute(
        """INSERT INTO boots
        (boot_id,started_at_us,last_heartbeat_at_us,stopped_at_us,state)
        VALUES (?,?,?,?,?)
        ON CONFLICT(boot_id) DO UPDATE SET
        last_heartbeat_at_us=excluded.last_heartbeat_at_us,stopped_at_us=NULL,state='alive'""",
        (SYNTHETIC_BOOT_ID, now_us, now_us, None, "alive"),
    )
    connection.execute(
        """INSERT INTO workers
        (worker_id,boot_id,started_at_us,last_heartbeat_at_us,stopped_at_us,state)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(worker_id) DO UPDATE SET
        boot_id=excluded.boot_id,last_heartbeat_at_us=excluded.last_heartbeat_at_us,
        stopped_at_us=NULL,state='alive'""",
        (SYNTHETIC_WORKER_ID, SYNTHETIC_BOOT_ID, now_us, now_us, None, "alive"),
    )


def _resolve_adopted_worker(
    connection: sqlite3.Connection, worker_id: str, now_us: int
) -> tuple[str, str]:
    row = connection.execute(
        """SELECT worker_id,boot_id,last_heartbeat_at_us,stopped_at_us,state
        FROM workers WHERE worker_id=?""",
        (worker_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"adopt worker not found: {worker_id}")
    selected_worker, boot_id, heartbeat_us, stopped_at_us, state = row
    if state != "alive" or stopped_at_us is not None:
        raise RuntimeError(f"adopt worker is not alive: {worker_id}")
    stale_after_us = RunHistorySettings().worker_stale_after_s * 1_000_000
    if now_us - int(heartbeat_us) > stale_after_us:
        raise RuntimeError(f"adopt worker heartbeat is stale: {worker_id}")
    return str(selected_worker), str(boot_id)


def _run_values(
    name: str,
    tenant_scope: str,
    events: list[dict[str, Any]],
    now: datetime,
    worker_id: str,
    boot_id: str,
) -> tuple[Any, ...]:
    statuses = {
        "active": ("running", "unknown"),
        "completed": ("completed", "answered"),
        "failed": ("failed", "unknown"),
        "cancelled": ("cancelled", "unknown"),
        "interrupted": ("interrupted", "unknown"),
        "stuck": ("running", "unknown"),
        "slow": ("completed", "answered"),
        "retry-degraded": ("completed", "answered"),
        "gap": ("completed", "answered"),
        "events-1000": ("completed", "answered"),
    }
    status, outcome = statuses[name]
    started_at = datetime.fromisoformat(events[0]["occurred_at"].replace("Z", "+00:00"))
    if name == "stuck":
        started_at = now - timedelta(seconds=600)
    elapsed_ms = {
        "active": 8000.0,
        "stuck": 600000.0,
        "slow": 45000.0,
        "interrupted": 90000.0,
    }.get(name, float(events[-1]["elapsed_ms"]))
    finished_at_us = (
        None
        if status == "running"
        else _to_us(started_at + timedelta(milliseconds=elapsed_ms))
    )
    current_nodes = ["retrieve"] if status == "running" else []
    failed_nodes = (
        ["generate"]
        if status == "failed"
        else ["graph.retrieve"]
        if name == "retry-degraded"
        else []
    )
    return (
        PREFIX + name,
        tenant_scope,
        boot_id,
        worker_id,
        status,
        outcome,
        _to_us(started_at),
        _to_us(now),
        finished_at_us,
        elapsed_ms,
        TOPOLOGY["id"],
        TOPOLOGY["revision"],
        TOPOLOGY["executor"],
        json.dumps(TOPOLOGY, ensure_ascii=False, separators=(",", ":")),
        max(event["seq"] for event in events),
        len(events),
        5 if name == "gap" else 1,
        json.dumps(current_nodes, separators=(",", ":")),
        json.dumps(failed_nodes, separators=(",", ":")),
        "hybrid",
        996 if name == "events-1000" else 1 if name in {"retry-degraded", "gap"} else 0,
        1 if name == "retry-degraded" else 0,
        "partial" if name == "gap" else "complete",
        "worker_stale" if name == "interrupted" else None,
        None,
    )


def _derive_fixture_scope(
    connection: sqlite3.Connection, tenant_id: str | None
) -> str:
    row = connection.execute(
        "SELECT value FROM registry_meta WHERE key='scope_key'"
    ).fetchone()
    if row is None:
        raise RuntimeError("RunHistory registry_meta.scope_key is missing")
    scope_key = bytes(row[0])
    if len(scope_key) != 32:
        raise RuntimeError("RunHistory registry_meta.scope_key must be 32 bytes")
    normalized_tenant = resolve_tenant(tenant_id, get_settings())
    return derive_tenant_scope(normalized_tenant, scope_key)


def _insert_fixtures(
    database: Path, *, adopt_worker_id: str | None, tenant_id: str | None, now: datetime
) -> str:
    fixtures = _fixture_events(now)
    now_us = _to_us(now)
    run_sql = """INSERT INTO runs (
        run_id,tenant_scope,boot_id,worker_id,status,outcome,started_at_us,updated_at_us,
        finished_at_us,elapsed_ms,topology_id,topology_revision,executor,topology_json,
        last_seq,event_count,earliest_available_seq,current_node_ids_json,failed_node_ids_json,
        route,degraded_count,retry_count,event_integrity,interruption_reason,query_fingerprint
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    event_sql = """INSERT INTO run_events
        (run_id,seq,event_type,node_id,attempt,occurred_at_us,elapsed_ms,duration_ms,event_json)
        VALUES (?,?,?,?,?,?,?,?,?)"""
    with sqlite3.connect(database, timeout=5.0) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("BEGIN IMMEDIATE")
        _upsert_synthetic_identity(connection, now_us)
        tenant_scope = _derive_fixture_scope(connection, tenant_id)
        adopted = (
            _resolve_adopted_worker(connection, adopt_worker_id, now_us)
            if adopt_worker_id is not None
            else None
        )
        connection.execute("DELETE FROM runs WHERE run_id LIKE ?", (PREFIX + "%",))
        for name, events in fixtures.items():
            worker_id, boot_id = (
                adopted
                if adopted is not None and name in {"active", "stuck"}
                else (SYNTHETIC_WORKER_ID, SYNTHETIC_BOOT_ID)
            )
            connection.execute(
                run_sql,
                _run_values(name, tenant_scope, events, now, worker_id, boot_id),
            )
            for event in events:
                occurred_at = datetime.fromisoformat(
                    event["occurred_at"].replace("Z", "+00:00")
                )
                connection.execute(
                    event_sql,
                    (
                        PREFIX + name,
                        event["seq"],
                        event["type"],
                        event["node_id"],
                        event["attempt"],
                        _to_us(occurred_at),
                        event["elapsed_ms"],
                        event["duration_ms"],
                        json.dumps(event, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
        connection.commit()
    return tenant_scope


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key).casefold()
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def check_seeded_fixtures(
    database: Path, *, adopted_worker_id: str | None, expected_scope: str
) -> dict[str, Any]:
    with sqlite3.connect(database) as connection:
        runs = connection.execute(
            """SELECT run_id,status,worker_id,earliest_available_seq,query_fingerprint,
            tenant_scope,last_seq,event_count FROM runs WHERE run_id LIKE ? ORDER BY run_id""",
            (PREFIX + "%",),
        ).fetchall()
        events = connection.execute(
            """SELECT run_id,seq,event_json FROM run_events
            WHERE run_id LIKE ? ORDER BY run_id,seq""",
            (PREFIX + "%",),
        ).fetchall()
    if len(runs) != 10:
        raise RuntimeError(f"expected 10 QA runs, found {len(runs)}")
    if any(row[4] is not None for row in runs):
        raise RuntimeError("QA run unexpectedly contains a query fingerprint")
    scopes = {str(row[5]) for row in runs}
    if scopes != {expected_scope}:
        raise RuntimeError("QA runs do not share the derived tenant scope")
    events_by_run: dict[str, list[int]] = {}
    for run_id, seq, _ in events:
        events_by_run.setdefault(str(run_id), []).append(int(seq))
    for row in runs:
        seqs = events_by_run.get(str(row[0]), [])
        actual_last_seq = max(seqs, default=0)
        if int(row[6]) != actual_last_seq or int(row[7]) != len(seqs):
            raise RuntimeError(f"QA run summary/event mismatch: {row[0]}")
    event_1000_count = sum(row[0] == PREFIX + "events-1000" for row in events)
    if event_1000_count != 1000:
        raise RuntimeError(f"events-1000 contains {event_1000_count} events")
    gap = [row for row in runs if row[0] == PREFIX + "gap"]
    if len(gap) != 1 or gap[0][3] != 5:
        raise RuntimeError("gap fixture does not start at earliest_available_seq=5")
    gap_seqs = [row[1] for row in events if row[0] == PREFIX + "gap"]
    if gap_seqs != [5, 6, 7, 8]:
        raise RuntimeError(f"gap fixture has unexpected seqs: {gap_seqs}")
    for _, _, event_json in events:
        payload = json.loads(event_json)
        found = FORBIDDEN_KEYS.intersection(_walk_keys(payload))
        if found:
            raise RuntimeError("forbidden QA event keys: " + ", ".join(sorted(found)))
    database_bytes = database.read_bytes()
    if any(sentinel in database_bytes for sentinel in SENTINELS):
        raise RuntimeError("sensitive sentinel found in QA database bytes")
    if adopted_worker_id is not None:
        assignments = {
            row[0]: row[2]
            for row in runs
            if row[0] in {PREFIX + "active", PREFIX + "stuck"}
        }
        if set(assignments.values()) != {adopted_worker_id}:
            raise RuntimeError("active/stuck fixtures did not adopt the requested worker")
    counts = Counter(row[1] for row in runs)
    return {
        "adopted_worker_id": adopted_worker_id,
        "database": str(database.resolve()),
        "event_count": len(events),
        "gap_earliest_available_seq": 5,
        "privacy_check": "clean",
        "run_count": len(runs),
        "scope_prefix": expected_scope[:8],
        "status_counts": dict(sorted(counts.items())),
    }


def seed_run_ops_qa(
    database: Path,
    *,
    adopt_worker_id: str | None = None,
    tenant_id: str | None = None,
    allow_live_qa_worker_adoption: bool = False,
) -> dict[str, Any]:
    database = validate_qa_database_path(database)
    if adopt_worker_id is not None and not allow_live_qa_worker_adoption:
        raise RuntimeError(
            "--adopt-worker-id requires --allow-live-qa-worker-adoption"
        )
    _ensure_schema(database)
    _reject_non_qa_runs(database)
    tenant_scope = _insert_fixtures(
        database,
        adopt_worker_id=adopt_worker_id,
        tenant_id=tenant_id,
        now=_utcnow(),
    )
    return check_seeded_fixtures(
        database,
        adopted_worker_id=adopt_worker_id,
        expected_scope=tenant_scope,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed development-only, privacy-safe Run Ops visual QA fixtures."
    )
    parser.add_argument(
        "--sqlite-path",
        type=Path,
        required=True,
        help="Explicit RunHistory SQLite path; no default production path is used.",
    )
    parser.add_argument(
        "--adopt-worker-id",
        help="Assign active/stuck fixtures to an existing alive worker with a fresh heartbeat.",
    )
    parser.add_argument(
        "--tenant-id",
        help="Tenant to derive through the database scope key; omitted uses configured default.",
    )
    parser.add_argument(
        "--allow-live-qa-worker-adoption",
        action="store_true",
        help="Explicitly acknowledge assigning active/stuck fixtures to a live QA worker.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        report = seed_run_ops_qa(
            args.sqlite_path,
            adopt_worker_id=args.adopt_worker_id,
            tenant_id=args.tenant_id,
            allow_live_qa_worker_adoption=args.allow_live_qa_worker_adoption,
        )
    except Exception as exc:
        parser.exit(2, f"seed_run_ops_qa: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
