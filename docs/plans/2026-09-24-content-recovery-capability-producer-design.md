# Stage23 Content Recovery capability producer design (2026-09-24)

## Context

`inspect_enterprise_content_recovery_capability` still contains its own
revision/schema-state ladder, while Stage25 Automation and Stage26/27 have
moved to `CatalogCapabilityPolicy` and the shared producer registry. Its
domain schema checker is already separate and should remain so.

The capability is security-sensitive: `not_available` is only valid for a
known pre-0033 catalog with no Content Recovery tables. Unknown, partial,
multi-revision, or post-0033 catalogs with missing tables must remain
`unavailable`.

## Decision

- Register a built-in `content_recovery` `CatalogCapabilityPolicy` through the
  existing `core.providers.ProviderRegistry`.
- Keep `inspect_enterprise_content_recovery_capability(bind)` as the public
  entry point, delegating to `inspect_catalog_capability`.
- Reuse the current `_enterprise_content_recovery_capability_issues` checker;
  it remains responsible for dialect-specific guards and all table/data
  invariants. Set policy-level `supported_dialects=None` to preserve that
  ownership.
- Preserve the exact minimum revision, tables, issue checker, exception
  prefix, and legacy revision/error text. The shared producer supplies the
  existing known-before-minimum / unknown / multiple-revision ladder.
- Reserve the built-in name against dynamic replacement/removal.

## Compatibility matrix

| Catalog state | Required outcome |
|---|---|
| Known 0032 revision, no Content Recovery tables | `not_available`, no issues |
| Known 0032 revision, any partial Content Recovery table | `unavailable`, pre-0033 issue |
| 0033 or later known revision with missing capability tables | `unavailable`, missing-table issues |
| Multiple revisions | `unavailable`, multiple-revisions issue |
| Unknown or missing revision | `unavailable`, existing catalog-revision issue |
| Complete current schema with valid guards | `ready`, no issues |

## Scope and risks

- No migration, database CHECK, OpenAPI/frontend vocabulary, authorization
  consumer, or capability state meaning changes.
- This is one bounded #17 producer migration; other historical/frozen
  producers remain untouched.
- Main risk is treating a damaged post-0033 schema as an old unsupported
  catalog. Permanent matrix tests pin the `not_available` versus `unavailable`
  boundary, and existing Stage23 readiness/service tests remain unchanged.

## Verification and review

Run the new producer matrix and dispatch guard, existing Content Recovery
readiness/service tests, shared Stage25/26/27 capability tests, Stage17
authorization security regressions, Ruff/format/`py_compile`, and diff checks.
Perform a process-local reverse validation of live registry dispatch and
request independent sub-agent review before handoff.
