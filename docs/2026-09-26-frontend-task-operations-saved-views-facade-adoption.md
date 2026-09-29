# Frontend Task Operations Saved Views facade adoption

Date: 2026-09-26

## Outcome

Migrated `SavedViewsPanel` to the shared UI compatibility boundary:

- `Button`, `Empty`, and `Tag` now come from `frontend/src/ui`.
- The historical text action maps to `type="text"`.
- Active views expose `aria-pressed` while retaining the existing
  `.is-active` CSS state.
- Warning tags explicitly use `variant="light-outline"` so native and
  TDesign renderers share the same visual contract.

Authority safety was tightened during review:

- `error` now renders an explicit alert;
- `idle/loading/partial/unavailable/error` show `未返回` instead of an
  inferred count;
- only `ready/empty` states show a numeric count.

Selection remains available in read-only mode because it changes only the
display filter and does not execute persistence or task mutations.

## Review fixes

Independent review found and the implementation closed:

- P1 silent Saved Views `error` state;
- P2 missing `.rag-button` Saved View truncation CSS;
- P2 non-ready numeric counts;
- P2 renderer-dependent warning Tag variant;
- P3 missing accessible active-state semantics.

Final independent follow-up review: **PASS**, no actionable P0–P3 findings.

## Verification

- Task Operations suite: **18 files / 72 tests passed**.
- Saved Views/Task Operations focused suite: **3 files / 18 tests passed**.
- `npx tsc --noEmit`: passed.
- `npm run build`: passed; Vite transformed **7,129 modules**.
- Focused ESLint: **0 errors**; 3 existing Task Operations warnings remain.
- `git diff --check`: passed.

## Scope boundary

This slice does not migrate `TaskOperationDetailDrawer`,
`TaskMutationDialog`, `TaskOperationsCenter`, or the direct `Alert`/`Loading`
state notices in `taskOperationsUi.tsx`.

Design:
`docs/plans/2026-09-26-frontend-task-operations-saved-views-facade-adoption-design.md`
