# Frontend Task Operations Saved Views facade adoption design

## Context

`SavedViewsPanel` still imports TDesign `Button`, `Empty`, and `Tag`
directly. The shared UI facade now owns all three boundaries, including the
renderer-policy `empty` adapter introduced for the adjacent Reconciliation
panel slice.

Saved Views is a controlled presentation shortcut: selecting a view changes
the visible filter surface but does not mutate task authority. Its active
state, pinned marker, authority notices, empty copy, read-only note, and
selection callback must remain unchanged.

## Decision

- Migrate `SavedViewsPanel` to shared `Button`, `Empty`, and `Tag`.
- Map the historical text action to `type="text"`.
- Preserve active CSS state, pinned labels, alert/empty states, selection
  callback, read-only copy, panel ARIA, and all existing task-operation
  vocabulary.
- Do not change controller shape, saved-view persistence, API, route, or
  backend contracts.

## Verification plan

1. Add source and behavior tests before implementation and observe the
   expected red direct-import boundary.
2. Implement the isolated consumer migration.
3. Run Saved Views/Task Operations Center/shared UI focused tests, TypeScript,
   focused ESLint, build, and `git diff --check`.
4. Request an independent sub-agent review, fix all findings, rerun checks,
   and write the handoff/progress entry.

## Scope boundary

This slice does not migrate `TaskOperationDetailDrawer`,
`TaskMutationDialog`, `TaskOperationsCenter`, or the direct `Alert`/`Loading`
state notices in `taskOperationsUi.tsx`.
