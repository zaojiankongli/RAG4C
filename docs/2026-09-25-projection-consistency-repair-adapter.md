# Projection consistency repair adapter

## Outcome

Completed the durable-repair extension seam for chunk-shaped projection
consistency reports:

- `D:\program_project\python_project\RAG4C\core\projection_consistency_repairs.py`
- `D:\program_project\python_project\RAG4C\scripts\reconcile_chunk_authority.py`
- `D:\program_project\python_project\RAG4C\tests\test_projection_consistency_repairs.py`
- `D:\program_project\python_project\RAG4C\tests\test_knowledgeops_chunk_reconcile.py`

The reconciler now resolves an explicit repair adapter only when
`repair=True`. A registered reader alone cannot enqueue durable work.

## Compatibility

- Built-in `milvus_chunks/reconcile` remains immutable and keeps the previous
  CAS, attempt, transaction, payload, manifest hash, and dedup identity.
- Existing persisted Milvus repair operations remain discoverable after the
  refactor.
- Custom target adapters use a target/operation dedup namespace so the global
  `IndexOperation.dedup_key` constraint cannot collapse custom work into a
  Milvus operation.
- Target and operation names are bounded to the persisted `String(32)` and
  `String(24)` fields.
- Backend-dependent factories are lazy and instantiated once at resolution.
- Unregistered custom repair, Graph, raw replacement, and raw removal remain
  fail-closed.

No migration, DB CHECK, OpenAPI, frontend, or authorization contract changed.

## Verification

- `tests/test_projection_consistency_repairs.py`: **6 passed**
- Full chunk reconciliation suite: **44 passed**
- Reader/report-fence/repair targeted selection: **66 passed**
- Consistency API: included in the **66 passed** selection
- Projection target/handler/requeue regression: **47 passed**
- Ruff, `.venv` `py_compile`, and `git diff --check`: passed

Independent sub-agent review initially found a P1 Milvus parity issue and
three lower-priority gaps. The implementation and tests were revised;
follow-up review: **PASS**, with no remaining P0/P1/P2/P3 findings.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-projection-consistency-repair-adapter-design.md`.
