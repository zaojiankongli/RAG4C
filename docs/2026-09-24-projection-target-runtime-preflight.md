# Projection target runtime preflight (2026-09-24)

## Review finding addressed

The earlier target producer test proved only that `custom_vector` could be
written to the outbox. The production worker mapping contained only Milvus and
Graph handlers, and a newly emitted target lacked revision, attempt-lifecycle,
and durable-delete integration. Such a task could retry/dead-letter, while
document deletion could leave the external target stale.

## Implementation

- Added `indexing/projection_target_runtime.py` as a preflight over the existing
  registries. Every active candidate needs all four projection operations
  (`upsert`, `reconcile`, `delete`, `delete_document`), a revision strategy, an
  attempt-lifecycle policy, and durable-delete coverage.
- `DocumentIngestJob` resolves and validates the full target snapshot before
  persisting chunk authority or creating any outbox operation. One incomplete
  target rejects the whole snapshot, avoiding partial Milvus/Graph/custom
  enqueue.
- `ProjectionHandlers.as_mapping()` adds a generic worker dispatch entry for
  registered target stores. Built-in Graph remains absent when no graph builder
  is configured.
- `core.document_deletion.document_delete_projection_targets()` exposes the
  current durable-delete target set to the preflight without duplicating it.
  The set is now an ordered `ProviderRegistry` policy in
  `core/document_delete_targets.py`; custom activation goes through
  `register_projection_delete_target_runtime`, which requires the worker,
  revision, attempt, and consistency/requeue contracts first.
- Each custom delete-target policy carries a runtime preflight callback. The
  delete request revalidates every registered target after checking idempotent
  replay and before changing document state or writing child operations.
- Added `core.projection_target_contract.py` as the shared source for the
  KnowledgeOps consistency API's dead-letter operation allowlist and the
  preflight's minimum `upsert`/`delete`/`delete_document` coverage check. Adding
  a store to the delete set without adding its consistency/requeue contract no
  longer enables it.
- Projection operation strategies now reject async/generator callbacks at
  registration and reject deferred/non-`None` results at invocation. Registry
  lookup is isolated from strategy invocation, so an implementation's own
  `UnknownProviderError` is not misclassified as a missing operation.
- Notification route adapters now reject async/generator callbacks and safely
  reject an unexpected awaitable result at the route boundary.

## Compatibility and open boundaries

- Milvus and Graph remain production-enabled with their prior operation order,
  revision fences, attempt barriers, and durable-delete behavior.
- A custom operation/revision/lifecycle combination can execute through a real
  worker. The default durable-delete targets remain Milvus and Graph; a custom
  producer is only enabled after it is explicitly registered through the
  validated delete-target runtime API.
- Retiring a custom target removes it from new ingest production but retains
  its delete policy and runtime handlers. Do not physically remove those
  handlers while documents may still have projections in that store.
- Read-side consistency projection remains a separate open part of extensibility
  axis #3. Sharing the dead-letter allowlist is only a fail-closed activation
  guard; this slice does not claim arbitrary stores are fully supported.
- Runtime registrations are startup configuration. Install them before
  creating the worker and restart workers after changing them.
- No database migration/CHECK, API/OpenAPI, or frontend contract changed.

## Verification

- Projection worker/target/delete regression:
  **76 passed, 1,467 existing deprecation warnings**.
- Target producer + KnowledgeOps consistency API/QA-authority regression:
  **44 passed, 1,120 existing deprecation warnings**.
- Notification adapter/receipt + provider registry regression:
  **54 passed, 87 existing deprecation warnings**.
- The 107-test Automation/Stage26/27/Stage17 capability regression, nine-file
  frontend suite (**41 passed**), ESLint, production build, Ruff, `py_compile`,
  focused formatter checks, and `git diff --check` passed.
- Reverse validation: with process-local registry substitution, the custom
  target is rejected when its consistency operation pairs are absent; disabling
  only that guard makes the same preflight accept it.
- Independent follow-up review: **PASS**, no new P1/P2 correctness findings.
  It confirmed the shared consistency contract closes the prior gap where a
  future durable-delete target could be admitted without requeue coverage.

## Next

Continue axis #3 with a separate design for read-side consistency evidence.
Do not widen the producer registry's production reach beyond the shared
dead-letter/requeue contract until read-side consistency can inspect each target.
