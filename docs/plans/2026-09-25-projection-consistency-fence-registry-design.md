# Projection consistency target-observation fence registry design (2026-09-25)

## Context

The projection consistency reader registry now separates chunk-shaped read
adapters from projection writers, but `ProjectionConsistencyReadResult.snapshot_token`
is not yet a trust boundary. A reader could return an opaque token, while the
reconciler has no target-specific way to prove that the projection did not
change during the read.

Milvus remains deliberately unfenced in this slice: its current client does
not expose a generation or snapshot token that can be compared with the
Catalog. This change must not promote Milvus' `best_effort` read into a
cross-store authority claim.

## Design

1. Add `core/projection_consistency_fences.py` as a separate Adapter +
   Strategy/Registry seam, reusing `core.providers.ProviderRegistry`.
2. A **document-scoped target-observation adapter** owns two synchronous steps:
   - `begin(request)` captures an opaque target generation/snapshot observation.
   - `finish(request, begin_token, read_result)` compares the target state after
     the materialized read and returns `stable`, `changed`, or `unavailable`.
3. The reader host invokes the optional fence at the boundary. A reader-owned
   `snapshot_token` is never trusted by itself:
   - no registered fence => the result is explicitly `unfenced` and the token is
     cleared;
   - `stable` => the result is marked `target_observation_stable` with the
     adapter-owned token;
   - `changed` or `unavailable` => the result is converted to an explicit
     incomplete read, so comparison and repair cannot treat it as an empty or
     stable projection.
4. Registration validates exact target keys, sync callable shape, and verdict
   shape. `graph_projection` remains intentionally unsupported; adding a fence
   cannot turn graph entities/relations into chunk rows.
5. `reconcile_chunk_authority()` resolves one adapter once per report, but
   invokes its begin/finish pair for each document. The summary therefore calls
   the result a target observation, not a report-wide fence. A future
   dataset-scoped slice must add a typed Catalog snapshot identity and a
   report-level begin/finish lifecycle before any cross-store authority claim.
   Existing Milvus behavior remains `unfenced`, `best_effort`, `catalog_only`,
   and non-confirmable.
6. This slice adds no database column, migration, OpenAPI enum, or frontend
   contract. A future target can register a real fence without editing the
   reconciler, but it still cannot enable repair or multi-target authority
   without separate target-specific contracts.

## Verification

- Fence registry tests cover duplicate/invalid/deferred registrations,
  stable/changed/unavailable observations, unknown/Graph refusal, and the
  explicit "reader token without a fence is not trusted" boundary.
- A reconciliation integration test registers a custom chunk reader and a
  generation fence, then verifies stable reads compare normally while changed
  and unavailable observations are surfaced as incomplete and excluded from
  drift counts.
- Existing Milvus reader/reconcile/API regressions remain unchanged; the
  public API/OpenAPI/frontend contract is deliberately not extended.
- An independent sub-agent reviews the implementation before handoff.

## Explicit non-goals

- This slice does not bind a target generation to the Catalog dataset
  mutation generation or document snapshot fingerprint.
- It does not provide a dataset-wide target enumeration, so deleted-catalog
  document orphans remain out of scope.
- It does not set `confirmable=true`, enable repair for custom targets, or
  claim that a stable per-document observation is an atomic multi-document
  snapshot.
