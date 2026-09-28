# Graph projection consumer Adapter

## Outcome

Completed the graph consumer extension seam for the current Graph projection
path:

- `core/graph_projection_adapters.py`
- `core/graph_store_registry.py`
- `core/graph_store.py`
- `indexing/graph_builder.py`
- `retrieval/graph_retriever.py`
- `server/documents.py`
- `tests/test_graph_store_consumer_adapters.py`

`GraphStoreRegistry` still owns backend factory/registry assembly. The new
`GraphProjectionAdapter` is the explicit consumer port used by
`GraphBuilder`, `GraphRetriever`, and collection initialization.

## Design and compatibility

- `GraphBuilder.store` and `GraphRetriever.store` remain available for
  compatibility, but graph operations go through `.adapter`.
- Adapter validation is operation-specific and checks callable signatures
  before side effects. Builder write, cascade-delete, count, retriever, and
  collection-init operations each declare their required method set.
- Existing adapters are revalidated when reused at another consumer seam.
- Legacy backends that omit optional `include_vectors` remain supported.
- Collection initialization can adapt a legacy `builder.store` with only
  `ensure_collections`; this setup-only path does not require a `graph`
  attribute. Builder/retriever paths still require graph settings.
- `tenant_id` is forwarded through search, lookup, upsert, raw rewrite, and
  delete calls. Raw rewrites reject rows whose stored tenant is missing or
  differs from the requested tenant, and perform that validation before
  creating a Milvus client.
- `NoopGraphStore` remains healthy, empty, and side-effect free while
  exposing the consumer surface.
- Raw delete filters are tenant-aware in `RagGraphStore`.

This slice deliberately does not add `dataset_id`, Graph snapshot authority,
Graph comparator support, Catalog-deleted target enumeration, Catalog
mutation generation, or atomic consistency repair. Graph is not promoted to
Catalog consistency authority.

## Review

An independent sub-agent reviewed the slice and found issues in the first
passes around method signatures, legacy optional parameters, tenant
forwarding, adapter reuse, and collection initialization. Those findings were
fixed with regression coverage. The final follow-up review was **PASS** with
no actionable P0/P1/P2/P3 findings.

## Verification

- Focused graph/registry tests: **21 passed**
- Required projection/retrieval/delete/ingest regression command:
  **76 passed**, 837 existing deprecation warnings
- Ruff: passed
- `.venv` `compileall`: passed
- `git diff --check`: passed

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-graph-projection-consumer-adapter-design.md`

## Remaining boundary

Axis #3 is still open overall. The authority-dependent Graph comparator,
Catalog-deleted target enumeration, verifiable Catalog mutation generation,
and target-specific atomic repair remain intentionally unimplemented.
