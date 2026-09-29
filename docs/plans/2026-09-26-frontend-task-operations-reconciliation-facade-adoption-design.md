# Frontend Task Operations Reconciliation panel facade adoption design

## Context

`ReconciliationPanel` still imports TDesign `Button`, `Empty`, and `Tag`
directly. The shared facade already owns the action/tag renderer boundary,
but `Empty` is not yet registered as a renderer-selectable compatibility
component. The panel is a controlled, read-only-by-default presentation
surface and must not alter reconciliation authority or mutation callbacks.

## Decision

- Add a renderer-policy `empty` component and a shared `Empty` facade with a
  small native fallback that preserves `type`, `title`, `description`,
  accessibility/data attributes, and class/style ownership.
- Migrate `ReconciliationPanel` to shared `Button`, `Empty`, and `Tag`.
- Map the historical text button to `type="text"` and preserve the
  read-only disabled boundary, severity themes, alert tags, empty state, and
  panel ARIA/CSS structure.
- Add native and TDesign Empty compatibility tests, renderer-registry
  parity/source tests, and Reconciliation/Task Operations Center behavior
  coverage.

## Verification plan

1. Add the Empty facade and consumer/source tests before implementation and
   observe the expected red boundary.
2. Implement the renderer registry, native/TDesign Empty adapter, and panel
   migration.
3. Run the shared UI and Task Operations focused suites, TypeScript,
   focused ESLint, build, and `git diff --check`.
4. Request independent review, fix all findings, rerun verification, and
   write the handoff/progress entry.

## Scope boundary

This slice does not migrate `SavedViewsPanel`, `TaskOperationDetailDrawer`,
`TaskMutationDialog`, `TaskOperationsCenter`, or the direct `Alert`/`Loading`
state notices in `taskOperationsUi.tsx`.
