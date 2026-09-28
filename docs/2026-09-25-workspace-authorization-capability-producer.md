# Stage17 Workspace Authorization capability producer handoff (2026-09-25)

## What changed

- Registered the reserved `workspace_authorization` producer in the existing
  catalog capability `ProviderRegistry`. The public
  `inspect_workspace_authorization_capability()` API now dispatches through
  `inspect_catalog_capability("workspace_authorization", bind)`.
- Preserved `_KNOWLEDGE_SERVING_ORIGINAL_WORKSPACE_AUTHORIZATION_CAPABILITY`
  unchanged and wired it as the policy's legacy fallback. No second registry
  or dispatch kernel was introduced.
- Added a revision-aware checker adapter that forwards the observed Alembic
  revision as `approval_action_revision` to the existing
  `_workspace_authorization_capability_issues` checker.
- The producer probes only the Stage17 policy table; workspace-control
  dependencies, approval-action checks, structural contracts, and data
  invariants remain in the original domain checker.
- Reserved built-in identity remains protected by public registration guards
  and runtime factory identity checks, including raw private-registry
  replacement/removal.

## Compatibility and security contract

- A known pre-0027 revision with no policy table remains `not_available`.
- A pre-0027 partial schema delegates to the frozen inspector and retains its
  legacy revision issue.
- At 0027 or a later known revision, zero policy tables fail closed with the
  active wrapper's sorted `required tables are missing: ...` issue. Partial
  schemas continue through the revision-aware original checker.
- The checker receives the exact observed revision, preserving approval-action
  selection for Stage17 and later catalog revisions.
- Unknown, absent, empty, and multiple Alembic revision cases delegate to the
  frozen inspector after the shared probe connection closes.
- Unstamped catalogs preserve the special legacy distinction: an empty policy
  table is `not_available`, while a non-empty policy table is unavailable with
  the known-revision issue.
- The active wrapper still propagates first-probe/checker exceptions. The
  legacy fallback retains its own catch-and-return behavior for fallback
  connection failures.
- Existing fail-closed authorization security regressions remain unchanged.

The compatibility tests compare the registered producer against
`_knowledge_serving_revision_compatible(..., revision_aware=True)` and directly
against the frozen inspector on fallback branches. They cover pre-0027/0027+,
unknown/missing/empty/multiple revisions, zero/partial tables,
approval-action forwarding, unstamped empty/non-empty policy state, complete
catalog state, probe failure, fallback connection failure, and built-in
replacement/removal.

## Verification

- Capability producer file: **132 passed**.
- Stage17/18 authorization and registry, catalog schema, readiness, enterprise
  authorization API/core, and security regression selection: **387 passed**
  with **1,686 existing deprecation warnings**.
- Ruff check, `.venv` `py_compile`, and `git diff --check` passed.
- Independent sub-agent review: **PASS**, no actionable findings. The reviewer
  independently ran 20 Stage17 producer tests, 24 security regressions, 14
  catalog-capability tests, and 1 schema-focused test.

## Boundaries

- No migration, DB CHECK, public response shape, API/OpenAPI, frontend,
  readiness consumer, approval behavior, or authorization policy change.
- This completes only the Stage17 Workspace Authorization capability producer
  slice. Axis #17 remains open; other frozen wrappers and standalone
  capability inspectors require separate compatibility reviews.

Design record:
`docs/plans/2026-09-24-workspace-authorization-capability-producer-design.md`.
