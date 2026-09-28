# Stage23 Content Recovery capability frozen fallback

## Outcome

Completed the missing historical compatibility seam for the Content Recovery
capability producer:

- `D:\program_project\python_project\RAG4C\core\catalog_schema.py`
- `D:\program_project\python_project\RAG4C\tests\test_catalog_capability_producers.py`

The pre-registry inspector is now preserved as the private
`_knowledge_serving_original_content_recovery_capability` function. The
reserved `content_recovery` `CatalogCapabilityPolicy` delegates historical and
ambiguous revision states to that frozen implementation, while known 0033+
catalogs continue through the shared producer and the existing domain checker.

## Preserved behavior

- Known pre-0033 without Content Recovery tables remains `not_available`.
- Known pre-0033 partial schemas remain `unavailable` with the legacy issue.
- Unknown, missing, and multiple revision states retain their original
  fail-closed results.
- 0033+ schema/data/trigger checks remain owned by
  `_enterprise_content_recovery_capability_issues`.
- Exception prefix, readiness consumers, authorization behavior, migrations,
  OpenAPI, and frontend contracts are unchanged.

## Verification

- Catalog capability producer suite: **133 passed**.
- Content Recovery/readiness targeted regression excluding two application
  mount tests: all selected tests passed.
- Ruff: passed.
- `py_compile`: passed.
- `git diff --check`: passed.
- Independent sub-agent review: **PASS**, no remaining findings.

Note: the two excluded application-mount tests fail in the current
environment before test execution because `pymysql` is not installed; this
slice does not claim those tests pass.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-content-recovery-capability-frozen-fallback-design.md`.
