# Projection attempt readiness strategy design (2026-09-23)

## Decision

Convert the remaining `target_store`-dependent attempt lifecycle decisions in `IndexOperationWorker._advance_attempt_lifecycle` into `ProjectionAttemptLifecyclePolicy` entries using the existing `core.providers.ProviderRegistry`.

Each immutable policy declares:
- `role`: primary or secondary; a primary operation may transition `running -> primary_ready`;
- `blocks_finalization`: whether pending work for that target transitions `primary_ready -> finalizing`;
- synchronous `is_ready(context)`: a predicate for the current operation's projection readiness.

The worker resolves all currently represented target policies before the handler and keeps an attempt snapshot across `run_once` calls for its long-lived worker instance. This prevents handler-time replacement from changing the policy used by a later operation in the same attempt. Registry mutation is a startup/configuration action, not a live hot-swap contract; deployments that change policy semantics should drain in-flight attempts across workers before activating the new policy version.

After the projection handler and revision strategy, but before `queue.complete_operation`, the worker snapshots document revision facts in a short SQLAlchemy session, closes the session, and invokes the current target's readiness predicate. A false result or callback exception uses the existing durable retry/dead-letter boundary; the operation is never marked succeeded while its own policy says not ready. Callbacks are synchronous, bounded, read-only predicates; they do not receive an open session or mutable ORM object.

The post-completion lifecycle method invokes no plugin callback. It continues to own attempt aggregation, the current-attempt and desired/indexed fences, chunk-count/quota update and terminal transaction. Built-in policies preserve the primary/graph roles; the graph revision predicate now gates operation completion, avoiding the prior state where a succeeded last graph operation could leave an attempt with no durable work to re-evaluate readiness.

## Invariants / out of scope

- Preserve `running -> primary_ready -> finalizing -> completed` transitions and the point at which `primary_index_ready_at` is recorded.
- Keep the catalog current-attempt recheck after callback execution, so a newer attempt cannot be finalized by the older operation.
- Keep the global `desired_index_revision == indexed_revision` finalization fence.
- No database CHECK, public contract, queue schema, producer, durable-delete lifecycle, or consistency API changes in this slice.
- This does not complete axis #3: state-machine target production, durable deletion stores/lifecycle and consistency projection remain open.

## Proof

- Real `IndexOperationWorker.run_once` with registered custom target and two operations proves attempt-level policy pinning across worker calls.
- False-then-true and exception-then-true readiness sequences prove durable retry rather than silently stranded success.
- A callback that starts a newer attempt proves the finalizer rechecks the current attempt after the callback and does not mark the new document head completed.
- Missing policies fail before handler side effects; duplicate, role and callback signature are rejected at registration.
- Existing worker, projection and delete regressions remain unchanged. A source guard prevents reintroducing literal Milvus/graph target branches in attempt aggregation; a reverse validation disables policy use and expects the dynamic registration path to fail.
- Independent reviewer findings are resolved before the handoff is marked final.
