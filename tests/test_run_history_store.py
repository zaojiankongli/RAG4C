from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from config.settings import RunHistorySettings
from core.run_history_store import (
    LOCK_BACKOFF_SECONDS,
    PersistenceMutation,
    RunHistoryReadError,
    RunHistoryStore,
)
from core.run_registry import RunListQuery, RunSummary


NOW = datetime(2026, 8, 23, 8, 0, tzinfo=timezone.utc)


def _settings(tmp_path: Path, **overrides: Any) -> RunHistorySettings:
    values: dict[str, Any] = {
        "sqlite_path": str(tmp_path / "run-history.sqlite3"),
        "writer_queue_capacity": 32,
        "writer_batch_size": 8,
        "writer_flush_ms": 20,
        "writer_shutdown_grace_ms": 2000,
    }
    values.update(overrides)
    return RunHistorySettings(**values)


def _summary(
    run_id: str = "run-a",
    *,
    status: str = "running",
    seq: int = 1,
    started_at: datetime = NOW,
    event_integrity: str = "complete",
    fingerprint: str | None = None,
) -> RunSummary:
    finished_at = None if status == "running" else started_at + timedelta(seconds=1)
    return RunSummary(
        schema_version=1,
        run_id=run_id,
        status=status,  # type: ignore[arg-type]
        outcome="answered" if status == "completed" else "unknown",
        started_at=started_at,
        updated_at=started_at + timedelta(milliseconds=seq * 10),
        finished_at=finished_at,
        elapsed_ms=float(seq * 10),
        boot_id="boot-a",
        worker_id="worker-a",
        topology_id="rag.query",
        topology_revision="rev-a",
        executor="sequential_stream",
        last_seq=seq,
        event_count=seq,
        earliest_available_seq=1,
        current_node_ids=("retrieval",) if status == "running" else (),
        failed_node_ids=(),
        route="hybrid",
        degraded_count=0,
        retry_count=0,
        attention=(),
        event_integrity=event_integrity,  # type: ignore[arg-type]
        persistence_status="pending",
        interruption_reason=None,
        query_fingerprint=fingerprint,
    )


def _mutation(
    run_id: str = "run-a",
    *,
    scope: str = "scope-a",
    seq: int = 1,
    status: str = "running",
    outcome: str = "unknown",
    event_type: str | None = None,
    started_at: datetime = NOW,
    event_integrity: str = "complete",
) -> PersistenceMutation:
    kind = event_type or ("run.started" if seq == 1 else "node.finished")
    event = {
        "schema_version": 1,
        "run_id": run_id,
        "seq": seq,
        "occurred_at": (started_at + timedelta(milliseconds=seq * 10))
        .isoformat()
        .replace("+00:00", "Z"),
        "elapsed_ms": float(seq * 10),
        "topology_id": "rag.query",
        "topology_revision": "rev-a",
        "type": kind,
        "node_id": None if kind.startswith("run.") else "retrieval",
        "attempt": None if kind.startswith("run.") else 1,
        "duration_ms": None if kind == "run.started" else 4.5,
        "attributes": {"outcome": outcome, "route": "hybrid"},
        "error": None,
    }
    topology = {
        "id": "rag.query",
        "revision": "rev-a",
        "executor": "sequential_stream",
        "nodes": [{"id": "retrieval", "label": "检索"}],
        "edges": [],
    }
    return PersistenceMutation.from_values(
        scope,
        _summary(
            run_id, status=status, seq=seq, started_at=started_at, event_integrity=event_integrity
        ),
        topology,
        event,
    )


@pytest.fixture
def store(tmp_path: Path):
    value = RunHistoryStore.open(_settings(tmp_path), "boot-a", "worker-a", now=lambda: NOW)
    try:
        yield value
    finally:
        value.close(2000)


def _write_complete_run(
    store: RunHistoryStore,
    run_id: str = "run-a",
    *,
    scope: str = "scope-a",
    started_at: datetime = NOW,
) -> None:
    assert store.enqueue(_mutation(run_id, scope=scope, seq=1, started_at=started_at))
    assert store.enqueue(
        _mutation(
            run_id,
            scope=scope,
            seq=2,
            status="completed",
            outcome="answered",
            event_type="run.completed",
            started_at=started_at,
        )
    )
    assert store.flush_for_test()


