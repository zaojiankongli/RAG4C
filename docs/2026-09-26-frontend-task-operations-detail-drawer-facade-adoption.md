# Frontend Task Operations detail drawer facade adoption

Date: 2026-09-26

## Outcome

Migrated `TaskOperationDetailDrawer` to the shared UI compatibility boundary:

- `Button`, `Drawer`, `Empty`, `Spin`, and `Tag` now come from
  `frontend/src/ui`.
- Historical button props map explicitly:
  `variant="text"` → `type="text"`,
  `variant="outline"` → `type="default"`,
  `theme="danger"` → `danger`.
- The Drawer owns the single modal dialog semantics; the inner detail shell
  no longer creates a second `role="dialog"`.
- `attach="body"` works for the native facade through a body portal.
- Native and TDesign Drawer Tab traps skip an independently open nested modal,
  so Retry/Cancel confirmation dialogs retain keyboard focus.
- Native `Spin` now exposes a visible indicator, `role="status"`,
  `aria-busy`, and `aria-live`.

The drawer preserves focus return, Escape/overlay close, loading/unavailable/
error/empty states, safe snapshot filtering, event-chain labels, verified
callbacks, read-only disabled actions, and explicit `light-outline` Tag
variants.

## Review fixes

Independent review initially found:

- P1 nested confirmation Dialog focus being reclaimed by the Drawer trap;
- P1 nested modal dialog semantics;
- P2 portal/header/body style and `attach="body"` parity gaps;
- P2 Tag renderer-variant drift;
- P2 loading semantics drift.

All functional findings were fixed. Final independent follow-up review:
**PASS**, no actionable P0/P1/P2 findings. A persistent TDesign Drawer
runtime suite remains a non-blocking P3 enhancement; native runtime,
source, and compatibility contracts are covered.

## Verification

- Task Operations suite: **21 files / 79 tests passed**.
- Detail Drawer/shared UI focused suite: **6 files / 27 tests passed**.
- `npx tsc --noEmit`: passed.
- `npm run build`: passed; Vite transformed **7,129 modules**.
- Focused ESLint: **0 errors**; 3 existing Task Operations warnings remain.
- `git diff --check`: passed.
- Final independent review: **PASS**.

## Scope boundary

This slice does not migrate `TaskMutationDialog`, `TaskOperationsCenter`, or
the direct `Alert`/`Loading` state notices in `taskOperationsUi.tsx`.
`TaskMutationDialog` only received the explicit `aria-modal` contract needed
for nested-modal focus isolation.

Design:
`docs/plans/2026-09-26-frontend-task-operations-detail-drawer-facade-adoption-design.md`
