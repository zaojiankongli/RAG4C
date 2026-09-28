# Task Operations vocabulary contract

## Outcome

Added `/core/task_vocabulary.py` as the backend's single contract for Task
Operations category and normalized-status vocabularies:

- canonical category and fact-status tuples stay immutable and closed;
- category/status aliases normalize only to canonical values;
- the compatibility HTTP category input tuple is explicit and separate from
  both the alias registry and canonical persisted vocabulary;
- `succeeded → completed` is an explicit display projection used by the task
  service response builder; persisted facts remain `succeeded`;
- task source-kind validation, authority canonicalization, service status and
  category filters, schema CHECK declarations, and the API category/status
  request contracts now consume the shared vocabulary.

No new source kind, category, DB value, or status was introduced. DB CHECKs
remain canonical. Historical reconciliation `started` versus schema
`running` remains an explicitly separate, unresolved compatibility seam.

## Files

- `D:\program_project\python_project\RAG4C\core\task_vocabulary.py`
- `D:\program_project\python_project\RAG4C\core\task_source_kinds.py`
- `D:\program_project\python_project\RAG4C\core\enterprise_task_operations.py`
- `D:\program_project\python_project\RAG4C\core\enterprise_task_operations_service.py`
- `D:\program_project\python_project\RAG4C\core\catalog_schema.py`
- `D:\program_project\python_project\RAG4C\server\enterprise_task_operations_api.py`
- `D:\program_project\python_project\RAG4C\tests\test_task_vocabulary_contract.py`
- `D:\program_project\python_project\RAG4C\tests\test_task_api_vocabulary_derivation.py`

## Verification

- Focused task vocabulary/core/service/API/schema regression:
  **81 passed / 1 deselected**.
- The deselected test is the `server.app` HTTP-mount smoke test. Running it
  directly in this environment fails during app import because optional
  `pymysql` is not installed; this is recorded as an environment limitation,
  not a component-test pass.
- Ruff: passed.
- `.venv` Python compile check: passed.
- `git diff --check`: passed.
- Independent sub-agent review: **PASS**, no P0–P3 findings.

## Follow-up boundaries

- The frontend intentionally retains display categories `content` and
  `source`; its TS model/parser contract is a separate follow-up slice.
- The complete cross-layer task label chain still needs a frontend-owned
  vocabulary contract so UI labels derive consistently without importing
  Python code.
- Projection operation read/display strings remain open for historical facts;
  all mutation paths must continue to require registered operations.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-26-task-vocabulary-contract-design.md`
