# Durable delete target registry (2026-09-24)

## Change

- Replaced the closed `_DELETE_STORES` tuple with an ordered
  `ProviderRegistry` policy in `core/document_delete_targets.py`. Built-in
  `milvus_chunks` / `graph_projection` order is unchanged and those targets
  cannot be replaced or removed.
- Custom deletion activation is exposed through
  `register_projection_delete_target_runtime`. It verifies all projection
  handlers, revision and attempt policies, and the consistency/requeue contract
  before installing a durable-delete target policy.
- Each custom target policy stores a runtime preflight callback. A new delete
  request re-runs it after idempotency replay detection but before changing
  document state, writing parent rows, or enqueueing child operations.
- Custom target retirement removes only its ingestion producer policy. The
  durable-delete target policy and worker/revision/lifecycle handlers stay
  registered so future deletes continue to clean old external data.
- `request_batch` resolves the ordered target snapshot once and uses it for all
  accepted items in that batch, `required_store_count`, and persisted
  `delete_document` children.
- Completion, retry/dead-letter counts, and finalizer readiness now count
  persisted child rows scoped by `delete_operation_id`. They no longer depend
  on the current registry after work is durably recorded.

## Safety and compatibility

- Existing Milvus/Graph delete order, dedup keys, generation fences, source
  suppression, chunk manifest checks, and finalizer barrier are preserved.
- A target is retired from ingestion, not physically removed from the delete
  registry. Existing and future deletes continue to include it. If its runtime
  handler becomes unavailable, preflight fails before mutation and the delete
  cannot silently finalize.
- No schema migration, CHECK, API/OpenAPI, or frontend contract changed.
- Read-side consistency evidence remains open; the operation/requeue contract
  is a guard, not a general consistency reporter.

## Verification

The custom target integration asserts it is persisted as a child, counts
toward the required/completed store barrier, remains in later delete requests
after ingestion retirement, and that a post-retirement delete runs its own
children through finalization.

- Full durable-delete regression:
  **85 passed, 1 skipped, 2,478 existing deprecation warnings** after retirement
  semantics and the test-isolation fix.
- Focused registry/runtime/finalizer regression after the retirement fix:
  **3 passed**.
- Ruff, `py_compile`, focused format checks, and `git diff --check` passed.
- Independent follow-up review initially found that physically unregistering a
  target could omit its old external data from future deletes (P1). Retirement
  now removes only the ingestion producer while retaining delete coverage. The
  reviewer confirmed the later delete reaches finalization; test teardown also
  removes its temporary registration so it cannot contaminate subsequent tests.
  Follow-up review: **PASS**, no remaining P1/P2 findings.
