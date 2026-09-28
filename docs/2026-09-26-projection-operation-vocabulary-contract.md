# Projection operation action contract

Date: 2026-09-26

## Outcome

Added the frontend action-admission contract
`frontend/src/consistency/projectionOperationVocabulary.ts`. Consistency
dead-letter `target_store` and `operation` values remain open strings for
read/display compatibility, while the UI now exposes a requeue action only for
the eight verified built-in backend requeue pairs:

- `milvus_chunks`: `upsert`, `delete`, `reconcile`, `delete_document`;
- `graph_projection`: `upsert`, `delete`, `delete_document`;
- `catalog_finalize`: `finalize_document_delete`.

Unknown historical or dynamically registered pairs remain visible but render a
read-only `仅展示` tag. Existing requeue outcomes and already-requeued rows
take precedence, so a completed backend outcome is still represented as
completed even if the frontend does not know the pair. The backend remains the
final authority and its registry/fail-closed behavior was not changed.

The pair list and each nested pair are frozen. The frontend contract test
parses the backend registry in declaration order and fails if it encounters an
unrecognized declaration, preventing backend additions from being silently
missed. Runtime matching uses an exact `Set`, so malformed and
prototype-shaped inputs cannot acquire an action by property lookup or
coercion. The requeue callback repeats the pair guard before touching mutation
state or calling the API; hiding the button is not the only safety layer.

## Files

- `frontend/src/consistency/projectionOperationVocabulary.ts`
- `frontend/src/consistency/projectionOperationVocabulary.test.ts`
- `frontend/src/pages/ConsistencyPage.tsx`
- `frontend/src/pages/ConsistencyPage.test.tsx`
- `docs/plans/2026-09-26-projection-operation-vocabulary-contract-design.md`

## Verification

- Frontend consistency/vocabulary focused suite: **3 files / 25 tests passed**.
- Backend requeue registry suite: **9 passed**.
- `npx tsc --noEmit`: passed.
- Vite build: passed; **7,129 modules transformed**.
- Focused ESLint: passed with no errors.
- `git diff --check`: passed.
- Independent sub-agent review initially found that the registry parity parser
  could skip a declaration with an inline comment (P2). Parsing now fails on
  every unrecognized nonempty declaration line; follow-up review **PASS**, no
  actionable P0–P3 findings.

## Boundaries

This slice deliberately does not close the broader projection authority gap.
Graph comparator, Catalog-deleted target enumeration, verifiable Catalog
mutation generation, and target-specific atomic repair remain open as
documented Axis #3 work. Runtime custom backend pairs can be registered and
read, but do not automatically receive a frontend mutation affordance.

Design:
`docs/plans/2026-09-26-projection-operation-vocabulary-contract-design.md`
