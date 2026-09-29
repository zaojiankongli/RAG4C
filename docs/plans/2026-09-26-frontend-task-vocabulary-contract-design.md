# Frontend Task Operations vocabulary contract design (2026-09-26)

## Context

The backend now has a canonical Task Operations category/status contract, but
the frontend still repeats source-kind/category maps and `succeeded →
completed` projection in its strict API model and presentation adapter. The
saved-view parser also maintains a second category alias table.

The frontend must keep its own TypeScript boundary; it must not import Python
runtime code or infer unknown values as known labels.

## Decision

Add a frontend-owned `taskVocabulary.ts` contract that declares:

- canonical backend source-kind/category/status values used by the UI model;
- the fixed `source_kind → backend category → UI category` mapping;
- read-compatible category aliases and the narrower HTTP category values;
- the `succeeded → completed` display mapping and stable UI status labels.

`taskModel.ts`, `taskApi.ts`, and Task Operations presentation types/components
will consume this contract. Unknown categories, source kinds, and statuses
remain fail-closed. Query enums remain narrower than saved-view read
compatibility; aliases are normalized for safe display, not emitted as novel
persisted categories.

The task-list query was also checked against the real FastAPI route and
generated OpenAPI. The client had phantom `category`, `dataset_id`, and
`workspace_id` filters, and modeled the scalar `source_kind` as a multi-value
array. FastAPI ignored unsupported filters and only honored a single source
kind. The client query type/serializer now matches the generated route exactly
and rejects unknown runtime keys. Saved-view category inputs instead use the
actual seven-value HTTP compatibility enum; the model continues to expose the
five-value UI display vocabulary.

## Compatibility

- UI category vocabulary remains `content/indexing/source/compliance/quality`.
- Backend persisted category vocabulary remains
  `documents/indexing/sources/compliance/quality`.
- Public HTTP category inputs remain the existing seven-value compatibility
  set.
- Backend task fact status remains `succeeded`; UI display remains `completed`.
- No server route, OpenAPI, DB, authorization, or persistence contract changes.
  The client task-list query is aligned to the existing server contract;
  supported Saved View category inputs remain unchanged.

## Verification plan

1. Add failing contract/source-parity tests before implementation.
2. Implement the typed vocabulary module and migrate model/API/label consumers.
3. Verify status/source/category vocabularies and exact task-list query
   parameters against generated OpenAPI, reject display-only `completed` in
   fact-status queries, and reject unsupported task-list query keys.
4. Run focused task model/API/component tests, TypeScript, focused ESLint, and
   `git diff --check`.
5. Request independent sub-agent review; fix findings and rerun focused checks.
6. Write the slice handoff and update cumulative progress.
