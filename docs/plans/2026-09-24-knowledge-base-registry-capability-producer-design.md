# Stage28 Knowledge Base Registry capability producer design (2026-09-24)

## Context

Stage28 `inspect_enterprise_knowledge_base_registry_capability()` still
routes directly through `_knowledge_serving_revision_compatible`, while later
catalog capabilities now use the shared `CatalogCapabilityPolicy` +
`ProviderRegistry` producer. Stage28 is a parent authority for release,
quality, and workspace consumers; its revision-aware approval-check contract
must remain exact.

## Decision

- Register `knowledge_base_registry` as a reserved built-in in the existing
  catalog capability registry.
- Preserve the original Stage28 inspector as
  `_KNOWLEDGE_SERVING_ORIGINAL_REGISTRY_CAPABILITY` and use it for legacy
  revision branches after the shared probe connection closes.
- Reuse `_knowledge_base_registry_capability_issues` unchanged, passing the
  observed revision through the policy's `revision_aware` checker contract.
- Use the Stage28 registry table set for the producer's presence probe.
  Dependency tables remain the issue checker's responsibility, preserving its
  detailed missing-table and approval-action behavior.
- Pin the active wrapper's generic sorted
  `required tables are missing: ...` issue for 0028+ zero-registry-table
  catalogs; the frozen original remains responsible only for legacy/unknown
  fallback branches.
- Keep Stage28 schema, migrations, approval/action contracts, readiness
  consumers, APIs/OpenAPI, and authorization behavior unchanged.

## Compatibility matrix

Compare the new policy against
`_knowledge_serving_revision_compatible(bind, original, ..., revision_aware=True)`:

1. Known pre-0028 revision with no registry tables: `not_available`.
2. Known pre-0028 revision with partial registry tables: frozen original
   revision issue.
3. Revision 0028 and later with zero registry tables: exact sorted
   `required tables are missing: ...` issue for the Stage28 registry table
   set.
4. Revision 0028+ with partial tables: exact revision-aware domain-checker
   issues, including dependencies and approval-action checks.
5. Unknown, absent, empty, and multiple Alembic revisions; compare fallback
   branches directly with the frozen original.
6. Complete Stage28+ schema and existing ownership/idempotency/audit data
   readiness corruption cases.
7. First probe exception versus fallback connection exception.

Built-in public and raw-registry replacement/removal must fail closed.

## Boundaries

- No DB migration/CHECK, API/OpenAPI, frontend, readiness consumer,
  approval behavior, or authorization change.
- Do not remove the frozen original alias.
- Keep this as a separate behavior-equivalence slice; it does not close
  backend extensibility axis #17.

## Verification

- Add real shared-registry dispatch, revision-aware compatibility-matrix,
  fallback, exception, and reserved built-in tests.
- Run Stage28 registry/release readiness, Stage29–32 capability/migration,
  enterprise readiness API, Stage17/18 regressions, and capability producer
  tests.
- Run Ruff, `.venv` `py_compile`, OpenAPI export check, and `git diff --check`.
- Request an independent sub-agent code review after implementation.
