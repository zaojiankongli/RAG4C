# Stage26/Stage27 Knowledge Serving and Operations frozen fallback

## Outcome

Completed the historical compatibility seam for the Stage26 and Stage27
catalog capability producers:

- `D:\program_project\python_project\RAG4C\core\catalog_schema.py`
- `D:\program_project\python_project\RAG4C\tests\test_catalog_capability_producers.py`

The pre-registry inspectors are preserved as:

- `_knowledge_serving_original_knowledge_serving_reliability_capability`
- `_knowledge_serving_original_knowledge_operations_feedback_capability`

The public inspectors remain thin shared-registry wrappers, and both reserved
policies now delegate historical/ambiguous revision branches to their frozen
inspector through `fallback_inspector`.

## Preserved behavior

- Known pre-minimum revisions with no capability tables remain
  `not_available`.
- Known pre-minimum partial schemas retain the original unavailable
  minimum-revision issue.
- Missing, empty, unknown, and multiple Alembic revision states retain their
  original state and issue text.
- Exact minimum and later revisions continue through the existing Stage26/27
  schema/data/guard/parent checkers.
- Unsupported dialect and inspection-error behavior remain unchanged.
- No migration, DB CHECK, readiness/API/OpenAPI, frontend, authorization, or
  data contract changed.

## Verification

- `tests/test_catalog_capability_producers.py`: **172 passed**.
- Stage26 readiness/migration/API selection: **57 passed**.
- Stage27 readiness/migration/API selection: **16 passed**.
- Catalog upgrade/directory Stage26/27 selection: **4 passed**.
- AST normalization against `HEAD` confirmed both new frozen inspector bodies
  match the former public inspector bodies.
- Ruff check, `.venv` `py_compile`, and `git diff --check` passed.
- Full-file Ruff format check remains affected by pre-existing formatting
  drift in the large shared `catalog_schema.py` and test module; no formatter
  rewrite was applied to unrelated worktree changes.

Independent sub-agent review: initial review found a P2 oracle-coupling risk
and a P3 matrix gap. The tests now assert the expected state/issues
independently for exact/later and historical branches and add partial
multiple/empty-revision combinations. Follow-up review: **PASS**, with no
remaining P0/P1/P2/P3 findings.

The current Axis #17 capability set (Stage17 and Stage23–32) now has a
dedicated frozen fallback and compatibility matrix for every registered
producer. Axis #17 remains a standing rule for future capabilities, not an
unresolved branch in this current set.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-knowledge-serving-operations-frozen-fallback-design.md`.
