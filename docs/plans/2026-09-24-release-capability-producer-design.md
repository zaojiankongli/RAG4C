# Stage29 Knowledge Base Release capability producer design (2026-09-24)

## Context

The Stage29 `inspect_enterprise_knowledge_base_release_capability()` active
inspector still routes through `_knowledge_serving_revision_compatible`, while
Stage23/24/25/26/27 now use the shared
`CatalogCapabilityPolicy` + `ProviderRegistry` producer.

Stage29 is a parent capability for Release Quality certification and has
tenant/release projection checks. The migration change must therefore preserve
the frozen original and compare the new producer against the exact active
compatibility wrapper before switching dispatch.

## Decision

- Register a reserved `knowledge_base_releases` built-in policy using the
  existing shared registry.
- Preserve `_KNOWLEDGE_SERVING_ORIGINAL_RELEASE_CAPABILITY` unchanged and
  delegate known legacy/unknown/missing/multiple revision branches to it after
  the shared probe connection is closed.
- Use the existing `_enterprise_knowledge_base_release_capability_issues`
  checker unchanged for partial and complete Stage29+ schemas.
- Pin the generic missing-table message/state and inspection-error propagation
  that the active wrapper currently owns.
- Keep Stage29 table/constraint contracts, migration history, Stage30 parent
  readiness checks, readiness consumers, and public response shape unchanged.

## Compatibility matrix

Compare the new policy with
`_knowledge_serving_revision_compatible(bind, original, ...)` for:

1. Known pre-0029 revision with no release tables: `not_available`.
2. Known pre-0029 revision with a partial release schema: `unavailable` with
   the frozen pre-0029 message.
3. Revision 0029 and later with no release tables: exact generic
   `required tables are missing: ...` issue.
4. Revision 0029+ with partial tables: exact domain-checker issues.
5. Unknown, absent, empty, and multiple Alembic revisions.
6. Complete Stage29 schema and live data corruption cases already covered by
   release-readiness tests.
7. First probe exception vs. legacy fallback second-connection exception.

For fallback branches, also compare directly with
`_KNOWLEDGE_SERVING_ORIGINAL_RELEASE_CAPABILITY`. Built-in raw replacement and
removal must fail closed.

## Boundaries

- No DB migration/CHECK, API/OpenAPI/frontend, authorization, or policy-consumer
  change.
- Do not remove the frozen original alias.
- If any compatibility row differs, keep the current public wrapper and amend
  the producer contract rather than weakening the test.

## Verification

- Add real policy dispatch and reserved-built-in tests.
- Run Stage29 release capability/migration tests, Stage30 certification tests,
  catalog capability producer tests, readiness API, and Stage17 authorization
  security regressions.
- Run Ruff, `py_compile`, OpenAPI export check, and `git diff --check`.
- Independent sub-agent review after implementation.
