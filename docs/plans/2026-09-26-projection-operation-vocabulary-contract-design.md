# Projection operation action contract design (2026-09-26)

## Context

Consistency dead-letter responses intentionally expose `target_store` and
`operation` as strings so historical or custom facts remain readable. Backend
requeue and repair paths are gated by the registered target/operation policy,
but the frontend previously rendered a requeue button for every row. The UI
must not imply that an unknown persisted pair is actionable.

## Decision

Add a frontend-owned, frozen allow-list for the built-in target/operation
pairs with verified requeue support. Keep the dead-letter read model open:

- unknown values remain visible exactly as returned;
- rows with unknown pairs show read-only status rather than a mutation button;
- the mutation callback independently rejects unknown pairs before any API call;
- known built-in pairs retain current requeue behavior;
- backend pair validation remains authoritative and unchanged;
- dynamically registered backend pairs do not gain a UI action implicitly.

This is an action-admission contract, not a closed persistence vocabulary.

## Built-in pairs

- `milvus_chunks`: `upsert`, `delete`, `reconcile`, `delete_document`;
- `graph_projection`: `upsert`, `delete`, `delete_document`;
- `catalog_finalize`: `finalize_document_delete`.

## Verification

1. Add tests for exact built-in coverage, malformed/prototype inputs, and
   unknown-value read-only rendering.
2. Centralize frontend action admission and guard the Consistency Console
   action cell.
3. Run focused consistency tests, TypeScript, ESLint, and `git diff --check`.
4. Request independent review, fix findings, and rerun affected checks.
5. Write the handoff and append cumulative progress.
