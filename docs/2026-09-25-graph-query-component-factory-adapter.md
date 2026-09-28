# Graph query component Factory + Adapter

## Outcome

The visualization Graph API now depends on an explicit query component port
instead of assembling a raw store and embedder inside each route:

- `D:\program_project\python_project\RAG4C\core\graph_query_components.py`
- `D:\program_project\python_project\RAG4C\core\graph_projection_adapters.py`
- `D:\program_project\python_project\RAG4C\server\app.py`

`GraphQueryComponentFactory` resolves the selected graph engine through the
existing Graph Factory/Registry, adapts it through the graph consumer Adapter,
and exposes stable `search` / `subgraph` result ports. The routes retain their
existing payloads, tenant header behavior, `milvus_vector_graph` override,
empty-result degradation, score rounding, graph gate, and subgraph ID cap.

## Hot-reload cache

`server.app._get_graph_components()` keeps its historical tuple projection for
compatibility, but no longer uses `functools.lru_cache`. It uses
`core.generation_cache.GenerationAwareSingletonCache`:

- concurrent callers share one build for a generation;
- `cache_clear()` advances the generation and invalidates the cached value;
- a build that started before configuration reload may finish for its original
  caller, but cannot write its stale tuple back into the current generation;
- waiters that observe an invalidation retry against the current generation.
- at most one obsolete generation is allowed to keep building alongside the
  current generation; further reload churn fails closed instead of starting
  unbounded provider constructions.
- `clear()` wakes obsolete-generation waiters immediately. Assembly failures
  are not cached as a successful `(None, None)` sentinel, so a transient
  dependency/configuration failure can recover on a later request.

The old `_get_graph_components.cache_clear()` seam remains available to the
configuration hot-reload path.

## Timeout contract

The graph route's outer `deadline` remains a total request budget. It does not
claim that arbitrary synchronous Python or SDK calls can be safely interrupted.
Provider behavior is explicit:

- `ApiEmbedder` declares `per_call`; its `api_timeout` is capped to the graph
  budget before construction.
- `RagGraphStore` declares `per_call`; `MilvusSettings.timeout` is capped to
  the graph budget before construction.
- `Bge3LocalEmbedder` declares `soft`, because local synchronous inference
  cannot be forcibly cancelled by this route.
- custom graph stores and embedders must declare
  `graph_query_timeout_contract` as `per_call` or `soft` when a graph budget is
  supplied; an absent or invalid declaration fails closed during assembly.
- component operations check cooperative `remaining_budget()` before starting
  embedding, entity, relation, and subgraph provider calls, so an exhausted
  request does not start another backend operation.
- `per_call` is verified at the port: the embedder must expose
  `embed_query_with_timeout`, and retrieval Adapter methods must accept
  `timeout_s`. The API embedder disables nested SDK retries and creates a
  per-attempt timeout option; the Milvus graph store forwards the remaining
  budget to `search`/`query`. Providers that cannot expose this capability use
  the explicit `soft` contract.

The default graph-store path receives a component-owned `GraphStoreRegistry`
when a graph budget is active. This prevents the process-wide engine-name cache
from reusing an older Milvus store whose timeout was created from a previous,
larger configuration snapshot.

## WeKnora-aligned rationale

The local WeKnora review showed that factories should own selection,
construction, authorization/error semantics, and capability declarations,
while callers consume a stable port. This slice applies that lesson without
copying WeKnora's remote parser fallback: an unknown or incomplete graph
provider remains fail-closed.

## Verification

- Graph query component, timeout contract, generation cache, graph registry and
  Adapter tests: **54 passed**
- Graph/retrieval/projection/delete/ingest/admin/folder regression selected for
  this slice: **129 passed**
- Existing deprecation warnings: **837**
- Ruff: passed
- `.venv` `py_compile`: passed
- `git diff --check`: passed

The full `server.app` HTTP mounting suite is not counted as green in this
environment because importing the application currently requires the optional
`pymysql` dependency. Component, source-guard, cache-concurrency, registry,
tenant, and route-contract tests are run without fabricating an HTTP green
light.

Independent sub-agent follow-up review: **PASS**, with no actionable
P0/P1/P2/P3 findings. The review explicitly rechecked Noop timeout
compatibility, legacy tuple assembly fallback, generation invalidation, and
per-call API/Milvus/Adapter timeout forwarding.

## Scope boundary

This slice does not add a graph database, alter entity/relation schemas,
change tenant authorization semantics, or make Graph a Catalog consistency
authority. Axis #3 remains open for the authority-dependent Graph comparator,
Catalog-deleted target enumeration, verifiable Catalog mutation generation,
and target-specific atomic repair.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-graph-query-component-factory-adapter-design.md`
