# Catalog-deleted target candidate audit design (2026-09-26)

## Context

The existing consistency scan enumerates Catalog `Document` rows and then
reads projection chunks per document. It cannot discover rows whose `doc_id`
has no Catalog row. Milvus has a dataset count but no dataset-scoped, bounded
row enumeration contract. Graph is not part of this slice.

Catalog and target do not share a verifiable generation. Therefore enumeration
must not be interpreted as an authoritative orphan set, confirmed drift, or
repair plan.

## Decision

Add a separate dataset-scoped `ProjectionTargetCandidateEnumerator` port and a
Milvus implementation for an **internal CLI-only best-effort candidate
audit**. Keep it separate from `ProjectionConsistencyReader`:

- the existing reader answers “read chunks for this known Catalog document”;
- the new enumerator answers “list bounded target row references in this
  tenant/dataset scope so they can be compared to one Catalog document-ID
  snapshot.”

The audit will:

1. Capture all Catalog document IDs, including lifecycle tombstones, inside
   the existing stable Catalog snapshot transaction.
2. Enumerate only materialized Milvus projection references under both exact
   tenant and dataset filters, also surfacing empty/null-scope rows so they
   cannot disappear as false negatives.
3. Report rows whose exact-scoped `doc_id` is absent from the Catalog snapshot
   as **possible Catalog-deleted target candidates**, never as confirmed
   orphans.
4. Mark the result `best_effort` and `complete=false` by construction; cap,
   malformed/missing scope, duplicate identity, or malformed rows add an
   `incomplete` status/reason.
5. Pseudonymize all document/chunk references and expose no row content.
6. Keep candidates out of the existing drift fields and enqueue path. No
   public API, confirmation endpoint, Graph comparator, or repair behavior is
   added.

All incomplete reasons are a closed, non-sensitive code vocabulary:
`target_scope_unavailable`, `target_enumeration_limit_reached`,
`target_enumeration_invalid`, and `target_enumeration_unavailable`. Adapter
and backend details are never copied into the report. Invalid adapter/backend
results are converted to structured incomplete reports by the audit host.
Milvus identity fields are type-checked before they enter the typed reference
contract; no arbitrary value is stringified into a candidate ID.

The CLI mode is read-only and cannot be combined with `--repair`, cursors, or
`--fail-on-drift`. An incomplete enumeration returns a non-success exit code;
finding a candidate alone is informational.

## Compatibility and safety invariants

- Existing `reconcile_chunk_authority()` report shape and default CLI behavior
  remain unchanged when the new audit flag is absent.
- Dataset rows with lifecycle `deleted` remain in the Catalog ID set and are
  not classified as Catalog-deleted.
- Only exact tenant/dataset rows can be candidates. Empty/null scope is
  incomplete and excluded from candidate classification.
- A query at the bounded row cap is conservatively incomplete.
- No target generation is claimed; candidates remain best-effort even when the
  Catalog SQL snapshot is stable.
- No queue/operation/attempt is created and no external projection is mutated.

## Verification

1. Add failing tests for bounded Milvus enumeration, unscoped visibility,
   candidate-vs-tombstone behavior, incomplete conditions, pseudonymization,
   and zero mutation/API effects.
2. Implement the separate enumerator adapter and CLI-only report path.
3. Run the focused backend suite, Ruff, `py_compile`, and `git diff --check`.
4. Request independent review, fix findings, and rerun affected tests.
5. Write the implementation handoff and update cumulative progress.
