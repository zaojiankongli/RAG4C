# Catalog-bound report projection observation fence

## Outcome

Added a report-level Adapter + Strategy/Registry seam:

- `D:\program_project\python_project\RAG4C\core\projection_consistency_report_fences.py`
- `D:\program_project\python_project\RAG4C\core\projection_consistency_readers.py`
- `D:\program_project\python_project\RAG4C\scripts\reconcile_chunk_authority.py`

Each report invocation/page now captures a typed
`CatalogProjectionSnapshotIdentity` and, when the target registers a report
fence:

1. starts one immutable target session after the Catalog scan state is known;
2. passes the same target snapshot token to every document reader request;
3. finishes the session after all documents in that invocation are read;
4. resets all drift/count/repair facts when the target reports `changed` or
   `unavailable`.

Report statuses are explicit:

- `report_target_observation_stable`
- `report_target_changed`
- `report_target_unavailable`

The stable status is still an observation, not cross-store authority.

## Fail-closed boundaries

- `milvus_chunks` remains explicitly report-unfenced; raw registry injection
  cannot make the resolver construct a report fence.
- Graph remains unsupported for chunk-shaped consistency readers.
- Stable report sessions must match tenant, dataset, Catalog identity digest,
  document snapshot fingerprint/count, and the target token.
- A non-stable report refuses repair even when the invocation read zero
  documents; CLI/report read status also becomes incomplete.
- Resume pages start a new target session; no opaque external token is silently
  reused across pages.
- No database/Milvus schema, OpenAPI, or frontend contract changed.

## Deliberate non-goals

The identity binds the target observation to the Catalog scan identity, but
does not create a Catalog mutation-generation authority. This slice still does
not enumerate target rows for Catalog-deleted documents, compare Graph facts,
enable custom atomic repair, or make the API confirmable.

## Verification

- Report-fence registry and integration tests: **6 passed**.
- Reader + reconcile regression: **62 passed**.
- Consistency API regression: **34 passed**.
- Final targeted rerun after review fixes: **7 passed**.
- Ruff, `py_compile`, and `git diff --check`: passed.
- Independent sub-agent review found two P1/P2 edge cases (empty report
  repair/CLI handling); both were fixed and final re-review: **PASS**.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-dataset-scoped-projection-observation-fence-design.md`.
