# Projection consistency target-observation fence registry

## Outcome

Added a separate Adapter + Strategy/Registry seam for target-owned generation
or snapshot observations:

- `D:\program_project\python_project\RAG4C\core\projection_consistency_fences.py`
- `D:\program_project\python_project\RAG4C\core\projection_consistency_readers.py`
- `D:\program_project\python_project\RAG4C\scripts\reconcile_chunk_authority.py`

The implementation reuses the single `core.providers.ProviderRegistry`.
A custom chunk-shaped target may register a synchronous `begin/finish` fence.
The host validates the observation and maps outcomes as follows:

| Target observation | Reader/report behavior |
|---|---|
| `stable` | `target_observation_stable`; comparison may proceed for that document |
| `changed` | `target_changed` + explicit incomplete read; no drift classification |
| `unavailable` | `unavailable` + explicit incomplete read; no drift classification |
| no fence | `unfenced`; reader-owned snapshot tokens are cleared |

Incomplete observations never become an empty projection. Repair remains
fail-closed.

## Preserved boundaries

- `milvus_chunks` is explicitly reserved as `unfenced`; even raw registry
  injection cannot make the resolver construct a Milvus fence.
- Graph remains unsupported for chunk-shaped readers and cannot be enabled by
  registering a fence.
- A reader-provided `snapshot_token` is not trusted without a separately
  registered target adapter.
- Registered factory errors, including `UnknownProviderError`, are propagated
  rather than misclassified as a missing registration.
- Existing API/OpenAPI/frontend contracts remain unchanged. Milvus remains
  `best_effort`, `catalog_only`, and non-confirmable.

## Scope that remains open

This is a **document-scoped target observation**, not a dataset-wide or
Catalog-aligned consistency authority. It does not:

- bind target generation to Catalog mutation generation or document snapshot
  fingerprint;
- enumerate projection rows for documents already deleted from Catalog;
- enable `confirmable=true`, custom repair, multi-target reconciliation, Graph
  comparison, or target-specific atomic repair.

Those require a separate dataset-scoped design with a typed Catalog snapshot
identity and report-level fence lifecycle.

## Verification

- Focused reader + reconcile regression: **61 passed**.
- Final reader + fence integration rerun: **22 passed**.
- Consistency API regression: **34 passed**.
- `py_compile`: passed.
- Ruff focused check: passed.
- `git diff --check`: passed.
- Independent sub-agent final review: **PASS**, no P0/P1/P2/P3 findings.

Design plan:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-projection-consistency-fence-registry-design.md`.
