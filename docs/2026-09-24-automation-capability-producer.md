# Stage25 Automation capability producer handoff (2026-09-24)

## Change

- The active public `inspect_enterprise_automation_workflows_capability(bind)`
  now dispatches through the built-in `automation_workflows` policy in the
  shared catalog capability producer registry.
- `CatalogCapabilityPolicy` can now declare optional compatibility behavior
  for absent/unknown revisions, pre-minimum revisions, and missing capability
  tables. `supported_dialects=None` lets a domain issue checker own dialect
  validation (as Automation already does).
- The existing `ProviderRegistry` remains the only registry implementation.
  Stage26/27 policies keep their previous defaults.
- The previously captured `_KNOWLEDGE_SERVING_LEGACY_AUTOMATION_CAPABILITY`
  and its original function body are unchanged.

## Preserved behavior

The special Stage25 state and issue outcomes are recorded in
`docs/plans/2026-09-24-automation-capability-producer-design.md` and pinned by
the readiness tests. No DB migration/CHECK, API/OpenAPI/server contract,
authorization consumer, domain issue checker, or frontend vocabulary changed.
This is a partial #17 implementation; historical compatibility producers and
other one-off inspectors remain in scope.

## Verification

- Initial TDD run showed the expected missing behavior for optional domain
  dialect delegation, active-inspector registry dispatch, and Automation's
  built-in reservation. A draft assertion for the existing 0035 no-table state
  was corrected to the actual `not_available` + issue contract before coding.
- Automation producer/readiness/service/Admin API regression:
  **80 passed, 822 existing deprecation warnings**.
- Shared capability producer + Automation/Stage26/Stage27 readiness +
  Stage17 authorization security + capability consumer regression:
  **107 passed, 1,365 existing deprecation warnings**.
- Ruff checks passed for all changed Python modules/tests; formatter check
  passed for the new policy module, provider registry, and changed tests.
  `py_compile` and `git diff --check` passed.
- Frontend regression for the concurrent App shell/routing work:
  **9 files / 41 tests passed**; ESLint completed with 0 errors and 98 existing
  warnings; production build passed (7,121 modules transformed).
- Reverse validation used process-local substitutions only (no source-file
  mutation): baseline Automation no-table-at-0035 result matched the contract;
  replacing registry dispatch changed the public inspector result, and
  removing the policy's exact-minimum missing-table issue changed the result
  from `not_available` with the required issue to an empty `not_available`.
- Independent read-only review of the current working-tree changes and the
  follow-up safety fixes: **PASS after remediation**. The initial review
  surfaced projection operation, notification adapter, and incomplete target
  runtime issues outside the Stage25 capability producer; those findings are
  fixed and re-reviewed. No Stage25-specific finding remains.

## Follow-up

Do not expand #17 to frozen `_KNOWLEDGE_SERVING_ORIGINAL_*` producers without
first proving behavior equivalence against their captured versions. The
inventory explicitly keeps #17 open.
