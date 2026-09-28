# Catalog-bound report-invocation projection observation fence design (2026-09-25)

## Context

The previous slice added a document-scoped target observation fence. It can
detect that one target read changed during one document query, but it cannot
prevent a report from mixing target generations between document A and
document B.

This slice adds a separate report-level adapter seam. It must bind the target
observation to the exact Catalog scan identity without pretending that the
existing Catalog cursor identity is a database mutation generation or that
Milvus already has a snapshot API.

## Design

1. Add `core/projection_consistency_report_fences.py` using the shared
   `ProviderRegistry`.
2. Define a typed `CatalogProjectionSnapshotIdentity` containing:
   tenant, dataset, the existing opaque dataset identity digest, document
   snapshot fingerprint, and document count. The names deliberately say
   *identity*, not mutation generation.
3. A report fence has synchronous `begin_report(identity)` and
   `finish_report(identity, session, documents_read)` methods. Begin returns a
   typed immutable session containing the target snapshot token and the
   Catalog identity it is bound to.
4. Reconciliation resolves one report fence once per report invocation/page,
   starts it after the Catalog scan state is captured, passes the target
   snapshot token to every reader request, and finishes it after all document
   reads for that invocation. Resume pages do not silently reuse a previous
   external token.
5. If the report fence is changed or unavailable, discard all comparison
   counts, drift IDs, repair plans, and stale reasons for that report; expose
   explicit incomplete status and refuse repair. A report-level target change
   must never be represented as an empty projection or partial drift result.
6. Milvus remains explicitly reserved as report-unfenced. Graph remains
   unsupported. Existing document-scoped observation fences remain available
   for targets that do not yet implement the report-level contract.
7. No database schema, Milvus schema, OpenAPI, or frontend contract changes.
   This still does not enumerate target rows for Catalog-deleted documents,
   provide Graph comparison, or enable confirmable repair. This is not a
   cross-page dataset-wide target snapshot authority.

## Verification

- Registry tests cover identity/session binding, duplicate/invalid/deferred
  factories, raw Graph injection, and Milvus reservation.
- Reconcile integration covers one token shared across multiple documents,
  stable report comparison, changed report fail-closed reset, unavailable
  report refusal, and no repair enqueue.
- Existing reader, document-fence, reconcile, and API regressions remain green.
- Independent sub-agent review is required before handoff.
