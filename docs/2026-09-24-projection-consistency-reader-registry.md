# Projection consistency reader registry handoff (2026-09-24)

## What changed

- Added `core/projection_consistency_readers.py`: exact `target_store` adapter
  registry using the shared `ProviderRegistry`, with typed request/result
  contracts and host-side validation for materialization, duplicate IDs,
  document identity, tenant, and dataset scope.
- Adapted `reconcile_chunk_authority()` to resolve a reader once per report.
  Existing positional Milvus callers and default CLI/API behavior remain
  `milvus_chunks`.
- Added an audit-only unscoped visibility option to
  `RagMilvusClient.query_chunks_by_doc()`. It returns only the requested
  tenant/dataset plus empty/null-scope rows. A returned unscoped row is marked
  incomplete; non-empty foreign tenant/dataset values are rejected.
- At the 16,384-row cap or when row scope is incomplete, reconciliation skips
  that document's drift comparison and excludes it from both
  `authoritative_heads` and `projection_chunks`. The incomplete document count
  and read status remain explicit. Incomplete repair reports fail before any
  enqueue.
- Graph is reserved as unsupported at both reader registration and resolution.
  Graph facts and delete-reference checks are not treated as chunk projections.
- Added API `projection_read_status` and
  `projection_read_incomplete_documents`; the generated OpenAPI JSON/TypeScript
  and consistency page were updated. The UI uses an amber incomplete state and
  never styles zero classified drift as clean when the read is incomplete.

## Guarantees and boundaries

- Existing comparator, pseudonymization, cursor HMAC, Catalog snapshot fence,
  and Milvus repair behavior remain in place for in-scope best-effort reads.
- `ReconcileReport.complete` still means only that catalog pagination has no
  next cursor. CLI output now also contains `catalog_scan_complete` and
  `projection_read_status`.
- `projection_read_status=best_effort` is not a shared Catalog/projection
  snapshot guarantee. The API remains `complete=false`,
  `confirmable=false`, and `snapshot_guarantee=catalog_only`.
- The API remains Milvus-only; this slice adds an in-process adapter seam, not
  a multi-target endpoint. Custom target repair remains disabled.
- Axis #3 remains open: dataset-wide projection enumeration (including deleted
  catalog documents), a Catalog-aligned target generation/fence, a Graph
  facts comparator, and target-specific atomic repair need separate designs.

## Verification

- `.venv` backend regression: **86 passed**, including registry, reconcile,
  cursor/snapshot, repair, and consistency API tests. After the final
  low-level audit-scope guard, reader + reconcile targeted tests were **18
  passed**; the independent reviewer separately reran all reader tests (**15
  passed**).
- Frontend consistency model/page tests: **20 passed**; focused ESLint passed;
  production build passed (**7,123 modules**).
- OpenAPI export and generated TypeScript used the repository `.venv`; the
  subsequent `scripts/export_openapi.py --check` passed
  (**259 paths / 201 schemas**).
- Ruff check, focused Ruff format check (excluding pre-existing formatting in
  `core/milvus_client.py`), `py_compile`, and `git diff --check` passed.

## Review record

- First independent review found a dataset-scope P1, a Graph registration
  bypass, and an incomplete/clean-state ambiguity. The implementation added
  dataset-scoped reads, reserved Graph in the resolver, and surfaced explicit
  API/UI incomplete status.
- The first scope remediation review found that an exact Milvus filter could
  hide a matching row with empty/null scope and make it look missing. The reader
  now includes only requested or empty/null scope rows in its query; the adapter
  marks the latter incomplete and skips their counts/classification.
- Follow-up review confirmed the empty/null-scope query cannot pass non-empty
  foreign tenant/dataset rows, and that unscoped rows produce `incomplete`
  rather than false missing drift. It also verified Graph remains rejected
  under raw-registry injection, the UI does not render incomplete as clean,
  and the canonical `.venv` OpenAPI check passes. The final P3 guard requiring
  both scope dimensions before an unscoped query was also independently
  checked; follow-up review: **PASS, no new findings**.

Design record: `docs/plans/2026-09-24-projection-consistency-reader-registry-design.md`.
