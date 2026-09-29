# Frontend Task Operations table facade adoption design (2026-09-26)

## Context

`TaskOperationsTable` is the largest remaining Task Operations presentation
consumer with a direct TDesign import. It uses `Button`, `Tag`, `Loading`, and
`PrimaryTable`; the mobile card surface and desktop table share the same task
facts and mutation callbacks.

The shared UI facade already owns the native/TDesign-compatible `Button` and
`Tag` boundaries and exposes a renderer-independent `Table` contract. The
desktop table can use that contract without changing task data, filtering,
mutation authority, or focus labels.

## Decision

- Replace direct TDesign `Button`, `Tag`, and `Loading` imports with the
  existing shared facade.
- Replace `PrimaryTableCol` with a local, typed table-column shape and adapt
  the existing cell renderers to the shared `Table` `dataSource`/`columns`
  contract.
- Keep the desktop wrapper test id, horizontal scrolling, row key, empty
  message, loading state, column labels, task identity/status/facts, and
  action callbacks unchanged.
- Preserve read-only fail-closed behavior: retry/cancel callbacks remain
  guarded both by the rendered control state and the click handler.
- Add only the table CSS compatibility needed for the native facade table;
  do not change task model, API, controller, or backend contracts.

## Verification plan

1. Add source and behavior tests before implementation and observe the
   expected red source-boundary failure.
2. Implement the import and table-contract migration.
3. Run Task Operations table/center tests, TypeScript, focused ESLint, build,
   and `git diff --check`.
4. Request an independent sub-agent review, fix any findings, and repeat the
   focused verification.
5. Write the handoff and append the cumulative progress ledger.

## Scope boundary

This slice does not migrate `TaskOperationDetailDrawer`, `TaskMutationDialog`,
`ReconciliationPanel`, `SavedViewsPanel`, `TaskOperationsCenter`, or the
shared `taskOperationsUi` state notices. Those remain independent slices.
