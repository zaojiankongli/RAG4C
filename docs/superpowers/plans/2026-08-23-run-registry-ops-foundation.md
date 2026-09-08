# RAG4C Route B RunRegistry and Ops Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Repository note:** This workspace has no `.git` metadata as of 2026-08-23. Commit steps are advisory for a Git-enabled checkout; here, preserve the same task boundaries in the implementation ledger and verification evidence.

**Goal:** Persist and query the existing canonical streaming `RunEvent` facts through a failure-silent, tenant-scoped RunRegistry and expose them as accessible Visualize and Monitor operations views without changing query, cache, SSE, REST, or LangGraph behavior.

**Architecture:** `core/run_registry.py` redacts canonical events before maintaining a bounded memory projection and submitting non-blocking persistence mutations. `core/run_history_store.py` owns the independent stdlib SQLite WAL database, identity, recovery, retention, and durable reads; `server/run_ops.py` owns operator access, tenant scoping, signed cursors, response models, merged reads, and long-poll. `server/app.py` only bootstraps that runtime and adds its sink beside the existing SSE sink on the same sequencer; React consumes the API through separate DTO, buffer, projection, hook, and view modules while retaining live typed, coarse-phase, and legacy-trace fallbacks.

**Tech Stack:** Python 3.10+, Pydantic v2, FastAPI/Starlette, stdlib `sqlite3` WAL, threads/queues/conditions, pytest, Ruff, React 18, TypeScript 5.6, Vitest 3, Ant Design 5, DOM/CSS timeline rendering, Playwright browser QA.

**Spec:** `docs/superpowers/specs/2026-08-23-run-registry-ops-foundation-design.md`

## Global Constraints

- Route B consumes `core/run_events.py` facts; it never creates a second lifecycle protocol or synthetic canonical terminal event.
- `server/app.py` remains owner of `POST /api/query/stream`, immutable topology, and exactly one terminal event.
- Do not implement typed-event parity for `POST /api/query`, `rag_graph.py`, or LangGraph.
- Do not change QueryResult, answers, citations, abstention, cache, single-flight, queueing, timeout, cancellation, legacy SSE, typed SSE, or desync semantics.
- Registry, writer, SQLite, API, and cleanup failures remain failure-silent for query execution and SSE fan-out.
- Business threads perform no SQLite I/O and never wait for queue capacity; persistence submission is `put_nowait` only.
- Raw query, answer, prompt, token, ACL, tenant/dataset ID, document/chunk content or IDs, paths, embeddings, full scores, credentials, headers, stack traces, and complete exception messages never enter Registry memory, SQLite, API, cursor, metrics labels, or logs.
- Query fingerprint is absent unless `RAG4C_RUN_HISTORY_FINGERPRINT_SECRET` is configured; only the boundary-computed HMAC enters Registry.
- `ops_bearer_token` and `fingerprint_secret` come only from environment/file secret and never appear in or update through `/api/config`.
- Each Runs request resolves one tenant and applies one opaque scope in the first memory/SQL lookup; unknown and cross-scope IDs share the same 404.
- Persistence uses project-root `data/run-history.sqlite3`, never the catalog database.
- Memory, SQLite, queues, pages, long-poll clients, retention, and shutdown drain remain bounded by approved defaults.
- `interrupted` is a Registry summary state; recovery/shutdown never insert fake run terminal events.
- The frontend never substitutes demo history after auth, network, persistence, or retention failures.
- A current browser query may remain only in `RunMonitorContext`; it never enters Runs caches, event buffers, URL state, or persistent storage.
- Sources stay explicit: `server-history`, `typed-events`, `coarse-phase`, `trace-inferred`.
- Visualize remains route-lazy, renders one event page, provides DOM/table equivalents, keyboard focus, non-color cues, and reduced motion.
- Performance targets must be met without suppressing canonical events or changing business execution.

---

## File Map

### Backend

- Create `core/run_registry.py`: redaction, HMAC helpers, bounded projection, rollups, attention, conditions, failure-silent sink.
- Create `core/run_history_store.py`: schema/migration, WAL writer, watermarks, identity, heartbeat, recovery, retention, corruption handling, readers.
- Create `server/run_ops.py`: access, tenant scope, cursor, DTOs, merged service, long-poll, router, runtime lifecycle.
- Create `tests/test_run_registry.py`, `tests/test_run_history_store.py`, `tests/test_run_ops_api.py`, `tests/test_run_registry_integration.py`.
- Modify `config/settings.py:838-1073`, `core/metrics.py:148-286`, `server/app.py:55-86,455-528,958-1600,2420-2638`.
- Extend `tests/test_config_hot_reload.py`, `tests/test_sse_run_event_compat.py`, and `tests/test_run_observability_parity.py`.

### Frontend

- Create `frontend/src/types/runs.ts`, `frontend/src/api/runs.ts`, `frontend/src/api/runs.test.ts`.
- Create `frontend/src/run/serverRunProjection.ts`, `serverRunProjection.test.ts`, `runEventBuffer.ts`, `runEventBuffer.test.ts`, `useRunHistory.ts`, `useRunHistory.test.tsx`.
- Create `frontend/src/components/RunHistorySidebar.tsx`, `RunTimeline.tsx`, `RunEventTable.tsx`, `RunKnowledgePanel.tsx`, `RunOpsBanners.tsx` and adjacent tests.
- Modify `frontend/src/api/client.ts:21-126`, `run/runViewState.ts:1-32`, `run/runProjection.ts:10-330`, `run/runMonitorStore.ts:12-220`, `run/RunMonitorContext.tsx:39-146` and tests.
- Modify `frontend/src/pages/VisualizePage.tsx:1-584`, `MonitorPage.tsx:348-943`, `components/RunFlowGraph.tsx`, `styles.css`, and `App.tsx`.

### Release documentation and evidence

- Modify `README.md`, `frontend/README.md`, `docs/模块实现说明.md`, and `docs/RAG4C-Waku运行观测与评估可视化-最终实现说明-2026-08-23.md`.
- Synchronize `C:\Users\饶策\Desktop\RAG4C-Waku运行观测与评估可视化-最终实现说明-2026-08-23.md` after checksum verification.
- Create screenshots under `frontend/output/shots/run-registry-ops-foundation/`.

## Dependencies and Checkpoints

```text
1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8
                    5 -> 9 -> 10 -> 11 -> 12 -> 13 -> 14
                              7 -----------------> 12
```

- Freeze backend names after Task 5 before API queries or TypeScript DTOs diverge.
- Require streaming parity from Task 7 before editing Visualize or Monitor.
- Require Tasks 9-11 green before UI composition work.
- Start Task 14 only after every focused command in Tasks 1-13 is green.

---

### Task 1: Configuration, security helpers, and defensive redaction

**Depends on:** Existing `core/run_events.py` and the approved spec.

**Files:**
- Create: `core/run_registry.py`
- Create: `tests/test_run_registry.py`
- Modify: `config/settings.py:838-1073`
- Modify: `tests/test_config_hot_reload.py`

**Interfaces:**
- Consumes: `RunEvent` and `run_event_dict(event) -> dict[str, Any]`.
- Produces: `RunHistorySettings`, `RegistryIdentity`, `BoundRunContext`, `RedactionResult`, `derive_tenant_scope(normalized_tenant_id: str, scope_key: bytes) -> str`, `fingerprint_query(query: str, secret: bytes) -> str`, `sanitize_run_event(event: RunEvent, settings: RunHistorySettings) -> RedactionResult`.
- Contract: only allowlisted detached JSON leaves this task; no upstream event mutation or sensitive logging.

- [ ] **Step 1: Write failing settings, HMAC, and redaction tests**

```python
from config.settings import RunHistorySettings
from core.run_registry import derive_tenant_scope, fingerprint_query, sanitize_run_event


def test_approved_defaults() -> None:
    value = RunHistorySettings()
    assert (value.sqlite_path, value.memory_max_active_runs) == ("data/run-history.sqlite3", 128)
    assert (value.memory_max_recent_runs, value.memory_max_events_per_run) == (512, 1024)
    assert (value.max_event_json_bytes, value.max_topology_json_bytes) == (16384, 131072)
    assert (value.long_poll_max_ms, value.long_poll_max_clients) == (25000, 64)


def test_scope_and_fingerprint_are_domain_separated_hmacs() -> None:
    scope = derive_tenant_scope("Acme-研发", b"s" * 32)
    assert scope == derive_tenant_scope("Acme-研发", b"s" * 32)
    assert scope != derive_tenant_scope("Acme-研发", b"t" * 32)
    assert "Acme" not in scope
    assert fingerprint_query("  Ａ\u3000B\nC  ", b"f" * 32) == fingerprint_query("A B C", b"f" * 32)


def test_unknown_sensitive_attribute_rejects_registry_copy(run_started_event) -> None:
    hostile = run_started_event.model_copy(update={"attributes": {"query": "SENTINEL_QUERY"}})
    result = sanitize_run_event(hostile, RunHistorySettings())
    assert result.event is None
    assert result.reason == "attributes_unknown_key"
    assert "SENTINEL_QUERY" not in repr(result)
```

Also add named tests for: invalid bounds; secrets as `SecretStr`; secrets hidden/unwritable in config API; envelope, attributes, error, and topology allowlists; 96/128 code-point rules; finite/non-negative numbers; depth 4; array 32; object 64; 16 KiB event and 128 KiB topology caps; revision mismatch; safe partial `run.started` fallback; and sentinels for query, answer, prompt, ACL, chunk, path, key, token, and exception message.

- [ ] **Step 2: Run RED**

```powershell
pytest tests/test_run_registry.py tests/test_config_hot_reload.py -q
```

Expected: collection fails because `RunHistorySettings` and `core.run_registry` do not exist.

- [ ] **Step 3: Implement exact settings and redaction boundary**

Add all approved defaults from spec section 6 with Pydantic `Field` bounds; add `Settings.run_history`. Use explicit config visibility/update checks so `ops_bearer_token` and `fingerprint_secret` are neither returned nor writable.

