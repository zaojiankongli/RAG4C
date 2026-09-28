# Durable delete target registry design (2026-09-24)

## Context

Axis #3 still has a closed `_DELETE_STORES` tuple in
`core/document_deletion.py`. The tuple selects child operations and is repeated
in validation and completion/dead-letter counts. Simply replacing it with the
current registry at every read would be unsafe: registry changes after a delete
request could make persisted child work disappear from the finalization barrier.

## Decision

- Represent durable-delete target selection as an ordered immutable policy in
  the existing `ProviderRegistry` kernel.
- Keep built-in Milvus and Graph policies reserved, ordered, and behaviorally
  identical.
- Expose custom target activation through
  `register_projection_delete_target_runtime`, which requires worker operation,
  revision, attempt-lifecycle, and consistency/requeue contracts before adding
  the delete target. The stored policy re-runs this preflight before a delete
  request can write state or outbox rows.
- Snapshot the ordered target list once per new delete batch, after idempotent
  replay has been checked and before any mutation. Use that same snapshot for
  each accepted item in the batch, its `required_store_count`, and its persisted
  `IndexOperation` children.
- Treat persisted child rows for a `delete_operation_id` as the authoritative
  finalization set. Completion, dead-letter counts, and finalizer checks count
  those child rows instead of consulting the current registry.
- Retire a target by removing only its ingestion producer. Keep its delete
  target policy and runtime handlers active so new delete requests continue to
  clean old data. Do not physically unregister a target while its projection
  data can remain.
- Keep `projection_plan` fail-closed if a persisted target's handler/runtime is
  missing. Its child stays visible/retryable and cannot silently finalize.

## Compatibility and scope

- Existing built-in target order and the legacy `"milvus"` / `"graph"` dedup
  keys remain unchanged.
- New target activation still requires the worker handler, revision and attempt
  policies, the consistency/requeue contract, and durable-delete registration
  validated by the runtime preflight.
- No schema migration is planned: target store values and each required child
  are already persisted in `IndexOperation`.
- Read-side consistency reporting remains a separate open part of axis #3.

## Verification

- Registry tests cover deterministic order, duplicate/invalid registration,
  and built-in protection.
- A real delete-request integration verifies custom targets produce persisted
  children and the expected durable barrier count.
- Completion/finalizer regressions verify persisted children, not the live
  registry, drive the barrier.
