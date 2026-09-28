# Stage17 Workspace Authorization capability producer design (2026-09-24)

## Context

`inspect_workspace_authorization_capability()` still routes directly through
`_knowledge_serving_revision_compatible`, while the Stage28 registry and later
capabilities now use the shared `CatalogCapabilityPolicy` +
`ProviderRegistry` producer. This is a security-sensitive parent capability:
the migration must preserve fail-closed behavior and the original inspector's
special handling of unstamped catalogs.

## Decision

- Register `workspace_authorization` as a reserved built-in in the existing
  catalog capability registry.
- Preserve the original Stage17 inspector as
  `_KNOWLEDGE_SERVING_ORIGINAL_WORKSPACE_AUTHORIZATION_CAPABILITY` and use it
  for legacy, unknown, missing-revision, and unstamped fallback branches.
- Reuse `_workspace_authorization_capability_issues` unchanged through a
  revision-aware positional adapter that forwards the observed revision as
  `approval_action_revision`.
- Probe only the Stage17 policy table for capability presence. Dependency
  tables, approval-action checks, workspace-control columns, indexes, and data
  invariants remain in the original domain checker.
- Pin the active wrapper's generic sorted `required tables are missing: ...`
  issue for 0027+ zero-policy-table catalogs, while preserving the frozen
  original's special `not_available` behavior for known pre-0027 and
  unstamped empty-policy catalogs.
- Keep authorization consumers, migrations, APIs/OpenAPI, and all
  fail-closed security boundaries unchanged.

## Compatibility matrix

Compare the new policy against
`_knowledge_serving_revision_compatible(bind, original, ..., revision_aware=True)`:

1. Known pre-0027 with no/partial policy table: frozen original behavior.
2. Revision 0027 and later with no policy table: exact generic sorted
   `required tables are missing: ...` issue.
3. Revision 0027+ with partial tables: exact revision-aware domain-checker
   issues.
4. Unknown, absent, empty, and multiple Alembic revisions; compare fallback
   branches directly with the frozen original.
5. Unstamped empty policy table versus unstamped non-empty policy table.
6. Complete Stage17+ schema and existing authorization security regressions.
7. First probe exception versus fallback connection exception.

Built-in public and raw-registry replacement/removal must fail closed.

## Boundaries

- No DB migration/CHECK, API/OpenAPI, frontend, readiness consumer, or
  authorization policy behavior change.
- Do not remove the frozen original alias.
- Keep this as a separate behavior-equivalence slice; it does not close
  backend extensibility axis #17.

## Verification

- Add real shared-registry dispatch, revision-aware compatibility-matrix,
  unstamped special-case, fallback, exception, and reserved built-in tests.
- Run Stage17/18 security and readiness tests, Stage28–32 capability/migration
  tests, enterprise readiness API, and capability producer tests.
- Run Ruff, `.venv` `py_compile`, OpenAPI export check, and `git diff --check`.
- Request an independent sub-agent code review after implementation.
