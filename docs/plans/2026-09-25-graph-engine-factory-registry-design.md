# Graph engine Factory/Registry design (2026-09-25)

## Context

The graph consumer Adapter now isolates `GraphBuilder`, `GraphRetriever`, and
collection initialization from backend method shapes.  Backend assembly is
still not fully open: `GraphStoreFactory.create()` contains a hard-coded
`none`/Milvus branch, while settings resolution contains a second copy of the
engine aliases.

Adding a graph engine currently requires editing the factory branch, the
engine type alias, and the settings resolver.  This is exactly the engine
registration seam highlighted by the local WeKnora review: selection,
construction, aliases, and failure semantics should be declared together.

## Decision

- Reuse the existing `core.providers.ProviderRegistry` as the only dispatch
  kernel.
- Add a graph-engine registration declaration containing:
  - canonical engine name;
  - aliases accepted by configuration/overrides;
  - a synchronous `factory(settings)` callable.
- Resolve aliases to one canonical name before factory dispatch.
- Register the built-in `none` and `milvus_vector_graph` engines once at
  module import.  Their existing aliases and construction/error behavior stay
  unchanged.
- Expose explicit registration helpers for future engines and tests:
  `register_graph_engine`, `unregister_graph_engine`, and
  `graph_engine_names`.
- Reject duplicate names/aliases, invalid names, non-callable or
  asynchronous factories, and factories with an incompatible signature at
  registration time.
- At resolution, reject deferred factory results (coroutines, generators, and
  async generators) and validate the returned object against the complete
  graph consumer Adapter port before it enters a cache.
- Reserve built-in canonical names and aliases from replacement/removal.
  Custom engines may be replaced or removed only through the explicit
  registration API.
- Make per-instance graph-store cache creation atomic.  Replacing or
  unregistering a custom engine invalidates matching live instance caches so
  a later request cannot silently reuse the old implementation.
- Keep nested `UnknownProviderError` from a registered factory intact; it
  describes a provider selected inside that engine, not an unknown graph
  engine.
- Keep unknown engine resolution fail-closed as `GraphStoreError`; never fall
  back to `none` when Graph is explicitly enabled with an unknown name.
- Keep `GraphEngineType` runtime-extensible (`str`) because engine names are
  an infrastructure plugin axis, not an OpenAPI enum.

## Compatibility boundary

- `GraphStoreFactory.create("none"|"noop"|"")` still creates a healthy
  `NoopGraphStore`.
- `milvus`, `rag4c`, and `milvus_vector_graph` still resolve to the same
  canonical engine and use the same `RagGraphStore` constructor.
- Existing `GraphStoreRegistry` instance caching and active-engine behavior
  remain single-active-engine behavior, with atomic first construction and
  explicit invalidation after custom registration changes.
- This slice does not change graph entity/relation data, tenant semantics,
  query API behavior, dataset authority, or projection consistency contracts.
- Pipeline flags keep their current default-to-Milvus behavior; custom engine
  selection is available through the registered graph engine setting or
  explicit override.

## Verification plan

- Test built-in aliases and construction parity.
- Test live custom registration through resolution and `create_graph_store`
  without changing the host factory.
- Test duplicate/alias collision/invalid/async/signature registration
  failures, built-in reservation, replacement, and unregister cleanup.
- Test unknown enabled engines remain fail-closed.
- Run the graph registry/Adapter/retrieval/projection regression suite,
  Ruff, `.venv` compile checks, and `git diff --check`.
- Request an independent sub-agent review after implementation and record
  the final result in a handoff document.
