# Projection target runtime preflight design (2026-09-24)

## Problem

Registering a target producer previously let `DocumentIngestJob` enqueue work
without a corresponding production-worker handler. The projection target axis
also crosses revision advancement, attempt finalization, durable deletion, and
consistency reporting; registering only the producer is not a complete plugin.

## Decision

- Build a single `indexing.projection_target_runtime` preflight that checks
  worker operations, revision strategy, attempt lifecycle policy, and durable
  delete coverage for every selected target.
- Share the KnowledgeOps dead-letter target-operation contract between the API
  and preflight. Require each candidate store to be covered for `upsert`,
  `delete`, and `delete_document`; adding a durable-delete store alone must not
  activate it.
- Resolve and validate the complete target snapshot before chunk-authority
  persistence or any outbox enqueue. Reject the whole snapshot if one target is
  incomplete, so a failed extension cannot leave a partially projected attempt.
- Derive `ProjectionHandlers.as_mapping()` entries from registered operation
  keys so a fully wired custom store receives a production-worker dispatch
  entry without adding a store-specific branch.
- Keep durable deletion as the activation fence. Until a target is covered by
  the durable delete barrier, it cannot be emitted for production work.

## Compatibility and scope

- Milvus and Graph retain their current order, operation behavior, revision
  fences, lifecycle barriers, delete children, and worker mapping.
- A custom target with only a producer policy is deliberately rejected before
  any operation is enqueued.
- No database CHECK, migration, API/OpenAPI, or frontend contract changes.
- This is a safety boundary, not completion of projection axis #3. Durable
  delete target registration and read-side consistency projection still need
  a separate design before arbitrary stores can be enabled end-to-end.
- Runtime registrations are startup configuration. Register operation,
  revision, and lifecycle strategies before starting the worker; restart workers
  after changing target strategy configuration.

## Verification

- Real ingest test proves an incomplete custom target returns an error and
  emits no outbox operation.
- Real worker test proves a fully registered custom operation/revision/lifecycle
  can dispatch and complete through `IndexOperationWorker`.
- Durable deletion regressions pin the existing Milvus/Graph barrier.
- Independent review and focused test results are recorded in the implementation
  handoff.
