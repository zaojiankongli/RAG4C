# Stage30 Release Quality certification capability producer handoff (2026-09-24)

## What changed

- Registered the reserved `release_quality_certification` producer in the
  existing catalog capability `ProviderRegistry`. The public
  `inspect_enterprise_release_quality_certification_capability()` API now
  dispatches through `inspect_catalog_capability("release_quality_certification", bind)`.
- Kept `_KNOWLEDGE_SERVING_ORIGINAL_QUALITY_CAPABILITY` unchanged and wired it
  as the policy's legacy fallback. No second registry or dispatch kernel was
  introduced.
- Reused `_enterprise_release_quality_certification_capability_issues`
  unchanged for Stage30 schema, policy, trigger, and parent-authority checks.
  The shared `CatalogCapabilityPolicy` owns only the revision ladder,
  required-table disposition, and probe error behavior.
- Reserved built-in identity remains protected by public registration guards
  and runtime factory identity checks, including raw private-registry
  replacement/removal.

## Compatibility contract

- A known pre-0030 revision with no Stage30 tables remains `not_available`.
- A pre-0030 partial schema delegates to the frozen inspector and retains its
  legacy revision issue.
- At 0030 or a later known revision, zero Stage30 tables fail closed with the
  sorted `required tables are missing: ...` issue. Partial and complete
  schemas continue through the unchanged Stage30 checker.
- Unknown, absent, empty, and multiple Alembic revision cases delegate to the
  frozen inspector after the shared probe connection closes.
- The active wrapper still propagates first-probe/checker exceptions. The
  legacy fallback retains its own catch-and-return behavior for fallback
  connection failures.
- Stage29 parent authority and Stage31 consumers remain unchanged.

The compatibility tests compare the registered producer against
`_knowledge_serving_revision_compatible(...)` and directly against the frozen
inspector on fallback branches. They cover pre-0030/0030+/later revisions,
zero/partial tables, unknown/missing/empty/multiple revisions, complete
catalog state, probe failure, fallback connection failure, and built-in
replacement/removal.

## Verification

- Capability producer file: **55 passed**.
- Stage29 release, Stage30 certification, Stage31 migration, catalog schema,
  Knowledge Serving readiness, enterprise readiness API, and Stage17
  authorization-security regression selection: **288 passed** with **1,381
  existing deprecation warnings**.
- Ruff check, `.venv` `py_compile`, and `git diff --check` passed.
- Independent sub-agent review: **PASS**, no actionable findings. The reviewer
  independently ran 19 Stage30-focused tests and 4 related readiness tests.

## Boundaries

- No migration, DB CHECK, public response shape, API/OpenAPI, frontend,
  readiness consumer, certification policy, or authorization change.
- This completes only the Stage30 Release Quality certification capability
  producer slice. Axis #17 remains open; other frozen wrappers and standalone
  capability inspectors require separate compatibility reviews.

Design record:
`docs/plans/2026-09-24-release-quality-certification-capability-producer-design.md`.
