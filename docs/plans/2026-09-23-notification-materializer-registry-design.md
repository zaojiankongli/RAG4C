# Notification source materializer strategy design (2026-09-23)

## Decision

Separate notification `source_kind` materialization behavior from the source-shape projection/receipt adapters. Add a `NotificationMaterializerPolicy` per source kind through the existing `core.providers.ProviderRegistry`, containing:
- a synchronous materialize strategy for one immutable request;
- a synchronous discovery strategy used by `reconcile_notification_sources`;
- a stable ordering value that preserves the current quality-alert-before-approval reconciliation order.

Keep the two public materializer function signatures as compatibility adapters that construct a typed request and dispatch through the registry. Move their existing bodies unchanged behind built-in policy adapters. Make reconciliation enumerate one pinned policy snapshot and invoke each policy's discovery/materialization behavior instead of hard-coding two SQL query/loop branches.

## Boundaries / invariants

- Preserve all source row locks, tenant/dataset/approver ACL checks, revision/digest validation, safe payload construction, transaction boundaries, idempotent bundle writes, recipient ordering/reasons, and public error classifications.
- Preserve existing source kinds, source/category/route/CHECK constraints, OpenAPI and frontend contracts. Registering a strategy is not a schema migration and does not authorize new DB values.
- Keep `core/notification_source_kinds.py` as the source-projection shape registry; this is a separate behavior registry. Reuse only `ProviderRegistry`, with no new registry kernel or autodiscovery.
- Do not touch notification receipts, route adapters, or authorization semantics.

## Proof

Use existing materializer contract suite as behavior-equivalence coverage; add registration validation, registry dispatch through a real persisted quality source, and a source guard proving reconciliation no longer has source-specific branches. Reverse validation temporarily bypasses the dispatcher and proves the registered strategy path fails. Independent subagent review is required.
