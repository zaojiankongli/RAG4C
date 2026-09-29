# Frontend Task Operations detail drawer facade adoption design

## Context

`TaskOperationDetailDrawer` still imports TDesign `Button`, `Drawer`,
`Empty`, `Loading`, and `Tag` directly. The shared facade now owns all of
these boundaries except the historical `Loading` name, which maps to the
existing renderer-selectable `Spin` facade.

The drawer is an authority-sensitive read surface with guarded retry,
cancel, acknowledge, and handoff actions. It must preserve focus return,
overlay/Escape behavior, safe snapshot filtering, event-chain rendering,
read-only controls, and explicit loading/unavailable/error states.

## Decision

- Migrate the drawer to shared `Button`, `Drawer`, `Empty`, `Spin`, and `Tag`.
- Map historical button props explicitly:
  `variant="text"` → `type="text"`,
  `variant="outline"` → `type="default"`,
  `theme="danger"` → `danger`.
- Keep the existing shared Drawer contract and its focus/overlay semantics,
  while closing the compatibility gaps required by this consumer:
  body-attached native portal mounting, nested-modal-aware Tab trapping,
  native header/body styling selectors, and loading semantics.
- Preserve authority-state copy, safe facts, event labels, empty states,
  callbacks, ARIA names, and read-only mutation guards.

## Verification plan

1. Add source and behavior tests before implementation and observe the
   expected direct-import boundary failure.
2. Implement the isolated consumer migration.
3. Run drawer/Task Operations/shared UI focused tests, TypeScript, focused
   ESLint, build, and `git diff --check`.
4. Request independent sub-agent review, fix findings, rerun verification,
   and write the handoff/progress entry.

## Scope boundary

This slice does not migrate `TaskMutationDialog`, `TaskOperationsCenter`, or
the direct `Alert`/`Loading` state notices in `taskOperationsUi.tsx`.
The mutation dialog receives only the modal `aria-modal` source contract
needed to keep its nested confirmation boundary visible to the Drawer trap.
