# Stage30 Release Quality certification capability producer design (2026-09-24)

## Context

Stage30 `inspect_enterprise_release_quality_certification_capability()` still
uses `_knowledge_serving_revision_compatible` directly. Stage29 Release now
uses the shared `CatalogCapabilityPolicy` + `ProviderRegistry` producer, and
Stage30 is its readiness-dependent child. The migration must preserve the
frozen original and active-wrapper behavior exactly.

## Decision

- Register `release_quality_certification` as a reserved built-in in the
  existing catalog capability registry.
- Preserve `_KNOWLEDGE_SERVING_ORIGINAL_QUALITY_CAPABILITY` unchanged and use
  it for the same legacy revision branches after the shared probe connection
  closes.
- Reuse `_enterprise_release_quality_certification_capability_issues`
  unchanged for partial and complete Stage30+ schemas.
- Pin the sorted generic missing-table issue and first-probe/checker exception
  propagation owned by the active compatibility wrapper.
- Keep Stage30 schema, migrations, Stage31 consumers, readiness contracts,
  APIs/OpenAPI, and policy behavior unchanged.

## Compatibility matrix

Compare the new policy against
`_knowledge_serving_revision_compatible(bind, original, ...)`:

1. Known 0029 revision with no Stage30 tables: `not_available`.
2. Known pre-0030 revision with partial Stage30 tables: frozen original
   revision issue.
3. Revision 0030 and later with zero Stage30 tables: exact sorted
   `required tables are missing: ...` issue.
4. Revision 0030+ with partial tables: exact domain-checker issues.
5. Unknown, absent, empty, and multiple Alembic revisions; compare fallback
   branches directly with `_KNOWLEDGE_SERVING_ORIGINAL_QUALITY_CAPABILITY`.
6. Complete Stage30+ schema and data/constraint corruption cases already
   covered by release-quality readiness tests.
7. First probe exception versus fallback connection exception.

Built-in public and raw-registry replacement/removal must fail closed.

## Boundaries

- No DB migration/CHECK, API/OpenAPI, frontend, readiness consumer,
  certification policy, or authorization change.
- Do not remove the frozen original alias.
- Keep this as a separate behavior-equivalence slice; it does not close
  backend extensibility axis #17.

## Verification

- Add real shared-registry dispatch, compatibility-matrix, fallback, exception,
  and reserved built-in tests.
- Run Stage29 release capability/migration, Stage30 certification migration,
  Stage31/readiness, capability producer, enterprise readiness API, and
  Stage17 authorization security regressions.
- Run Ruff, `.venv` `py_compile`, OpenAPI export check, and `git diff --check`.
- Request an independent sub-agent code review after implementation.