```python
class RunHistorySettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    enabled: bool = True
    persistence_enabled: bool = True
    sqlite_path: str = "data/run-history.sqlite3"
    memory_max_active_runs: int = Field(default=128, ge=1, le=4096)
    memory_max_recent_runs: int = Field(default=512, ge=1, le=100000)
    memory_max_events_per_run: int = Field(default=1024, ge=2, le=100000)
    memory_max_events_total: int = Field(default=32768, ge=2, le=1000000)
    memory_terminal_ttl_s: int = Field(default=21600, ge=60, le=604800)
    max_event_json_bytes: int = Field(default=16384, ge=1024, le=1048576)
    max_topology_json_bytes: int = Field(default=131072, ge=4096, le=4194304)
    writer_queue_capacity: int = Field(default=8192, ge=1, le=1000000)
    writer_batch_size: int = Field(default=64, ge=1, le=10000)
    writer_flush_ms: int = Field(default=100, ge=1, le=60000)
    writer_shutdown_grace_ms: int = Field(default=2000, ge=0, le=60000)
    retention_days: int = Field(default=30, ge=1, le=3650)
    max_persisted_runs: int = Field(default=100000, ge=1, le=10000000)
    cleanup_interval_s: int = Field(default=600, ge=10, le=86400)
    cleanup_batch_size: int = Field(default=1000, ge=1, le=100000)
    heartbeat_interval_s: int = Field(default=5, ge=1, le=300)
    worker_stale_after_s: int = Field(default=30, ge=2, le=3600)
    stuck_after_s: int = Field(default=300, ge=1, le=86400)
    slow_threshold_ms: int = Field(default=30000, ge=1000, le=3600000)
    api_default_page_size: int = Field(default=50, ge=1, le=100)
    api_max_page_size: int = Field(default=100, ge=1, le=100)
    events_max_page_size: int = Field(default=500, ge=1, le=500)
    long_poll_max_ms: int = Field(default=25000, ge=0, le=25000)
    long_poll_max_clients: int = Field(default=64, ge=1, le=4096)
    cursor_ttl_s: int = Field(default=3600, ge=60, le=86400)
    ops_bearer_token: SecretStr | None = None
    fingerprint_secret: SecretStr | None = None

    @model_validator(mode="after")
    def validate_related_bounds(self) -> "RunHistorySettings":
        if self.api_default_page_size > self.api_max_page_size:
            raise ValueError("api_default_page_size exceeds api_max_page_size")
        if self.writer_batch_size > self.writer_queue_capacity:
            raise ValueError("writer_batch_size exceeds writer_queue_capacity")
        if self.worker_stale_after_s <= self.heartbeat_interval_s:
            raise ValueError("worker_stale_after_s must exceed heartbeat_interval_s")
        return self
```


```python
@dataclass(frozen=True, slots=True)
class RegistryIdentity:
    boot_id: str
    worker_id: str
    scope_key: bytes
    cursor_key: bytes
    tenant_scope_stability: Literal["installation", "boot"]

@dataclass(frozen=True, slots=True)
class BoundRunContext:
    tenant_scope: str
    query_fingerprint: str | None

@dataclass(frozen=True, slots=True)
class RedactionResult:
    event: dict[str, Any] | None
    reason: str | None
    partial_started_envelope: dict[str, Any] | None = None


def derive_tenant_scope(normalized_tenant_id: str, scope_key: bytes) -> str:
    digest = hmac.new(scope_key, b"tenant\0" + normalized_tenant_id.encode(), hashlib.sha256).digest()[:18]
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def fingerprint_query(query: str, secret: bytes) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", query).strip().split())
    digest = hmac.new(secret, b"query\0" + normalized.encode(), hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
```

Implement redaction in this fixed order: detached `run_event_dict` copy, envelope allowlist, nested allowlists, scalar/collection/depth validation, canonical compact JSON with `allow_nan=False`, byte caps. Unknown keys reject; overlong safe strings become exactly `[redacted:length]`; topology is accepted or rejected as one revision-consistent unit.

- [ ] **Step 4: Run GREEN and lint**

```powershell
pytest tests/test_run_registry.py tests/test_config_hot_reload.py -q
ruff check core/run_registry.py config/settings.py tests/test_run_registry.py tests/test_config_hot_reload.py
```

- [ ] **Step 5: Advisory commit**

```powershell
git add config/settings.py core/run_registry.py tests/test_run_registry.py tests/test_config_hot_reload.py
git commit -m "feat: add run registry privacy boundary"
```

---

### Task 2: Bounded in-memory RunRegistry and failure-silent sink

**Depends on:** Task 1 types and redactor.

**Files:**
- Modify: `core/run_registry.py`
- Modify: `tests/test_run_registry.py`

**Interfaces:**
- Produces `RunSummary`, `RunDetail`, `RunEventSlice`, `RunListQuery`, `RunRegistry`, `RunRegistrySink`; `bound_sink(tenant_scope: str, query_fingerprint: str | None = None) -> RunRegistrySink`; `list_runs(query: RunListQuery) -> list[RunSummary]`; `get_detail(tenant_scope: str, run_id: str) -> RunDetail | None`; `get_events(tenant_scope: str, run_id: str, after_seq: int, limit: int) -> RunEventSlice | None`; `wait_for_change(run_id: str, version: int, timeout_s: float) -> int`.

- [ ] **Step 1: Write failing lifecycle, capacity, integrity, rollup, and sink tests**

```python
def test_terminal_is_immutable_and_late_event_is_ignored(registry, completed_events):
    sink = registry.bound_sink("scope-a", "fp-a")
    for event in completed_events:
        sink(event)
    before = registry.get_detail("scope-a", completed_events[0].run_id)
    sink(completed_events[-2].model_copy(update={"seq": 999}))
    assert registry.get_detail("scope-a", completed_events[0].run_id) == before
    assert before.summary.status == "completed"


def test_active_capacity_drops_registry_copy_only(one_slot_registry, two_started_events):
    for event in two_started_events:
        one_slot_registry.bound_sink("scope-a")(event)
    assert one_slot_registry.get_detail("scope-a", two_started_events[0].run_id) is not None
    assert one_slot_registry.get_detail("scope-a", two_started_events[1].run_id) is None
    assert one_slot_registry.health_snapshot().memory.dropped_runs == 1


def test_bound_sink_catches_registry_exception(monkeypatch, registry, run_started_event):
    monkeypatch.setattr(registry, "_accept", lambda context, event: (_ for _ in ()).throw(RuntimeError("boom")))
    registry.bound_sink("scope-a")(run_started_event)
    assert registry.health_snapshot().sink_errors == 1
```

Also test unseen events; same/different duplicate seq; gaps; run/topology mismatch; terminal LRU and 6-hour TTL; active non-eviction; per-run first-plus-tail ring; global cap terminal-first eviction; stable 32-item node lists; rollup attempts/durations/reasons; slow/stuck/errors/cancelled derivation; scope-first lookup; condition wakeup; safe log fields; and P95/P99 sink budget without persistence I/O.

- [ ] **Step 2: Run RED**

```powershell
pytest tests/test_run_registry.py -q
```

Expected: missing Registry projection and sink behavior.

- [ ] **Step 3: Implement state and capacity transitions**

```python
RunStatus = Literal["running", "completed", "failed", "cancelled", "interrupted"]
EventIntegrity = Literal["complete", "partial", "unknown"]
PersistenceStatus = Literal["pending", "durable", "partial", "memory_only", "unavailable"]

@dataclass(frozen=True, slots=True)
class RunSummary:
    schema_version: Literal[1]
    run_id: str
    status: RunStatus
    outcome: Literal["answered", "abstained", "unknown"]
    started_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    elapsed_ms: float
    boot_id: str
    worker_id: str
    topology_id: str
    topology_revision: str
    executor: str
    last_seq: int
    event_count: int
    earliest_available_seq: int
    current_node_ids: Sequence[str]
    failed_node_ids: Sequence[str]
    route: str | None
    degraded_count: int
    retry_count: int
    attention: Sequence[str]
    event_integrity: EventIntegrity
    persistence_status: PersistenceStatus
    interruption_reason: str | None
    query_fingerprint: str | None
```

Use one short-held `threading.RLock` with internal mutable projections, ordered recent LRU, first-plus-tail event deques, topology snapshots, rollups, and condition versions. Serialize/redact before locking; build response copies and enqueue after unlocking. `RunRegistrySink.__call__` catches all ordinary exceptions, increments `run_registry.sink_errors`, logs only short IDs/counts/status/exception type, and never raises.

- [ ] **Step 4: Run GREEN with kernel regression**

```powershell
pytest tests/test_run_registry.py tests/test_run_events.py -q
ruff check core/run_registry.py tests/test_run_registry.py
```

- [ ] **Step 5: Advisory commit**

```powershell
git add core/run_registry.py tests/test_run_registry.py
git commit -m "feat: add bounded in-memory run registry"
```

---

### Task 3: SQLite schema v1, migration, non-blocking writer, and durable reads

**Depends on:** Task 2 mutation and summary contracts.

**Files:**
- Create: `core/run_history_store.py`
- Create: `tests/test_run_history_store.py`

**Interfaces:**
- Produces `PersistenceMutation`, `StoreIdentity`, `StoreHealth`, `StoredRun`, `StoredEventSlice`; `RunHistoryStore.open(settings, boot_id, worker_id, now=utcnow) -> RunHistoryStore`; `enqueue(mutation) -> bool`; `list_runs(tenant_scope, query) -> list[RunSummary]`; `get_run(tenant_scope, run_id) -> StoredRun | None`; `get_events(tenant_scope, run_id, after_seq, limit) -> StoredEventSlice | None`; `close(grace_ms) -> None`.
- Contract: one writer connection belongs to the writer thread; readers use separate short-lived connections; enqueue uses `put_nowait` only.

- [ ] **Step 1: Write failing schema, WAL, writer, idempotency, and disk-privacy tests**

