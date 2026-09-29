# Frontend Task Operations table facade adoption

Date: 2026-09-26

## Outcome

Migrated `TaskOperationsTable` to the shared UI compatibility boundary:

- `Button`, `Tag`, and loading now use the shared facade (`Button`, `Tag`,
  `Spin`).
- The desktop `PrimaryTable`/`PrimaryTableCol` dependency is replaced by the
  shared renderer-independent `Table` contract.
- Historical button semantics are mapped explicitly:
  `variant="text"` → `type="text"`, outline actions → `type="default"`, and
  danger actions → `danger`.
- The shared `Table` now supports a focusable scroll-container contract,
  `size="small"`, `verticalAlign`, `scroll={{ x }}`, and forwarded keyboard
  handlers.

The real `.rag-table-scroll` is now the only horizontal scroll owner and
receives the table ARIA label and focus. ArrowLeft/ArrowRight move that
container by 96px. Desktop facts, columns, empty/loading states, mobile
cards, action callbacks, read-only guards, and the Task Operations Center
integration remain intact.

## Review fixes

The first independent review found:

- P1 nested scroll ownership and unreachable keyboard scrolling;
- P2 loss of compact/top-aligned table semantics;
- P2 native facade buttons missing the mobile flex rule;
- P3 missing runtime-focused coverage.

The P1/P2 findings were fixed. Regression coverage now includes the real
scroll node focus, outer-shell non-focusability, ArrowLeft/ArrowRight
movement, table sizing/alignment, loading with existing rows, and both
native/TDesign button CSS selectors. Final independent review found no
P0/P1/P2 issues. A persistent Playwright/e2e test for browser-computed
scroll/flex behavior remains a non-blocking P3 enhancement; manual Chrome
verification passed at 319px, 320px, and 321px.

## Verification

- Task Operations suite: **14 files / 58 tests passed**.
- Shared UI contract/style suite: **2 files / 19 tests passed**.
- `npx tsc --noEmit`: passed.
- `npm run build`: passed; Vite transformed **7,129 modules**.
- Focused ESLint: **0 errors**; 3 existing Task Operations warnings remain.
- `git diff --check`: passed.
- Final independent review: no actionable P0/P1/P2 findings.

## Scope boundary

This slice does not migrate:

- `TaskOperationDetailDrawer`
- `TaskMutationDialog`
- `ReconciliationPanel`
- `SavedViewsPanel`
- `TaskOperationsCenter`
- the direct state notices in `taskOperationsUi.tsx`

Those remain separate facade-adoption slices. No API, task model, route,
persistence, or backend contract changed.

Design:
`docs/plans/2026-09-26-frontend-task-operations-table-facade-adoption-design.md`
