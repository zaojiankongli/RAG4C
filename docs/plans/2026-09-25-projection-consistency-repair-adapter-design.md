# Projection consistency repair adapter design (2026-09-25)

## Context

The projection consistency report already resolves target-specific readers and
observation fences through registries. Repair enqueueing is still closed over
Milvus in `scripts/reconcile_chunk_authority.py`:

- repair mode rejects every non-`milvus_chunks` target;
- existing-operation lookup filters a literal target and operation;
- enqueueing writes a literal `milvus_chunks`/`reconcile` operation.

That leaves a real open extension axis: a target can be readable and observed
through adapters, but cannot opt into durable repair without editing the
reconciler host. The change must not imply that every reader is automatically
repairable.

## Decision

- Add `core/projection_consistency_repairs.py` with a synchronous
  `ProviderRegistry` of explicit target repair adapters.
- A repair adapter declares its exact target and operation and translates a
  validated `ProjectionConsistencyRepairRequest` into the existing durable
  `IndexOperationQueue` shape.
- Keep `milvus_chunks`/`reconcile` as an immutable built-in adapter with the
  current payload, deduplication, CAS, attempt, and transaction semantics.
- Change the reconciler to resolve an adapter only when `repair=True`; report
  mode remains independent from repair capability.
- A target with a reader but no registered repair adapter remains fail-closed
  and keeps the existing custom-repair refusal behavior.
- Built-in adapter identity is pinned against raw registry replacement or
  removal. Custom factories pass registration-time synchronous signature
  validation; their backend-dependent adapter shape and target/operation
  contract are validated once at resolution.
- The historical Milvus `dedup_key` remains byte-for-byte compatible. Custom
  target adapters receive an explicit target/operation namespace in the
  dedup key so the database-global unique constraint cannot collapse a custom
  repair into an unrelated Milvus operation.
- Do not add Graph support, change database schema/CHECK constraints, change
  OpenAPI/frontend contracts, or make a target repairable merely by registering
  a reader.

## Compatibility boundary

- Existing Milvus repair output, deduplication, attempt lifecycle, CAS checks,
  queue payloads, and operation names remain unchanged.
- Report-only reads do not resolve or require a repair adapter.
- Unknown/unsupported targets remain fail-closed through the existing reader
  and target registries.
- A custom target becomes repairable only when it explicitly registers all
  required read and repair adapters; no host branch is added.

## Verification

- Add registry unit tests for built-in reservation, raw replacement/removal,
  malformed/async adapters, and real custom dispatch.
- Add reconciler integration coverage proving a custom registered adapter
  receives the durable enqueue while an unregistered custom target remains
  refused.
- Run the existing chunk reconciliation, projection reader/fence/report-fence,
  worker/requeue, Ruff, `.venv` `py_compile`, and `git diff --check` checks.
- Request an independent sub-agent review and write a handoff document.