```python
def test_schema_v1_is_independent_wal(tmp_path, settings_factory):
    store = RunHistoryStore.open(
        settings_factory(sqlite_path=str(tmp_path / "run-history.sqlite3")),
        boot_id="boot-a",
        worker_id="worker-a",
    )
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        names = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"registry_meta", "boots", "workers", "runs", "run_events"} <= names


def test_conflicting_duplicate_event_preserves_first_and_marks_partial(store, mutation_factory):
    first = mutation_factory(seq=4, outcome="first")
    second = mutation_factory(seq=4, outcome="second")
    assert store.enqueue(first) and store.enqueue(second)
    store.flush_for_test()
    page = store.get_events("scope-a", first.run_id, 0, 20)
    assert page.events[3]["attributes"]["outcome"] == "first"
    assert store.get_run("scope-a", first.run_id).summary.event_integrity == "partial"


def test_sensitive_sentinels_are_absent_from_database_bytes(store, complete_mutations):
    for mutation in complete_mutations:
        assert store.enqueue(mutation)
    store.flush_for_test()
    raw = store.database_path.read_bytes()
    for value in (b"SENTINEL_QUERY", b"SENTINEL_ANSWER", b"SENTINEL_TOKEN", b"SENTINEL_STACK"):
        assert value not in raw
```

Also test 32-byte meta keys shared by two workers; 64/100 ms batching; locked retries at 25/50/100/200/400 ms; queue-full non-blocking return; durable watermark; idempotent upsert; parameterized scope-first reads; restart reads; two-worker concurrent WAL reads/writes; atomic v0-to-v1; schema-too-new write disable; 2000 ms drain; compact UTF-8 JSON; and NaN rejection.

- [ ] **Step 2: Run RED**

```powershell
pytest tests/test_run_history_store.py -q
```

Expected: collection fails because `core.run_history_store` does not exist.

- [ ] **Step 3: Implement schema and writer**

Use writer PRAGMAs `journal_mode=WAL` and `synchronous=NORMAL`; all connections use `foreign_keys=ON`, `busy_timeout=1000`, `temp_store=MEMORY`. Create the exact spec v1 tables and checks, then these indexes and only then set `user_version=1`:

```sql
CREATE TABLE registry_meta (key TEXT PRIMARY KEY, value BLOB NOT NULL);
CREATE TABLE boots (
    boot_id TEXT PRIMARY KEY, started_at_us INTEGER NOT NULL,
    last_heartbeat_at_us INTEGER NOT NULL, stopped_at_us INTEGER,
    state TEXT NOT NULL CHECK (state IN ('starting','alive','stopped','stale'))
);
CREATE TABLE workers (
    worker_id TEXT PRIMARY KEY, boot_id TEXT NOT NULL REFERENCES boots(boot_id),
    started_at_us INTEGER NOT NULL, last_heartbeat_at_us INTEGER NOT NULL,
    stopped_at_us INTEGER,
    state TEXT NOT NULL CHECK (state IN ('starting','alive','stopped','stale'))
);
CREATE TABLE runs (
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
    interruption_reason TEXT, query_fingerprint TEXT
);
CREATE TABLE run_events (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, event_type TEXT NOT NULL, node_id TEXT, attempt INTEGER,
    occurred_at_us INTEGER NOT NULL, elapsed_ms REAL NOT NULL, duration_ms REAL,
    event_json TEXT NOT NULL, PRIMARY KEY (run_id, seq)
);
```


```sql
CREATE INDEX idx_runs_scope_started ON runs(tenant_scope, started_at_us DESC, run_id DESC);
CREATE INDEX idx_runs_scope_status_started ON runs(tenant_scope, status, started_at_us DESC, run_id DESC);
CREATE INDEX idx_runs_worker_status ON runs(worker_id, status, updated_at_us);
CREATE INDEX idx_run_events_run_seq ON run_events(run_id, seq);
CREATE INDEX idx_workers_heartbeat ON workers(state, last_heartbeat_at_us);
```

```python
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

def enqueue(self, mutation: PersistenceMutation) -> bool:
    try:
        self._queue.put_nowait(mutation)
    except queue.Full:
        self._metrics.incr("run_history.mutations_dropped")
        return False
    self._metrics.incr("run_history.mutations_enqueued")
    return True
```

Gather at most `writer_batch_size` or `writer_flush_ms`, retry one preserved batch with the approved delays, content-check duplicate primary keys, update durable watermarks after commit, and expose only filename plus low-cardinality health reasons.

- [ ] **Step 4: Run GREEN, lint, and repeat concurrency**

```powershell
pytest tests/test_run_history_store.py -q
1..3 | ForEach-Object { pytest tests/test_run_history_store.py -q -k "two_workers or locked"; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
ruff check core/run_history_store.py tests/test_run_history_store.py
```

- [ ] **Step 5: Advisory commit**

```powershell
git add core/run_history_store.py tests/test_run_history_store.py
git commit -m "feat: persist run history in sqlite wal"
```

---

### Task 4: Boot heartbeat, restart recovery, retention, and corruption handling

**Depends on:** Task 3 store lifecycle.

**Files:**
- Modify: `core/run_history_store.py`
- Modify: `tests/test_run_history_store.py`

**Interfaces:**
- Produces `start_background_tasks() -> None`, `mark_heartbeat(now) -> None`, `recover_stale_workers(now) -> int`, `cleanup(now) -> int`, `mark_clean_shutdown(now, grace_ms) -> None` and safe health `recovery_action`/`quick_check`/`state`.

- [ ] **Step 1: Write failing recovery and retention tests**

```python
def test_stale_worker_becomes_interrupted_without_fake_terminal(store_factory, seeded_running_db):
    now = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)
    store = store_factory(existing=seeded_running_db, now=now)
    assert store.recover_stale_workers(now) == 1
    run = store.get_run("scope-a", "run-stale")
    page = store.get_events("scope-a", "run-stale", 0, 500)
    assert (run.summary.status, run.summary.interruption_reason) == ("interrupted", "worker_lost")
    assert run.summary.event_integrity == "partial"
    assert all(e["type"] not in {"run.completed", "run.failed", "run.cancelled"} for e in page.events)


def test_retention_never_deletes_running(store, frozen_now):
    store.cleanup(frozen_now)
    assert store.get_run("scope-a", "old-running") is not None
    assert store.get_run("scope-a", "old-terminal") is None
```

Also test starting/alive/stopped transitions; 5-second heartbeat; 30-second stale cutoff; no boot-to-installation hot switch; clean shutdown `process_shutdown`; failure-silent close; 30-day and 100000-run rules; 1000-row transaction cap; cascade; no VACUUM; passive checkpoint; quick-check rotation of database/WAL/SHM; rotation failure memory-only; and schema-too-new safe reason.

- [ ] **Step 2: Run RED**

```powershell
pytest tests/test_run_history_store.py -q -k "heartbeat or stale or recovery or retention or corrupt or shutdown"
```

- [ ] **Step 3: Implement deterministic lifecycle**

```python
def _bootstrap(self) -> StoreIdentity:
    self._open_writer_connection()
    self._run_quick_check_or_rotate()
    self._migrate_to_supported_version()
    scope_key = self._get_or_create_meta_bytes("scope_key", 32)
    cursor_key = self._get_or_create_meta_bytes("cursor_key", 32)
    self._register_boot_and_worker("starting")
    self.recover_stale_workers(self._now())
    self._mark_boot_and_worker_alive(self._now())
    return StoreIdentity(scope_key=scope_key, cursor_key=cursor_key)
```

The heartbeat daemon uses `heartbeat_interval_s`. Recovery updates stale-worker running summaries in one transaction to `interrupted`, current recovery time, `worker_lost`, and partial integrity. Shutdown stops enqueues, drains for the grace, changes unfinished current-worker summaries to `process_shutdown`, then marks worker/boot stopped. Corrupt rotation uses an atomic UTC `.corrupt-*` suffix for DB/WAL/SHM, retains backups, rebuilds v1, and keeps health degraded.

- [ ] **Step 4: Run GREEN and checkpoint foundation**

```powershell
pytest tests/test_run_registry.py tests/test_run_history_store.py tests/test_run_events.py -q
ruff check core/run_registry.py core/run_history_store.py tests/test_run_registry.py tests/test_run_history_store.py
```

Review and freeze Registry/store names, SQLite columns, state values, and privacy behavior.

- [ ] **Step 5: Advisory commit**

```powershell
git add core/run_history_store.py tests/test_run_history_store.py
git commit -m "feat: recover and retain run history"
```

---

### Task 5: Operator access, tenant resolution, cursor codec, and API models

**Depends on:** Tasks 1-4 frozen contracts.

**Files:**
- Create: `server/run_ops.py`
- Create: `tests/test_run_ops_api.py`

**Interfaces:**
- Produces `require_operator_access(request, settings) -> None`, `resolve_request_tenant(request, tenant_id, settings) -> str`, `CursorCodec.encode(payload) -> str`, `CursorCodec.decode(token, expected_scope, expected_filter_hash, now) -> CursorPayload`, `RunHealthResponse`, `RunListResponse`, `RunDetailResponse`, `RunEventsResponse`, and `RunNotFoundError`.
- Model contract: `schema_version=1`, forbidden extras, no NaN, RFC 3339 UTC, no tenant scope in responses.

- [ ] **Step 1: Write failing auth, tenant, cursor, and DTO tests**

```python
def test_remote_access_matrix(run_ops_client_factory):
    no_token = run_ops_client_factory(client_host="10.0.0.2", configured_token=None)
    assert no_token.get("/api/runs/health").status_code == 403

    protected = run_ops_client_factory(client_host="10.0.0.2", configured_token="ops-secret")
    missing = protected.get("/api/runs/health")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert protected.get(
        "/api/runs/health", headers={"Authorization": "Bearer ops-secret"}
    ).status_code == 200


def test_cursor_is_bound_to_scope_filter_and_expiry(cursor_codec, frozen_now):
    token = cursor_codec.encode(cursor_payload(scope="scope-a", exp=frozen_now.timestamp() + 60))
    assert cursor_codec.decode(token, "scope-a", "filter-a", frozen_now).last_run_id == "run-9"
    with pytest.raises(CursorError, match="cursor_scope_mismatch"):
        cursor_codec.decode(token, "scope-b", "filter-a", frozen_now)
```

