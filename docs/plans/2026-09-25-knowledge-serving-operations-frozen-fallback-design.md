# Stage26/Stage27 Knowledge Serving and Operations frozen fallback design (2026-09-25)

## Context

The Stage26 `knowledge_serving_reliability` and Stage27
`knowledge_operations_feedback` inspectors already use the shared
`CatalogCapabilityPolicy` + `ProviderRegistry` producer. Their policies,
however, currently have no frozen legacy inspector. The shared producer
therefore makes the historical/ambiguous revision decisions itself instead of
delegating those branches to the exact pre-registry behavior.

Axis #17 requires every registry-backed capability to preserve its former
revision ladder as an explicit compatibility boundary. This slice is limited
to the two new-style capabilities that were originally converted together.

## Decision

- Preserve the pre-registry Stage26 inspector as
  `_knowledge_serving_original_knowledge_serving_reliability_capability`.
- Preserve the pre-registry Stage27 inspector as
  `_knowledge_serving_original_knowledge_operations_feedback_capability`.
- Keep both public inspector names as thin
  `inspect_catalog_capability(...)` wrappers.
- Configure the two reserved policies with their frozen inspectors as
  `fallback_inspector`.
- Let the shared producer continue to own supported-dialect handling and
  known minimum-or-later revision checks; historical, missing, unknown, and
  multiple-revision branches delegate to the frozen implementation after the
  probe connection closes.
- Add an explicit compatibility matrix for empty/partial legacy catalogs,
  missing/unknown/multiple revisions, exact minimum and later revisions, and
  the raw registry dispatch boundary.

## Compatibility boundary

- Known revisions before Stage26/Stage27 with no capability tables remain
  `not_available`.
- Known pre-minimum revisions with partial capability tables retain the
  frozen `unavailable` minimum-revision issue.
- Missing, empty, unknown, and multiple Alembic revision states retain the
  frozen issue text and state.
- Exact minimum and later revisions with missing/partial tables continue to
  use the existing Stage26/Stage27 domain checkers.
- Unsupported dialect and first-probe exception behavior remain unchanged.
- No migration, database CHECK, readiness consumer, API/OpenAPI, frontend,
  authorization, or data contract changes.

## Verification

- Test public dispatch and frozen function identity.
- Compare both registered producers with their frozen inspectors across the
  historical compatibility matrix.
- Test first-probe and fallback exception behavior.
- Run focused capability/readiness regressions, Ruff, `.venv` `py_compile`,
  and `git diff --check`.
- Request an independent sub-agent code review before writing the handoff.
