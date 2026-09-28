# Catalog-deleted target candidate audit

Date: 2026-09-26

## Outcome

Added a separate dataset-scoped projection target enumeration boundary:

- `core/projection_consistency_enumerators.py`
- `core/milvus_client.py`
- `scripts/audit_catalog_deleted_projection_candidates.py`

The new `ProviderRegistry`/Adapter seam is intentionally separate from the
known-document consistency reader. It enumerates only projection identity
fields (`chunk_id`, `doc_id`, tenant, dataset), includes empty/null scope rows
for explicit incomplete handling, and conservatively marks a full page as
incomplete.

The new CLI-only audit compares exact-scoped target references with a stable
Catalog document-ID snapshot. A target row whose `doc_id` has no Catalog row
is reported only as a **possible Catalog-deleted candidate**. Lifecycle
`deleted` rows remain in the Catalog ID set and are not classified as missing.
The report is always `complete=false`, `confirmable=false`, and
`repairable=false`; it never creates an operation, attempt, repair plan, API
route, or external-store mutation.

All output references are HMAC-pseudonymized. Incomplete reasons are a closed
safe-code vocabulary, and invalid adapter/backend results become structured
incomplete reports without exposing raw IDs or backend exception text.
Milvus identity fields are type-checked instead of stringified.

Graph remains explicitly unsupported for this chunk-shaped enumerator.

## Verification

- New enumerator/candidate audit suite: **15 passed**.
- Existing projection reader/repair/requeue regression: **36 passed**.
- Existing chunk reconciliation regression: **44 passed**.
- Consistency API regression: **34 passed**.
- Ruff: passed.
- `.venv` `py_compile`: passed.
- `git diff --check`: passed.
- Independent sub-agent review initially found three P2 issues:
  identity stringification, unstructured exception output, and
  adapter-controlled reason leakage. All were fixed with strict typing,
  structured safe incomplete handling, and a closed reason-code set.
- Follow-up review: **PASS**, no actionable P0–P3 findings.

Existing deprecation warnings are unchanged.

## Explicit boundaries

This is not a Catalog/target authority and does not prove target snapshot
stability. It does not implement a Graph comparator, Catalog mutation
generation, confirmable orphan drift, or target-specific atomic repair.
Those remain blocked on the authority and product contracts documented in the
Axis #3 audit.

Design:
`docs/plans/2026-09-26-catalog-deleted-target-candidate-audit-design.md`