Also test IPv4/IPv6 loopback; no `X-Forwarded-For` trust; compare-digest Bearer; remote header requirement; query/header mismatch 400; local default `resolve_tenant(None)`; same 404 for unknown/cross-scope; tamper/expiry/filter codes; canonical sorted compact UTF-8 payload; 3600-second TTL; and response-model omission of scope, raw tenant, secrets, ACL, query, answer, and error message.

- [ ] **Step 2: Run RED**

```powershell
pytest tests/test_run_ops_api.py -q -k "access or tenant or cursor or model"
```

Expected: `server.run_ops` is missing.

- [ ] **Step 3: Implement security and public models**

```python
class CursorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    v: Literal[1] = 1
    tenant_scope: str
    filter_hash: str
    as_of_us: int
    last_started_at_us: int
    last_run_id: str
    exp: int

class CursorCodec:
    def __init__(self, key: bytes, ttl_s: int) -> None:
        self._key = key
        self._ttl_s = ttl_s

    def encode(self, payload: CursorPayload) -> str:
        raw = payload.model_dump_json(exclude_none=True).encode("utf-8")
        canonical = json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        signature = hmac.new(self._key, canonical, hashlib.sha256).digest()
        return f"{_b64(canonical)}.{_b64(signature)}"
```

Use `request.client.host` only. Loopback permits no token. Remote with no configured token is 403; configured token missing/invalid is 401 with challenge. Tenant resolution computes one value, checks remote header/query rules, calls existing `resolve_tenant`, and derives scope later through runtime identity. Define DTO fields exactly from spec sections 13.1-13.5.

- [ ] **Step 4: Run GREEN and freeze contracts**

```powershell
pytest tests/test_run_ops_api.py -q -k "access or tenant or cursor or model"
ruff check server/run_ops.py tests/test_run_ops_api.py
```

Freeze field names and error codes before Tasks 6 and 9.

- [ ] **Step 5: Advisory commit**

```powershell
git add server/run_ops.py tests/test_run_ops_api.py
git commit -m "feat: define protected run ops contracts"
```

---

### Task 6: Merged Runs service, router, views, detail, events, and long-poll

**Depends on:** Task 5 API contract plus Tasks 2-4 read interfaces.

**Files:**
- Modify: `server/run_ops.py`
- Modify: `tests/test_run_ops_api.py`

**Interfaces:**
- Produces `RunOpsService(registry, store, identity, settings, clock)`; async `health()`, `list_runs(scope, filters)`, `get_detail(scope, run_id)`, `get_events(scope, run_id, after_seq, limit, wait_ms)`; `create_run_ops_router() -> APIRouter`; `get_run_ops_service(request)` from `request.app.state.run_ops_runtime`.
- Query contract: stable `(started_at_us DESC, run_id DESC)` keyset; memory wins duplicate run IDs; all SQL has `tenant_scope = ?`.

- [ ] **Step 1: Write failing endpoint and long-poll tests**

```python
def test_recent_page_is_stable_across_concurrent_insert(client, seeded_runs):
    first = client.get("/api/runs?view=recent&limit=2").json()
    seeded_runs.insert_newer("run-new")
    second = client.get(f"/api/runs?view=recent&limit=2&cursor={first['next_cursor']}").json()
    ids = [item["run_id"] for item in first["items"] + second["items"]]
    assert ids == ["run-4", "run-3", "run-2", "run-1"]
    assert len(ids) == len(set(ids))


def test_event_long_poll_wakes_for_same_worker(client, registry, running_run):
    future = run_in_thread(lambda: client.get(f"/api/runs/{running_run}/events?after_seq=2&wait_ms=25000"))
    registry.bound_sink("scope-a")(event_factory(run_id=running_run, seq=3))
    response = future.result(timeout=2)
    assert response.status_code == 200
    assert [event["seq"] for event in response.json()["events"]] == [3]


def test_irrecoverable_event_gap_returns_409(client):
    response = client.get("/api/runs/run-gap/events?after_seq=3")
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "run_event_gap", "earliest_available_seq": 18, "latest_seq": 42
    }
```

Also test active/recent/slow/errors/stuck views; repeated status including cancelled; slow bounds; time bounds; optional fingerprint availability; 1-100 list limit; memory+SQLite dedupe; detail topology/rollup/history; event 1-500 limit; terminal immediate return; timeout response; SQLite fill of memory gaps; cross-worker 250 ms durable polling; 64-client rejection with 429/`Retry-After: 1`; known-run retention 404; safe 503; health ok/degraded/disabled; and fixed retention response.

- [ ] **Step 2: Run RED**

```powershell
pytest tests/test_run_ops_api.py -q -k "list or view or detail or events or poll or health or gap"
```

- [ ] **Step 3: Implement service merge and router**

```python
router = APIRouter(prefix="/api/runs", tags=["runs"])

@router.get("/health", response_model=RunHealthResponse)
async def run_health(
    request: Request,
    _: None = Depends(require_operator_access),
    service: RunOpsService = Depends(get_run_ops_service),
) -> RunHealthResponse:
    return await service.health()

@router.get("", response_model=RunListResponse)
async def list_runs(
    request: Request,
    view: RunView = "recent",
    status: Annotated[list[RunStatus] | None, Query()] = None,
    slow_ms: int = Query(default=30000, ge=1000, le=3600000),
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    fingerprint: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    tenant_id: str | None = None,
) -> RunListResponse:
    service, scope = resolve_service_and_scope(request, tenant_id)
    filters = RunListFilters(
        view=view,
        statuses=tuple(sorted(status or [])),
        slow_ms=slow_ms,
        started_after=started_after,
        started_before=started_before,
        fingerprint=fingerprint,
        limit=limit,
        cursor=cursor,
    )
    return await service.list_runs(scope, filters)
```

Normalize filter hash from view, sorted statuses, slow value, UTC bounds, fingerprint; exclude cursor and tenant text. First page freezes `as_of_us`; later pages reuse it and apply tuple keyset. Merge sorted memory/SQLite streams by run ID, choosing newer memory summaries. Derive slow/stuck against `as_of_us`. Long-poll acquires a bounded semaphore, checks current data before waiting, uses Registry condition locally or 250 ms SQLite watermark polling across workers, aborts at deadline, and releases admission in `finally`.

- [ ] **Step 4: Run GREEN and OpenAPI schema check**

```powershell
pytest tests/test_run_ops_api.py -q
ruff check server/run_ops.py tests/test_run_ops_api.py
```

- [ ] **Step 5: Advisory commit**

```powershell
git add server/run_ops.py tests/test_run_ops_api.py
git commit -m "feat: expose run history ops api"
```

---

### Task 7: App lifespan, runtime bootstrap, sequencer sink fan-out, fingerprint boundary, and metrics

**Depends on:** Tasks 1-6.

**Files:**
- Modify: `server/app.py:55-86,455-528,958-1600,2420-2638`
- Modify: `core/metrics.py:148-286` only if a safe helper is required
- Modify: `server/run_ops.py`
- Create: `tests/test_run_registry_integration.py`
- Modify: `tests/test_sse_run_event_compat.py`
- Modify: `tests/test_run_observability_parity.py`

**Interfaces:**
- Produces `RunOpsRuntime.bootstrap(settings) -> RunOpsRuntime`, `RunOpsRuntime.close() -> None`, `app.state.run_ops_runtime`, and `_HttpStreamRun(*, settings: Any, pipeline: Any, sink: _SseRunEventSink, retry_enabled: bool, extra_sinks: Sequence[RunEventSink] = ())`.
- Boundary contract: `query_stream` calls existing `resolve_tenant` once, derives opaque scope, optionally fingerprints `req.query`, obtains `registry.bound_sink`, and never passes raw query to Registry APIs.

- [ ] **Step 1: Write failing lifespan, fan-out, disabled/degraded, and parity tests**

```python
def test_stream_uses_same_sequencer_for_sse_and_registry(client, registry_runtime):
    frames = consume_stream(client, {"query": "private question", "retry": True})
    typed = [frame["event"] for frame in frames if frame["type"] == "run_event"]
    stored = registry_runtime.registry.get_events(
        registry_runtime.scope_for("default"), typed[0]["run_id"], 0, 500
    )
    assert stored.events == typed
    assert "private question" not in json.dumps(stored.events, ensure_ascii=False)


def test_throwing_registry_sink_preserves_all_stream_frames(client, throwing_runtime, baseline_frames):
    assert consume_stream(client, {"query": "same fixture"}) == baseline_frames


def test_rest_and_langgraph_boundaries_are_unchanged(monkeypatch, client, registry_spy):
    monkeypatch.setattr("rag_graph.answer_query", Mock(return_value=FIXED_RESULT))
    assert client.post("/api/query", json={"query": "q"}).json() == FIXED_REST_BODY
    assert not any(call.args for call in registry_spy.calls if call.args and call.args[0] == "rest")
```

Run the same fixture under Registry disabled, healthy WAL, throwing sink, permanent SQLite failure, and writer queue full. Assert equal QueryResult, filtered legacy SSE, typed SSE, cache payload, status, and error code; only Registry history/health may differ. Also test cache replays remain independent runs; fingerprint omission/presence; boot-scoped memory-only freeze; router mounting; lifespan shutdown; and low-cardinality metric names without run/tenant/fingerprint/route/error-message labels.

- [ ] **Step 2: Run RED**

```powershell
pytest tests/test_run_registry_integration.py tests/test_sse_run_event_compat.py tests/test_run_observability_parity.py -q
```

- [ ] **Step 3: Implement runtime and sink fan-out at the narrow boundary**

