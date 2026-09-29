# Frontend ExperimentHistory facade adoption design (2026-09-26)

## Context

`ExperimentHistory` is the remaining retrieval-quality history surface that
still imports its five controls directly from `tdesign-react`. The shared UI
facade already provides the required `Button`, `Card`, `Input`, `Select`, and
`Tag` adapters. The component also contains an important event boundary:
TDesign's text input emits a value, while the shared `Input` facade exposes an
event-shaped `{ target: { value } }` payload consistently across native and
TDesign renderers.

## Decision

Migrate only `ExperimentHistory` to `../../ui`:

- keep the native semantic table, keyset pagination, date projection,
  `projectExperiment`, and focus-opener contract unchanged;
- use the facade's `type` vocabulary for buttons:
  - default button for historical outline actions;
  - `type="primary"` for applying filters;
  - `type="text"` for opening a detail;
- extract `runId` and exact-query strings from the facade input event;
- keep `Select`'s value callback and `Tag` status themes unchanged;
- continue using the existing `Card bordered` boundary without adding a new
  component-specific adapter.

## Compatibility and failure boundary

- Unknown or framework-only props must not leak into native buttons or tags.
- Native and TDesign input renderers must both submit the actual text, never
  `"[object Object]"`.
- Native and TDesign select renderers must both preserve `""`, `"completed"`,
  and `"failed"` values.
- Pagination disabled state, table ARIA names, loading/error/empty rows, and
  `onSelect(item, opener)` behavior remain unchanged.
- No route, API, backend, storage, authorization, or persisted-state change.

## Verification

1. Add failing behavior/source tests for the facade import, input event
   normalization, button vocabulary, status tags, and existing table/pager
   behavior.
2. Implement the smallest consumer migration; do not expand the shared facade
   beyond existing contracts.
3. Run the focused ExperimentHistory/UI suite, TypeScript, and focused ESLint.
4. Request an independent sub-agent review and fix valid findings, including
   renderer-specific accessibility regressions in shared adapters.
5. Write the handoff/progress entry, then rerun the full frontend suite, build,
   lint, and `git diff --check` because the migrated component exercises
   multiple shared adapters.
