# Projection consistency reader registry design (2026-09-24)

## Context

Axis #3 now has extensible write, revision, attempt, durable-delete, and
dead-letter-replay policies. Its report reader is still hard-wired to
`RagMilvusClient.query_chunks_by_doc()` in
`scripts/reconcile_chunk_authority.py`.

The existing report has important limits which a registry must not hide:

- Milvus reads are capped at 16,384 rows per document and have no snapshot
  token exposed to the reconciler.
- The catalog transaction stabilizes catalog reads only; it does not fence an
  external projection generation.
- Scanning existing catalog documents cannot find projection rows belonging to
  documents already removed from the catalog.
- `ReconcileReport.complete` means the catalog document page has no next cursor;
  it does not mean the projection scan is complete.
- Graph entities and relations are not one-row-per-chunk records. The existing
  graph passage-reference check is deletion verification, not a consistency
  report reader.

Therefore this change introduces a **chunk-projection reader seam**, not
multi-target consistency authority. The KnowledgeOps API remains Milvus-only,
best-effort, non-confirmable, and non-repairable through its existing repair
endpoints.

## Design

1. Add a `ProjectionConsistencyReader` adapter contract and exact
   `target_store` registry in `core/projection_consistency_readers.py`, reusing
   the repository's `ProviderRegistry`.
2. A reader returns a typed, materialized result containing its chunk rows,
   enumeration status, and an optional target snapshot token. Best-effort rows
   require explicit tenant/dataset scope; mismatched rows fail closed. A reader
   may explicitly mark absent scope as incomplete, which skips comparison rather
   than converting the read into an empty projection.
3. Register the current Milvus behavior as a reserved built-in adapter. Its
   status remains best-effort because no target snapshot is available. At the
   16,384-row cap, the reconciler skips comparison for that document and records
   an incomplete read instead of reporting false missing/orphan drift. A
   best-effort row with absent scope is treated the same way. Its document
   query filters by tenant and dataset while admitting only empty/null-scope
   rows into the adapter so those rows become `incomplete` rather than
   disappearing as false missing chunks; non-empty foreign scopes are not
   selected.
4. The reconciler accepts an exact `target_store` for internal in-process use and
   resolves its adapter once per report. Only report-only chunk projections
   are eligible in this slice. Repair remains restricted to the existing
   Milvus target until target-specific atomic repair semantics are designed.
5. Add `projection_read_incomplete_documents` and a
   `projection_read_status` (`best_effort` / `incomplete`) to the report and API.
   Incomplete documents are excluded from both authoritative-head and projection
   totals as well as drift classification. Keep `complete` as the existing
   catalog-page/cursor property. The API continues to report
   `best_effort=true`, `confirmable=false`, and
   `snapshot_guarantee="catalog_only"`; it does not aggregate counts from
   multiple stores.
6. Reserve Graph as explicitly unsupported in the reader registry. Neither the
   public registration helper nor direct resolver dispatch can turn graph
   entities/relations into chunk rows. A future Graph audit requires its own
   fact snapshot, completeness contract, and comparator.

## Compatibility

- Existing callers may continue passing the Milvus client as the second
  positional argument.
- Default CLI/API behavior remains `milvus_chunks`; this slice does not expose
  a multi-target API selector.
- Existing drift comparison, pseudonymization, cursor signing, catalog fences,
  and repair payloads remain unchanged below the Milvus result cap.
- No database schema or migration is added, and no authorization or repair
  endpoint is opened. The API summary adds an explicit
  `projection_read_status` and incomplete-read count; generated OpenAPI types
  and the consistency view are updated together.

## Verification

- Registry tests: built-in immutability, exact key and sync factory shape,
  unknown target refusal, malformed/deferred reader result refusal.
- Reconcile tests: legacy Milvus behavior, registered custom chunk adapter
  dispatch, scope/duplicate-row rejection, capped read skipped and surfaced,
  custom repair refused before writes, and Graph/unknown target fail-closed.
- Existing catalog snapshot/cursor, repair enqueue, and API summary regressions.
- Independent sub-agent code review before handoff.