```python
class RunOpsRuntime:
    @classmethod
    def bootstrap(cls, settings: RunHistorySettings) -> "RunOpsRuntime":
        boot_id, worker_id = str(uuid.uuid4()), str(uuid.uuid4())
        try:
            store = RunHistoryStore.open(settings, boot_id=boot_id, worker_id=worker_id)
            identity = RegistryIdentity(boot_id, worker_id, store.scope_key, store.cursor_key, "installation")
        except Exception as exc:
            identity = boot_scoped_identity(boot_id, worker_id)
            store = None
        registry = RunRegistry(settings, identity, enqueue_mutation=store.enqueue if store else None)
        return cls(settings, identity, registry, store)
```

In lifespan, bootstrap before readiness, assign `app.state.run_ops_runtime`, include the router once at app construction, start background tasks, and close failure-silently after existing query executors settle. When disabled, install a disabled runtime, not `None`.

Refactor only the sink tuple:

```python
class _HttpStreamRun:
    def __init__(self, *, settings: Any, pipeline: Any, sink: _SseRunEventSink,
                 retry_enabled: bool, extra_sinks: Sequence[RunEventSink] = ()) -> None:
        self._sinks = (sink, *extra_sinks)

    def _new_sequencer(self, topology: RagTopology) -> RunEventSequencer:
        return RunEventSequencer(
            self.run_id,
            topology_id=topology.id,
            topology_revision=topology.revision,
            sinks=self._sinks,
        )
```

At `query_stream`, use `resolve_tenant(req.tenant_id, settings)` once, then derive scope. Convert `fingerprint_secret.get_secret_value()` to bytes only at this boundary. Never add a Registry sink to `POST /api/query` or `rag_graph.py`.

- [ ] **Step 4: Run GREEN with existing stream suites**

```powershell
pytest tests/test_run_registry_integration.py tests/test_sse_run_event_compat.py tests/test_run_observability_parity.py tests/test_stream_contract.py tests/test_run_events.py -q
ruff check server/app.py server/run_ops.py core/run_registry.py core/run_history_store.py tests/test_run_registry_integration.py
```

- [ ] **Step 5: Streaming parity checkpoint**

Record hashes or deep-equality evidence for legacy frames, typed frames, QueryResult, cache payload, HTTP status/error code in all five Registry modes. Confirm no diff in `rag_graph.py` and no RunRegistry references on REST execution.

- [ ] **Step 6: Advisory commit**

```powershell
git add server/app.py server/run_ops.py core/metrics.py tests/test_run_registry_integration.py tests/test_sse_run_event_compat.py tests/test_run_observability_parity.py
git commit -m "feat: attach run registry to streaming lifecycle"
```

---

### Task 8: Backend privacy, OpenAPI, failure, and performance release gate

**Depends on:** Tasks 1-7 complete.

**Files:**
- Modify: `tests/test_run_registry.py`
- Modify: `tests/test_run_history_store.py`
- Modify: `tests/test_run_ops_api.py`
- Modify: `tests/test_run_registry_integration.py`
- Modify implementation files only when a new regression test exposes a defect.

**Interfaces:**
- Produces no new public interface; freezes backend Route B behavior before TypeScript duplication.

- [ ] **Step 1: Add failing cross-layer sentinel, OpenAPI, and budget tests**

```python
def test_openapi_has_only_approved_run_ops_routes_and_schemas(client):
    schema = client.get("/openapi.json").json()
    assert set(path for path in schema["paths"] if path.startswith("/api/runs")) == {
        "/api/runs/health", "/api/runs", "/api/runs/{run_id}", "/api/runs/{run_id}/events"
    }
    blob = json.dumps(schema, ensure_ascii=False).lower()
    assert "tenant_scope" not in blob
    assert "ops_bearer_token" not in blob
    assert "fingerprint_secret" not in blob


def test_sink_and_api_budgets(benchmark_registry, api_benchmark):
    assert benchmark_registry.p95_ms < 1.0
    assert benchmark_registry.p99_ms < 2.0
    assert api_benchmark.events_1000_p95_ms < 100.0
    assert api_benchmark.list_100000_p95_ms < 150.0
```

Add a generated sensitive corpus through RunEvent -> Registry -> queue -> SQLite -> detail/events -> logs; assert every sentinel absent. Test disk full, unwritable directory, locked database, queue saturation, corrupt database, schema-too-new, API 503, long-poll 429, sink exception, and disabled persistence while query/SSE parity remains exact. Measure normal writer lag P95 below 500 ms.

- [ ] **Step 2: Run RED and capture every newly exposed defect**

```powershell
pytest tests/test_run_registry.py tests/test_run_history_store.py tests/test_run_ops_api.py tests/test_run_registry_integration.py -q
```

- [ ] **Step 3: Make only evidence-driven corrections**

Keep fixes inside Registry/Ops. Do not reduce canonical events or touch REST/LangGraph. Use low-cardinality metrics exactly from spec; allowed labels are fixed endpoint/view/status/result only.

```python
SAFE_METRIC_TAGS = frozenset({"endpoint", "view", "status", "result"})

def safe_metric_tags(**tags: str) -> dict[str, str]:
    if set(tags) - SAFE_METRIC_TAGS:
        raise ValueError("unsupported run ops metric label")
    return tags
```

- [ ] **Step 4: Run GREEN with full backend verification**

```powershell
pytest -q
ruff check config/settings.py core/run_registry.py core/run_history_store.py core/metrics.py server/run_ops.py server/app.py tests/test_run_registry.py tests/test_run_history_store.py tests/test_run_ops_api.py tests/test_run_registry_integration.py tests/test_sse_run_event_compat.py tests/test_run_observability_parity.py
```

Expected: full backend pytest and touched-file Ruff pass. Save counts and benchmark percentiles in the ledger.

- [ ] **Step 5: Advisory commit**

```powershell
git add tests/test_run_registry.py tests/test_run_history_store.py tests/test_run_ops_api.py tests/test_run_registry_integration.py core/run_registry.py core/run_history_store.py server/run_ops.py server/app.py
git commit -m "test: prove run registry backend compatibility"
```

---

### Task 9: Frontend Runs DTOs, runtime validation, API client, URL state, and server projection

**Depends on:** Task 5 frozen API fields and Task 8 backend gate.

**Files:**
- Create: `frontend/src/types/runs.ts`
- Create: `frontend/src/api/runs.ts`
- Create: `frontend/src/api/runs.test.ts`
- Create: `frontend/src/run/serverRunProjection.ts`
- Create: `frontend/src/run/serverRunProjection.test.ts`
- Modify: `frontend/src/api/client.ts:21-126`
- Modify: `frontend/src/run/runViewState.ts:1-32`
- Modify: `frontend/src/run/runViewState.test.ts`
- Modify: `frontend/src/run/runProjection.ts:10-330`
- Modify: `frontend/src/run/runProjection.test.ts`

**Interfaces:**
- Produces `RunStatus`, `RunAttention`, `RunSummaryDto`, `RunDetailDto`, `RunEventsDto`, `RunHealthDto`, `RunListFilters`; `fetchRunHealth`, `fetchRuns`, `fetchRunDetail`, `fetchRunEvents`; `RunOpsApiError`; `projectServerRun(detail, events) -> ServerRunProjection`; `RunDetailTab = "process" | "timeline" | "events" | "knowledge"` and `RunListView = "recent" | "active" | "slow" | "errors" | "stuck"`.
- Contract: runtime guards reject malformed schema/version/status/event payloads before state mutation.

- [ ] **Step 1: Write failing DTO, request, URL, and replay tests**

```typescript
it("encodes list filters without leaking tenant into cache keys", async () => {
  mockFetchJson({ schema_version: 1, items: [], next_cursor: null, as_of: NOW,
    source: "memory+sqlite", history_state: "complete", retention: { days: 30, max_runs: 100000 } });
  await fetchRuns({ view: "errors", status: ["failed", "interrupted"], limit: 50 });
  expect(lastRequestUrl()).toBe("/api/runs?view=errors&status=failed&status=interrupted&limit=50");
});

it("parses independent run tab and list view", () => {
  expect(parseRunViewState("?run=run-1&tab=events&view=errors&node=verify&follow=0")).toEqual({
    runId: "run-1", tab: "events", view: "errors", nodeId: "verify", follow: false,
  });
});

it("replays server events through the existing typed reducer", () => {
  const projection = projectServerRun(detailFixture, eventFixture);
  expect(projection.flow.topologySource).toBe("server-history");
  expect(projection.flow.nodes.find((node) => node.id === "generation")?.status).toBe("completed");
});
```

Also test every DTO enum/required field; unknown schema version; malformed numbers; structured 401/403/404/409/429/503 codes; AbortSignal forwarding; event `after_seq`/`limit`/`wait_ms`; cursor encoding; URL round trip; typed reducer parity; topology revision mismatch; node rollup; interval extraction; transitive overlap waves; greater-than-250-ms idle gaps; retry attempts; and Knowledge absent values remaining `undefined` rather than zero.

- [ ] **Step 2: Run RED**

```powershell
npm --prefix frontend test -- src/api/runs.test.ts src/run/runViewState.test.ts src/run/serverRunProjection.test.ts
```

- [ ] **Step 3: Implement strict types and adapters**

Export the existing `request<T>` helper from `client.ts` without changing behavior. In `runs.ts`, call it then validate `unknown` with explicit guards; do not add a runtime schema dependency.

```typescript
export interface RunEventsDto {
  schema_version: 1;
  run_id: string;
  events: BackendRunEvent[];
  after_seq: number;
  latest_seq: number;
  terminal: boolean;
  timed_out: boolean;
  history_state: "complete" | "partial" | "expired";
  earliest_available_seq: number;
  persistence_status: RunPersistenceStatus;
  retry_after_ms: number;
}

export async function fetchRunEvents(
  runId: string,
  options: { afterSeq: number; limit?: number; waitMs?: number; signal?: AbortSignal },
): Promise<RunEventsDto> {
  const query = new URLSearchParams({
    after_seq: String(options.afterSeq),
    limit: String(options.limit ?? 200),
    wait_ms: String(options.waitMs ?? 0),
  });
  return parseRunEvents(await request<unknown>(
    `/api/runs/` + encodeURIComponent(runId) + `/events?` + query,
    { method: "GET", timeoutMs: (options.waitMs ?? 0) + 5000, signal: options.signal },
  ));
}
```

