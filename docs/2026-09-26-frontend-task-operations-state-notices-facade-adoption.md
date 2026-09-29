# Frontend Task Operations state notices facade adoption

Date: 2026-09-26

## Outcome

Closed the remaining direct TDesign state-notice consumers in the Task
Operations flow:

- `taskOperationsUi.tsx` now uses the shared `Alert`, `Empty`, `Spin`, and
  `Tag` facade vocabulary.
- `TaskOperationsCenter.tsx` now uses the shared `Alert` facade for the
  Activity surface.
- Loading keeps the explicit `正在读取…` copy through the facade `Spin`
  `tip` prop.
- Unavailable, error, partial, and empty states keep their existing
  authority-bound copy and never estimate missing facts.
- Alert wrappers no longer add a second `role="alert"` around a facade
  component that already owns the alert role. This keeps one accessible alert
  region in both native and TDesign renderer modes.

No controller, API, task model, mutation, or backend contract changed.

## Verification

- State-notice and Activity focused suite: **4 files / 14 tests passed**.
- Full Task Operations suite: **26 files / 88 tests passed**.
- `npx tsc --noEmit`: passed.
- Focused ESLint: **0 errors / 12 existing warnings**.
- `npm run build`: passed; Vite transformed **7,129 modules**.
- `git diff --check`: passed.
- Independent sub-agent review: **PASS**, no P0/P1/P2 findings; no files changed.

## Scope boundary

This slice only replaces direct state-notice renderer imports and removes
duplicate outer alert roles. It does not change state ownership, activity
event rendering, controller/API/model/backend vocabulary, mutation behavior,
or the shared facade implementation.

Design:
`docs/plans/2026-09-26-frontend-task-operations-state-notices-facade-adoption-design.md`
