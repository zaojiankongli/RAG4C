# Projection dead-letter requeue policy registry design (2026-09-24)

## Problem and scope

Axis #3 already has registered worker handlers, revision strategies, attempt
lifecycle policies, durable-delete targets, and activation preflight. Its
dead-letter requeue boundary is not yet extensible: `core/projection_target_contract.py`
contains static `(target_store, operation)` sets, and
`server/knowledge_consistency_api.py` uses those sets to choose ordinary versus
document-delete lineage validation. Adding only a static allowlist entry is
unsafe because it does not select the ordinary validator.

This slice makes **dead-letter requeue eligibility and its additional
target-specific validation** a live, exact-pair registry. It does not claim to
add consistency-report readers for custom stores. `_default_reconcile()` still
audits Milvus through `reconcile_chunk_authority`; a future per-target report
reader needs its own snapshot/authority design and remains open.

## Decision

- Replace the static supported-pair sets with immutable
  `ProjectionRequeuePolicy` entries in one `ProviderRegistry`, keyed by
  `target_store:operation`.
- Each policy declares the existing semantic family (`ordinary` or
  `document_delete`) and a required synchronous target validator for every
  non-built-in pair. The validator is additive: common scope, generation,
  chunk, source, and delete-parent fences remain owned by the host and run
  before the plugin validator.
- Register the existing Milvus, Graph, and catalog-finalizer pairs as reserved
  built-ins. They cannot be replaced or removed.
- Make dead-letter API dispatch and projection-runtime preflight query the
  same registry live. Unknown/missing policies fail closed. Do not infer
  requeue eligibility from a worker handler alone.
- Keep requeue construction unchanged: after validation, copy target,
  operation, revisions, generation, delete parent, and payload from the
  canonical original operation. Preserve linked-replay idempotency before
  current-generation validation.

## Validator boundary

The extension validator receives an immutable snapshot of target/operation,
scope identifiers, attempt kind, revision/generation values, and a copied
payload. It cannot replace common lineage validation or construct the replay
operation. It must be synchronous and return exactly `True`; `False` or any
other return value rejects the requeue. Built-in policies have no extension
validator because their current host validators remain authoritative.

Custom `reconcile` is not required for target activation. It may be enabled
only by explicitly registering an exact `reconcile` pair and its validator;
the existing Milvus reconcile payload contract remains intact.

## Compatibility and invariants

- Preserve built-in pair eligibility and current response/error behavior.
- Preserve ordinary target revision checks against both document desired
  revision and attempt input revision.
- Preserve tenant/dataset/document/attempt scope, document generation,
  chunk-head/content revision, dataset generation, optional source generation,
  and delete-parent identity/generation/payload checks.
- Preserve the order where an already-linked idempotent requeue returns the
  existing operation before revalidating a generation that may have advanced.
- Preserve transaction rollback, audit, and raw operation copy semantics.
- No database schema, migration, OpenAPI, response, or frontend contract
  change.

## Tests and review

Add registry registration-shape and built-in immutability tests; replace the
durable-delete test's monkeypatched static allowlist with real custom policy
registration; and exercise a custom target through the authenticated requeue
endpoint, including live validator dispatch and preserved replay fields.
Retain and run existing tenant, generation, payload, delete-parent, idempotency,
and audit regressions. Run a reverse validation by temporarily bypassing live
policy lookup in-process and confirming the custom endpoint regression turns
red. Request independent sub-agent review before handoff.
