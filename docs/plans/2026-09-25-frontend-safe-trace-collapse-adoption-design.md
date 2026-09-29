# Frontend SafeTracePanel Collapse facade adoption design (2026-09-25)

## Context

The shared `Collapse` facade now owns the native/TDesign renderer boundary and
the stable `items` API. `SafeTracePanel` is the remaining retrieval-quality
consumer that imports `Collapse` directly from `tdesign-react` and uses the
child-only `Collapse.Panel` syntax. That leaves one small feature component
outside the common UI Adapter even though its behavior fits the existing
facade contract.

## Decision

Migrate `SafeTracePanel` to the app-owned facade:

```tsx
import { Collapse } from "../../ui";
```

Represent its single panel with the existing `items` API:

- `key: "trace"`;
- the existing trace heading, including the count;
- the existing ordered list and `rq-traces` class;
- `borderless`;
- `defaultValue={[]}` so the panel remains closed by default.

Keep the empty-trace early return so the component does not create an empty
disclosure. Do not change trace text, list keys, CSS ownership, or the parent
retrieval-quality components.

## Compatibility and failure boundary

- The component depends only on the facade's public `items` contract.
- It must not import `tdesign-react` directly.
- The existing Collapse normalization and native ARIA behavior remain the
  single source of truth for rendering and interaction.
- No route, API, storage, backend, or persisted-state contract changes.
- This slice does not migrate the other retrieval-quality components that use
  unrelated TDesign controls.

## Verification

1. Add a component regression test for empty traces, closed-by-default trace
   disclosure, preserved heading/count, list content, and toggle behavior.
2. Add a source guard proving the facade import and `items` contract while
   rejecting the direct TDesign dependency and child-panel syntax.
3. Run the focused SafeTracePanel/Collapse tests, TypeScript, and focused
   ESLint.
4. Request an independent sub-agent review and fix any in-scope findings.
5. Write the handoff document, update the extensibility progress ledger, then
   run the full frontend suite/build/lint because this changes a shared UI
   facade consumer.
