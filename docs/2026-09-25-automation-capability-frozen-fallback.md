# Stage25 Automation capability frozen fallback

## Outcome

Corrected the Stage25 Automation capability compatibility seam:

- `D:\program_project\python_project\RAG4C\core\catalog_schema.py`
- `D:\program_project\python_project\RAG4C\tests\test_catalog_capability_producers.py`

The actual pre-registry public Automation inspector is now preserved as
`_knowledge_serving_original_automation_capability`. The public wrapper
continues to dispatch through the shared `CatalogCapabilityPolicy` registry,
which delegates historical branches to the frozen implementation and keeps
known 0035+ checks on the Automation domain checker.

## Preserved behavior

- Missing revision: `catalog revision is missing`.
- Unknown revision: `catalog is not at a known revision`.
- Multiple revisions: original multiple-revision issue.
- Known pre-0035 without tables: `not_available`.
- Exact 0035 without tables: `not_available` with
  `Automation tables are missing`.
- Pre-0035 partial schemas: original unavailable/pre-minimum issue.
- 0035+ schemas with tables: existing Automation checker remains authoritative.
- No migration, database CHECK, OpenAPI, authorization, readiness contract, or
  frontend behavior changed.

## Verification

- Catalog capability producer suite: **135 passed**.
- Automation producer/readiness/service/API/migration targeted regression
  excluding one application-mount test: all selected tests passed.
- Ruff: passed.
- `py_compile`: passed.
- `git diff --check`: passed.
- Independent review found the exact-0035 mismatch; it was fixed and final
  review: **PASS**, no remaining findings.

Note: the excluded application-mount test requires `pymysql`, which is not
installed in the active environment.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-automation-capability-frozen-fallback-design.md`.
