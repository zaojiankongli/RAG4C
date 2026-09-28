# Graph projection consumer Adapter design (2026-09-25)

## Context

`GraphStoreRegistry` already selects the configured graph backend, but
`GraphBuilder` and `GraphRetriever` previously called the selected store
directly.  That made the consumer contract implicit: a new graph backend had
to imitate the current `RagGraphStore` surface, including legacy method
shapes, tenant forwarding, raw cascade-delete operations, and retrieval
lookups.

The graph path is also used by document ingest setup and delete workers.  A
partial backend must therefore fail before an external graph side effect,
while the disabled graph backend must remain healthy and no-op.

## Decision

- Add `core/graph_projection_adapters.py` with an explicit operational port
  and `GraphProjectionAdapterFactory`.
- Keep backend assembly in `core/graph_store_registry.py`; the registry
  exposes an adapted active consumer seam without moving engine selection into
  business code.
- Make `GraphBuilder` and `GraphRetriever` retain their `.store` compatibility
  attribute but use `.adapter` for every graph operation.
- Validate only the methods required by the current consumer operation:
  - write/merge requires text lookups and entity/relation upsert;
  - cascade delete requires passage lookup, raw rewrite, id lookup, and
    tenant-scoped delete;
  - count requires passage lookup;
  - retrieval requires vector search and ID lookups.
- Validate callable signatures at adapter construction and again when an
  already-created adapter is reused for a narrower or broader operation.
- Allow collection initialization to require only `ensure_collections`; this
  keeps old setup-only stores without a `graph` attribute compatible while
  builders/retrievers still require graph settings before using graph
  operations.
- Preserve legacy fake/backend method shapes that omit optional
  `include_vectors`; the adapter detects that optional parameter and does not
  pass it to those backends.
- Forward `tenant_id` through every graph read, write, raw rewrite, and delete
  operation.  `RagGraphStore` requires raw rewrite rows to already belong to
  the requested tenant, validates that before client initialization, and uses
  the tenant in delete filters.
- Keep `NoopGraphStore` healthy and side-effect free, while exposing the
  complete consumer surface and graph settings used by builders/retrievers.
- Route collection initialization through `GraphBuilder.adapter` so setup
  uses the same operational boundary as ingest and delete.

## Compatibility boundary

- `GraphBuilder.store` and `GraphRetriever.store` remain available for
  existing diagnostics and integrations.
- Existing Milvus engine selection and `GraphStoreFactory` behavior remain
  unchanged.
- Existing graph entity/relation data is not given a new `dataset_id` or
  snapshot authority.  `tenant_id` is only a defensive operational filter;
  this slice does not make Graph a Catalog consistency authority.
- The adapter does not add graph comparator, Catalog-deleted target
  enumeration, Catalog mutation generation, or atomic consistency repair.
- Unknown engines still fail closed; disabled Graph still resolves to a
  healthy no-op backend.

## Verification plan

- Test malformed backends and incompatible signatures fail before consumer
  side effects.
- Test tenant and `include_vectors` forwarding, legacy optional-parameter
  compatibility, adapter reuse validation, builder write/delete/count paths,
  retriever search/expansion paths, and collection initialization.
- Run the graph adapter/registry/retrieval tests plus projection handlers,
  document-delete, and folder-ingest regressions.
- Run Ruff, `.venv` `py_compile`, and `git diff --check`.
- Request an independent sub-agent follow-up review after fixes and record the
  review result in the handoff document.
