# Graph engine Factory/Registry

## Outcome

The graph backend assembly seam is now Strategy + Registry based:

- `D:\program_project\python_project\RAG4C\core\graph_store_registry.py`
- `D:\program_project\python_project\RAG4C\config\settings.py`
- `D:\program_project\python_project\RAG4C\tests\test_graph_store_registry.py`

The implementation reuses the existing `core.providers.ProviderRegistry`;
it does not introduce a second plugin kernel.

## Design

`GraphEngineRegistration` declares a canonical engine name and aliases.
`register_graph_engine()` adds a custom synchronous factory, while
`GraphStoreFactory.create()` resolves the live registration and constructs the
store without an engine-specific branch.

Built-ins remain:

- `none` with `noop` and empty-name compatibility;
- `milvus_vector_graph` with `milvus`, `rag4c`, and `auto` compatibility.

`GraphSettings.engine` defaults to `auto`, preserving the historical Milvus
selection when pipeline graph indexing/retrieval is enabled. A registered
custom engine can be selected from settings or an explicit `engine_override`.
Unknown enabled engines remain fail-closed.

Registration rejects duplicate/colliding names and aliases, invalid names,
async/generator factories, and built-in replacement/removal. Resolution
rejects deferred factory results and validates the returned store against the
complete graph consumer Adapter port. Nested `UnknownProviderError` from a
factory remains unchanged so an inner provider failure is not misreported as
an unknown graph engine.

`GraphStoreRegistry` uses an `RLock` and atomic `get_or_create` construction.
Replacing or unregistering a custom engine invalidates matching live instance
caches after releasing the global engine lock.

## WeKnora-aligned rationale

This follows the local WeKnora review: the engine registration declaration
owns construction aliases and capability/failure semantics, while callers use
the stable factory/Adapter seam. It intentionally does not copy WeKnora's
unknown-parser remote fallback: graph engines are projection infrastructure
and unknown names must fail closed.

## Verification and review

- Graph registry tests: **20 passed**
- Graph registry/Adapter/retrieval/projection/delete/ingest regression:
  **88 passed**, 837 existing deprecation warnings
- Ruff, `.venv` `py_compile`, and `git diff --check`: passed
- Independent sub-agent follow-up review: **PASS** for runtime behavior; the
  only P3 was the missing handoff document, now completed.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-graph-engine-factory-registry-design.md`

## Scope boundary

This slice does not add a new graph database, change graph entity/relation
storage, change tenant semantics, change API/OpenAPI vocabulary, or make Graph
a Catalog consistency authority. It only makes backend selection and
construction extensible.