def test_schema_v1_is_independent_wal_and_has_exact_core_objects(tmp_path: Path) -> None:
    value = RunHistoryStore.open(_settings(tmp_path), "boot-a", "worker-a", now=lambda: NOW)
    try:
        with sqlite3.connect(value.database_path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
            assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
            names = {
                r[0]
                for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            indexes = {
                r[0]
                for r in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
            }
        assert value.database_path == tmp_path / "run-history.sqlite3"
        assert {"registry_meta", "boots", "workers", "runs", "run_events"} <= names
        assert {
            "idx_runs_scope_started",
            "idx_runs_scope_status_started",
            "idx_runs_worker_status",
            "idx_run_events_run_seq",
            "idx_workers_heartbeat",
        } <= indexes
    finally:
        value.close(2000)


def test_meta_keys_are_32_bytes_and_shared_by_workers(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = RunHistoryStore.open(settings, "boot-a", "worker-a", now=lambda: NOW)
    second = RunHistoryStore.open(settings, "boot-b", "worker-b", now=lambda: NOW)
    try:
        assert len(first.scope_key) == len(first.cursor_key) == 32
        assert first.scope_key == second.scope_key
        assert first.cursor_key == second.cursor_key
        assert first.scope_key != first.cursor_key
    finally:
        first.close(2000)
        second.close(2000)


def test_enqueue_is_nonblocking_and_capacity_counts_inflight_batch(tmp_path: Path) -> None:
    value = RunHistoryStore.open(
        _settings(tmp_path, writer_queue_capacity=1, writer_batch_size=1, writer_flush_ms=1),
        "boot-a",
        "worker-a",
        now=lambda: NOW,
    )
    lock = sqlite3.connect(value.database_path, timeout=0, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    try:
        start = time.perf_counter()
        assert value.enqueue(_mutation("run-capacity"))
        assert (
            value.enqueue(_mutation("run-overflow", started_at=NOW + timedelta(seconds=1))) is False
        )
        assert time.perf_counter() - start < 0.05
        assert value.health_snapshot().dropped_mutations == 1
    finally:
        lock.rollback()
        lock.close()
        value.close(2000)




def test_health_telemetry_tracks_actual_commit_lag_and_heartbeat(tmp_path: Path) -> None:
    clock = [NOW]
    value = RunHistoryStore.open(
        _settings(tmp_path, writer_batch_size=1, writer_flush_ms=1),
        "boot-telemetry",
        "worker-telemetry",
        now=lambda: clock[0],
    )
    try:
        initial = value.health_snapshot()
        assert initial.last_commit_at is None
        assert initial.commit_lag_ms is None
        assert initial.dropped_mutations == 0
        assert initial.last_heartbeat_at == NOW

        clock[0] = NOW + timedelta(seconds=5)
        assert value.enqueue(_mutation("run-telemetry", started_at=NOW))
        assert value.flush_for_test(timeout_s=3)
        committed = value.health_snapshot()
        assert committed.last_commit_at == clock[0]
        assert committed.commit_lag_ms == pytest.approx(4990.0)
        assert committed.dropped_mutations == 0

        heartbeat = NOW + timedelta(seconds=7)
        value.mark_heartbeat(heartbeat)
        assert value.health_snapshot().last_heartbeat_at == heartbeat
    finally:
        value.close(2000)


def test_enqueue_accounts_pending_before_item_becomes_visible(tmp_path: Path) -> None:
    value = RunHistoryStore(_settings(tmp_path), "boot-a", "worker-a", lambda: NOW)

    class ImmediateConsumer:
        def put_nowait(self, mutation: PersistenceMutation) -> None:
            value._mark_batch_durable([mutation])

    value._queue = ImmediateConsumer()  # type: ignore[assignment]

    assert value.enqueue(_mutation("run-immediate"))
    assert value.health_snapshot().writer_queue_depth == 0


def test_writer_batches_and_updates_durable_watermark(tmp_path: Path) -> None:
    value = RunHistoryStore.open(
        _settings(tmp_path, writer_batch_size=3, writer_flush_ms=100),
        "boot-a",
        "worker-a",
        now=lambda: NOW,
    )
    seen: list[int] = []
    original = value._write_batch_once

    def observe(connection: sqlite3.Connection, batch: list[PersistenceMutation]) -> None:
        seen.append(len(batch))
        original(connection, batch)

    value._write_batch_once = observe  # type: ignore[method-assign]
    try:
        for seq in range(1, 8):
            assert value.enqueue(_mutation("run-batch", seq=seq))
        assert value.flush_for_test()
        assert seen and max(seen) <= 3
        assert value.durable_watermark("scope-a", "run-batch") == 7
    finally:
        value.close(2000)


def test_locked_write_uses_approved_backoff_and_recovers(tmp_path: Path) -> None:
    assert LOCK_BACKOFF_SECONDS == (0.025, 0.05, 0.1, 0.2, 0.4)
    value = RunHistoryStore.open(
        _settings(tmp_path, writer_batch_size=1, writer_flush_ms=1),
        "boot-a",
        "worker-a",
        now=lambda: NOW,
    )
    lock = sqlite3.connect(value.database_path, timeout=0, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    try:
        assert value.enqueue(_mutation("run-locked"))
        time.sleep(0.15)
        lock.rollback()
        lock.close()
        assert value.flush_for_test(timeout_s=4)
        assert value.get_run("scope-a", "run-locked") is not None
    finally:
        try:
            lock.close()
        except Exception:
            pass
        value.close(2000)


def test_duplicate_events_are_idempotent_and_conflicts_preserve_first(
    store: RunHistoryStore,
) -> None:
    assert store.enqueue(_mutation("run-conflict", seq=1, outcome="first"))
    assert store.enqueue(_mutation("run-conflict", seq=1, outcome="first"))
    assert store.enqueue(_mutation("run-conflict", seq=1, outcome="second"))
    assert store.flush_for_test()
    page = store.get_events("scope-a", "run-conflict", 0, 20)
    stored = store.get_run("scope-a", "run-conflict")
    assert page is not None and stored is not None
    assert len(page.events) == 1
    assert page.events[0]["attributes"]["outcome"] == "first"
    assert stored.summary.event_integrity == "partial"


def test_summary_topology_and_after_seq_reads_are_durable(store: RunHistoryStore) -> None:
    _write_complete_run(store, "run-readable")
    stored = store.get_run("scope-a", "run-readable")
    page = store.get_events("scope-a", "run-readable", after_seq=1, limit=20)
    assert stored is not None and page is not None
    assert stored.summary.status == "completed"
    assert stored.summary.persistence_status == "durable"
    assert stored.topology["revision"] == "rev-a"
    assert [event["seq"] for event in page.events] == [2]
    assert page.after_seq == page.latest_seq == 2
    assert page.terminal is True


def test_scope_first_parameterized_reads_do_not_cross_tenants(store: RunHistoryStore) -> None:
    _write_complete_run(store, "run-a", scope="scope-a")
    _write_complete_run(store, "run-b", scope="scope-b", started_at=NOW + timedelta(seconds=1))
    assert store.get_run("scope-b", "run-a") is None
    assert store.get_events("scope-b", "run-a", 0, 20) is None
    assert store.get_run("scope-a' OR 1=1 --", "run-a") is None
    assert [item.run_id for item in store.list_runs("scope-a", RunListQuery("scope-a"))] == [
        "run-a"
    ]


def test_list_runs_uses_stable_descending_keyset_order(store: RunHistoryStore) -> None:
    _write_complete_run(store, "run-a", started_at=NOW)
    _write_complete_run(store, "run-c", started_at=NOW + timedelta(seconds=1))
    _write_complete_run(store, "run-b", started_at=NOW + timedelta(seconds=1))
    query = RunListQuery("scope-a", limit=2, as_of=NOW + timedelta(seconds=2))
    first = store.list_runs("scope-a", query)
    second = store.list_runs(
        "scope-a",
        query,
        before=(first[-1].started_at, first[-1].run_id),
    )
    assert [item.run_id for item in first] == ["run-c", "run-b"]
    assert [item.run_id for item in second] == ["run-a"]


def test_restart_and_two_worker_wal_visibility(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = RunHistoryStore.open(settings, "boot-a", "worker-a", now=lambda: NOW)
    _write_complete_run(first, "run-restart")
    first.close(2000)
    second = RunHistoryStore.open(settings, "boot-b", "worker-b", now=lambda: NOW)
    third = RunHistoryStore.open(settings, "boot-c", "worker-c", now=lambda: NOW)
    try:
        assert second.get_run("scope-a", "run-restart") is not None
        assert third.enqueue(_mutation("run-other-worker", started_at=NOW + timedelta(seconds=2)))
        assert third.flush_for_test()
        assert second.get_run("scope-a", "run-other-worker") is not None
    finally:
        second.close(2000)
        third.close(2000)


def test_v0_database_migrates_atomically_to_v1(tmp_path: Path) -> None:
    path = tmp_path / "run-history.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_marker VALUES ('kept')")
    value = RunHistoryStore.open(_settings(tmp_path), "boot-a", "worker-a", now=lambda: NOW)
    try:
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
            assert connection.execute("SELECT value FROM legacy_marker").fetchone()[0] == "kept"
            assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        value.close(2000)


def test_schema_too_new_is_read_only_and_disables_writes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = RunHistoryStore.open(settings, "boot-a", "worker-a", now=lambda: NOW)
    _write_complete_run(first, "run-existing")
    first.close(2000)
    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA user_version=2")
    newer = RunHistoryStore.open(settings, "boot-b", "worker-b", now=lambda: NOW)
    try:
        with sqlite3.connect(settings.sqlite_path) as connection:
            assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "delete"
        health = newer.health_snapshot()
        assert health.status == "degraded"
        assert health.reason == "schema_too_new"
        assert health.write_enabled is False
        assert newer.get_run("scope-a", "run-existing") is not None
        assert newer.enqueue(_mutation("run-rejected")) is False
    finally:
        newer.close(2000)


def test_compact_utf8_json_and_nan_are_rejected_failure_silently(store: RunHistoryStore) -> None:
    utf8 = _mutation("run-utf8")
    event = json.loads(utf8.event_json)
    event["attributes"]["outcome"] = "中文"
    utf8 = replace(utf8, event_json=json.dumps(event, ensure_ascii=False, separators=(",", ":")))
    assert store.enqueue(utf8)
    assert store.enqueue(replace(utf8, event_seq=2, event_json='{"elapsed_ms":NaN}')) is False
    assert store.flush_for_test()
    with sqlite3.connect(store.database_path) as connection:
        raw = connection.execute(
            "SELECT event_json FROM run_events WHERE run_id = ? AND seq = 1", ("run-utf8",)
        ).fetchone()[0]
    assert "中文" in raw and " " not in raw
    assert not math.isnan(json.loads(raw)["elapsed_ms"])


def test_sensitive_sentinels_are_absent_from_database_bytes(store: RunHistoryStore) -> None:
    _write_complete_run(store, "run-private")
    raw = store.database_path.read_bytes()
    for value in (b"SENTINEL_QUERY", b"SENTINEL_ANSWER", b"SENTINEL_TOKEN", b"SENTINEL_STACK"):
        assert value not in raw


def test_many_runs_keep_only_a_bounded_recent_watermark_cache(tmp_path: Path) -> None:
    value = RunHistoryStore.open(
        _settings(
            tmp_path,
            writer_queue_capacity=512,
            writer_batch_size=64,
            writer_flush_ms=1,
        ),
        "boot-a",
        "worker-a",
        now=lambda: NOW,
    )
    try:
        for index in range(300):
            assert value.enqueue(
                _mutation(
                    f"run-many-{index:03d}",
                    started_at=NOW + timedelta(microseconds=index),
                )
            )
        assert value.flush_for_test(timeout_s=10)

        health = value.health_snapshot()
        assert health.durable_run_count == 300
        assert health.durable_cache_entries <= 256
        assert value.durable_watermark("scope-a", "run-many-000") == 1
    finally:
        value.close(2000)


def test_cross_scope_duplicate_run_id_cannot_move_or_mutate_original(
    store: RunHistoryStore,
) -> None:
    assert store.enqueue(_mutation("run-owned", scope="scope-a", seq=1))
    assert store.flush_for_test()
    assert store.enqueue(
        _mutation(
            "run-owned",
            scope="scope-b",
            seq=2,
            status="completed",
            event_type="run.completed",
        )
    )
    assert store.flush_for_test()

    original = store.get_run("scope-a", "run-owned")
    events = store.get_events("scope-a", "run-owned", 0, 20)
    assert original is not None and events is not None
    assert original.summary.status == "running"
    assert original.summary.last_seq == 1
    assert [event["seq"] for event in events.events] == [1]
    assert store.get_run("scope-b", "run-owned") is None
    assert store.health_snapshot().reason == "tenant_scope_conflict"


def test_same_scope_topology_mismatch_preserves_original_and_marks_partial(
    store: RunHistoryStore,
) -> None:
    assert store.enqueue(_mutation("run-topology-owned", seq=1))
    assert store.flush_for_test()
    mismatch = _mutation("run-topology-owned", seq=2)
    topology = json.loads(mismatch.topology_json)
    topology["revision"] = "rev-other"
    event = json.loads(mismatch.event_json)
    event["topology_revision"] = "rev-other"
    mismatch = replace(
        mismatch,
        summary=replace(mismatch.summary, topology_revision="rev-other"),
        topology_json=json.dumps(topology, ensure_ascii=False, separators=(",", ":")),
        event_json=json.dumps(event, ensure_ascii=False, separators=(",", ":")),
    )

    assert store.enqueue(mismatch)
    assert store.flush_for_test()

    stored = store.get_run("scope-a", "run-topology-owned")
    events = store.get_events("scope-a", "run-topology-owned", 0, 20)
    assert stored is not None and events is not None
    assert stored.summary.topology_revision == "rev-a"
    assert stored.topology["revision"] == "rev-a"
    assert stored.summary.last_seq == 1
    assert stored.summary.event_integrity == "partial"
    assert [event["seq"] for event in events.events] == [1]
    assert store.health_snapshot().reason == "topology_mismatch"


@pytest.mark.parametrize("invalid_seq", [1.9, True, "1"])
def test_event_json_seq_requires_a_positive_strict_integer(
    store: RunHistoryStore,
    invalid_seq: Any,
) -> None:
    mutation = _mutation(f"run-json-seq-{invalid_seq!r}")
    event = json.loads(mutation.event_json)
    event["seq"] = invalid_seq
    mutation = replace(
        mutation,
        event_json=json.dumps(event, ensure_ascii=False, separators=(",", ":")),
    )

    assert store.enqueue(mutation) is False


@pytest.mark.parametrize("invalid_seq", [1.9, True, "1"])
def test_mutation_seq_requires_a_positive_strict_integer(
    store: RunHistoryStore,
    invalid_seq: Any,
) -> None:
    mutation = replace(_mutation(f"run-mutation-seq-{invalid_seq!r}"), event_seq=invalid_seq)

    assert store.enqueue(mutation) is False


def test_topology_json_requires_an_object(store: RunHistoryStore) -> None:
    mutation = replace(_mutation("run-scalar-topology"), topology_json='"scalar"')

    assert store.enqueue(mutation) is False


def test_schema_v1_records_a_manifest_checksum(tmp_path: Path) -> None:
    value = RunHistoryStore.open(_settings(tmp_path), "boot-a", "worker-a", now=lambda: NOW)
    try:
        with sqlite3.connect(value.database_path) as connection:
            row = connection.execute(
                "SELECT checksum FROM schema_migrations WHERE version = 1"
            ).fetchone()
        assert row is not None
        assert len(row[0]) == 64
    finally:
        value.close(2000)


@pytest.mark.parametrize("user_version", [0, 1])
def test_drifted_existing_schema_disables_writes_without_stamping_success(
    tmp_path: Path,
    user_version: int,
) -> None:
    path = tmp_path / "run-history.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE registry_meta (key TEXT PRIMARY KEY, value BLOB NOT NULL)")
        connection.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, tenant_scope TEXT)")
        connection.execute(f"PRAGMA user_version={user_version}")
    try:
        value = RunHistoryStore.open(_settings(tmp_path), "boot-a", "worker-a", now=lambda: NOW)
    except Exception as exc:
        pytest.fail(f"schema drift must fail safe instead of escaping: {type(exc).__name__}")
    try:
        health = value.health_snapshot()
        assert health.status == "degraded"
        assert health.reason == "schema_drift"
        assert health.write_enabled is False
        assert value.enqueue(_mutation("run-rejected-drift")) is False
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == user_version
            migration_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
        assert migration_table is None
    finally:
        value.close(2000)


def test_wrong_migration_checksum_disables_writes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = RunHistoryStore.open(settings, "boot-a", "worker-a", now=lambda: NOW)
    first.close(2000)
    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT OR REPLACE INTO schema_migrations(version, checksum) VALUES (1, 'bad')"
        )

    second = RunHistoryStore.open(settings, "boot-b", "worker-b", now=lambda: NOW)
    try:
        health = second.health_snapshot()
        assert health.status == "degraded"
        assert health.reason == "schema_drift"
        assert health.write_enabled is False
    finally:
        second.close(2000)


def test_missing_required_v1_index_disables_writes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = RunHistoryStore.open(settings, "boot-a", "worker-a", now=lambda: NOW)
    first.close(2000)
    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.execute("DROP INDEX idx_runs_scope_started")

    second = RunHistoryStore.open(settings, "boot-b", "worker-b", now=lambda: NOW)
    try:
        health = second.health_snapshot()
        assert health.status == "degraded"
        assert health.reason == "schema_drift"
        assert health.write_enabled is False
    finally:
        second.close(2000)


def _seed_worker_run(
    path: Path,
    *,
    boot_id: str,
    worker_id: str,
    run_id: str,
    heartbeat_at: datetime,
    started_at: datetime,
) -> None:
    heartbeat_us = int(heartbeat_at.timestamp() * 1_000_000)
    started_us = int(started_at.timestamp() * 1_000_000)
    topology = json.dumps(
        {
            "id": "rag.query",
            "revision": "rev-a",
            "executor": "sequential_stream",
            "nodes": [],
            "edges": [],
        },
        separators=(",", ":"),
    )
    event = json.dumps(
        {
            "schema_version": 1,
            "run_id": run_id,
            "seq": 1,
            "occurred_at": started_at.isoformat().replace("+00:00", "Z"),
            "elapsed_ms": 0.0,
            "topology_id": "rag.query",
            "topology_revision": "rev-a",
            "type": "run.started",
            "node_id": None,
            "attempt": None,
            "duration_ms": None,
            "attributes": {},
            "error": None,
        },
        separators=(",", ":"),
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO boots VALUES (?,?,?,?,?)",
            (boot_id, started_us, heartbeat_us, None, "alive"),
        )
        connection.execute(
            "INSERT OR REPLACE INTO workers VALUES (?,?,?,?,?,?)",
            (worker_id, boot_id, started_us, heartbeat_us, None, "alive"),
        )
        connection.execute(
            """INSERT OR REPLACE INTO runs VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                "scope-a",
                boot_id,
                worker_id,
                "running",
                "unknown",
                started_us,
                started_us,
                None,
                0.0,
                "rag.query",
                "rev-a",
                "sequential_stream",
                topology,
                1,
                1,
                1,
                '["retrieval"]',
                "[]",
                "hybrid",
                0,
                0,
                "complete",
                None,
                None,
            ),
        )
        connection.execute(
            "INSERT OR REPLACE INTO run_events VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, 1, "run.started", None, None, started_us, 0.0, None, event),
        )


def _seed_terminal_runs(
    path: Path,
    *,
    count: int,
    finished_at: datetime,
    prefix: str,
) -> None:
    finished_us = int(finished_at.timestamp() * 1_000_000)
    topology = (
        '{"id":"rag.query","revision":"rev-a","executor":"sequential_stream",'
        '"nodes":[],"edges":[]}'
    )
    rows = []
    events = []
    for index in range(count):
        run_id = f"{prefix}-{index:06d}"
        stamp = finished_us + index
        rows.append(
            (
                run_id,
                "scope-a",
                "boot-seed",
                "worker-seed",
                "completed",
                "answered",
                stamp - 1_000_000,
                stamp,
                stamp,
                1000.0,
                "rag.query",
                "rev-a",
                "sequential_stream",
                topology,
                1,
                1,
                1,
                "[]",
                "[]",
                "hybrid",
                0,
                0,
                "complete",
                None,
                None,
            )
        )
        events.append(
            (
                run_id,
                1,
                "run.completed",
                None,
                None,
                stamp,
                1000.0,
                None,
                '{"type":"run.completed"}',
            )
        )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO boots VALUES (?,?,?,?,?)",
            ("boot-seed", finished_us, finished_us, finished_us, "stopped"),
        )
        connection.execute(
            "INSERT OR IGNORE INTO workers VALUES (?,?,?,?,?,?)",
            ("worker-seed", "boot-seed", finished_us, finished_us, finished_us, "stopped"),
        )
        connection.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        connection.executemany("INSERT INTO run_events VALUES (?,?,?,?,?,?,?,?,?)", events)


def test_heartbeat_bootstrap_and_marking_use_required_defaults(tmp_path: Path) -> None:
    assert RunHistorySettings().heartbeat_interval_s == 5
    assert RunHistorySettings().worker_stale_after_s == 30
    value = RunHistoryStore.open(
        _settings(tmp_path), "boot-current", "worker-current", now=lambda: NOW
    )
    later = NOW + timedelta(seconds=5)
    try:
        value.mark_heartbeat(later)
        expected = int(later.timestamp() * 1_000_000)
        with sqlite3.connect(value.database_path) as connection:
            boot = connection.execute(
                "SELECT state,last_heartbeat_at_us FROM boots WHERE boot_id=?", ("boot-current",)
            ).fetchone()
            worker = connection.execute(
                "SELECT state,last_heartbeat_at_us FROM workers WHERE worker_id=?",
                ("worker-current",),
            ).fetchone()
        assert boot == ("alive", expected)
        assert worker == ("alive", expected)
    finally:
        value.close(2000)


def test_background_heartbeat_uses_configured_interval_and_clock(tmp_path: Path) -> None:
    ticks = 0

    def clock() -> datetime:
        nonlocal ticks
        value = NOW + timedelta(seconds=ticks * 5)
        ticks += 1
        return value

    value = RunHistoryStore.open(
        _settings(tmp_path), "boot-current", "worker-current", now=clock
    )
    value._settings.heartbeat_interval_s = 0.01
    try:
        value.start_background_tasks()
        deadline = time.monotonic() + 0.5
        heartbeat_us = int(NOW.timestamp() * 1_000_000)
        while heartbeat_us <= int(NOW.timestamp() * 1_000_000) and time.monotonic() < deadline:
            with sqlite3.connect(value.database_path) as connection:
                heartbeat_us = connection.execute(
                    "SELECT last_heartbeat_at_us FROM workers WHERE worker_id=?",
                    ("worker-current",),
                ).fetchone()[0]
            time.sleep(0.01)
        assert heartbeat_us >= int((NOW + timedelta(seconds=5)).timestamp() * 1_000_000)
    finally:
        value._background_stop.set()
        value.close(2000)


def test_close_stops_lifecycle_thread_without_prior_clean_shutdown(tmp_path: Path) -> None:
    value = RunHistoryStore.open(
        _settings(tmp_path), "boot-current", "worker-current", now=lambda: NOW
    )
    value._settings.heartbeat_interval_s = 0.01
    value.start_background_tasks()
    thread = value._background_thread
    assert thread is not None and thread.is_alive()

    value.close(100)

    assert not thread.is_alive()


def test_close_releases_sqlite_files_after_slow_writer_exits_without_gc(tmp_path: Path) -> None:
    value = RunHistoryStore.open(
        _settings(tmp_path, writer_flush_ms=1000),
        "boot-current",
        "worker-current",
        now=lambda: NOW,
    )
    assert value.enqueue(_mutation("run-slow-close"))
    dequeue_deadline = time.monotonic() + 0.5
    while not value._queue.empty() and time.monotonic() < dequeue_deadline:
        time.sleep(0.005)
    writer = value._writer_thread
    assert writer is not None

    started = time.monotonic()
    value.close(10)
    elapsed = time.monotonic() - started

    assert elapsed < 0.2
    writer.join(timeout=2.0)
    assert not writer.is_alive()
    targets = (
        value.database_path,
        Path(f"{value.database_path}-wal"),
        Path(f"{value.database_path}-shm"),
    )
    release_deadline = time.monotonic() + 1.0
    remaining = [target for target in targets if target.exists()]
    while remaining and time.monotonic() < release_deadline:
        for target in tuple(remaining):
            moved = target.with_name(f"released-{target.name}")
            try:
                os.replace(target, moved)
                moved.unlink()
            except FileNotFoundError:
                remaining.remove(target)
                continue
            except PermissionError:
                continue
            remaining.remove(target)
        if remaining:
            time.sleep(0.01)
    assert remaining == []
    assert value.health_snapshot().database_filename == "run-history.sqlite3"


def test_shutdown_uses_one_deadline_when_sqlite_is_locked(tmp_path: Path) -> None:
    value = RunHistoryStore.open(
        _settings(tmp_path), "boot-current", "worker-current", now=lambda: NOW
    )
    lock = sqlite3.connect(value.database_path, timeout=0, isolation_level=None)
    lock.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        value.mark_clean_shutdown(NOW + timedelta(seconds=1), 10)
        elapsed = time.monotonic() - started

        assert elapsed < 0.2
        assert value.health_snapshot().status == "degraded"
    finally:
        lock.rollback()
        lock.close()
        value.close(100)


def test_stale_recovery_uses_30_second_cutoff_without_fake_terminal(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    seed = RunHistoryStore.open(settings, "boot-seed", "worker-seed", now=lambda: NOW)
    seed.close(2000)
    _seed_worker_run(
        Path(settings.sqlite_path),
        boot_id="boot-old",
        worker_id="worker-old",
        run_id="run-stale",
        heartbeat_at=NOW,
        started_at=NOW - timedelta(minutes=1),
    )
    value = RunHistoryStore.open(
        settings,
        "boot-current",
        "worker-current",
        now=lambda: NOW + timedelta(seconds=29),
    )
    try:
        assert value.recover_stale_workers(NOW + timedelta(seconds=29)) == 0
        assert value.recover_stale_workers(NOW + timedelta(seconds=31)) == 1
        run = value.get_run("scope-a", "run-stale")
        page = value.get_events("scope-a", "run-stale", 0, 500)
        assert run is not None and page is not None
        assert (run.summary.status, run.summary.interruption_reason) == (
            "interrupted",
            "worker_lost",
        )
        assert run.summary.finished_at == NOW + timedelta(seconds=31)
        assert run.summary.event_integrity == "partial"
        assert all(
            event["type"] not in {"run.completed", "run.failed", "run.cancelled"}
            for event in page.events
        )
    finally:
        value.close(2000)


def test_shutdown_interrupts_current_running_without_fake_terminal(tmp_path: Path) -> None:
    value = RunHistoryStore.open(
        _settings(tmp_path), "boot-current", "worker-current", now=lambda: NOW
    )
    mutation = _mutation("run-shutdown")
    mutation = replace(
        mutation,
        summary=replace(mutation.summary, boot_id="boot-current", worker_id="worker-current"),
    )
    assert value.enqueue(mutation)
    assert value.flush_for_test()
    stopped_at = NOW + timedelta(seconds=2)

    value.mark_clean_shutdown(stopped_at, 2000)

    run = value.get_run("scope-a", "run-shutdown")
    page = value.get_events("scope-a", "run-shutdown", 0, 500)
    assert run is not None and page is not None
    assert run.summary.status == "interrupted"
    assert run.summary.interruption_reason == "process_shutdown"
    assert run.summary.event_integrity == "partial"
    assert all(event["type"] == "run.started" for event in page.events)
    with sqlite3.connect(value.database_path) as connection:
        boot_state = connection.execute(
            "SELECT state FROM boots WHERE boot_id=?", ("boot-current",)
        ).fetchone()[0]
        worker_state = connection.execute(
            "SELECT state FROM workers WHERE worker_id=?", ("worker-current",)
        ).fetchone()[0]
    assert (boot_state, worker_state) == ("stopped", "stopped")
    value.close(2000)


def test_retention_deletes_expired_terminal_but_never_running_and_cascades(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    value = RunHistoryStore.open(settings, "boot-current", "worker-current", now=lambda: NOW)
    value.close(2000)
    _seed_terminal_runs(
        Path(settings.sqlite_path),
        count=1,
        finished_at=NOW - timedelta(days=31),
        prefix="old-terminal",
    )
    _seed_worker_run(
        Path(settings.sqlite_path),
        boot_id="boot-running",
        worker_id="worker-running",
        run_id="old-running",
        heartbeat_at=NOW,
        started_at=NOW - timedelta(days=31),
    )
    value = RunHistoryStore.open(settings, "boot-cleanup", "worker-cleanup", now=lambda: NOW)
    try:
        assert value.cleanup(NOW) == 1
        assert value.get_run("scope-a", "old-running") is not None
        assert value.get_run("scope-a", "old-terminal-000000") is None
        with sqlite3.connect(value.database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM run_events WHERE run_id=?", ("old-terminal-000000",)
            ).fetchone()[0] == 0
    finally:
        value.close(2000)


def test_retention_capacity_rule_orders_oldest_terminal_and_uses_exact_defaults(
    tmp_path: Path,
) -> None:
    assert RunHistorySettings().retention_days == 30
    assert RunHistorySettings().max_persisted_runs == 100000
    settings = _settings(tmp_path, max_persisted_runs=3)
    value = RunHistoryStore.open(settings, "boot-current", "worker-current", now=lambda: NOW)
    value.close(2000)
    _seed_terminal_runs(Path(settings.sqlite_path), count=5, finished_at=NOW, prefix="capacity")
    value = RunHistoryStore.open(settings, "boot-cleanup", "worker-cleanup", now=lambda: NOW)
    try:
        assert value.cleanup(NOW) == 2
        with sqlite3.connect(value.database_path) as connection:
            remaining = [row[0] for row in connection.execute("SELECT run_id FROM runs ORDER BY run_id")]
        assert remaining == ["capacity-000002", "capacity-000003", "capacity-000004"]
    finally:
        value.close(2000)


def test_retention_hard_caps_each_transaction_at_1000_and_never_vacuums(tmp_path: Path) -> None:
    settings = _settings(tmp_path, cleanup_batch_size=5000)
    value = RunHistoryStore.open(settings, "boot-current", "worker-current", now=lambda: NOW)
    value.close(2000)
    _seed_terminal_runs(
        Path(settings.sqlite_path),
        count=1001,
        finished_at=NOW - timedelta(days=31),
        prefix="batch",
    )
    value = RunHistoryStore.open(settings, "boot-cleanup", "worker-cleanup", now=lambda: NOW)
    statements: list[str] = []
    assert value._executor is not None and value._writer_connection is not None
    value._executor.submit(value._writer_connection.set_trace_callback, statements.append).result()
    try:
        assert value.cleanup(NOW) == 1000
        with sqlite3.connect(value.database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
            assert connection.execute("PRAGMA freelist_count").fetchone()[0] > 0
        normalized = [statement.lower().replace(" ", "") for statement in statements]
        assert any("pragmawal_checkpoint(passive)" in statement for statement in normalized)
        assert all("vacuum" not in statement for statement in normalized)
    finally:
        value.close(2000)


def test_corrupt_database_wal_and_shm_are_rotated_with_utc_suffix(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    path = Path(settings.sqlite_path)
    path.write_bytes(b"not-a-sqlite-database")
    Path(f"{path}-wal").write_bytes(b"broken-wal")
    Path(f"{path}-shm").write_bytes(b"broken-shm")

    value = RunHistoryStore.open(settings, "boot-current", "worker-current", now=lambda: NOW)
    try:
        health = value.health_snapshot()
        assert health.status == "degraded"
        assert health.state == "ready"
        assert health.quick_check == "ok"
        assert health.recovery_action == "corrupt_db_rotated"
        assert health.write_enabled is True
        assert path.is_file()
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        suffix = ".corrupt-20260823T080000000000Z"
        assert Path(f"{path}{suffix}").read_bytes() == b"not-a-sqlite-database"
        assert Path(f"{path}-wal{suffix}").read_bytes() == b"broken-wal"
        assert Path(f"{path}-shm{suffix}").read_bytes() == b"broken-shm"
    finally:
        value.close(2000)


def test_corrupt_rotation_failure_is_memory_only_and_does_not_hot_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    path = Path(settings.sqlite_path)
    path.write_bytes(b"not-a-sqlite-database")

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        raise OSError("rotation denied")

    monkeypatch.setattr(os, "replace", fail_replace)
    value = RunHistoryStore.open(settings, "boot-current", "worker-current", now=lambda: NOW)
    try:
        health = value.health_snapshot()
        assert health.status == "degraded"
        assert health.state == "memory_only"
        assert health.quick_check == "failed"
        assert health.recovery_action is None
        assert health.write_enabled is False
        path.unlink()
        sqlite3.connect(path).close()
        value.start_background_tasks()
        assert value.health_snapshot().state == "memory_only"
        assert value.write_enabled is False
    finally:
        value.close(2000)


def test_schema_too_new_health_keeps_safe_reason_and_read_only_state(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    first = RunHistoryStore.open(settings, "boot-a", "worker-a", now=lambda: NOW)
    first.close(2000)
    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.execute("PRAGMA user_version=2")

    newer = RunHistoryStore.open(settings, "boot-b", "worker-b", now=lambda: NOW)
    try:
        health = newer.health_snapshot()
        assert health.status == "degraded"
        assert health.state == "read_only"
        assert health.reason == "schema_too_new"
        assert health.quick_check == "ok"
        assert health.recovery_action is None
    finally:
        newer.close(2000)



def test_store_internal_limit_plus_one_traverses_150_runs(tmp_path: Path) -> None:
    value = RunHistoryStore.open(
        _settings(tmp_path, writer_queue_capacity=512, writer_batch_size=64),
        "boot-a",
        "worker-a",
        now=lambda: NOW,
    )
    try:
        for index in range(150):
            _write_complete_run(
                value,
                f"run-{index:03}",
                started_at=NOW + timedelta(seconds=index),
            )
        assert value.flush_for_test(timeout_s=5)
        query = RunListQuery("scope-a", limit=101, as_of=NOW + timedelta(seconds=200))
        first = value.list_runs("scope-a", query)
        second = value.list_runs(
            "scope-a", query, before=(first[-1].started_at, first[-1].run_id)
        )
        assert len(first) == 101
        assert len(second) == 49
        assert len({item.run_id for item in [*first, *second]}) == 150
    finally:
        value.close(2000)



def test_stuck_view_requires_alive_fresh_worker(tmp_path: Path) -> None:
    value = RunHistoryStore.open(_settings(tmp_path), "boot-current", "worker-current", now=lambda: NOW)
    try:
        started = NOW - timedelta(seconds=400)
        _seed_worker_run(
            value.database_path,
            boot_id="boot-alive",
            worker_id="worker-alive",
            run_id="run-alive",
            heartbeat_at=NOW,
            started_at=started,
        )
        _seed_worker_run(
            value.database_path,
            boot_id="boot-stale",
            worker_id="worker-stale",
            run_id="run-stale-worker",
            heartbeat_at=NOW - timedelta(seconds=31),
            started_at=started,
        )
        _seed_worker_run(
            value.database_path,
            boot_id="boot-dead",
            worker_id="worker-dead",
            run_id="run-dead-worker",
            heartbeat_at=NOW,
            started_at=started,
        )
        with sqlite3.connect(value.database_path) as connection:
            connection.execute(
                "UPDATE workers SET state='stopped' WHERE worker_id='worker-dead'"
            )
        items = value.list_runs(
            "scope-a", RunListQuery("scope-a", view="stuck", as_of=NOW, limit=101)
        )
        assert [item.run_id for item in items] == ["run-alive"]
    finally:
        value.close(2000)



def test_read_faults_raise_typed_unavailable_signal(
    store: RunHistoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_read():
        raise sqlite3.OperationalError("disk read fault")

    monkeypatch.setattr(store, "_read_connection", fail_read)
    with pytest.raises(RunHistoryReadError):
        store.list_runs("scope-a", RunListQuery("scope-a"))
    with pytest.raises(RunHistoryReadError):
        store.get_run("scope-a", "run-a")
    with pytest.raises(RunHistoryReadError):
        store.get_events("scope-a", "run-a", 0, 20)
    with pytest.raises(RunHistoryReadError):
        store.durable_watermark("scope-a", "run-a")


def test_disk_full_writer_failure_is_nonblocking_degraded_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value = RunHistoryStore.open(
        _settings(tmp_path, writer_batch_size=1, writer_flush_ms=1),
        "boot-a",
        "worker-a",
        now=lambda: NOW,
    )
    original = value._write_batch_once

    def disk_full(*_args: Any, **_kwargs: Any) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(value, "_write_batch_once", disk_full)
    try:
        started = time.perf_counter()
        assert value.enqueue(_mutation("run-disk-full"))
        assert time.perf_counter() - started < 0.05
        deadline = time.monotonic() + 2.0
        while value.health_snapshot().reason != "write_failed" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert value.health_snapshot().reason == "write_failed"

        monkeypatch.setattr(value, "_write_batch_once", original)
        assert value.flush_for_test(timeout_s=5)
        assert value.get_run("scope-a", "run-disk-full") is not None
    finally:
        value.close(2000)


def test_unwritable_path_and_disabled_persistence_fail_safe(tmp_path: Path) -> None:
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("occupied", encoding="utf-8")
    unwritable = RunHistoryStore.open(
        _settings(blocked_parent), "boot-a", "worker-a", now=lambda: NOW
    )
    disabled_settings = _settings(tmp_path / "disabled", persistence_enabled=False)
    disabled = RunHistoryStore.open(
        disabled_settings, "boot-b", "worker-b", now=lambda: NOW
    )
    try:
        assert unwritable.health_snapshot().state == "memory_only"
        assert unwritable.health_snapshot().reason == "open_failed"
        assert unwritable.enqueue(_mutation("run-unwritable")) is False
        assert disabled.health_snapshot().state == "disabled"
        assert disabled.enqueue(_mutation("run-disabled")) is False
        assert not Path(disabled_settings.sqlite_path).exists()
    finally:
        unwritable.close(2000)
        disabled.close(2000)


def test_default_writer_persistence_lag_p95_is_below_500_ms(tmp_path: Path) -> None:
    value = RunHistoryStore.open(
        _settings(
            tmp_path,
            writer_queue_capacity=256,
            writer_batch_size=64,
            writer_flush_ms=100,
        ),
        "boot-a",
        "worker-a",
        now=lambda: NOW,
    )
    enqueued_at: list[float] = []
    try:
        for index in range(128):
            enqueued_at.append(time.perf_counter())
            assert value.enqueue(
                _mutation(f"run-lag-{index:03d}", started_at=NOW + timedelta(microseconds=index))
            )
        assert value.flush_for_test(timeout_s=5)
        durable_at = time.perf_counter()
        samples_ms = sorted((durable_at - started) * 1000 for started in enqueued_at)
        p95_ms = samples_ms[int(len(samples_ms) * 0.95) - 1]
        assert p95_ms < 500.0
    finally:
        value.close(2000)