Replace the `view`-means-tab URL ambiguity with `tab` and `view`. Extend `FlowTopologySource` with `server-history`. `projectServerRun` locates `run.started`, invokes `projectTypedRunStarted`, folds later events through `projectTypedRunEvent`, then changes only the source label and derives timeline/knowledge structures from safe fields.

- [ ] **Step 4: Run GREEN, lint, and typecheck**

```powershell
npm --prefix frontend test -- src/api/runs.test.ts src/run/runViewState.test.ts src/run/serverRunProjection.test.ts src/run/runProjection.test.ts
npm --prefix frontend run lint
npm --prefix frontend run build
```

- [ ] **Step 5: Advisory commit**

```powershell
git add frontend/src/types/runs.ts frontend/src/api/runs.ts frontend/src/api/runs.test.ts frontend/src/api/client.ts frontend/src/run/runViewState.ts frontend/src/run/runViewState.test.ts frontend/src/run/serverRunProjection.ts frontend/src/run/serverRunProjection.test.ts frontend/src/run/runProjection.ts frontend/src/run/runProjection.test.ts
git commit -m "feat: add run history frontend contracts"
```

---

### Task 10: Local sanitized raw-event buffer for current live runs

**Depends on:** Task 9 DTOs and existing SSE reducer.

**Files:**
- Create: `frontend/src/run/runEventBuffer.ts`
- Create: `frontend/src/run/runEventBuffer.test.ts`
- Modify: `frontend/src/run/runMonitorStore.ts:12-220`
- Modify: `frontend/src/run/RunMonitorContext.tsx:39-146`
- Modify: `frontend/src/run/RunMonitorContext.test.ts`

**Interfaces:**
- Produces `RunEventBufferSnapshot`, `createRunEventBuffer(runId: string, maxEvents?: number)`, `appendRunEvent(buffer, event) -> RunEventBufferSnapshot`, `mergeRunEvents(buffer, events) -> RunEventBufferSnapshot`.
- Snapshot fields: `runId`, `events`, `lastContiguousSeq`, `earliestAvailableSeq`, `historyState`, `topologyId`, `topologyRevision`.
- Contract: memory-only; never localStorage/sessionStorage/IndexedDB; stores only typed canonical event objects, never query or answer response.

- [ ] **Step 1: Write failing buffer and monitor tests**

```typescript
it("deduplicates and advances only the contiguous frontier", () => {
  let buffer = createRunEventBuffer("run-1", 1000);
  buffer = appendRunEvent(buffer, event(1, "run.started"));
  buffer = appendRunEvent(buffer, event(3, "node.completed"));
  expect(buffer.lastContiguousSeq).toBe(1);
  expect(buffer.historyState).toBe("partial");
  buffer = appendRunEvent(buffer, event(2, "node.started"));
  expect(buffer.lastContiguousSeq).toBe(3);
});

it("never stores local query or response", () => {
  const state = runMonitorReducer(initialRunMonitorState, {
    type: "start", id: "local-1", query: "SENTINEL_QUERY", startedAt: 1,
  });
  const serializedBuffer = JSON.stringify(state.current?.eventBuffer ?? {});
  expect(serializedBuffer).not.toContain("SENTINEL_QUERY");
  expect(serializedBuffer).not.toContain("answer");
});
```

Also test exact duplicate idempotency; conflicting duplicate marks partial and keeps first; run/topology mismatch rejection; first-plus-tail capacity; terminal flag; merging server fill events; frozen copies; and SSE `run_event_desync` remaining `liveTransportDesync` rather than altering Registry integrity.

- [ ] **Step 2: Run RED**

```powershell
npm --prefix frontend test -- src/run/runEventBuffer.test.ts src/run/RunMonitorContext.test.ts
```

- [ ] **Step 3: Implement immutable buffer and context integration**

```typescript
export interface RunEventBufferSnapshot {
  runId: string;
  events: readonly BackendRunEvent[];
  lastContiguousSeq: number;
  earliestAvailableSeq: number;
  historyState: "complete" | "partial";
  topologyId?: string;
  topologyRevision?: string;
  terminal: boolean;
}

export function appendRunEvent(
  current: RunEventBufferSnapshot,
  event: BackendRunEvent,
  maxEvents = 1000,
): RunEventBufferSnapshot {
  if (event.run_id !== current.runId) return current;
  return rebuildBoundedSnapshot(current, event, maxEvents);
}
```

Attach `eventBuffer` to `MonitoredRun` only after canonical `run.started` establishes the backend run ID. Keep `query` in the surrounding current-run model for local display, not inside the buffer. Expose a read-only snapshot from context for Task 11 merge.

- [ ] **Step 4: Run GREEN**

```powershell
npm --prefix frontend test -- src/run/runEventBuffer.test.ts src/run/RunMonitorContext.test.ts src/run/runProjection.test.ts
npm --prefix frontend run lint
```

- [ ] **Step 5: Advisory commit**

```powershell
git add frontend/src/run/runEventBuffer.ts frontend/src/run/runEventBuffer.test.ts frontend/src/run/runMonitorStore.ts frontend/src/run/RunMonitorContext.tsx frontend/src/run/RunMonitorContext.test.ts
git commit -m "feat: buffer sanitized live run events"
```

---

### Task 11: Run history hook, capability probe, pagination, selection, and reconnect

**Depends on:** Tasks 9-10 and Task 7 live endpoint behavior.

**Files:**
- Create: `frontend/src/run/useRunHistory.ts`
- Create: `frontend/src/run/useRunHistory.test.tsx`

**Interfaces:**
- Produces `RunHistoryState` and `useRunHistory(options)` returning `capability`, `health`, `items`, `selectedRunId`, `detail`, `events`, `loading`, `reconnecting`, `expired`, `historyGap`, `legacyMode`, `selectRun`, `setView`, `loadMore`, `refresh`, and `retryNow`.
- Contract: AbortController plus request generation rejects late list/detail/event responses after run switch or unmount.

- [ ] **Step 1: Write failing hook state-machine tests**

```typescript
it("rejects stale detail after selecting another run", async () => {
  const api = deferredRunsApi();
  const { result } = renderHook(() => useRunHistory({ api, liveRun: null }));
  act(() => result.current.selectRun("run-a"));
  act(() => result.current.selectRun("run-b"));
  api.resolveDetail("run-a", detail("run-a"));
  api.resolveDetail("run-b", detail("run-b"));
  await waitFor(() => expect(result.current.detail?.summary.run_id).toBe("run-b"));
});

it("backs off 500_1000_2000_5000_with_jitter_then_resumes", async () => {
  vi.useFakeTimers();
  const api = reconnectingApi([networkError(), networkError(), eventsPage(3)]);
  renderHook(() => useRunHistory({ api, liveRun: liveRunAtSeq(2), random: () => 0 }));
  await vi.advanceTimersByTimeAsync(500);
  await vi.advanceTimersByTimeAsync(1000);
  expect(api.eventCalls.at(-1)?.afterSeq).toBe(2);
});
```

Also test health: 200 ok/degraded, 200 disabled, 401/403, 404 old backend, initial network/5xx, later network failure retaining data; list pagination/dedupe; live/server same-run merge; maximum contiguous seq; timeout immediate repoll; terminal stop; hidden page 25-second wait; visible immediate request; 409 gap refresh from earliest; known 404 expiry; unmount/run-switch abort; no demo injection; and URL selection/view synchronization.

- [ ] **Step 2: Run RED**

```powershell
npm --prefix frontend test -- src/run/useRunHistory.test.tsx
```

- [ ] **Step 3: Implement one explicit reducer-driven hook**

```typescript
export type RunsCapability = "loading" | "available" | "disabled" | "unauthorized" | "legacy" | "unavailable";

export function useRunHistory({ api, liveRun, initial }: UseRunHistoryOptions): RunHistoryState {
  const generation = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const [state, dispatch] = useReducer(runHistoryReducer, initialRunHistoryState(initial));

  const selectRun = useCallback((runId: string) => {
    generation.current += 1;
    abortRef.current?.abort();
    dispatch({ type: "select", runId, generation: generation.current });
  }, []);
  return useRunHistoryEffects(state, dispatch, { api, liveRun, generation, abortRef });
}
```

Use primitive effect dependencies, functional updates, and separate list/detail/event effects. Merge current live buffer before server history only for the same run ID; server fills gaps and becomes authoritative when complete. Reconnect delays are 500, 1000, 2000, 5000 ms capped with 0-20% jitter. Treat SSE desync, Registry partial, persistence partial/unavailable, and client reconnecting as independent flags.

- [ ] **Step 4: Run GREEN and state checkpoint**

```powershell
npm --prefix frontend test -- src/run/useRunHistory.test.tsx src/api/runs.test.ts src/run/runEventBuffer.test.ts src/run/serverRunProjection.test.ts
npm --prefix frontend run lint
npm --prefix frontend run build
```

Review that late responses cannot change current selection and no state contains raw query outside the live-run object.

- [ ] **Step 5: Advisory commit**

```powershell
git add frontend/src/run/useRunHistory.ts frontend/src/run/useRunHistory.test.tsx
git commit -m "feat: manage run history and reconnect state"
```

---

### Task 12: Refactor Visualize into server history sidebar, process view, and accessible timeline

**Depends on:** Tasks 7 and 9-11 checkpoints.

