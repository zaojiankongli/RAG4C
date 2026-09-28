# Frontend Task Operations vocabulary contract

Date: 2026-09-26

## Outcome

Added a TypeScript-owned Task Operations vocabulary boundary at
`frontend/src/enterprise-task-operations/model/taskVocabulary.ts`. It keeps
the backend fact categories/statuses separate from the narrower UI labels,
declares the complete source-kind-to-category mapping, normalizes safe
read-compatible aliases, and owns the `succeeded → completed` display mapping
and status labels. Vocabulary arrays/maps are frozen and unknown values fail
closed.

The model, API client, Task Operations presentation types, labels, and
Saved-View-to-tab adapter now use this boundary instead of maintaining parallel
source/category/status lists or display mappings. The API client distinguishes
the seven-value Saved View HTTP category input enum from the five-value UI
display vocabulary; fact-status queries continue to use only the seven
canonical backend values.

The slice also closed pre-existing client/server chain mismatches. The frontend
accepted and sent task-list `category`, `dataset_id`, and `workspace_id`
filters that the `/api/enterprise/tasks` FastAPI route and generated OpenAPI do
not declare; FastAPI silently ignored them. The client also modeled the
route's scalar `source_kind` as a multi-value array. `TaskPageQuery` now
matches the route's exact five query parameters (`cursor`, `limit`, `status`,
`source_kind`, `action_required`), with `sourceKind` represented as a single
value; unsupported runtime query keys are rejected. This did not change the
server route or generated OpenAPI. Saved-View category inputs remain supported
and now use the actual HTTP enum type.

## Compatibility boundaries

- Persisted category values remain `documents`, `indexing`, `sources`,
  `compliance`, and `quality`.
- UI category values remain `content`, `indexing`, `source`, `compliance`, and
  `quality`.
- Saved-View HTTP category values remain the existing seven-value set:
  `content`, `documents`, `indexing`, `source`, `sources`, `compliance`, and
  `quality`.
- Task fact status remains `succeeded`; display status remains `completed`.
  `completed` is not accepted as a task-list fact-status query.
- Alias normalization occurs at the client read/input boundary and is
  uniqueness-checked after normalization. It does not widen persisted values.
- Reconciliation-run status (`started/running/completed/failed`) remains a
  separate contract from Task Operations fact status.

## Files

- `frontend/src/enterprise-task-operations/model/taskVocabulary.ts`
- `frontend/src/enterprise-task-operations/model/taskVocabulary.test.ts`
- `frontend/src/enterprise-task-operations/model/taskModel.ts`
- `frontend/src/enterprise-task-operations/model/taskModel.test.ts`
- `frontend/src/enterprise-task-operations/api/taskApi.ts`
- `frontend/src/enterprise-task-operations/api/taskApi.test.ts`
- `frontend/src/enterprise-task-operations/components/taskOperationsTypes.ts`
- `frontend/src/enterprise-task-operations/components/taskOperationsUi.tsx`
- `frontend/src/enterprise-task-operations/TaskOperationsPage.tsx`
- `frontend/src/enterprise-task-operations/TaskOperationsPage.test.tsx`
- `docs/plans/2026-09-26-frontend-task-vocabulary-contract-design.md`

## Verification

- Focused Task Operations vocabulary/model/API/page/component regression:
  **5 files / 32 tests passed**.
- `npx tsc --noEmit`: passed.
- `npm run build`: passed; Vite transformed **7,128 modules**.
- Focused ESLint: **0 errors**; 11 existing warnings (`react-refresh` in
  `taskOperationsUi.tsx` and `no-explicit-any` in the existing page test).
- `git diff --check`: passed.
- Independent review identified three unsupported task-list query mismatches
  (category/dataset/workspace and the source-kind cardinality). The client
  contract and runtime validation were corrected. Follow-up review closed all
  three findings and reported **PASS**, with no remaining P0–P3 issues.
- The repository-wide frontend suite was not completed; an exploratory run was
  stopped after its first batch. The focused 5-file suite, typecheck, and build
  are the completed verification for this slice.

## Follow-up

This slice makes the Task Operations frontend vocabulary explicit but does not
complete the broader front-end redesign or backend extensibility program.
Backend Axis #3 and the documented projection-operation read/display contract
remain separate open work.

Design: `docs/plans/2026-09-26-frontend-task-vocabulary-contract-design.md`
