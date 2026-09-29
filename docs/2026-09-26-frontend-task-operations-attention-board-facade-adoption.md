# Frontend Task Operations Attention Board facade adoption

Date: 2026-09-26

## Outcome

Migrated the Task Operations attention board's tenant-scope badge to the
shared UI facade:

- `frontend/src/enterprise-task-operations/components/TaskAttentionBoard.tsx`
  now consumes `Tag` from `frontend/src/ui` instead of importing TDesign
  directly.

Metric labels, counts, hints, tone classes, icon selection, tenant badge
text, region ARIA, and unavailable-summary behavior remain unchanged. When
the summary is absent, the board still renders `未返回` rather than
inventing zeroes. No API, task model, route, persistence, or backend
contract changed.

## Verification

- Attention Board/source/Task Operations Center focused suite:
  **5 files / 29 tests passed**.
- `npx tsc --noEmit`: passed.
- Focused ESLint: **0 errors**; only existing warnings remain.
- `npm run build`: passed; Vite transformed **7,129 modules**.
- `git diff --check`: passed.
- Independent sub-agent review: **PASS**, no actionable P0–P3 findings.

## Scope boundary

This is an isolated consumer migration. The shared UI facade remains the
renderer compatibility boundary for future native/TDesign changes. Other
Task Operations direct TDesign consumers remain separate slices:

- `TaskOperationsTable`
- `TaskOperationDetailDrawer`
- `TaskMutationDialog`
- `ReconciliationPanel`
- `SavedViewsPanel`
- `TaskOperationsCenter`

Design:
`docs/plans/2026-09-26-frontend-task-operations-attention-board-facade-adoption-design.md`
