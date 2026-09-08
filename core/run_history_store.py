"""Failure-silent asynchronous SQLite history for redacted run facts."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import sqlite3
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping, Sequence

from config.settings import RunHistorySettings
from core.metrics import get_metrics
from core.run_registry import RegistryMutation, RunListQuery, RunSummary


_SCHEMA_VERSION = 1
_DURABLE_WATERMARK_CACHE_CAP = 256
LOCK_BACKOFF_SECONDS = (0.025, 0.05, 0.1, 0.2, 0.4)

_MIGRATION_TABLE_SQL = """CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)"""

_SCHEMA_STATEMENTS = (
    _MIGRATION_TABLE_SQL,
    """CREATE TABLE IF NOT EXISTS registry_meta (
        key TEXT PRIMARY KEY, value BLOB NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS boots (
        boot_id TEXT PRIMARY KEY, started_at_us INTEGER NOT NULL,
        last_heartbeat_at_us INTEGER NOT NULL, stopped_at_us INTEGER,
        state TEXT NOT NULL CHECK (state IN ('starting','alive','stopped','stale')))""",
    """CREATE TABLE IF NOT EXISTS workers (
        worker_id TEXT PRIMARY KEY, boot_id TEXT NOT NULL REFERENCES boots(boot_id),
        started_at_us INTEGER NOT NULL, last_heartbeat_at_us INTEGER NOT NULL,
        stopped_at_us INTEGER,
        state TEXT NOT NULL CHECK (state IN ('starting','alive','stopped','stale')))""",
    """CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY, tenant_scope TEXT NOT NULL, boot_id TEXT NOT NULL,
        worker_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('running','completed','failed','cancelled','interrupted')),
        outcome TEXT NOT NULL CHECK (outcome IN ('answered','abstained','unknown')),
        started_at_us INTEGER NOT NULL, updated_at_us INTEGER NOT NULL,
        finished_at_us INTEGER, elapsed_ms REAL NOT NULL,
        topology_id TEXT NOT NULL, topology_revision TEXT NOT NULL,
        executor TEXT NOT NULL, topology_json TEXT NOT NULL,
        last_seq INTEGER NOT NULL, event_count INTEGER NOT NULL,
        earliest_available_seq INTEGER NOT NULL,
        current_node_ids_json TEXT NOT NULL, failed_node_ids_json TEXT NOT NULL,
        route TEXT, degraded_count INTEGER NOT NULL, retry_count INTEGER NOT NULL,
        event_integrity TEXT NOT NULL CHECK (event_integrity IN ('complete','partial','unknown')),
        interruption_reason TEXT, query_fingerprint TEXT)""",
    """CREATE TABLE IF NOT EXISTS run_events (
        run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        seq INTEGER NOT NULL, event_type TEXT NOT NULL, node_id TEXT, attempt INTEGER,
        occurred_at_us INTEGER NOT NULL, elapsed_ms REAL NOT NULL, duration_ms REAL,
        event_json TEXT NOT NULL, PRIMARY KEY (run_id, seq))""",
    """CREATE INDEX IF NOT EXISTS idx_runs_scope_started
       ON runs(tenant_scope, started_at_us DESC, run_id DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_runs_scope_status_started
       ON runs(tenant_scope, status, started_at_us DESC, run_id DESC)""",
    """CREATE INDEX IF NOT EXISTS idx_runs_worker_status
       ON runs(worker_id, status, updated_at_us)""",
    """CREATE INDEX IF NOT EXISTS idx_run_events_run_seq ON run_events(run_id, seq)""",
    """CREATE INDEX IF NOT EXISTS idx_workers_heartbeat
       ON workers(state, last_heartbeat_at_us)""",
)

_EXPECTED_TABLE_NAMES = (
    "registry_meta",
    "boots",
    "workers",
    "runs",
    "run_events",
    "schema_migrations",
)
_EXPECTED_INDEX_NAMES = (
    "idx_runs_scope_started",
    "idx_runs_scope_status_started",
    "idx_runs_worker_status",
    "idx_run_events_run_seq",
    "idx_workers_heartbeat",
)


class RunHistoryReadError(RuntimeError):
    """Durable read path is unavailable; absence was not established."""

    def __init__(self) -> None:
        super().__init__("run history read unavailable")


class _SchemaDriftError(RuntimeError):
    pass


class _CorruptDatabaseError(RuntimeError):
    pass


def _normalized_ddl(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def _schema_shape(
    connection: sqlite3.Connection,
    *,
    include_migration: bool,
) -> dict[str, Any]:
    table_names = [
        name for name in _EXPECTED_TABLE_NAMES if include_migration or name != "schema_migrations"
    ]
    tables: dict[str, Any] = {}
    for name in table_names:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        if row is None:
            tables[name] = None
            continue
        columns = [
            (str(item[1]), str(item[2]).upper(), int(item[3]), item[4], int(item[5]))
            for item in connection.execute(f'PRAGMA table_info("{name}")')
        ]
        foreign_keys = [
            (
                str(item[2]),
                str(item[3]),
                str(item[4]),
                str(item[5]),
                str(item[6]),
                str(item[7]),
            )
            for item in connection.execute(f'PRAGMA foreign_key_list("{name}")')
        ]
        tables[name] = {
            "sql": _normalized_ddl(str(row[0])),
            "columns": columns,
            "foreign_keys": foreign_keys,
        }
    indexes: dict[str, Any] = {}
    for name in _EXPECTED_INDEX_NAMES:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (name,)
        ).fetchone()
        if row is None:
            indexes[name] = None
            continue
        columns = [
            (str(item[2]), int(item[3]), str(item[4]))
            for item in connection.execute(f'PRAGMA index_xinfo("{name}")')
            if int(item[5]) == 1
        ]
        indexes[name] = {"sql": _normalized_ddl(str(row[0])), "columns": columns}
    return {"tables": tables, "indexes": indexes}


def _build_expected_schema_shape(*, include_migration: bool) -> dict[str, Any]:
    with closing(sqlite3.connect(":memory:")) as connection:
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)
        return _schema_shape(connection, include_migration=include_migration)


_EXPECTED_CORE_SCHEMA_SHAPE = _build_expected_schema_shape(include_migration=False)
_EXPECTED_SCHEMA_SHAPE = _build_expected_schema_shape(include_migration=True)
_SCHEMA_V1_CHECKSUM = hashlib.sha256(
    json.dumps(_EXPECTED_SCHEMA_SHAPE, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def _validate_schema_shape(
    connection: sqlite3.Connection,
    *,
    include_migration: bool,
) -> None:
    expected = _EXPECTED_SCHEMA_SHAPE if include_migration else _EXPECTED_CORE_SCHEMA_SHAPE
    if _schema_shape(connection, include_migration=include_migration) != expected:
        raise _SchemaDriftError("schema shape mismatch")


def _validate_existing_v0_objects(connection: sqlite3.Connection) -> None:
    actual = _schema_shape(connection, include_migration=True)
    expected = _EXPECTED_SCHEMA_SHAPE
    for category in ("tables", "indexes"):
        for name, shape in actual[category].items():
            if shape is not None and shape != expected[category][name]:
                raise _SchemaDriftError("schema shape mismatch")


_RUN_UPSERT = """
INSERT INTO runs (
 run_id,tenant_scope,boot_id,worker_id,status,outcome,started_at_us,updated_at_us,
 finished_at_us,elapsed_ms,topology_id,topology_revision,executor,topology_json,
 last_seq,event_count,earliest_available_seq,current_node_ids_json,
 failed_node_ids_json,route,degraded_count,retry_count,event_integrity,
 interruption_reason,query_fingerprint)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(run_id) DO UPDATE SET
 boot_id=excluded.boot_id,worker_id=excluded.worker_id,
 status=excluded.status,outcome=excluded.outcome,started_at_us=excluded.started_at_us,
 updated_at_us=excluded.updated_at_us,finished_at_us=excluded.finished_at_us,
 elapsed_ms=excluded.elapsed_ms,last_seq=excluded.last_seq,
 event_count=excluded.event_count,earliest_available_seq=excluded.earliest_available_seq,
 current_node_ids_json=excluded.current_node_ids_json,
 failed_node_ids_json=excluded.failed_node_ids_json,route=excluded.route,
 degraded_count=excluded.degraded_count,retry_count=excluded.retry_count,
 event_integrity=CASE WHEN runs.event_integrity='partial'
 OR excluded.event_integrity='partial' THEN 'partial' ELSE excluded.event_integrity END,
 interruption_reason=excluded.interruption_reason,query_fingerprint=excluded.query_fingerprint
WHERE excluded.last_seq >= runs.last_seq
"""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_us(value: datetime) -> int:
    return int(round(_utc(value).timestamp() * 1_000_000))


def _from_us(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1_000_000, tz=timezone.utc)


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _load_json(text: str) -> Any:
    return json.loads(text, parse_constant=_reject_constant)


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value


def _compact_json(value: Any) -> str:
    return json.dumps(
        _json_compatible(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )


def _normalize_json_text(value: str) -> str:
    return _compact_json(_load_json(value))


def _event_time_us(value: Any) -> int:
    if isinstance(value, datetime):
        return _to_us(value)
    if not isinstance(value, str):
        raise ValueError("occurred_at must be an RFC 3339 string")
    return _to_us(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _finite_float(value: Any) -> float:
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        raise ValueError("non-finite number")
    return number


def _strict_positive_seq(value: Any) -> int:
    if type(value) is not int or value < 1:
        raise ValueError("seq must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class PersistenceMutation:
    tenant_scope: str
    summary: RunSummary
    topology_json: str
    event_json: str
    event_seq: int
    event_type: str
    node_id: str | None
    attempt: int | None
    occurred_at_us: int
    duration_ms: float | None

    @classmethod
    def from_values(
        cls,
        tenant_scope: str,
        summary: RunSummary,
        topology: Mapping[str, Any],
        event: Mapping[str, Any],
    ) -> "PersistenceMutation":
        return cls(
            tenant_scope=tenant_scope,
            summary=summary,
            topology_json=_compact_json(topology),
            event_json=_compact_json(event),
            event_seq=_strict_positive_seq(event["seq"]),
            event_type=str(event["type"]),
            node_id=None if event.get("node_id") is None else str(event["node_id"]),
            attempt=None if event.get("attempt") is None else int(event["attempt"]),
            occurred_at_us=_event_time_us(event["occurred_at"]),
            duration_ms=None
            if event.get("duration_ms") is None
            else _finite_float(event["duration_ms"]),
        )

    @classmethod
    def from_registry(cls, mutation: RegistryMutation) -> "PersistenceMutation":
        return cls.from_values(
            mutation.context.tenant_scope, mutation.summary, mutation.topology, mutation.event
        )


@dataclass(frozen=True, slots=True)
class StoreIdentity:
    scope_key: bytes
    cursor_key: bytes


@dataclass(frozen=True, slots=True)
class StoreHealth:
    status: Literal["ok", "degraded", "disabled"]
    write_enabled: bool
    reason: str | None
    database_filename: str
    writer_queue_depth: int
    durable_run_count: int
    durable_cache_entries: int
    state: Literal["ready", "read_only", "memory_only", "disabled"]
    quick_check: Literal["ok", "failed", "not_run"]
    recovery_action: str | None
    last_commit_at: datetime | None
    commit_lag_ms: float | None
    dropped_mutations: int
    last_heartbeat_at: datetime | None


@dataclass(frozen=True, slots=True)
class StoredRun:
    summary: RunSummary
    topology: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StoredEventSlice:
    run_id: str
    events: tuple[Mapping[str, Any], ...]
    after_seq: int
    latest_seq: int
    terminal: bool
    earliest_available_seq: int
    persistence_status: Literal["durable", "partial", "unavailable"]


@dataclass(frozen=True, slots=True)
class _BatchWriteResult:
    durable_mutations: tuple[PersistenceMutation, ...]
    reasons: tuple[str, ...]


class RunHistoryStore:
    """Bounded MPSC submission with one asynchronous SQLite writer."""

    def __init__(
        self,
        settings: RunHistorySettings,
        boot_id: str,
        worker_id: str,
        now: Callable[[], datetime],
    ) -> None:
        self._settings = settings
        self._boot_id = boot_id
        self._worker_id = worker_id
        self._now = now
        self.database_path = Path(settings.sqlite_path).expanduser().resolve()
        self._queue: queue.Queue[PersistenceMutation] = queue.Queue(
            maxsize=settings.writer_queue_capacity
        )
        self._capacity = threading.BoundedSemaphore(settings.writer_queue_capacity)
        self._state_lock = threading.RLock()
        self._drained = threading.Condition(self._state_lock)
        self._pending_count = 0
        self._recent_watermarks: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._accepting = True
        self._closed = False
        self._stop_requested = threading.Event()
        self._abort_requested = threading.Event()
        self._reason: str | None = None
        self._status: Literal["ok", "degraded", "disabled"] = "ok"
        self._write_enabled = settings.persistence_enabled
        self._executor: ThreadPoolExecutor | None = None
        self._writer_connection: sqlite3.Connection | None = None
        self._writer_thread: threading.Thread | None = None
        self._background_stop = threading.Event()
        self._background_thread: threading.Thread | None = None
        self._connection_cleanup_thread: threading.Thread | None = None
        self._shutdown_marked = False
        self._last_checkpoint_day: str | None = None
        self._deleted_since_checkpoint = 0
        self._persistence_state: Literal[
            "ready", "read_only", "memory_only", "disabled"
        ] = "ready" if settings.persistence_enabled else "disabled"
        self._quick_check: Literal["ok", "failed", "not_run"] = "not_run"
        self._recovery_action: str | None = None
        self._last_commit_at: datetime | None = None
        self._commit_lag_ms: float | None = None
        self._dropped_mutations = 0
        self._last_heartbeat_at: datetime | None = None
        self._identity = StoreIdentity(os.urandom(32), os.urandom(32))

    @classmethod
    def open(
        cls,
        settings: RunHistorySettings,
        boot_id: str,
        worker_id: str,
        now: Callable[[], datetime] | None = None,
    ) -> "RunHistoryStore":
        store = cls(settings, boot_id, worker_id, now or (lambda: datetime.now(timezone.utc)))
        store._bootstrap()
        return store

    @property
    def identity(self) -> StoreIdentity:
        return self._identity

    @property
    def scope_key(self) -> bytes:
        return self._identity.scope_key

    @property
    def cursor_key(self) -> bytes:
        return self._identity.cursor_key

    @property
    def write_enabled(self) -> bool:
        with self._state_lock:
            return self._write_enabled and self._accepting and not self._closed

    def _bootstrap(self) -> None:
        if not self._settings.persistence_enabled:
            with self._state_lock:
                self._write_enabled = False
                self._status = "disabled"
                self._persistence_state = "disabled"
            return
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._bootstrap_once():
                return
            self._start_writer()
        except _CorruptDatabaseError:
            with self._state_lock:
                self._quick_check = "failed"
            try:
                self._rotate_corrupt_files(self._now())
                if not self._bootstrap_once():
                    self._fall_back_to_memory_only("corrupt_db_rebuild_failed")
                    return
                self._start_writer()
            except Exception:
                self._fall_back_to_memory_only("corrupt_db_rotation_failed", quick_check="failed")
                return
            with self._state_lock:
                self._status = "degraded"
                self._reason = "corrupt_db_rotated"
                self._recovery_action = "corrupt_db_rotated"
        except _SchemaDriftError:
            self._fall_back_to_memory_only("schema_drift")
        except Exception:
            self._fall_back_to_memory_only("open_failed")

    def _bootstrap_once(self) -> bool:
        self._probe_database_file()
        try:
            connection = self._connect()
        except sqlite3.DatabaseError as exc:
            if self._is_corrupt_database_error(exc):
                raise _CorruptDatabaseError from exc
            raise
        with closing(connection):
            try:
                check = connection.execute("PRAGMA quick_check").fetchone()
            except sqlite3.DatabaseError as exc:
                if self._is_corrupt_database_error(exc):
                    raise _CorruptDatabaseError from exc
                raise
            if check is None or check[0] != "ok":
                raise _CorruptDatabaseError("quick_check failed")
            with self._state_lock:
                self._quick_check = "ok"
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > _SCHEMA_VERSION:
                self._identity = self._read_identity(connection)
                with self._state_lock:
                    self._write_enabled = False
                    self._status = "degraded"
                    self._reason = "schema_too_new"
                    self._persistence_state = "read_only"
                return False
            if version == 0:
                self._migrate_v0_to_v1(connection)
            else:
                self._validate_or_adopt_v1(connection)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            self._identity = self._bootstrap_identity(connection)
            now = self._now()
            self._register_boot_and_worker(connection, now)
            self._recover_stale_workers_connection(connection, now)
            self._mark_boot_and_worker_alive(connection, now)
            with self._state_lock:
                self._write_enabled = True
                self._persistence_state = "ready"
                self._last_heartbeat_at = _utc(now)
            return True

    @staticmethod
    def _is_corrupt_database_error(exc: sqlite3.DatabaseError) -> bool:
        message = str(exc).lower()
        return any(
            marker in message
            for marker in (
                "not a database",
                "database disk image is malformed",
                "malformed database schema",
                "file is encrypted",
            )
        )

    def _probe_database_file(self) -> None:
        if not self.database_path.is_file() or self.database_path.stat().st_size == 0:
            return
        uri = f"file:{self.database_path.as_posix()}?mode=ro&immutable=1"
        try:
            with closing(
                sqlite3.connect(uri, uri=True, timeout=1.0, isolation_level=None)
            ) as connection:
                check = connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.DatabaseError as exc:
            if self._is_corrupt_database_error(exc):
                raise _CorruptDatabaseError from exc
            raise
        if check is None or check[0] != "ok":
            raise _CorruptDatabaseError("quick_check failed")

    def _rotate_corrupt_files(self, now: datetime) -> None:
        suffix = f".corrupt-{_utc(now).strftime('%Y%m%dT%H%M%S%fZ')}"
        for path in (
            self.database_path,
            Path(f"{self.database_path}-wal"),
            Path(f"{self.database_path}-shm"),
        ):
            if path.exists():
                os.replace(path, Path(f"{path}{suffix}"))

    def _fall_back_to_memory_only(
        self,
        reason: str,
        *,
        quick_check: Literal["ok", "failed", "not_run"] | None = None,
    ) -> None:
        with self._state_lock:
            self._write_enabled = False
            self._status = "degraded"
            self._reason = reason
            self._persistence_state = "memory_only"
            self._recovery_action = None
            if quick_check is not None:
                self._quick_check = quick_check

    def _start_writer(self) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"run-history-sqlite-{self._worker_id[:12]}"
        )
        self._writer_connection = self._executor.submit(self._open_writer_connection).result()
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name=f"run-history-writer-{self._worker_id[:12]}", daemon=True
        )
        self._writer_thread.start()

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            uri = f"file:{self.database_path.as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=1.0, isolation_level=None)
        else:
            connection = sqlite3.connect(self.database_path, timeout=1.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=1000")
        connection.execute("PRAGMA temp_store=MEMORY")
        if read_only:
            connection.execute("PRAGMA query_only=ON")
        return connection

    def _open_writer_connection(self) -> sqlite3.Connection:
        connection = self._connect()
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _migrate_v0_to_v1(self, connection: sqlite3.Connection) -> None:
        _validate_existing_v0_objects(connection)
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in _SCHEMA_STATEMENTS:
                connection.execute(statement)
            _validate_schema_shape(connection, include_migration=True)
            connection.execute(
                "INSERT INTO schema_migrations(version, checksum) VALUES (?, ?)",
                (_SCHEMA_VERSION, _SCHEMA_V1_CHECKSUM),
            )
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _validate_or_adopt_v1(self, connection: sqlite3.Connection) -> None:
        migration_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if migration_exists is None:
            _validate_schema_shape(connection, include_migration=False)
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(_MIGRATION_TABLE_SQL)
                _validate_schema_shape(connection, include_migration=True)
                connection.execute(
                    "INSERT INTO schema_migrations(version, checksum) VALUES (?, ?)",
                    (_SCHEMA_VERSION, _SCHEMA_V1_CHECKSUM),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return
        _validate_schema_shape(connection, include_migration=True)
        row = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE version = ?", (_SCHEMA_VERSION,)
        ).fetchone()
        if row is None or not hmac.compare_digest(str(row[0]), _SCHEMA_V1_CHECKSUM):
            raise _SchemaDriftError("schema checksum mismatch")

    @staticmethod
    def _read_identity(connection: sqlite3.Connection) -> StoreIdentity:
        try:
            rows = connection.execute(
                "SELECT key, value FROM registry_meta WHERE key IN (?, ?)",
                ("scope_key", "cursor_key"),
            )
        except sqlite3.DatabaseError:
            return StoreIdentity(os.urandom(32), os.urandom(32))
        values = {str(row["key"]): bytes(row["value"]) for row in rows}
        return StoreIdentity(
            values.get("scope_key", os.urandom(32)), values.get("cursor_key", os.urandom(32))
        )

    def _bootstrap_identity(self, connection: sqlite3.Connection) -> StoreIdentity:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for key in ("scope_key", "cursor_key"):
                connection.execute(
                    "INSERT OR IGNORE INTO registry_meta(key,value) VALUES (?,?)",
                    (key, os.urandom(32)),
                )
            identity = self._read_identity(connection)
            if len(identity.scope_key) != 32 or len(identity.cursor_key) != 32:
                raise sqlite3.DatabaseError("invalid registry identity")
            connection.commit()
            return identity
        except Exception:
            connection.rollback()
            raise

    def _register_boot_and_worker(
        self, connection: sqlite3.Connection, now: datetime
    ) -> None:
        now_us = _to_us(now)
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """INSERT INTO boots
                (boot_id,started_at_us,last_heartbeat_at_us,stopped_at_us,state)
                VALUES (?,?,?,?, 'starting')
                ON CONFLICT(boot_id) DO UPDATE SET
                last_heartbeat_at_us=excluded.last_heartbeat_at_us,
                stopped_at_us=NULL,state='starting'""",
                (self._boot_id, now_us, now_us, None),
            )
            connection.execute(
                """INSERT INTO workers
                (worker_id,boot_id,started_at_us,last_heartbeat_at_us,stopped_at_us,state)
                VALUES (?,?,?,?,?, 'starting')
                ON CONFLICT(worker_id) DO UPDATE SET
                boot_id=excluded.boot_id,last_heartbeat_at_us=excluded.last_heartbeat_at_us,
                stopped_at_us=NULL,state='starting'""",
                (self._worker_id, self._boot_id, now_us, now_us, None),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _mark_boot_and_worker_alive(
        self, connection: sqlite3.Connection, now: datetime
    ) -> None:
        now_us = _to_us(now)
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """UPDATE boots SET state='alive',last_heartbeat_at_us=?,stopped_at_us=NULL
                WHERE boot_id=?""",
                (now_us, self._boot_id),
            )
            connection.execute(
                """UPDATE workers SET state='alive',last_heartbeat_at_us=?,stopped_at_us=NULL
                WHERE worker_id=?""",
                (now_us, self._worker_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _serialized(
        self,
        operation: Callable[[sqlite3.Connection], Any],
        *,
        timeout_s: float | None = None,
    ) -> Any:
        executor, connection = self._executor, self._writer_connection
        if executor is None or connection is None:
            raise sqlite3.OperationalError("persistence writer unavailable")
        return executor.submit(operation, connection).result(timeout=timeout_s)

    def mark_heartbeat(self, now: datetime) -> None:
        """Refresh current boot/worker liveness without allowing failures to escape."""
        with self._state_lock:
            if self._persistence_state != "ready" or self._closed or self._shutdown_marked:
                return
        try:
            self._serialized(lambda connection: self._mark_boot_and_worker_alive(connection, now))
            with self._state_lock:
                self._last_heartbeat_at = _utc(now)
        except Exception:
            self._degrade("heartbeat_failed")

    def _recover_stale_workers_connection(
        self, connection: sqlite3.Connection, now: datetime
    ) -> int:
        now_us = _to_us(now)
        cutoff_us = _to_us(now - timedelta(seconds=self._settings.worker_stale_after_s))
        connection.execute("BEGIN IMMEDIATE")
        try:
            stale_rows = connection.execute(
                """SELECT worker_id,boot_id FROM workers
                WHERE state IN ('starting','alive') AND last_heartbeat_at_us <= ?""",
                (cutoff_us,),
            ).fetchall()
            if not stale_rows:
                connection.commit()
                return 0
            worker_ids = [str(row["worker_id"]) for row in stale_rows]
            boot_ids = sorted({str(row["boot_id"]) for row in stale_rows})
            worker_slots = ",".join("?" for _ in worker_ids)
            boot_slots = ",".join("?" for _ in boot_ids)
            connection.execute(
                f"UPDATE workers SET state='stale' WHERE worker_id IN ({worker_slots})",
                worker_ids,
            )
            connection.execute(
                f"""UPDATE boots SET state='stale'
                WHERE boot_id IN ({boot_slots}) AND state IN ('starting','alive')""",
                boot_ids,
            )
            cursor = connection.execute(
                f"""UPDATE runs SET status='interrupted',updated_at_us=?,finished_at_us=?,
                elapsed_ms=MAX(elapsed_ms,(? - started_at_us)/1000.0),
                interruption_reason='worker_lost',event_integrity='partial'
                WHERE status='running' AND worker_id IN ({worker_slots})""",
                (now_us, now_us, now_us, *worker_ids),
            )
            recovered = max(0, int(cursor.rowcount))
            connection.commit()
            return recovered
        except Exception:
            connection.rollback()
            raise

    def recover_stale_workers(self, now: datetime) -> int:
        with self._state_lock:
            if self._persistence_state != "ready" or self._closed:
                return 0
        try:
            return int(
                self._serialized(
                    lambda connection: self._recover_stale_workers_connection(connection, now)
                )
            )
        except Exception:
            self._degrade("recovery_failed")
            return 0

    def _cleanup_connection(self, connection: sqlite3.Connection, now: datetime) -> int:
        limit = min(1000, self._settings.cleanup_batch_size)
        cutoff_us = _to_us(now - timedelta(days=self._settings.retention_days))
        connection.execute("BEGIN IMMEDIATE")
        try:
            expired = [
                str(row[0])
                for row in connection.execute(
                    """SELECT run_id FROM runs
                    WHERE status IN ('completed','failed','cancelled','interrupted')
                    AND finished_at_us IS NOT NULL AND finished_at_us < ?
                    ORDER BY finished_at_us,run_id LIMIT ?""",
                    (cutoff_us, limit),
                )
            ]
            total_row = connection.execute("SELECT COUNT(*) FROM runs").fetchone()
            total = 0 if total_row is None else max(0, int(total_row[0]))
            capacity_needed = max(
                0, total - len(expired) - self._settings.max_persisted_runs
            )
            remaining = min(limit - len(expired), capacity_needed)
            run_ids = list(expired)
            if remaining:
                params: list[Any] = []
                exclusion = ""
                if run_ids:
                    exclusion = f" AND run_id NOT IN ({','.join('?' for _ in run_ids)})"
                    params.extend(run_ids)
                params.append(remaining)
                run_ids.extend(
                    str(row[0])
                    for row in connection.execute(
                        """SELECT run_id FROM runs
                        WHERE status IN ('completed','failed','cancelled','interrupted')
                        AND finished_at_us IS NOT NULL"""
                        + exclusion
                        + " ORDER BY finished_at_us,run_id LIMIT ?",
                        params,
                    )
                )
            if run_ids:
                slots = ",".join("?" for _ in run_ids)
                connection.execute(f"DELETE FROM runs WHERE run_id IN ({slots})", run_ids)
            connection.commit()
        except Exception:
            connection.rollback()
            raise

        deleted = len(run_ids)
        checkpoint_day = _utc(now).date().isoformat()
        with self._state_lock:
            self._deleted_since_checkpoint += deleted
            checkpoint_due = (
                self._last_checkpoint_day != checkpoint_day
                or self._deleted_since_checkpoint >= 10000
            )
        if checkpoint_due:
            try:
                connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            except Exception:
                self._degrade("checkpoint_failed")
            else:
                with self._state_lock:
                    self._last_checkpoint_day = checkpoint_day
                    self._deleted_since_checkpoint = 0
        return deleted

    def cleanup(self, now: datetime) -> int:
        with self._state_lock:
            if self._persistence_state != "ready" or self._closed:
                return 0
        try:
            return int(
                self._serialized(lambda connection: self._cleanup_connection(connection, now))
            )
        except Exception:
            self._degrade("cleanup_failed")
            return 0

    def _background_loop(self) -> None:
        next_cleanup = time.monotonic() + self._settings.cleanup_interval_s
        while not self._background_stop.wait(self._settings.heartbeat_interval_s):
            self.mark_heartbeat(self._now())
            if time.monotonic() >= next_cleanup:
                self.cleanup(self._now())
                next_cleanup = time.monotonic() + self._settings.cleanup_interval_s

    def start_background_tasks(self) -> None:
        with self._state_lock:
            if (
                self._persistence_state != "ready"
                or self._closed
                or self._shutdown_marked
                or (self._background_thread is not None and self._background_thread.is_alive())
            ):
                return
            self._background_stop.clear()
            self._background_thread = threading.Thread(
                target=self._background_loop,
                name=f"run-history-lifecycle-{self._worker_id[:12]}",
                daemon=True,
            )
            self._background_thread.start()

    def _mark_shutdown_connection(
        self, connection: sqlite3.Connection, now: datetime
    ) -> None:
        now_us = _to_us(now)
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """UPDATE runs SET status='interrupted',updated_at_us=?,finished_at_us=?,
                elapsed_ms=MAX(elapsed_ms,(? - started_at_us)/1000.0),
                interruption_reason='process_shutdown',event_integrity='partial'
                WHERE worker_id=? AND status='running'""",
                (now_us, now_us, now_us, self._worker_id),
            )
            connection.execute(
                """UPDATE workers SET state='stopped',last_heartbeat_at_us=?,stopped_at_us=?
                WHERE worker_id=?""",
                (now_us, now_us, self._worker_id),
            )
            connection.execute(
                """UPDATE boots SET state='stopped',last_heartbeat_at_us=?,stopped_at_us=?
                WHERE boot_id=?""",
                (now_us, now_us, self._boot_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def mark_clean_shutdown(self, now: datetime, grace_ms: int) -> None:
        deadline = time.monotonic() + max(0.0, grace_ms / 1000.0)

        def remaining() -> float:
            return max(0.0, deadline - time.monotonic())

        with self._state_lock:
            if self._shutdown_marked:
                return
            self._shutdown_marked = True
            self._accepting = False
        self._background_stop.set()
        background = self._background_thread
        if background is not None:
            try:
                background.join(timeout=remaining())
            except Exception:
                self._degrade("shutdown_background_failed")
        writer = self._writer_thread
        if writer is not None:
            self._stop_requested.set()
            try:
                writer.join(timeout=remaining())
            except Exception:
                self._degrade("shutdown_writer_failed")
            if writer.is_alive():
                self._abort_requested.set()
                try:
                    writer.join(timeout=remaining())
                except Exception:
                    self._degrade("shutdown_writer_failed")
        if (background is not None and background.is_alive()) or (
            writer is not None and writer.is_alive()
        ):
            self._degrade("shutdown_timeout")
            return
        try:
            if self._persistence_state == "ready":
                timeout_s = remaining()
                if timeout_s <= 0:
                    self._degrade("shutdown_timeout")
                    return
                self._serialized(
                    lambda connection: self._mark_shutdown_connection(connection, now),
                    timeout_s=timeout_s,
                )
        except TimeoutError:
            self._degrade("shutdown_timeout")
        except Exception:
            self._degrade("shutdown_mark_failed")

    def enqueue(self, mutation: PersistenceMutation | RegistryMutation) -> bool:
        """Offer without waiting or touching SQLite; every failure returns False."""
        try:
            if isinstance(mutation, RegistryMutation):
                mutation = PersistenceMutation.from_registry(mutation)
            mutation = self._normalized_mutation(mutation)
            with self._drained:
                if not self._write_enabled or not self._accepting or self._closed:
                    return False
                if not self._capacity.acquire(blocking=False):
                    self._dropped_mutations += 1
                    self._degrade("queue_full")
                    get_metrics().incr("run_history.mutations_dropped")
                    return False
                self._pending_count += 1
                try:
                    self._queue.put_nowait(mutation)
                except queue.Full:
                    self._pending_count -= 1
                    self._capacity.release()
                    self._dropped_mutations += 1
                    self._degrade("queue_full")
                    get_metrics().incr("run_history.mutations_dropped")
                    return False
            get_metrics().incr("run_history.mutations_enqueued")
            return True
        except Exception:
            get_metrics().incr("run_history.mutations_rejected")
            return False

    @staticmethod
    def _normalized_mutation(mutation: PersistenceMutation) -> PersistenceMutation:
        event_json = _normalize_json_text(mutation.event_json)
        topology_json = _normalize_json_text(mutation.topology_json)
        event = _load_json(event_json)
        topology = _load_json(topology_json)
        if not isinstance(event, dict):
            raise ValueError("event JSON must be an object")
        if not isinstance(topology, dict):
            raise ValueError("topology JSON must be an object")
        if event.get("run_id") != mutation.summary.run_id:
            raise ValueError("event run_id mismatch")
        event_seq = _strict_positive_seq(event.get("seq"))
        mutation_seq = _strict_positive_seq(mutation.event_seq)
        if event_seq != mutation_seq:
            raise ValueError("event seq mismatch")
        if str(event.get("type", "")) != mutation.event_type:
            raise ValueError("event type mismatch")
        _finite_float(event.get("elapsed_ms", 0.0))
        _finite_float(mutation.summary.elapsed_ms)
        if mutation.duration_ms is not None:
            _finite_float(mutation.duration_ms)
        return replace(
            mutation,
            event_seq=mutation_seq,
            event_json=event_json,
            topology_json=topology_json,
        )

    def _writer_loop(self) -> None:
        batch: list[PersistenceMutation] = []
        while True:
            if self._abort_requested.is_set():
                self._discard_batch(batch)
                self._discard_queued()
                return
            if not batch:
                if self._stop_requested.is_set() and self._queue.empty():
                    return
                try:
                    batch.append(self._queue.get(timeout=self._settings.writer_flush_ms / 1000.0))
                except queue.Empty:
                    continue
                deadline = time.monotonic() + self._settings.writer_flush_ms / 1000.0
                while len(batch) < self._settings.writer_batch_size:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        batch.append(self._queue.get(timeout=remaining))
                    except queue.Empty:
                        break
            result = self._write_batch_with_retry(batch)
            if result is not None:
                for reason in result.reasons:
                    self._degrade(reason)
                self._mark_batch_durable(batch, result.durable_mutations)
                batch = []
                continue
            self._degrade("write_failed")
            self._abort_requested.wait(LOCK_BACKOFF_SECONDS[-1])

    def _write_batch_with_retry(self, batch: list[PersistenceMutation]) -> _BatchWriteResult | None:
        executor, connection = self._executor, self._writer_connection
        if executor is None or connection is None:
            return None
        delays = (0.0, *LOCK_BACKOFF_SECONDS)
        for attempt, delay in enumerate(delays):
            if self._abort_requested.is_set():
                return None
            if delay:
                time.sleep(delay)
            try:
                result = executor.submit(self._write_batch_once, connection, batch).result()
                if result is None:
                    return _BatchWriteResult(tuple(batch), ())
                return result
            except sqlite3.OperationalError as exc:
                if not any(marker in str(exc).lower() for marker in ("locked", "busy")):
                    return None
                if attempt == len(delays) - 1:
                    return None
            except Exception:
                return None
        return None

    def _write_batch_once(
        self, connection: sqlite3.Connection, batch: list[PersistenceMutation]
    ) -> _BatchWriteResult:
        durable: list[PersistenceMutation] = []
        reasons: set[str] = set()
        connection.execute("BEGIN IMMEDIATE")
        try:
            for mutation in batch:
                summary = mutation.summary
                existing_run = connection.execute(
                    """SELECT tenant_scope,topology_id,topology_revision,executor,topology_json
                    FROM runs WHERE run_id=?""",
                    (summary.run_id,),
                ).fetchone()
                if existing_run is not None:
                    if str(existing_run["tenant_scope"]) != mutation.tenant_scope:
                        reasons.add("tenant_scope_conflict")
                        continue
                    topology_mismatch = (
                        str(existing_run["topology_id"]) != summary.topology_id
                        or str(existing_run["topology_revision"]) != summary.topology_revision
                        or str(existing_run["executor"]) != summary.executor
                        or not hmac.compare_digest(
                            hashlib.sha256(str(existing_run["topology_json"]).encode()).digest(),
                            hashlib.sha256(mutation.topology_json.encode()).digest(),
                        )
                    )
                    if topology_mismatch:
                        connection.execute(
                            "UPDATE runs SET event_integrity='partial' WHERE run_id=?",
                            (summary.run_id,),
                        )
                        reasons.add("topology_mismatch")
                        continue
                existing_event = connection.execute(
                    "SELECT event_json FROM run_events WHERE run_id=? AND seq=?",
                    (summary.run_id, mutation.event_seq),
                ).fetchone()
                if existing_event is not None and not hmac.compare_digest(
                    hashlib.sha256(str(existing_event["event_json"]).encode()).digest(),
                    hashlib.sha256(mutation.event_json.encode()).digest(),
                ):
                    connection.execute(
                        "UPDATE runs SET event_integrity='partial' WHERE run_id=?",
                        (summary.run_id,),
                    )
                    reasons.add("event_checksum_conflict")
                    continue
                connection.execute(_RUN_UPSERT, self._run_values(mutation))
                if existing_event is None:
                    event = _load_json(mutation.event_json)
                    connection.execute(
                        """INSERT INTO run_events
                        (run_id,seq,event_type,node_id,attempt,occurred_at_us,
                         elapsed_ms,duration_ms,event_json) VALUES (?,?,?,?,?,?,?,?,?)""",
                        (
                            summary.run_id,
                            mutation.event_seq,
                            mutation.event_type,
                            mutation.node_id,
                            mutation.attempt,
                            mutation.occurred_at_us,
                            _finite_float(event["elapsed_ms"]),
                            mutation.duration_ms,
                            mutation.event_json,
                        ),
                    )
                durable.append(mutation)
            connection.commit()
            return _BatchWriteResult(tuple(durable), tuple(sorted(reasons)))
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _run_values(mutation: PersistenceMutation) -> tuple[Any, ...]:
        summary = mutation.summary
        return (
            summary.run_id,
            mutation.tenant_scope,
            summary.boot_id,
            summary.worker_id,
            summary.status,
            summary.outcome,
            _to_us(summary.started_at),
            _to_us(summary.updated_at),
            None if summary.finished_at is None else _to_us(summary.finished_at),
            _finite_float(summary.elapsed_ms),
            summary.topology_id,
            summary.topology_revision,
            summary.executor,
            mutation.topology_json,
            summary.last_seq,
            summary.event_count,
            summary.earliest_available_seq,
            _compact_json(summary.current_node_ids),
            _compact_json(summary.failed_node_ids),
            summary.route,
            summary.degraded_count,
            summary.retry_count,
            summary.event_integrity,
            summary.interruption_reason,
            summary.query_fingerprint,
        )

    def _mark_batch_durable(
        self,
        batch: Sequence[PersistenceMutation],
        durable_mutations: Sequence[PersistenceMutation] | None = None,
    ) -> None:
        durable = batch if durable_mutations is None else durable_mutations
        committed_at = _utc(self._now())
        source = durable or batch
        source_time_us = max(
            (
                max(_to_us(mutation.summary.updated_at), mutation.occurred_at_us)
                for mutation in source
            ),
            default=None,
        )
        with self._drained:
            self._last_commit_at = committed_at
            self._commit_lag_ms = (
                None
                if source_time_us is None
                else max(0.0, (_to_us(committed_at) - source_time_us) / 1000.0)
            )
            for mutation in durable:
                key = (mutation.tenant_scope, mutation.summary.run_id)
                watermark = max(mutation.event_seq, self._recent_watermarks.get(key, 0))
                self._recent_watermarks[key] = watermark
                self._recent_watermarks.move_to_end(key)
                while len(self._recent_watermarks) > _DURABLE_WATERMARK_CACHE_CAP:
                    self._recent_watermarks.popitem(last=False)
            self._release_capacity_locked(len(batch))
        get_metrics().incr("run_history.mutations_durable", value=float(len(durable)))

    def _discard_batch(self, batch: Sequence[PersistenceMutation]) -> None:
        with self._drained:
            self._dropped_mutations += len(batch)
            self._release_capacity_locked(len(batch))

    def _discard_queued(self) -> None:
        count = 0
        while True:
            try:
                self._queue.get_nowait()
                count += 1
            except queue.Empty:
                break
        with self._drained:
            self._dropped_mutations += count
            self._release_capacity_locked(count)

    def _release_capacity_locked(self, count: int) -> None:
        for _ in range(count):
            self._capacity.release()
        self._pending_count = max(0, self._pending_count - count)
        if self._pending_count == 0:
            self._drained.notify_all()

    def _degrade(self, reason: str) -> None:
        with self._state_lock:
            if self._status != "disabled":
                self._status = "degraded"
                self._reason = reason

    def flush_for_test(self, timeout_s: float = 3.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_s)
        with self._drained:
            while self._pending_count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._drained.wait(remaining)
            return True

    def durable_watermark(self, tenant_scope: str, run_id: str) -> int:
        key = (tenant_scope, run_id)
        with self._state_lock:
            local = self._recent_watermarks.get(key)
            if local is not None:
                self._recent_watermarks.move_to_end(key)
                return local
        try:
            with closing(self._read_connection()) as connection:
                row = connection.execute(
                    "SELECT last_seq FROM runs WHERE tenant_scope=? AND run_id=?",
                    (tenant_scope, run_id),
                ).fetchone()
        except Exception as exc:
            self._degrade("read_failed")
            raise RunHistoryReadError() from exc
        if row is None:
            return 0
        watermark = int(row[0])
        with self._state_lock:
            self._recent_watermarks[key] = watermark
            self._recent_watermarks.move_to_end(key)
            while len(self._recent_watermarks) > _DURABLE_WATERMARK_CACHE_CAP:
                self._recent_watermarks.popitem(last=False)
        return watermark

    def list_runs(
        self, tenant_scope: str, query: RunListQuery, *, before: tuple[datetime, str] | None = None
    ) -> list[RunSummary]:
        where = ["tenant_scope = ?"]
        params: list[Any] = [tenant_scope]
        if query.statuses:
            where.append(f"status IN ({','.join('?' for _ in query.statuses)})")
            params.extend(query.statuses)
        if query.view == "active":
            where.append("status='running'")
        elif query.view == "errors":
            where.append("status IN ('failed','interrupted')")
        if query.started_after is not None:
            where.append("started_at_us >= ?")
            params.append(_to_us(query.started_after))
        if query.started_before is not None:
            where.append("started_at_us < ?")
            params.append(_to_us(query.started_before))
        if query.fingerprint is not None:
            where.append("query_fingerprint = ?")
            params.append(query.fingerprint)
        if before is not None:
            before_us = _to_us(before[0])
            where.append("(started_at_us < ? OR (started_at_us = ? AND run_id < ?))")
            params.extend((before_us, before_us, before[1]))
        as_of = _utc(query.as_of or self._now())
        where.append("started_at_us <= ?")
        params.append(_to_us(as_of))
        slow_ms = query.slow_ms or self._settings.slow_threshold_ms
        if query.view == "slow":
            where.append("(elapsed_ms >= ? OR (status='running' AND (? - started_at_us) >= ?))")
            params.extend((slow_ms, _to_us(as_of), slow_ms * 1000))
        elif query.view == "stuck":
            where.append("status='running' AND (? - updated_at_us) >= ?")
            params.extend((_to_us(as_of), self._settings.stuck_after_s * 1_000_000))
            where.append(
                "EXISTS (SELECT 1 FROM workers WHERE workers.worker_id = runs.worker_id "
                "AND workers.state='alive' AND workers.last_heartbeat_at_us >= ?)"
            )
            params.append(
                _to_us(as_of) - self._settings.worker_stale_after_s * 1_000_000
            )
        internal_max = self._settings.api_max_page_size + 1
        limit = max(1, min(query.limit, internal_max))
        sql = f"SELECT * FROM runs WHERE {' AND '.join(where)} "
        sql += "ORDER BY started_at_us DESC, run_id DESC LIMIT ?"
        params.append(limit)
        try:
            with closing(self._read_connection()) as connection:
                rows = connection.execute(sql, params).fetchall()
            return [self._row_to_summary(row, as_of=as_of, slow_ms=slow_ms) for row in rows]
        except Exception as exc:
            self._degrade("read_failed")
            raise RunHistoryReadError() from exc

    def get_run(self, tenant_scope: str, run_id: str) -> StoredRun | None:
        try:
            with closing(self._read_connection()) as connection:
                row = connection.execute(
                    "SELECT * FROM runs WHERE tenant_scope=? AND run_id=?",
                    (tenant_scope, run_id),
                ).fetchone()
            if row is None:
                return None
            topology = _load_json(str(row["topology_json"]))
            if not isinstance(topology, dict):
                return None
            return StoredRun(
                self._row_to_summary(row, as_of=_utc(self._now())), MappingProxyType(topology)
            )
        except Exception as exc:
            self._degrade("read_failed")
            raise RunHistoryReadError() from exc

    def get_events(
        self, tenant_scope: str, run_id: str, after_seq: int, limit: int
    ) -> StoredEventSlice | None:
        bounded_limit = max(1, min(limit, self._settings.events_max_page_size))
        try:
            with closing(self._read_connection()) as connection:
                run = connection.execute(
                    """SELECT status,last_seq,earliest_available_seq,event_integrity
                    FROM runs WHERE tenant_scope=? AND run_id=?""",
                    (tenant_scope, run_id),
                ).fetchone()
                if run is None:
                    return None
                rows = connection.execute(
                    """SELECT event_json FROM run_events WHERE run_id=? AND seq>?
                    ORDER BY seq ASC LIMIT ?""",
                    (run_id, max(0, after_seq), bounded_limit),
                ).fetchall()
            events = tuple(MappingProxyType(_load_json(str(row["event_json"]))) for row in rows)
            next_after = int(events[-1]["seq"]) if events else max(0, after_seq)
            return StoredEventSlice(
                run_id=run_id,
                events=events,
                after_seq=next_after,
                latest_seq=int(run["last_seq"]),
                terminal=str(run["status"]) != "running",
                earliest_available_seq=int(run["earliest_available_seq"]),
                persistence_status=(
                    "durable" if run["event_integrity"] == "complete" else "partial"
                ),
            )
        except Exception as exc:
            self._degrade("read_failed")
            raise RunHistoryReadError() from exc

    def _read_connection(self) -> sqlite3.Connection:
        return self._connect(read_only=True)

    def _row_to_summary(
        self, row: sqlite3.Row, *, as_of: datetime, slow_ms: int | None = None
    ) -> RunSummary:
        started_at = _from_us(int(row["started_at_us"]))
        status = str(row["status"])
        elapsed = float(row["elapsed_ms"])
        if status == "running":
            elapsed = max(elapsed, max(0.0, (as_of - started_at).total_seconds() * 1000.0))
        attention: list[str] = []
        if status in {"failed", "interrupted"}:
            attention.append("error")
        if status == "interrupted":
            attention.append("interrupted")
        if status == "cancelled":
            attention.append("cancelled")
        if elapsed >= (slow_ms or self._settings.slow_threshold_ms):
            attention.append("slow")
        return RunSummary(
            schema_version=1,
            run_id=str(row["run_id"]),
            status=status,
            outcome=str(row["outcome"]),  # type: ignore[arg-type]
            started_at=started_at,
            updated_at=_from_us(int(row["updated_at_us"])),
            finished_at=None
            if row["finished_at_us"] is None
            else _from_us(int(row["finished_at_us"])),
            elapsed_ms=elapsed,
            boot_id=str(row["boot_id"]),
            worker_id=str(row["worker_id"]),
            topology_id=str(row["topology_id"]),
            topology_revision=str(row["topology_revision"]),
            executor=str(row["executor"]),
            last_seq=int(row["last_seq"]),
            event_count=int(row["event_count"]),
            earliest_available_seq=int(row["earliest_available_seq"]),
            current_node_ids=tuple(str(item) for item in _load_json(row["current_node_ids_json"])),
            failed_node_ids=tuple(str(item) for item in _load_json(row["failed_node_ids_json"])),
            route=None if row["route"] is None else str(row["route"]),
            degraded_count=int(row["degraded_count"]),
            retry_count=int(row["retry_count"]),
            attention=tuple(attention),
            event_integrity=str(row["event_integrity"]),
            persistence_status="durable",  # type: ignore[arg-type]
            interruption_reason=None
            if row["interruption_reason"] is None
            else str(row["interruption_reason"]),
            query_fingerprint=None
            if row["query_fingerprint"] is None
            else str(row["query_fingerprint"]),
        )

    def _count_durable_runs(self) -> int:
        if not self.database_path.is_file():
            return 0
        try:
            with closing(self._read_connection()) as connection:
                row = connection.execute("SELECT COUNT(*) FROM runs").fetchone()
            return 0 if row is None else max(0, int(row[0]))
        except Exception:
            return 0

    def health_snapshot(self) -> StoreHealth:
        durable_run_count = self._count_durable_runs()
        with self._state_lock:
            return StoreHealth(
                status=self._status,
                write_enabled=self._write_enabled and self._accepting and not self._closed,
                reason=self._reason,
                database_filename=self.database_path.name,
                writer_queue_depth=self._pending_count,
                durable_run_count=durable_run_count,
                durable_cache_entries=len(self._recent_watermarks),
                state=self._persistence_state,
                quick_check=self._quick_check,
                recovery_action=self._recovery_action,
                last_commit_at=self._last_commit_at,
                commit_lag_ms=self._commit_lag_ms,
                dropped_mutations=self._dropped_mutations,
                last_heartbeat_at=self._last_heartbeat_at,
            )

    def _schedule_connection_cleanup(self) -> None:
        writer = self._writer_thread
        executor = self._executor
        connection = self._writer_connection
        if executor is None or connection is None:
            return
        with self._state_lock:
            existing = self._connection_cleanup_thread
            if existing is not None:
                return

            def finalize() -> None:
                try:
                    if writer is not None:
                        writer.join()
                    executor.submit(connection.close).result()
                except Exception:
                    self._degrade("close_failed")
                finally:
                    try:
                        executor.shutdown(wait=True, cancel_futures=False)
                    except Exception:
                        self._degrade("close_failed")
                    with self._state_lock:
                        if self._executor is executor:
                            self._executor = None
                        if self._writer_connection is connection:
                            self._writer_connection = None

            self._connection_cleanup_thread = threading.Thread(
                target=finalize,
                name=f"run-history-close-{self._worker_id[:12]}",
                daemon=True,
            )
            self._connection_cleanup_thread.start()

    def close(self, grace_ms: int | None = None) -> None:
        with self._state_lock:
            if self._closed:
                return
        configured = self._settings.writer_shutdown_grace_ms if grace_ms is None else grace_ms
        try:
            self.mark_clean_shutdown(self._now(), configured)
        except Exception:
            self._degrade("close_failed")
        self._schedule_connection_cleanup()
        with self._state_lock:
            self._closed = True
            self._write_enabled = False
            if self._status == "ok":
                self._reason = "closed"


__all__ = [
    "LOCK_BACKOFF_SECONDS",
    "PersistenceMutation",
    "RunHistoryReadError",
    "RunHistoryStore",
    "StoreHealth",
    "StoreIdentity",
    "StoredEventSlice",
    "StoredRun",
]
