# Stage23 Content Recovery capability frozen fallback design (2026-09-25)

## Context

The Stage23 Content Recovery capability already dispatches through the shared
`CatalogCapabilityPolicy` registry, but its producer currently owns all
revision-state behavior directly. The extensibility standard requires a
frozen legacy inspector for historical branches so the shared producer can
delegate unknown, missing, multiple, and pre-minimum catalog states to the
exact prior behavior.

## Design

1. Preserve the pre-registry Content Recovery inspector as the private
   `_knowledge_serving_original_content_recovery_capability` function.
2. Keep the public `inspect_enterprise_content_recovery_capability` wrapper
   delegating through `inspect_catalog_capability`.
3. Add the frozen inspector as `fallback_inspector` on the reserved
   `content_recovery` policy.
4. Keep the shared producer responsible for known 0033+ inspection and the
   existing domain checker; fallback owns only historical/ambiguous branches.
5. Add explicit identity and compatibility tests comparing the public
   producer to the frozen inspector across fallback states.

## Compatibility

- Known pre-0033 catalogs retain the exact `not_available`/partial-schema
  distinction.
- Unknown, missing, and multiple revision states retain the original issue
  text and exception behavior.
- 0033+ schema/data/guard checks remain on the existing domain checker.
- No migration, database CHECK, OpenAPI, authorization, readiness response, or
  frontend contract changes.

## Verification

- Content Recovery producer matrix, legacy fallback comparison, and dispatch
  tests.
- Existing Content Recovery readiness/service and capability regressions.
- Ruff, `py_compile`, and `git diff --check`.
- Independent sub-agent review before handoff.

## Completed verification (2026-09-25)

- Catalog capability producer suite: **133 passed**.
- Content Recovery/readiness targeted regression with application-mount tests
  excluded: **all selected tests passed**.
- Ruff, `py_compile`, and `git diff --check`: passed.
- The broader combined command exposed two environment-only failures while
  importing `server.app`: `pymysql` is not installed in the active Python
  environment. Those app-mount tests were not used to claim a green result.
- Independent sub-agent review: **PASS**, no remaining findings after the
  frozen function naming correction.
