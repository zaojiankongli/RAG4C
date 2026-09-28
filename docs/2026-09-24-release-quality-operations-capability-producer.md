# Stage31 Release Quality Operations capability producer handoff (2026-09-24)

## What changed

- Registered the reserved `release_quality_operations` producer in the
  existing catalog capability `ProviderRegistry`. The public
  `inspect_enterprise_release_quality_operations_capability()` API now
  dispatches through `inspect_catalog_capability("release_quality_operations", bind)`.
- Kept `_KNOWLEDGE_SERVING_ORIGINAL_QUALITY_OPERATIONS_CAPABILITY` unchanged
  and wired it as the policy's legacy fallback. No second registry or dispatch
  kernel was introduced.
- Reused `_enterprise_release_quality_operations_capability_issues` unchanged
  for Stage31 schema, observation-guard, operational-data, and parent-authority
  checks. The shared policy supplies only the revision ladder, required-table
  disposition, and probe error behavior.
- Reserved built-in identity remains protected by public registration guards
  and runtime factory identity checks, including raw private-registry
  replacement/removal.

## Compatibility contract

- A known pre-0031 revision with no Stage31 tables remains `not_available`.
- A pre-0031 partial schema delegates to the frozen inspector and retains its
  legacy revision issue.
- At 0031 or a later known revision, zero Stage31 tables fail closed with the
  sorted `required tables are missing: ...` issue. Partial and complete
  schemas continue through the unchanged Stage31 checker.
- Unknown, absent, empty, and multiple Alembic revision cases delegate to the
  frozen inspector after the shared probe connection closes.
- The active wrapper still propagates first-probe/checker exceptions. The
  legacy fallback retains its own catch-and-return behavior for fallback
  connection failures.
- Stage30 parent authority and later readiness consumers remain unchanged.

The compatibility tests compare the registered producer against
`_knowledge_serving_revision_compatible(...)` and directly against the frozen
inspector on fallback branches. They cover pre-0031/0031+/later revisions,
zero/partial tables, unknown/missing/empty/multiple revisions, complete
catalog state, probe failure, fallback connection failure, and built-in
replacement/removal.

## Verification

- Capability producer file: **74 passed**.
- Stage29/30/31 capability and migration, catalog schema, Knowledge Serving
  readiness, enterprise readiness API, and Stage17 authorization-security
  regression selection: **307 passed** with **1,382 existing deprecation
  warnings**.
- Ruff check, `.venv` `py_compile`, and `git diff --check` passed.
- Independent sub-agent review: **PASS**, no actionable findings. The reviewer
  independently ran 19 Stage31-focused tests, 3 catalog-schema tests, and 1
  parent/readiness regression.

## Boundaries

- No migration, DB CHECK, public response shape, API/OpenAPI, frontend,
  readiness consumer, operational policy, or authorization change.
- This completes only the Stage31 Release Quality Operations capability
  producer slice. Axis #17 remains open; other frozen wrappers and standalone
  capability inspectors require separate compatibility reviews.

Design record:
`docs/plans/2026-09-24-release-quality-operations-capability-producer-design.md`.
