# Stage25 Automation capability producer design (2026-09-24)

## Context

Stage26 Knowledge Serving and Stage27 Knowledge Operations already use one
revision-aware Template Method plus the repository's `ProviderRegistry`.
Stage25 Automation still has a second active revision ladder even though its
domain readiness checker is already independent.

The earlier function captured as
`_KNOWLEDGE_SERVING_LEGACY_AUTOMATION_CAPABILITY` is a compatibility artifact.
It must remain unchanged; this slice only moves the later active public
inspector onto the shared producer.

## Decision

- Route `inspect_enterprise_automation_workflows_capability(bind)` through the
  built-in `automation_workflows` capability producer.
- Reuse `CatalogCapabilityPolicy`, `build_catalog_capability_producer`, and the
  existing `ProviderRegistry`; do not create a second registry.
- Add optional policy facts for dialect delegation and Automation-specific
  revision/missing-table outcomes so the shared Template Method can represent
  existing semantics without another inline state ladder.
- Leave `_enterprise_automation_workflows_capability_issues`, the captured
  legacy function, API/server consumers, and public return shape untouched.

## Compatibility facts

- No explicit inspector dialect allowlist is added: Automation's existing
  issue checker handles supported dialects.
- Missing Alembic revision remains `unavailable` with
  `"catalog revision is missing"`; unknown revision remains `unavailable` with
  `"catalog is not at a known revision"`.
- A known pre-0035 revision with no Automation tables remains
  `not_available`; the same revision with Automation tables remains
  `unavailable` with the existing pre-0035 message.
- At exactly 0035 with no Automation tables, preserve `not_available` plus
  `"Automation tables are missing"`.
- A later known revision without Automation tables remains `not_available`;
  with tables present, the existing domain checker runs.
- Preserve multi-revision and exception result text.

## Verification

- Structural guard proves the active public inspector delegates through the
  shared registry.
- Real SQLite-path tests pin old/current/later revision and missing-table
  outcomes; existing readiness/service/API tests cover domain behavior.
- Run shared Stage26/27 and Stage17 authorization suites because the common
  Template Method gains optional policy branches.
- Reverse-validate the Automation dispatcher and policy-specific no-table rule,
  then request independent read-only review.
