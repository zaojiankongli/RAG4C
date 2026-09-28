# Graph query component Factory + Adapter design (2026-09-25)

## Context

The graph API in `server/app.py` assembles a raw graph store and embedder in
one cached tuple, then each route calls backend methods directly.  This
duplicates the consumer contract already formalized by
`core.graph_projection_adapters.py` and makes the API route responsible for
backend method shapes, embedding failures, and response projection.

The local WeKnora review recommends keeping selection, construction, and
failure semantics at a factory seam while callers depend on a stable port.

## Decision

- Add `core/graph_query_components.py` with:
  - `GraphQueryComponentProtocol` for graph search and subgraph operations;
  - `GraphQueryComponentFactory`;
  - explicit typed assembly/search errors;
  - a concrete component that owns the Graph consumer Adapter and embedder.
- Resolve the graph store through the existing `GraphStoreFactory`/Registry,
  then adapt it with `GRAPH_RETRIEVER_REQUIRED_METHODS`.
- Keep the graph API's query engine override
  `engine_override="milvus_vector_graph"` so `/api/graph/*` retains its
  historical flag-independent observation behavior.
- Resolve the tenant through the existing Knowledge Actor permission dependency
  for remote requests and bind it to the signed actor tenant. Direct loopback
  requests without an actor token retain the bridge's configured-default
  behavior; `X-RAG4C-Tenant` is still forwarded to the Adapter.
- Do not classify Graph reads as operator-admin paths: this keeps CORS
  preflight working with the frontend's Knowledge Actor and tenant headers.
- Apply the graph gate to both search and subgraph routes and cap the combined
  subgraph ID count before backend reads.
- Make `server/app.py` routes depend on the component port rather than raw
  `search_entities`, `search_relations`, `get_entities_by_ids`, or
  `embed_query` calls.
- Keep the existing response shapes, score rounding, tenant defaults, graph
  gate (429), and unavailable-component empty-result behavior.
- Make factory dependencies injectable for tests: settings, store factory,
  embedder factory, and engine override.
- Preserve the compatibility `_get_graph_components()` tuple projection while
  adding a cached component assembly seam; this avoids breaking diagnostics or
  tests that still inspect the old helper.
- Replace the tuple projection's `lru_cache` with a generation-aware
  single-value cache.  `cache_clear()` advances a generation, so a build that
  began before hot reload can still finish for its caller but cannot
  repopulate the current cache with stale settings.
- Bound stale-build churn to one obsolete generation. If repeated reloads
  arrive before obsolete synchronous construction finishes, the cache fails
  closed rather than creating an unbounded number of provider instances.
- Clearing a generation wakes waiters on the obsolete build. They observe the
  generation mismatch and join the current build without waiting for the old
  synchronous provider call to finish.
- Treat the graph timeout as two explicit boundaries:
  - built-in API embedding and Milvus client settings are capped to the graph
    budget before construction;
  - local or custom synchronous providers must declare
    `graph_query_timeout_contract` as `per_call` or `soft`.  Unknown contracts
    fail closed at assembly rather than being described as hard-cancellable.
  The route deadline remains a total request budget; it does not claim to
  interrupt arbitrary synchronous provider code.
- Query operations check cooperative `remaining_budget()` before each provider
  call, preventing an exhausted request from starting another
  embedding/search/read operation. This is intentionally a between-call guard,
  not a claim of cancellation for a call already inside a synchronous SDK.
- `per_call` is a verifiable capability at the query port: the embedder must
  implement `embed_query_with_timeout`, and graph retrieval Adapter methods
  must accept `timeout_s`. The built-in API embedder disables nested SDK
  retries and recreates a per-attempt client option; the built-in Milvus graph
  store forwards the remaining budget to `search`/`query`. Providers that
  cannot expose that capability must use the explicit `soft` contract.
- When the default `create_graph_store` path is assembled with a graph budget,
  use a component-owned `GraphStoreRegistry`.  The process registry is keyed
  by engine name, so reusing an older pipeline store could otherwise retain a
  larger Milvus timeout despite the bounded settings snapshot.

## Failure contract

- Missing/unknown graph engine, invalid consumer Adapter, or graph store
  construction failure becomes `GraphQueryAssemblyError` and is logged by the
  app assembly helper; failed assembly is not cached as a successful
  `(None, None)` sentinel, so a later request can recover without a config
  reload.
- Embedder construction failure is classified as an embedding assembly error.
- Query-time embedding failure remains the existing safe empty-result error
  payload.
- Query-time graph backend failures are classified separately from embedding
  failures; the API returns a safe graph-unavailable response without
  fabricating entities or relations.
- No WeKnora-style remote fallback is added for unknown graph engines.

## Compatibility boundary

- No graph entity/relation schema, dataset authority, tenant contract,
  request-body OpenAPI model, or projection consistency semantics change.
- No new graph database is added.
- The retrieval pipeline's `GraphRetriever` assembly remains independent; this
  slice targets the visualization API's query component.
- The existing tenant header is reused; no new public request field is added.

## Verification plan

- Test factory assembly, custom injected engine/store, adapter tenant
  forwarding, search projection, subgraph expansion, and typed failures.
- Test bounded Milvus/API settings, explicit soft/unknown timeout contracts,
  public timeout-contract typing, cooperative budget guards, and
  generation-aware cache invalidation under an in-flight old build, including
  stale-build saturation.
- Add source guards proving the graph routes no longer call raw backend or
  embedder methods.
- Run graph registry/Adapter/retrieval/projection regressions plus focused
  query-component tests, Ruff, `py_compile`, and `git diff --check`.
- Request independent sub-agent review and write a handoff document.
