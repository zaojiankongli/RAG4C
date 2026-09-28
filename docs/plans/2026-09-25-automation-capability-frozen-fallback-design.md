# Stage25 Automation capability frozen fallback design (2026-09-25)

## Context

The pre-registry Stage25 Automation inspector still exists in
`catalog_schema.py`, but its function name is later overwritten by the public
Registry wrapper. The legacy alias therefore points at the new wrapper rather
than the frozen implementation, so the policy cannot provide a real
behavior-equivalence fallback.

## Design

1. Rename the preserved pre-registry body to the private
   `_knowledge_serving_original_automation_capability` function.
2. Keep `inspect_enterprise_automation_workflows_capability` as the public
   `inspect_catalog_capability("automation_workflows", bind)` wrapper.
3. Configure the reserved Automation policy with the frozen function as its
   `fallback_inspector`.
4. Add identity and historical compatibility tests for pre-0035, missing,
   unknown, and multiple revision branches.
5. Keep known 0035+ schema checks on the shared producer and existing
   `_enterprise_automation_workflows_capability_issues` checker.

## Compatibility

- Existing Automation missing-table, revision, exception, readiness, and
  domain-checker results remain unchanged.
- No migration, database CHECK, OpenAPI, authorization, or frontend contract
  changes.

## Verification

- Automation producer/readiness/service regression and frozen fallback matrix.
- Ruff, `py_compile`, and `git diff --check`.
- Independent sub-agent review before handoff.

## Completed verification (2026-09-25)

- Catalog capability producer suite: **135 passed**.
- Automation producer/readiness/service/API/migration regression excluding
  the application-mount test: **all selected tests passed**.
- Ruff, `py_compile`, and `git diff --check`: passed.
- The excluded application-mount test fails before execution in the current
  environment because `pymysql` is not installed.
- Independent review found and the implementation fixed the exact-0035
  missing-table compatibility mismatch; final review: **PASS**.
