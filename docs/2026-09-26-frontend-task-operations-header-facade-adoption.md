# Frontend Task Operations Header facade adoption

Date: 2026-09-26

## Outcome

Migrated the Task Operations header boundary to the shared UI facade:

- `frontend/src/enterprise-task-operations/components/TaskOperationsHeader.tsx`
  now consumes facade `Button` and `Tag` instead of direct TDesign controls.
- `TaskBoundaryTag` in
  `frontend/src/enterprise-task-operations/components/taskOperationsUi.tsx`
  now consumes the shared `Tag` facade.
- Historical TDesign Button variants map to facade vocabulary:
  `variant="outline"` → `type="default"` and `variant="text"` → `type="text"`.

Existing title, tenant label, authority date, refresh callback, settings
affordance, read-only labels, icons, CSS classes, and ARIA names remain
unchanged. No API, task model, route, persistence, or backend contract
changed.

## Verification

- Header/source/TaskOperationsCenter focused suite: **3 files / 15 tests passed**.
- `npx tsc --noEmit`: passed.
- `npm run build`: passed; Vite transformed **7,129 modules**.
- Focused ESLint: **0 errors / 10 existing Fast Refresh warnings** in
  `taskOperationsUi.tsx`.
- `git diff --check`: passed.
- Independent sub-agent review: **PASS**, no actionable P0–P3 findings.

The repository-wide frontend suite was not rerun for this isolated consumer
slice.

## Scope boundary

Other direct TDesign consumers in Task Operations remain separate migration
slices. The shared UI facade remains the compatibility boundary for future
native/TDesign renderer changes.

Design:
`docs/plans/2026-09-26-frontend-task-operations-header-facade-adoption-design.md`
