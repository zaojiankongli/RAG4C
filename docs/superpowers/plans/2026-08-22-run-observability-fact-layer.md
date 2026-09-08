# RAG4C Run Observability Fact Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Execution status:** The user approved the first backend observability batch, and Tasks 1–8 were completed on 2026-08-23. Git commit steps were not executable because this workspace has no `.git` metadata; the implementation ledger and test evidence are authoritative.

**Final verification:** backend `381 passed`; frontend `47 passed`; frontend lint/build passed; touched backend Ruff passed; 27 browser screenshots were reviewed across desktop/mobile and light/dark modes.

**Goal:** Build an engine-neutral, request-scoped run-event fact layer for the streaming RAG path while preserving all existing answers, citations, abstention behavior, cache behavior, and legacy SSE frames.

**Architecture:** `server/app.py` owns one `RunEventSequencer` and immutable topology snapshot per HTTP request. `rag_stream.py` and `retrieval/pipeline.py` report semantic lifecycle facts through a failure-silent `RunObserver`; the server emits separate `type="run_event"` SSE frames while preserving legacy `phase/token/done/error`. The frontend consumes typed events when present and keeps its current coarse-phase fallback for older servers.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI/Starlette SSE, contextvars, pytest, React 18, TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-08-22-run-observability-fact-layer.md`

## Global Constraints

- Backend implementation requires explicit user approval before Task 1 begins.
- Do not change `rag_graph.py`, LangGraph behavior, Provider registration, strategy defaults, or query result semantics in this batch.
- Legacy SSE `phase/token/done/error` payloads and their filtered order must remain byte-equivalent after removing new `run_event` frames.
- Do not write run events into `QueryResult.traces` or answer cache payloads.
- Do not persist raw query, prompt, token text, answer, ACL, document text, embeddings, or score arrays in event attributes.
- Sink failures must never change QueryResult, SSE legacy frames, cache contents, metrics cleanup, or exception behavior.
- Token deltas remain legacy transient SSE and do not become canonical run events.
- Every backend run must have one immutable topology snapshot and exactly one terminal canonical event.
- Current workspace has no Git metadata; commit commands below are for a Git-enabled execution checkout.

---

## File Map

### New backend files

- `core/run_events.py`: strict event models, lifecycle sequencer, failure-silent observer, in-memory test sink.
- `rag_topology.py`: semantic topology models, canonical hashing, effective-component builder, cache replay topology.
- `tests/test_run_events.py`: event model, lifecycle, serialization and concurrency tests.
- `tests/test_rag_topology.py`: topology stability, plugin and revision tests.
- `tests/test_stream_observability.py`: `rag_stream.py` and retrieval semantic-event tests.
- `tests/test_sse_run_event_compat.py`: server SSE, cache, single-flight, timeout and cancellation compatibility tests.

### Modified backend files

- `core/tracing.py`: optional `RunObserver` binding alongside existing spans.
- `rag_stream.py`: optional run observer/query ID and business lifecycle instrumentation.
- `retrieval/pipeline.py`: request-scoped internal node/route/degradation facts.
- `server/app.py`: run ownership, SSE event sink, cache/queue/terminal facts and correct request counters.
- `tests/test_stream_contract.py`: legacy event-order regression assertions.

### Modified frontend files

- `frontend/src/types/rag.ts`: typed backend run-event and topology contracts.
- `frontend/src/api/client.ts`: parse `type="run_event"` and expose `onRunEvent`.
- `frontend/src/run/runMonitorStore.ts`: reduce backend run ID/seq/topology events.
- `frontend/src/run/runProjection.ts`: project typed topology and lifecycle; keep coarse/trace fallback.
- `frontend/src/run/RunMonitorContext.tsx`: accept backend events without generating conflicting IDs.
- `frontend/src/pages/QueryPage.tsx`: feed `onRunEvent` into the monitor context.
- `frontend/src/pages/VisualizePage.tsx`: mark typed topology as authoritative.
- Frontend tests under `frontend/src/run/*.test.ts` and `frontend/src/api/*.test.ts`.

---

### Task 1: Implement the strict run-event kernel

**Files:**
- Create: `core/run_events.py`
- Create: `tests/test_run_events.py`

**Interfaces:**
- Produces: `RunEvent`, `EventError`, `RunEventSink`, `RunEventSequencer`, `RunObserver`, `MemoryRunEventSink`, `run_event_dict`, `run_event_json`.
- Consumed later by: topology/SSE/server/stream instrumentation tasks.

- [ ] **Step 1: Write failing event-model and lifecycle tests**

Create tests with fixed clocks:

```python
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from core.run_events import MemoryRunEventSink, RunEventSequencer


def _sequencer(run_id: str = "run-1"):
    monotonic_values = iter([10.0, 10.1, 10.2, 10.3, 10.4])
    sink = MemoryRunEventSink()
    seq = RunEventSequencer(
        run_id,
        topology_id="rag.query",
        topology_revision="rev-1",
        monotonic=lambda: next(monotonic_values),
        utcnow=lambda: datetime(2026, 8, 22, tzinfo=timezone.utc),
        sinks=(sink,),
    )
    return seq, sink


def test_run_started_is_seq_one_and_terminal_is_last():
    seq, sink = _sequencer()
    seq.start_run(executor_requested="sequential_stream", retry=True)
    seq.start_node("search", attempt=1)
    seq.complete_node("search", attempt=1, attributes={"chunks": 3})
    seq.complete_run(outcome="answered", executor_used="sequential_stream")

    assert [event.seq for event in sink.events] == [1, 2, 3, 4]
    assert sink.events[0].type == "run.started"
    assert sink.events[-1].type == "run.completed"
    assert seq.observer().start_node("generate") is None
```

Also add tests named:

```text
test_event_models_reject_unknown_fields
test_event_json_rejects_nan_and_non_json_attributes
test_node_cannot_complete_without_start
test_node_attempt_cannot_start_twice
test_skipped_node_was_not_started
test_repeatable_attempts_are_contiguous
test_terminal_is_exactly_once
test_sink_exception_does_not_escape_observer
test_twenty_runs_have_independent_gapless_sequences
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
pytest tests/test_run_events.py -q
```

Expected: collection failure because `core.run_events` does not exist.

- [ ] **Step 3: Implement strict models and sequencer**

Use Pydantic v2 with:

```python
class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    seq: int = Field(ge=1)
    occurred_at: datetime
    elapsed_ms: float = Field(ge=0)
    topology_id: str = Field(min_length=1)
    topology_revision: str = Field(min_length=1)
    type: EventType
    node_id: str | None = None
    attempt: int | None = Field(default=None, ge=1)
    duration_ms: float | None = Field(default=None, ge=0)
    attributes: EventAttributes = Field(default_factory=dict)
    error: EventError | None = None
```

Implementation details:

- Store `started_at_monotonic` once.
- Protect seq, run lifecycle and node-attempt state with `threading.RLock`.
- Dispatch sinks in the same locked order; catch all sink exceptions.
- `RunObserver` catches `RunEventStateError` and returns `None` after debug logging.
- `MemoryRunEventSink.events` returns a copied list.
- `run_event_json` uses `ensure_ascii=False`, `allow_nan=False`, compact separators.
- Recursive JSON validation rejects `set`, `Exception`, model objects and non-finite floats.

- [ ] **Step 4: Run kernel tests and full core tests**

Run:

```powershell
pytest tests/test_run_events.py tests/test_engineering_policies.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit in a Git-enabled checkout**

```bash
git add core/run_events.py tests/test_run_events.py
git commit -m "feat: add request-scoped run event kernel"
```

---

### Task 2: Build immutable semantic topology snapshots

**Files:**
- Create: `rag_topology.py`
- Create: `tests/test_rag_topology.py`

**Interfaces:**
- Consumes: JSON type aliases from `core.run_events`.
- Produces: `TopologyNode`, `TopologyEdge`, `RagTopology`, `TopologyFragment`, `RagTopologyPlugin`, `build_rag_topology`, `build_cache_replay_topology`, `topology_dict`, `enabled_components_from_settings`, `available_components_from_pipeline`.

- [ ] **Step 1: Write failing topology tests**

Hand-write expected core IDs and edges:

```python
EXPECTED_CORE_IDS = [
    "receive", "complexity_gate", "rewrite", "route", "embed", "search",
    "diversity", "gate.retrieval", "generate", "verify", "gate.final", "finalize",
]


def test_core_topology_uses_stable_semantic_ids():
    topology = build_rag_topology(
        executor="sequential_stream",
        enabled_components=frozenset(),
        available_components=frozenset(),
    )
    assert [node.id for node in topology.nodes] == EXPECTED_CORE_IDS


@dataclass(frozen=True)
class _Plugin:
    name: str
    priority: int
    node_id: str

    def contribute(self, context: TopologyContext) -> TopologyFragment:
        node = TopologyNode(
            id=self.node_id,
            label=self.name,
            group="extension",
            description=f"{self.name} test node",
            optional=True,
            plugin=self.name,
        )
        return TopologyFragment(nodes=(node,))


def test_plugin_order_does_not_change_revision():
    plugin_a = _Plugin(name="a", priority=10, node_id="plugin.a.run")
    plugin_b = _Plugin(name="b", priority=20, node_id="plugin.b.run")
    kwargs = {
        "executor": "sequential_stream",
        "enabled_components": frozenset({"a", "b"}),
        "available_components": frozenset({"a", "b"}),
    }
    first = build_rag_topology(**kwargs, plugins=[plugin_b, plugin_a])
    second = build_rag_topology(**kwargs, plugins=[plugin_a, plugin_b])
    assert first.revision == second.revision
```

Add tests for:

```text
test_retry_is_only_allowed_cycle
test_configured_but_unavailable_plugin_remains_in_snapshot
test_plugin_node_requires_namespace
test_plugin_cannot_override_core_node
test_plugin_edge_requires_existing_endpoints
test_enabled_component_set_changes_revision
test_cache_replay_topology_is_distinct
test_module_imports_without_langgraph_installed
```

- [ ] **Step 2: Run topology tests and verify RED**

```powershell
pytest tests/test_rag_topology.py -q
```

Expected: import failure for `rag_topology`.

- [ ] **Step 3: Implement topology builder and canonical hash**

Required builder signature:

```python
def build_rag_topology(
    *,
    executor: Literal["sequential_stream", "sequential", "langgraph"],
    enabled_components: frozenset[str],
    available_components: frozenset[str],
    plugins: Iterable[RagTopologyPlugin] = (),
) -> RagTopology:
    context = TopologyContext(
        executor=executor,
        enabled_components=enabled_components,
        available_components=available_components,
    )
    nodes = list(CORE_NODES)
    edges = list(CORE_EDGES)
    node_ids = {node.id for node in nodes}
    edge_ids = {edge.id for edge in edges}
    for plugin in sorted(plugins, key=lambda item: (item.priority, item.name)):
        fragment = plugin.contribute(context)
        for node in fragment.nodes:
            if not node.id.startswith(f"plugin.{plugin.name}."):
                raise ValueError(f"plugin node must use namespace plugin.{plugin.name}: {node.id}")
            if node.id in node_ids:
                raise ValueError(f"duplicate topology node: {node.id}")
            node_ids.add(node.id)
            nodes.append(node)
        for edge in fragment.edges:
            if edge.id in edge_ids:
                raise ValueError(f"duplicate topology edge: {edge.id}")
            if edge.source not in node_ids or edge.target not in node_ids:
                raise ValueError(f"unknown topology edge endpoint: {edge.id}")
            edge_ids.add(edge.id)
            edges.append(edge)
    validate_topology(nodes, edges)
    canonical = {
        "id": "rag.query",
        "executor": executor,
        "nodes": [node_dict(node) for node in sorted(nodes, key=lambda item: item.id)],
        "edges": [edge_dict(edge) for edge in sorted(edges, key=lambda item: item.id)],
    }
    revision = topology_revision(canonical)
    return RagTopology(
        id="rag.query",
        revision=revision,
        executor=executor,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )
```

Canonical revision:

```python
payload = topology_dict_without_revision(topology)
raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
revision = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
```

Include configured plugin nodes:

```text
plugin.hyde.expand
plugin.subqueries.expand
plugin.stepback.expand
graph.retrieve
rerank
sentence_window
```

`available=False` when configured but not assembled.

- [ ] **Step 4: Run topology and import-safety tests**

```powershell
pytest tests/test_rag_topology.py tests/test_phase6_graph_retrieval.py -q
```

Expected: all pass and no LangGraph import requirement.

- [ ] **Step 5: Commit**

```bash
git add rag_topology.py tests/test_rag_topology.py
git commit -m "feat: describe immutable rag topologies"
```

---

### Task 3: Bind the observer to existing tracing without changing spans

**Files:**
- Modify: `core/tracing.py`
- Modify: `tests/test_run_events.py`
- Test: existing tracing/metrics tests through `tests/test_engineering_policies.py`

**Interfaces:**
- Consumes: `RunObserver`.
- Produces: `trace_session(query_id, *, observer=None)`, `current_run_observer()`.

- [ ] **Step 1: Add failing compatibility tests**

```python
def test_trace_as_list_is_identical_with_observer():
    observer = recording_observer()
    with trace_session("q-1", observer=observer) as trace:
        trace.add_span("search", 12.5)
    assert trace.as_list() == ["search:12.50ms"]


def test_tracing_disabled_does_not_disable_run_observer():
    set_tracing_enabled(False)
    with trace_session("q-1", observer=observer):
        assert current_trace() is None
        assert current_run_observer() is observer
```

The run observer remains available when span tracing is disabled; product process visibility must not depend on the internal tracing switch.

- [ ] **Step 2: Run tests and verify RED**

```powershell
pytest tests/test_run_events.py -q
```

Expected: missing observer parameters/functions.

- [ ] **Step 3: Add a separate observer ContextVar**

Implement:

```python
_run_observer_var: ContextVar[RunObserver | None] = ContextVar(
    "rag4c_run_observer", default=None
)

def current_run_observer() -> RunObserver | None:
    return _run_observer_var.get()
```

Update `trace_session` so observer binding is independent of `_tracing_enabled`. Do not modify `add_span`, span observers, or `as_list` output.

- [ ] **Step 4: Run tracing, metrics and new tests**

```powershell
pytest tests/test_run_events.py tests/test_engineering_policies.py tests/test_phase7_usage_accounting.py -q
```

- [ ] **Step 5: Commit**

```bash
git add core/tracing.py tests/test_run_events.py
git commit -m "feat: bind run observer beside tracing spans"
```

---

### Task 4: Instrument the retrieval pipeline through the parent thread

**Files:**
- Modify: `retrieval/pipeline.py`
- Create: `tests/test_stream_observability.py`
- Extend retrieval fake/stub tests as needed.

**Interfaces:**
- Consumes: `current_run_observer()`.
- Produces semantic node and route events without changing `RetrievalPipeline.run()` signature or `RetrievalResult`.

- [ ] **Step 1: Write failing parity and degradation tests**

Create a fake observer and assert:

```python
def test_observer_does_not_change_retrieval_result(fake_pipeline):
    without = fake_pipeline.run("q")
    with trace_session("q", observer=recording_observer()) as _:
        observed = fake_pipeline.run("q")
    assert observed == without


def test_rerank_failure_reports_degraded_and_keeps_rrf_order(fake_pipeline_factory):
    pipeline = fake_pipeline_factory(
        reranker=RerankerRaises(RerankError("down"))
    )
    result, events = run_with_recording_observer(pipeline)
    assert result.reranked is False
    assert event_types(events, node_id="rerank") == ["node.started", "node.failed", "degraded"]
```

Add cases for gate-skipped, router path, HyDE unavailable, subquery partial failure, stepback failure, graph no-match, graph failure, rerank disabled, rerank failure, Sentence Window failure.

- [ ] **Step 2: Run targeted tests and verify RED**

```powershell
pytest tests/test_stream_observability.py tests/test_phase6_graph_retrieval.py -q
```

Expected: no semantic events.

- [ ] **Step 3: Add small instrumentation helpers**

Inside `retrieval/pipeline.py` add private helpers only:

```python
def _observer() -> RunObserver | None:
    return current_run_observer()

@contextmanager
def _observed_node(node_id: str, *, attempt: int = 1, attributes=None):
    observer = _observer()
    started = time.perf_counter()
    if observer:
        observer.start_node(node_id, attempt=attempt, attributes=attributes)
    try:
        yield
    except Exception as exc:
        if observer:
            observer.fail_node(
                node_id,
                attempt=attempt,
                duration_ms=(time.perf_counter() - started) * 1000,
                error_type=type(exc).__name__,
                recoverable=False,
            )
        raise
    else:
        if observer:
            observer.complete_node(
                node_id,
                attempt=attempt,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
```

Use explicit observer calls for optional failures so recoverable degradation remains distinct from fatal failure. Emit events from the parent thread after Future completion; enhancer worker threads never mutate seq.

- [ ] **Step 4: Run retrieval parity, timeout and degradation tests**

```powershell
pytest tests/test_stream_observability.py tests/test_phase6_graph_retrieval.py tests/test_phase8_dense_cosine.py -q
```

Assert exact `RetrievalResult` equality for no observer, recording observer and throwing sink.

- [ ] **Step 5: Commit**

```bash
git add retrieval/pipeline.py tests/test_stream_observability.py
git commit -m "feat: emit retrieval lifecycle facts"
```

---

### Task 5: Instrument the sequential streaming business lifecycle

**Files:**
- Modify: `rag_stream.py`
- Extend: `tests/test_stream_contract.py`
- Extend: `tests/test_stream_observability.py`

**Interfaces:**
- Consumes: external `query_id` and `RunObserver` keyword-only parameters.
- Produces business facts for retrieval, gates, generation, verification and second-round retry.

- [ ] **Step 1: Add failing legacy-order and semantic-event tests**

Required public signature test:

```python
sig = inspect.signature(answer_query_stream)
assert sig.parameters["query_id"].kind is inspect.Parameter.KEYWORD_ONLY
assert sig.parameters["observer"].kind is inspect.Parameter.KEYWORD_ONLY
```

Legacy order test:

```python
legacy = [event for event in events if event["type"] != "run_event"]
assert [event["type"] for event in legacy] == [
    "phase", "phase", "phase", "token", "phase", "done"
]
```

Add retry cases: disabled, retrieval failure, no new evidence, success, exhausted.

- [ ] **Step 2: Run stream tests and verify RED**

```powershell
pytest tests/test_stream_contract.py tests/test_stream_observability.py -q
```

- [ ] **Step 3: Add optional observer/query ID and business events**

Signature:

```python
def answer_query_stream(
    query: str,
    acl: list[str] | None = None,
    retry: bool = True,
    settings: Settings | None = None,
    tenant_id: str | None = None,
    dataset_id: str | None = None,
    *,
    query_id: str | None = None,
    observer: RunObserver | None = None,
) -> Iterator[dict[str, Any]]:
```

Use `trace_session(query_id, observer=observer)`. Report:

```text
search attempt 1 -> gate.retrieval -> generate 1 -> verify 1
retry attempt 2 -> search 2 -> generate 2 -> verify 2
gate.final -> finalize
```

Do not emit token canonical events. Do not terminal when `observer` is server-owned; the server owns terminal.

- [ ] **Step 4: Run stream and QueryResult parity tests**

```powershell
pytest tests/test_stream_contract.py tests/test_stream_observability.py tests/test_abstention_paths.py tests/test_verifier_robustness.py -q
```

Expected: legacy stream and final result unchanged.

- [ ] **Step 5: Commit**

```bash
git add rag_stream.py tests/test_stream_contract.py tests/test_stream_observability.py
git commit -m "feat: observe sequential streaming lifecycle"
```

---

### Task 6: Own runs at the HTTP boundary and bridge typed events to SSE

**Files:**
- Modify: `server/app.py`
- Create: `tests/test_sse_run_event_compat.py`
- Extend: `tests/test_phase5_concurrency.py`
- Extend: `scripts/smoke_stream_cache.py` assertions if this file exists in execution checkout.

**Interfaces:**
- Consumes: `build_rag_topology`, `RunEventSequencer`, `RunObserver`.
- Produces: independent `type="run_event"` SSE frames and new request counters.

- [ ] **Step 1: Write failing server boundary tests**

Add tests for:

```text
L1 cache hit uses cache_replay topology and a fresh run ID
L2 cache hit does not enter retrieval
same-process waiter has its own run ID
queue full -> one run.cancelled/rejected terminal and existing 429
slot timeout -> one run.failed/timed-out terminal and existing status
producer failure -> existing stream_error plus one run.failed
client cancellation -> no cache put and one run.cancelled sink event
normal stream -> typed seq gapless and legacy frames unchanged
20 concurrent streams never mix run IDs
```

Golden helper:

```python
def legacy_frames(frames):
    return [frame for frame in frames if frame["type"] != "run_event"]
```

- [ ] **Step 2: Run server tests and verify RED**

```powershell
pytest tests/test_sse_run_event_compat.py tests/test_phase5_concurrency.py -q
```

- [ ] **Step 3: Add an SSE sink and run ownership**

At request entry:

```python
settings = get_settings()
pipeline = get_pipeline(settings)
topology = build_rag_topology(
    executor="sequential_stream",
    enabled_components=enabled_components_from_settings(settings),
    available_components=available_components_from_pipeline(pipeline),
)
run = RunEventSequencer(
    run_id=f"run-{uuid.uuid4().hex[:16]}",
    topology_id=topology.id,
    topology_revision=topology.revision,
    sinks=(sse_sink,),
)
observer = run.observer()
observer.start_run(
    executor_requested="sequential_stream",
    attributes={"topology": topology_dict(topology), "retry_enabled": req.retry},
)
```

`SseRunEventSink` writes:

```python
{"type": "run_event", "event": run_event_dict(event)}
```

into the existing bounded producer queue. Never put token text into this sink.

Pass `query_id=run.run_id, observer=observer` into `answer_query_stream` explicitly because executor threads do not inherit contextvars.

- [ ] **Step 4: Add correct counters while retaining old metrics**

Increment exactly once at these boundaries:

```text
query.requests             request accepted by endpoint
query.cache_hits           L1 or L2 answer cache hit
query.singleflight_joined  request waits for an existing owner
query.rejected             queue admission rejection
query.timeouts             queue/single-flight/stream deadline
query.cancelled            client cancellation or response abandoned
query.errors               no QueryResponse produced
query.completed            answered or abstained QueryResponse produced
```

Keep old `query.total` and `query.total.errors` writes unchanged for compatibility.

- [ ] **Step 5: Run compatibility, cache, concurrency and full backend tests**

```powershell
pytest tests/test_sse_run_event_compat.py tests/test_stream_contract.py tests/test_phase5_concurrency.py tests/test_tenant_isolation.py tests/test_config_hot_reload.py -q
```

Then run the project backend suite:

```powershell
pytest tests -q
```

- [ ] **Step 6: Commit**

```bash
git add server/app.py tests/test_sse_run_event_compat.py tests/test_phase5_concurrency.py
git commit -m "feat: expose typed run events over compatible sse"
```

---

### Task 7: Consume backend typed events in the React monitor

**Files:**
- Modify: `frontend/src/types/rag.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/run/runMonitorStore.ts`
- Modify: `frontend/src/run/runProjection.ts`
- Modify: `frontend/src/run/RunMonitorContext.tsx`
- Modify: `frontend/src/pages/QueryPage.tsx`
- Modify: `frontend/src/pages/VisualizePage.tsx`
- Create/extend frontend tests.

**Interfaces:**
- Consumes: SSE `{type:"run_event", event: RunEvent}`.
- Produces: authoritative `topologySource="typed-events"` projection with coarse fallback.

- [ ] **Step 1: Write failing parser and reducer tests**

Add client test fixture:

```ts
const frames = [
  { type: "run_event", event: runStartedEvent },
  { type: "phase", phase: "retrieving" },
  { type: "run_event", event: searchStartedEvent },
  { type: "token", text: "答案" },
  { type: "done", result: response },
];
```

Assertions:

```ts
expect(onRunEvent).toHaveBeenCalledTimes(2);
expect(onPhase).toHaveBeenCalledWith("retrieving", expect.anything());
expect(onToken).toHaveBeenCalledWith("答案");
```

Reducer tests:

```text
backend run ID replaces frontend provisional ID
seq gaps or regressions are rejected
run.started installs topology snapshot once
node lifecycle updates status and duration
typed terminal makes run immutable
coarse phase is ignored after typed mode activates
server without run_event continues existing behavior
```

- [ ] **Step 2: Run frontend tests and verify RED**

```powershell
cd frontend
npm test -- --run src/api src/run
```

- [ ] **Step 3: Add typed contracts and stream handler**

Add:

```ts
export interface BackendRunEvent {
  schema_version: 1;
  run_id: string;
  seq: number;
  occurred_at: string;
  elapsed_ms: number;
  topology_id: string;
  topology_revision: string;
  type: string;
  node_id?: string;
  attempt?: number;
  duration_ms?: number;
  attributes: Record<string, unknown>;
}

interface StreamHandlers {
  onRunEvent?: (event: BackendRunEvent) => void;
  onPhase?: (phase: string, data: Record<string, unknown>) => void;
  onToken?: (text: string) => void;
}
```

Handle `case "run_event"` without changing existing cases.

- [ ] **Step 4: Project typed topology and events**

When `run.started` arrives:

- set backend `run_id`, topology ID/revision and immutable nodes/edges;
- set source `typed-events`;
- preserve node positions for the run;
- reject config/topology changes with the same run ID;
- keep QueryPage answer state independent from monitoring state.

When old server frames arrive without run events, retain current provisional/coarse behavior.

- [ ] **Step 5: Run all frontend verification**

```powershell
cd frontend
npm test
npm run lint
npm run build
```

Expected: all pass and lazy-loaded Visualize/Eval/Monitor chunks remain separate.

- [ ] **Step 6: Commit**

```bash
git add frontend/src frontend/package.json frontend/package-lock.json
git commit -m "feat: render backend-authoritative run events"
```

---

### Task 8: Final parity, privacy and completion audit

**Files:**
- Modify tests only unless a proven defect is found.
- Update project docs after verification.

**Interfaces:**
- Verifies all prior task outputs together.

- [ ] **Step 1: Add the observer non-interference regression**

Run the same fake pipeline in three modes:

```python
baseline = run_query(observer=None)
recorded = run_query(observer=recording_observer)
throwing = run_query(observer=observer_with_throwing_sink)

assert recorded.query_result.model_dump() == baseline.query_result.model_dump()
assert throwing.query_result.model_dump() == baseline.query_result.model_dump()
assert legacy_frames(recorded.sse) == legacy_frames(baseline.sse)
assert legacy_frames(throwing.sse) == legacy_frames(baseline.sse)
assert recorded.cache_payload == baseline.cache_payload
assert throwing.cache_payload == baseline.cache_payload
```

- [ ] **Step 2: Add privacy scans**

Create events with sentinel values in query, answer, ACL and document text, then assert serialized run events do not contain the sentinels.

```python
serialized = "\n".join(run_event_json(event) for event in events)
for secret in (raw_query, raw_answer, acl_value, chunk_text):
    assert secret not in serialized
```

- [ ] **Step 3: Run complete verification**

Backend:

```powershell
pytest tests -q
```

Frontend:

```powershell
cd frontend
npm test
npm run lint
npm run build
```

Smoke checks when browser/process permissions permit:

```powershell
python scripts/smoke_stream_cache.py
```

- [ ] **Step 4: Self-review against the spec**

Confirm every spec section has evidence:

```text
one terminal per run
gapless seq
immutable topology
legacy SSE compatibility
cache replay new run IDs
queue/timeout/cancel coverage
no raw sensitive content
no QueryResult/cache changes
frontend typed fallback compatibility
no rag_graph behavior change
```

- [ ] **Step 5: Commit**

```bash
git add tests frontend/src docs
git commit -m "test: prove run observability compatibility"
```

---

## Execution Checkpoints

After Task 2: review event/topology public contract before inserting events into business code.

After Task 5: review `rag_stream.py` parity; no server or frontend migration proceeds if legacy stream differs.

After Task 6: user-visible checkpoint—inspect exact SSE examples, metrics definitions and cancellation semantics.

After Task 7: inspect the live graph using typed events and verify older backend fallback.

After Task 8: only then request approval for the second backend batch: LangGraph parity, persistence, Ops history and EvalReport v2.
