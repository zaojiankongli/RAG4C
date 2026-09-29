# Frontend Automation state notices facade adoption

Date: 2026-09-26

## Outcome

Migrated the Automation Workflows state-notice consumers to the shared UI
facade:

- `automationUi.tsx` now uses shared `Alert`, `Empty`, `Spin`, and `Tag`.
- `AutomationCenter` and `AutomationAttentionBoard` now use shared `Alert`.
- `AutomationTables` uses shared `Alert` and `Spin` for activity partial
  notices and loading surfaces while retaining its unrelated TDesign
  Button/Table/Tag/Timeline controls.
- Loading keeps explicit `正在读取…` copy through `Spin.tip`.
- Unavailable, error, partial, empty, capability, mutation, summary, and
  activity notices retain their authority-bound copy and fail-closed meaning.
- Outer duplicate alert roles were removed where the shared `Alert` already
  owns the single accessible `role="alert"`.
- Native facade alert/empty selectors were added to the Automation CSS so
  spacing parity remains intact when the native renderer is selected.

No controller, API, model, mutation, or backend contract changed.

## Verification

- State-notice focused suite after review: **5 files / 5 tests passed**.
- Full Automation Workflows suite after review: **11 files / 52 tests passed**.
- `npx tsc --noEmit` after review: passed.
- Focused ESLint after review: **0 errors / 14 existing warnings**.
- `npm run build` after review: passed; Vite transformed **7,129 modules**.
- `git diff --check` after review: passed.
- Independent sub-agent review: **PASS**; it found and fixed one P2 CSS
  parity issue, with no remaining P0/P1/P2 findings.

## Scope boundary

This slice only replaces state-notice renderer consumers. It does not migrate
Automation table/button/dialog/drawer/timeline controls outside the state
notice surface, and does not change state ownership or backend behavior.

Design:
`docs/plans/2026-09-26-frontend-automation-state-notices-facade-adoption-design.md`
