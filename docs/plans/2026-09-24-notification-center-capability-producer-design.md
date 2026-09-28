# Stage32 Notification Center capability producer design (2026-09-24)

## Context

Stage32 `inspect_enterprise_notification_center_capability()` still routes
directly through `_knowledge_serving_revision_compatible`, while the prior
Stage29–31 capability inspectors now use the shared
`CatalogCapabilityPolicy` + `ProviderRegistry` producer. Notification Center
owns event guards, receipt/data invariants, and later readiness parents, so
the migration must preserve the frozen original and active-wrapper behavior.

## Decision

- Register `notification_center` as a reserved built-in in the existing
  catalog capability registry.
- Preserve the original Stage32 inspector as
  `_KNOWLEDGE_SERVING_ORIGINAL_NOTIFICATION_CAPABILITY` and use it for legacy
  revision branches after the shared probe connection closes.
- Reuse `_enterprise_notification_center_capability_issues` unchanged for
  partial and complete Stage32+ schemas.
- Pin the generic sorted missing-table issue and probe/checker exception
  propagation owned by the active compatibility wrapper.
- Keep Stage32 schema, migrations, notification materialization/receipt
  consumers, readiness contracts, APIs/OpenAPI, and data/guard behavior
  unchanged.

## Compatibility matrix

Compare the new policy against
`_knowledge_serving_revision_compatible(bind, original, ...)`:

1. Known pre-0032 revision with no Notification Center tables:
   `not_available`.
2. Known pre-0032 revision with a partial notification schema: frozen
   original revision issue.
3. Revision 0032 and later with zero Notification Center tables: exact sorted
   `required tables are missing: ...` issue.
4. Revision 0032+ with partial tables: exact domain-checker issues.
5. Unknown, absent, empty, and multiple Alembic revisions; compare fallback
   branches directly with the frozen original.
6. Complete Stage32+ schema and existing notification data/trigger readiness
   corruption cases.
7. First probe exception versus fallback connection exception.

Built-in public and raw-registry replacement/removal must fail closed.

## Boundaries

- No DB migration/CHECK, API/OpenAPI, frontend, readiness consumer,
  notification materializer/receipt behavior, or authorization change.
- Do not remove the frozen original alias.
- Keep this as a separate behavior-equivalence slice; it does not close
  backend extensibility axis #17.

## Verification

- Add real shared-registry dispatch, compatibility-matrix, fallback,
  exception, and reserved built-in tests.
- Run Stage29–31 capability/migration tests, Stage32 migration/readiness and
  notification receipt/materializer tests, capability producer tests,
  enterprise readiness API, and Stage17 authorization security regressions.
- Run Ruff, `.venv` `py_compile`, OpenAPI export check, and `git diff --check`.
- Request an independent sub-agent code review after implementation.