**Files:**
- Create: `frontend/src/components/RunHistorySidebar.tsx`
- Create: `frontend/src/components/RunHistorySidebar.test.tsx`
- Create: `frontend/src/components/RunTimeline.tsx`
- Create: `frontend/src/components/RunTimeline.test.tsx`
- Create: `frontend/src/components/RunOpsBanners.tsx`
- Create: `frontend/src/components/RunOpsBanners.test.tsx`
- Modify: `frontend/src/pages/VisualizePage.tsx:1-584`
- Modify: `frontend/src/components/RunFlowGraph.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- `RunHistorySidebar({ items, selectedRunId, view, loading, hasMore, onSelect, onViewChange, onLoadMore })`.
- `RunTimeline({ intervals, waves, idleGaps, selectedNodeId, onSelectNode })`.
- `RunOpsBanners({ capability, health, liveTransportDesync, eventIntegrity, persistenceStatus, reconnecting, expired })`.
- Visualize URL contract: `/visualize?run=<run_id>&tab=process|timeline|events|knowledge&view=recent|active|slow|errors|stuck`.

- [ ] **Step 1: Write failing sidebar, timeline, banner, and page tests**

```typescript
it("renders server summaries without query or answer previews", () => {
  render(React.createElement(RunHistorySidebar, sidebarProps([summary({ run_id: "run-123456789" })])));
  expect(screen.getByText("run-123456789".slice(-8))).toBeInTheDocument();
  expect(screen.queryByText(/question|answer|SENTINEL_QUERY/i)).not.toBeInTheDocument();
});

it("groups transitive overlap into one wave and exposes equivalent rows", () => {
  render(React.createElement(RunTimeline, timelineProps([
    interval("a", 0, 100), interval("b", 80, 180), interval("c", 170, 240),
  ])));
  expect(screen.getByRole("group", { name: /并发波 1/ })).toHaveTextContent("3 个节点");
  expect(screen.getAllByRole("row")).toHaveLength(4);
});

it("selects a timeline bar with enter and links it to the flow graph", async () => {
  render(<VisualizePage />);
  const bar = await screen.findByRole("button", { name: /generation.*attempt 1/i });
  bar.focus();
  await user.keyboard("{Enter}");
  expect(screen.getByTestId("flow-node-generation")).toHaveAttribute("aria-current", "true");
});
```

Also verify each status/attention badge; pagination; retention/source text; tabs; current-live precedence; authority labels; topology unavailable; open/partial intervals; stable ordering; retry attempt badge; 6 px minimum bar; greater-than-250-ms idle gap; maximum concurrency; Enter/Space; chronological Tab order; graph-to-timeline selection; banner combinations; reduced motion; 375 px responsive behavior; and no 200 ms live-region timer announcements.

- [ ] **Step 2: Run RED**

```powershell
npm --prefix frontend test -- src/components/RunHistorySidebar.test.tsx src/components/RunTimeline.test.tsx src/components/RunOpsBanners.test.tsx
```

- [ ] **Step 3: Implement the operations-ledger layout and timeline algorithm**

Keep the established RAG4C Ant Design palette/typography. The distinctive device is the canonical sequence ledger: run rows, graph nodes, timeline bars, and event rows share compact monospace seq/attempt labels and status vocabulary. Avoid introducing a second decorative palette or a canvas-only chart.

```text
desktop:  [bounded run ledger] [process/timeline/events/knowledge workspace] [node inspector]
mobile:   [filters + selected run] -> [tabs] -> [workspace] -> [inspector drawer]
```

Build waves with a linear sweep over stable-sorted intervals. Join the current wave when `interval.startMs <= wave.maxEndMs` so transitive overlap remains in one wave. Compute max concurrency with sorted start/end points, idle gaps between adjacent wave bounds when greater than 250 ms, and bar position against run wall time with `max(6px, percentage)`. Render each bar as a native button in a labelled group and render an always-available table with node, attempt, start, end, duration, status, and wave.

Refactor `VisualizePage` to orchestrate `useRunHistory`, URL, selected node, four tabs, Graph Explorer, and the inspector. Keep expensive timeline/event/knowledge projections behind the active tab and memoized by event snapshot identity. Keep components at module scope and use primitive effect dependencies.

- [ ] **Step 4: Run GREEN, lint, and production build**

```powershell
npm --prefix frontend test -- src/components/RunHistorySidebar.test.tsx src/components/RunTimeline.test.tsx src/components/RunOpsBanners.test.tsx src/run/serverRunProjection.test.ts src/run/useRunHistory.test.tsx
npm --prefix frontend run lint
npm --prefix frontend run build
```

- [ ] **Step 5: Advisory commit**

```powershell
git add frontend/src/components/RunHistorySidebar.tsx frontend/src/components/RunHistorySidebar.test.tsx frontend/src/components/RunTimeline.tsx frontend/src/components/RunTimeline.test.tsx frontend/src/components/RunOpsBanners.tsx frontend/src/components/RunOpsBanners.test.tsx frontend/src/pages/VisualizePage.tsx frontend/src/components/RunFlowGraph.tsx frontend/src/styles.css
git commit -m "feat: rebuild visualize around run history"
```

---

### Task 13: Event table, knowledge facts, accessibility, Monitor drill-through, and legacy fallback

**Depends on:** Task 12 layout and Task 11 capability state.

**Files:**
- Create: `frontend/src/components/RunEventTable.tsx`
- Create: `frontend/src/components/RunEventTable.test.tsx`
- Create: `frontend/src/components/RunKnowledgePanel.tsx`
- Create: `frontend/src/components/RunKnowledgePanel.test.tsx`
- Modify: `frontend/src/pages/VisualizePage.tsx`
- Modify: `frontend/src/pages/MonitorPage.tsx:348-943`
- Modify: `frontend/src/monitor/monitorProjection.ts`
- Modify: `frontend/src/monitor/monitorProjection.test.ts`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- `RunEventTable({ events, topology, pageSize, filters, onFiltersChange })` copies only `JSON.stringify(event, null, 2)` from API/buffer data.
- `RunKnowledgePanel({ knowledge, nodeRollup })` renders only safe route, enable/skip/degrade flags, counts, attempts, verification state, duration, and reason codes.
- `MonitorAttentionItem` identity is `runId`; drill-through target is `/visualize?run=<id>&tab=timeline` or `tab=events`.

- [ ] **Step 1: Write failing event, knowledge, Monitor, fallback, and accessibility tests**

```typescript
it("filters a semantic event table and copies only sanitized JSON", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  render(React.createElement(RunEventTable, eventTableProps({ writeText })));
  await user.selectOptions(screen.getByLabelText("事件类型"), "node.failed");
  expect(screen.getAllByRole("row")).toHaveLength(2);
  await user.click(screen.getByRole("button", { name: "复制事件 7" }));
  expect(writeText).toHaveBeenCalledWith(JSON.stringify(sanitizedEvent7, null, 2));
  expect(screen.getByRole("status")).toHaveTextContent("已复制事件 7");
});

it("does not infer missing knowledge counts as zero", () => {
  render(<RunKnowledgePanel knowledge={{ route: "hybrid" }} nodeRollup={[]} />);
  expect(screen.getByText("本次运行未记录该项")).toBeInTheDocument();
  expect(screen.queryByText(/^0$/)).not.toBeInTheDocument();
});

it("monitor drills through by stable run id", async () => {
  render(<MonitorPage active />);
  await user.click(await screen.findByRole("link", { name: /run-abcdef12.*失败/ }));
  expect(window.location.search).toBe("?run=run-abcdef12&tab=events&view=errors");
});
```

Also verify native table headings; `details/summary/pre` JSON; event type/node/attempt/failed/degraded/retry filters; keyboard filter/select/tab/expand path; copy failure live region; 200/1000-event pagination or content-visibility; route/effective-route; HyDE/SubQueries/Stepback/graph/rerank/window facts; attempt comparison; L1/L2/L3; no content fetch; all attention groups; cancelled separate from errors; parallel Monitor requests; stable IDs; 404 legacy metrics fallback; 200 disabled fallback; 401/403 permission state without demo; network/5xx stale-data reconnect; and no `recent_queries` array-index identity.

- [ ] **Step 2: Run RED**

```powershell
npm --prefix frontend test -- src/components/RunEventTable.test.tsx src/components/RunKnowledgePanel.test.tsx src/monitor/monitorProjection.test.ts
```

- [ ] **Step 3: Implement semantic event/knowledge views and Monitor attention**

```tsx
<table className="run-event-table">
  <caption>脱敏后的 canonical 运行事件</caption>
  <thead><tr><th scope="col">Seq</th><th scope="col">时间</th><th scope="col">类型</th>
    <th scope="col">节点</th><th scope="col">Attempt</th><th scope="col">耗时</th>
    <th scope="col">摘要</th><th scope="col">详情</th></tr></thead>
  <tbody>{visibleEvents.map((event) => <RunEventRow key={event.seq} event={event} />)}</tbody>
</table>
```

Use native paging at 200 rows by default; keep at most the current page in the rendered body and apply `content-visibility: auto` as a secondary optimization. The copy control never reads query state or response content. Announce copy result once through a polite live region.

Build Knowledge from `projectServerRun` safe facts only. For every absent optional field, render the approved “本次运行未记录该项”; never coerce with `value ?? 0`.

In Monitor, start independent active/stuck/slow/errors/cancelled requests together with `Promise.allSettled`. Link attention rows by `run_id`. Only Runs health 404 selects legacy `fetchMetrics().recent_queries`; 401/403, disabled, and network/5xx retain their distinct states and never inject `DEMO_METRICS`.

- [ ] **Step 4: Run GREEN, full frontend unit suite, lint, and build**

```powershell
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

- [ ] **Step 5: Frontend accessibility checkpoint**

Using only keyboard in component tests and the browser, complete: choose list filter; select run; switch all four tabs; select graph node; select timeline bar; filter events; expand JSON; invoke copy. Confirm visible focus, status text independent of color, equivalent tables, and reduced-motion media query.

- [ ] **Step 6: Advisory commit**

