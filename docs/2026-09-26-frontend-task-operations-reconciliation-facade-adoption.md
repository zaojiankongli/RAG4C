# Frontend Task Operations Reconciliation panel facade adoption

Date: 2026-09-26

## Outcome

Migrated `ReconciliationPanel` to the shared UI compatibility boundary:

- `Button`, `Empty`, and `Tag` now come from `frontend/src/ui`.
- The historical text action maps to `type="text"` and keeps the
  read-only fail-closed behavior.
- Added the renderer-policy `empty` component with native and TDesign
  adapters. Root `id`, `role`, `tabIndex`, `aria-*`, and `data-*` attributes
  remain addressable on both renderers.

The panel preserves severity labels, task/reconciliation facts, action
callbacks, CSS classes, ARIA naming, and empty-state copy. Authority safety
was tightened during review:

- `error` now renders an explicit alert;
- non-ready authority states show `open 未返回` instead of an inferred count;
- ready/empty states count only verified `status === "open"` items, so
  resolved records are not counted as open.

## Review fixes

Independent review found and the implementation closed:

- P1 silent `error` authority state;
- P2 unverified/non-open reconciliation counts;
- P2 native facade action CSS missing the `.rag-button` selector;
- P3 Empty root `tabIndex`/ARIA/data boundary and missing regression coverage.

Final review found no actionable P0/P1/P2 findings. A persistent browser
e2e suite remains outside this isolated unit/compatibility slice.

## Verification

- Task Operations suite: **16 files / 65 tests passed**.
- Reconciliation/shared UI focused suite: **9 files / 52 tests passed**.
- `npx tsc --noEmit`: passed.
- `npm run build`: passed; Vite transformed **7,129 modules**.
- Focused ESLint: **0 errors**; 3 existing Task Operations warnings remain.
- `git diff --check`: passed.
- Final independent review: no actionable P0/P1/P2 findings.

## Scope boundary

This slice does not migrate `SavedViewsPanel`, `TaskOperationDetailDrawer`,
`TaskMutationDialog`, `TaskOperationsCenter`, or the direct `Alert`/`Loading`
state notices in `taskOperationsUi.tsx`.

Design:
`docs/plans/2026-09-26-frontend-task-operations-reconciliation-facade-adoption-design.md`
