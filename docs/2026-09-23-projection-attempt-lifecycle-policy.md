# Projection attempt lifecycle policy implementation record (2026-09-23)

## Scope

This slice converts the `target_store` decisions in `IndexOperationWorker._advance_attempt_lifecycle` to per-target policies. It closes only attempt readiness/finalization semantics for axis #3; it does **not** complete projection target production, durable delete integration, or consistency/readiness APIs.

## Implementation

- Added `core/projection_attempt_lifecycle.py`, reusing `core.providers.ProviderRegistry` with exact canonical target keys. The immutable policy declares primary/secondary role, whether pending work blocks finalization, and a synchronous readiness predicate. Registration rejects duplicate keys and malformed role/callback signatures.
- Registered `milvus_chunks` as the primary policy and `graph_projection` as a secondary finalization barrier.
- `IndexOperationWorker` resolves all target policies for an attempt before its handler and pins them in the long-lived worker's per-attempt cache across `run_once` calls. The existing server worker is a long-lived loop. Policy changes are not a hot-swap contract; drain active attempts across the worker fleet before deploying changed policy semantics.
- After handler and revision advancement, the worker snapshots immutable document revision facts, closes its SQLAlchemy session, then evaluates the current target policy **before** `complete_operation`. A false result or callback exception enters the existing durable retry/dead-letter path instead of completing the queue row and silently stranding an attempt.
- Post-completion attempt aggregation executes no policy callback. It retains the current-attempt check, desired/indexed revision fence, chunk/quota transaction, and state-machine transitions. If a newer attempt is made current while readiness executes, the old operation cannot mark the document completed.
- Milvus operations whose `target_revision` is older than `desired_index_revision` are treated as stale no-op successes by readiness, matching the existing revision strategy behavior; they do not advance `indexed_revision`. The independent final desired/indexed fence remains in place.
- Durable-delete/finalizer paths remain outside these ingestion attempt policies.

## Tests and verification

New/updated real-worker tests cover custom target registration, two-operation attempt policy pinning, false-then-true and exception-then-true retry paths, missing-policy rejection before handler effects, secondary finalizing barriers, and a newer attempt becoming current during readiness. A source guard prevents literal Milvus/graph store branches from returning to `_advance_attempt_lifecycle`.

Full focused regression command:

```text
pytest -q tests/test_index_worker.py tests/test_projection_handlers.py tests/test_document_delete_worker_v2.py
```

Result: **57 passed**. `python -m py_compile indexing/index_worker.py core/projection_attempt_lifecycle.py tests/test_index_worker.py`, Ruff on the touched Python files, and `git diff --check` passed.

Reverse validation: temporarily replacing the pre-completion readiness call with `pass` made `test_attempt_readiness_failure_is_durably_retried[not_ready]` fail on the expected assertion (`first.retried` was 0 instead of 1); source bytes were restored exactly.

## Review and remaining boundaries

Independent subagent review initially found three medium issues: readiness could strand completed work, readiness ran in the finalization session, and policy pinning was only per operation. They were fixed with pre-completion durable retry, callback/session separation plus final current-attempt recheck, and an attempt snapshot cache tested across two operations. Follow-up review then found a stale-Milvus compatibility regression; policy now explicitly accepts older-than-desired revisions as no-op readiness while the independent desired/indexed fence remains. Reviewer re-ran the stale-operation regression: **1 passed**, no new blockers. Full focused regression: **57 passed**. Final independent review: **PASS**.

Axis #3 remains open until the state-machine producer, durable delete store set/handlers/lifecycle, and consistency projection/readiness facts are also made extensible and verified.