```powershell
git add frontend/src/components/RunEventTable.tsx frontend/src/components/RunEventTable.test.tsx frontend/src/components/RunKnowledgePanel.tsx frontend/src/components/RunKnowledgePanel.test.tsx frontend/src/pages/VisualizePage.tsx frontend/src/pages/MonitorPage.tsx frontend/src/monitor/monitorProjection.ts frontend/src/monitor/monitorProjection.test.ts frontend/src/styles.css
git commit -m "feat: add run events knowledge and monitor drilldown"
```

---

### Task 14: Full backend/frontend/visual QA and documentation synchronization

**Depends on:** Tasks 1-13 green with no unresolved Critical or Important review finding.

**Files:**
- Modify: `README.md`
- Modify: `frontend/README.md`
- Modify: `docs/模块实现说明.md`
- Modify: `docs/RAG4C-Waku运行观测与评估可视化-最终实现说明-2026-08-23.md`
- Modify: `C:\Users\饶策\Desktop\RAG4C-Waku运行观测与评估可视化-最终实现说明-2026-08-23.md`
- Create: `frontend/output/shots/run-registry-ops-foundation/*.png`
- Modify implementation/tests only for defects reproduced during this gate.

**Interfaces:**
- Produces release evidence: exact test counts, Ruff/lint/build output, benchmark percentiles, screenshot manifest, keyboard checklist, privacy scan, OpenAPI snapshot, and synchronized documentation checksum.

- [ ] **Step 1: Run the complete automated GREEN gate from a clean process state**

```powershell
pytest -q
ruff check config/settings.py core/run_registry.py core/run_history_store.py core/metrics.py server/run_ops.py server/app.py tests/test_run_registry.py tests/test_run_history_store.py tests/test_run_ops_api.py tests/test_run_registry_integration.py tests/test_sse_run_event_compat.py tests/test_run_observability_parity.py
npm --prefix frontend test
npm --prefix frontend run lint
npm --prefix frontend run build
```

Expected: all commands exit 0. Record exact counts; do not write “tests pass” without the command output.

- [ ] **Step 2: Run release performance and privacy checks**

```powershell
pytest tests/test_run_registry_integration.py -q -k "budget or latency or privacy or sentinel"
pytest tests/test_run_history_store.py tests/test_run_ops_api.py -q -k "100000 or 1000_events or two_workers or sensitive"
$matches = rg -n -i "SENTINEL_QUERY|SENTINEL_ANSWER|SENTINEL_TOKEN|authorization|tenant_scope" server-run.log frontend/output/shots/run-registry-ops-foundation
if ($LASTEXITCODE -eq 0) { $matches; throw "sensitive sentinel found" }
if ($LASTEXITCODE -ne 1) { exit $LASTEXITCODE }
```

Expected: benchmarks meet P95/P99 targets. The sentinel scan returns no match; `tenant_scope` may appear only in source/test identifiers, not database query output, logs, screenshots, or API captures. Use the test-controlled temporary database for byte assertions because `rg` is not a substitute for binary-safe tests.

- [ ] **Step 3: Start local QA services and seed approved safe fixtures**

Terminal A:

```powershell
.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

Terminal B:

```powershell
npm --prefix frontend run dev -- --host 127.0.0.1 --port 1420
```

Use test helpers or a development-only fixture loader to create runs for active, completed, failed, cancelled, interrupted, stuck, slow, retry attempt 2, degraded, history gap, memory-only, reconnecting, and retention-expired UI states. Fixture events contain only allowlisted enums/counts and synthetic IDs; no real query or document content.

- [ ] **Step 4: Capture and review the visual matrix**

Capture at 1440x900, 1366x768, and 375x812 in light and dark themes. Required files:

```text
visualize-process-active-1440-light.png
visualize-process-completed-1440-dark.png
visualize-process-failed-1366-light.png
visualize-process-cancelled-1366-dark.png
visualize-process-interrupted-375-light.png
visualize-process-stuck-375-dark.png
visualize-timeline-wave-1440-light.png
visualize-timeline-retry-1440-dark.png
visualize-events-1000-1366-light.png
visualize-knowledge-degraded-1366-dark.png
visualize-mobile-gap-375-light.png
visualize-mobile-reconnecting-375-dark.png
monitor-attention-desktop-light.png
monitor-attention-mobile-dark.png
ops-memory-only-banner.png
ops-retention-expired-banner.png
ops-legacy-backend-banner.png
```

Review every screenshot for clipping, readable node labels, stable ledger widths, visible focus, status text beyond color, DOM timeline/table equivalence, dark contrast, mobile wrapping, no query/answer preview, and no demo substitution. Exercise 200 and 1000 event pages interactively.

- [ ] **Step 5: Run manual operator and failure matrix**

Verify loopback; remote 403 without token; remote 401 bad/missing configured token; remote success with token; cross-tenant identical 404; tampered/expired/scope/filter cursor codes; same-worker wake; cross-worker durable poll; 409 gap recovery; 429 poll cap; SQLite locked/unwritable/corrupt/schema-too-new; writer queue full; disabled Registry; clean restart recovery; and terminal retention. For every injected fault, issue the same stream request and compare QueryResult, cache payload, legacy SSE, typed SSE, status, and error code against baseline.

- [ ] **Step 6: Update project documentation with exact final evidence**

Document:

- module ownership and boundaries;
- all approved defaults and environment variable names;
- loopback/Bearer/tenant behavior;
- privacy allowlists and forbidden data;
- schema v1, WAL, recovery, retention, and corruption behavior;
- endpoint/cursor/long-poll contracts and errors;
- frontend source precedence, four tabs, banners, Monitor links, and legacy behavior;
- exact test/lint/build counts, benchmark percentiles, screenshot directory, and exclusions for REST/LangGraph/EvalReport v2.

Do not claim permanent retention, raw events, multi-tenant administration, or REST/LangGraph parity.

- [ ] **Step 7: Synchronize the desktop document and verify checksum**

```powershell
Copy-Item -LiteralPath 'D:\program_project\python_project\RAG4C\docs\RAG4C-Waku运行观测与评估可视化-最终实现说明-2026-08-23.md' -Destination 'C:\Users\饶策\Desktop\RAG4C-Waku运行观测与评估可视化-最终实现说明-2026-08-23.md' -Force
Get-FileHash -Algorithm SHA256 -LiteralPath 'D:\program_project\python_project\RAG4C\docs\RAG4C-Waku运行观测与评估可视化-最终实现说明-2026-08-23.md','C:\Users\饶策\Desktop\RAG4C-Waku运行观测与评估可视化-最终实现说明-2026-08-23.md'
```

Expected: both SHA-256 hashes are identical.

- [ ] **Step 8: Independent review and advisory release commit**

Request a specification and code review. Resolve every Critical or Important item, rerun affected focused tests, then rerun Step 1.

```powershell
git add README.md frontend/README.md docs/模块实现说明.md docs/RAG4C-Waku运行观测与评估可视化-最终实现说明-2026-08-23.md frontend/output/shots/run-registry-ops-foundation
git commit -m "docs: finalize run registry ops foundation"
```

---

## Specification Coverage Matrix

| Approved design area | Owning tasks |
|---|---|
| Upstream fact-layer ownership and exclusions | Global Constraints, 7, 8, 14 |
| Configuration, boot identity, tenant HMAC, optional fingerprint | 1, 4, 5, 7 |
| Redaction, topology validation, size limits, safe logging | 1, 2, 8 |
| Bounded active/recent/events projection and attention views | 2, 6 |
| SQLite schema, WAL writer, queue, watermarks, migrations | 3 |
| Heartbeat, stale recovery, clean shutdown, retention, corrupt rotation | 4 |
| Loopback/Bearer access, one-scope tenant resolution, hidden 404 | 5, 6 |
| Health/list/detail/events APIs, signed pagination, long-poll | 5, 6 |
| Failure-silent sequencer fan-out and query/SSE/cache parity | 7, 8 |
| Frontend DTO validation, URL state, server replay | 9 |
| Current-run sanitized event buffer | 10 |
| Capability probe, pagination, abort, reconnect, gap, expiry | 11 |
| History sidebar, process graph, DOM timeline, banners | 12 |
| Event table, knowledge facts, Monitor drill-through, legacy fallback | 13 |
| Metrics, privacy, performance, OpenAPI, full QA, documentation | 8, 14 |

## Final Verification Ledger

The executor records concrete values for every row before completion:

| Gate | Command/evidence | Required result |
|---|---|---|
| Backend | `pytest -q` | Exit 0 with exact pass count |
| Python quality | touched-file `ruff check` | Exit 0 |
| Frontend unit | `npm --prefix frontend test` | Exit 0 with exact pass count |
| Frontend quality | lint and production build | Both exit 0 |
| Privacy | memory/SQLite/API/log sentinel suite | No forbidden value |
| Parity | five Registry modes | Identical QueryResult/cache/SSE/status/error |
| Persistence | restart/two-worker/retention/corruption | All approved outcomes |
| Performance | sink, writer, list, events budgets | Each percentile below budget |
| Accessibility | full keyboard path and semantic equivalents | Complete without pointer/canvas |
| Visual QA | 3 viewports, 2 themes, critical states | Reviewed screenshot manifest |
| Documentation | project and desktop SHA-256 | Identical |
| Review | independent findings | No Critical or Important issue |

## Execution Checkpoints

1. After Task 4: review Registry/store state, privacy, schema, and no-synthetic-terminal invariant.
2. After Task 5: freeze Python API models and cursor/error names before TypeScript duplication.
3. After Task 7: prove streaming/REST/LangGraph boundaries and failure-silent parity.
4. After Task 11: review frontend DTO, event buffer, URL, selection, and reconnect state before UI work.
5. After Task 13: complete frontend accessibility and legacy-fallback review.
6. After Task 14: accept only current command output, screenshots, benchmark values, and document hashes as completion evidence.

Plan complete. Implementation should use subagent-driven development with a fresh worker and review gate per task, or execute inline with the executing-plans skill and the checkpoints above.
