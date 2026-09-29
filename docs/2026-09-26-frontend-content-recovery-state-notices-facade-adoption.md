# Frontend Content Recovery state notices facade adoption

Date: 2026-09-26

## Outcome

Migrated the Content Recovery state-notice consumers to the shared UI facade:

- `recoveryUi.tsx` now uses shared `Alert`, `Empty`, `Spin`, and `Tag`.
- `ContentRecoveryCenter` uses shared `Alert` for mutation errors.
- `RecoveryDetailDrawer` uses shared `Alert`, `Empty`, and `Spin` while
  retaining its unrelated TDesign Button/Drawer/Tag controls.
- `RecoveryEntryTable` uses shared `Spin` for loading while retaining its
  unrelated TDesign Button/PrimaryTable/Tag controls.
- Loading keeps explicit authority/detail copy through `Spin.tip`.
- Unavailable, error, partial, empty, mutation-error, and detail-authority
  notices retain their fail-closed copy and semantics.
- Outer duplicate alert roles were removed where shared `Alert` owns the
  single accessible `role="alert"`.

No controller, API, model, mutation, or backend contract changed.

## Verification

- State-notice focused suite after review: **5 files / 5 tests passed**.
- Full Content Recovery suite after review: **11 files / 41 tests passed**.
- `npx tsc --noEmit`: passed.
- Focused ESLint: **0 errors / 11 existing warnings**.
- `npm run build`: passed; Vite transformed **7,129 modules**.
- `git diff --check`: passed.
- Independent sub-agent review: **PASS**, no P0/P1/P2 findings; no files
  changed.

## Scope boundary

This slice only replaces Content Recovery state-notice renderer consumers. It
does not migrate unrelated buttons, tables, drawers, filters, dialogs, or
policy controls, and does not change state ownership or backend behavior.

Design:
`docs/plans/2026-09-26-frontend-content-recovery-state-notices-facade-adoption-design.md`
