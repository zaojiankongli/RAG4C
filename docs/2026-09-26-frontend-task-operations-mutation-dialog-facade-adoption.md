# Frontend Task Operations mutation dialog facade adoption

Date: 2026-09-26

## Outcome

Migrated `TaskMutationDialog` to shared `Dialog`, `Checkbox`, and `Tag`
facades, and added the renderer-policy contracts required by the consumer.

The native/TDesign-compatible boundary now preserves:

- body-attached modal rendering and focus return;
- topmost-only Tab/Escape handling for nested Drawer/Dialog stacks;
- explicit `aria-modal` semantics;
- confirm/cancel/loading/disabled mappings;
- boolean Checkbox change values and common input props;
- danger confirm visual parity;
- task-level body padding and portal CSS;
- retry/cancel confirmation reset when task identity changes.

The mutation boundary remains fail-closed: read-only, saving, unavailable
action, and missing-task states disable confirmation, Checkbox, cancel, and
close controls as appropriate. `TaskMutationDialog` retains its server-owned
submission callback and does not add local mutation logic.

## Review fixes

Independent review found and the implementation closed:

- P1 confirmation reuse across task identity changes;
- P1 nested modal focus/Escape ownership;
- P2 saving cancel/close bypass;
- P2 duplicate TDesign cancel/close callbacks;
- P2 danger confirm renderer mismatch;
- P2 body-portal CSS mismatch;
- P2 incomplete Checkbox prop forwarding.

Final independent review: no P0/P1/P2 findings. A persistent TDesign
runtime matrix for every Dialog/Checkbox branch remains a non-blocking P3
enhancement; the production registry defaults to the tested native renderer.

## Verification

- Task Operations suite: **23 files / 85 tests passed**.
- Mutation Dialog/shared UI focused suite: **8 files / 48 tests passed**.
- `npx tsc --noEmit`: passed.
- `npm run build`: passed; Vite transformed **7,129 modules**.
- Focused ESLint: **0 errors**; 3 existing Task Operations warnings remain.
- `git diff --check`: passed.
- Final independent review: **PASS** for P0–P2.

## Scope boundary

This slice does not migrate `TaskOperationsCenter` or the direct
`Alert`/`Loading` state notices in `taskOperationsUi.tsx`. The shared
Dialog/Checkbox adapters are now available for later enterprise dialog
migrations.

Design:
`docs/plans/2026-09-26-frontend-task-operations-mutation-dialog-facade-adoption-design.md`
