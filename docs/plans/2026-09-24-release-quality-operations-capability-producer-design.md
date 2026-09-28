# Stage31 Release Quality Operations capability producer design (2026-09-24)

## Context

Stage31 `inspect_enterprise_release_quality_operations_capability()` still
routes directly through `_knowledge_serving_revision_compatible`, while its
parent Stage30 certification capability now uses the shared
`CatalogCapabilityPolicy` + `ProviderRegistry` producer. Stage31 owns
observation guards, operational data invariants, and parent-authority checks,
so the migration must preserve the frozen original and active-wrapper behavior.

## Decision

- Register `release_quality_operations` as a reserved built-in in the
  existing catalog capability registry.
- Preserve `_KNOWLEDGE_SERVING_ORIGINAL_QUALITY_OPERATIONS_CAPABILITY`
  unchanged and use it for legacy revision branches after the shared probe
  connection closes.
- Reuse `_enterprise_release_quality_operations_capability_issues` unchanged
  for partial and complete Stage31+ schemas.
- Pin the generic sorted missing-table issue and probe/checker exception
  propagation owned by the active compatibility wrapper.
- Keep Stage31 schema, migrations, operational consumers, readiness contracts,
  APIs/OpenAPI, and parent authority behavior unchanged.

## Compatibility matrix

Compare the new policy against
`_knowledge_serving_revision_compatible(bind, original, ...)`:

1. Known pre-0031 revision with no Stage31 tables: `not_available`.
2. Known pre-0031 revision with a partial Stage31 schema: frozen original
   revision issue.
3. Revision 0031 and later with zero Stage31 tables: exact sorted
   `required tables are missing: ...` issue.
4. Revision 0031+ with partial tables: exact domain-checker issues.
5. Unknown, absent, empty, and multiple Alembic revisions; compare fallback
   branches directly with the frozen original.
6. Complete Stage31+ schema and existing operational data/trigger/parent
   readiness corruption cases.
7. First probe exception versus fallback connection exception.

Built-in public and raw-registry replacement/removal must fail closed.

## Boundaries

- No DB migration/CHECK, API/OpenAPI, frontend, readiness consumer,
  operational policy, or authorization change.
- Do not remove the frozen original alias.
- Keep this as a separate behavior-equivalence slice; it does not close
  backend extensibility axis #17.

## Verification

- Add real shared-registry dispatch, compatibility-matrix, fallback,
  exception, and reserved built-in tests.
- Run Stage29/30 capability and migration tests, Stage31 migration/readiness
  tests, capability producer tests, enterprise readiness API, and Stage17
  authorization security regressions.
- Run Ruff, `.venv` `py_compile`, OpenAPI export check, and `git diff --check`.
- Request an independent sub-agent code review after implementation.
