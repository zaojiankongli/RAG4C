# Frontend Collapse native Adapter design (2026-09-25)

## Context

The shared UI renderer policy now owns 29 conditional components, but the
facade's `Collapse` export still renders TDesign directly. The current
compatibility wrapper only translates an `items` array into
`TCollapse.Panel`, so the extension boundary is split again.

The active facade consumer is `AnswerCard`, which uses an `items` array for
retrieval traces. A second retrieval-quality panel still imports TDesign
directly and is intentionally outside this slice; migrating it requires a
separate child-API adoption decision.

## Decision

Add `collapse` to `uiRendererComponentNames` and implement a native
`Collapse` Adapter in `frontend/src/ui/index.tsx`.

The registry remains `native` for every entry. The native branch will provide
the stable semantics needed by current callers:

- `items[].key`, `items[].label`, `items[].children`;
- controlled `value` and uncontrolled `defaultValue`;
- `onChange` with the active key collection;
- `accordion` single-open mode;
- disabled items;
- linked `button`/`region` ARIA structure;
- `aria-*`, `data-*`, root identity and class/style metadata.

TDesign-only visual props (`ghost`, `borderless`, `size`) become native
modifier classes rather than leaking to DOM. Unknown item shapes fail closed
to a non-expandable item instead of creating unsafe markup.
Native panels remain mounted and use `hidden` while closed, matching TDesign's
default `destroyOnCollapse=false` behavior and keeping `aria-controls` stable.

## State contract

- In multi mode, active keys are a string array.
- In accordion mode, at most one key is active; public `value`/`onChange`
  still use a one-element-or-empty array to match the existing items adapter's
  collection-oriented shape.
- Numeric keys are normalized to strings in both renderer branches.
- A controlled `value` is never mutated internally.
- An uncontrolled component initializes from `defaultValue`, otherwise no
  item is open.
- Disabled items never toggle or emit.

## Failure and compatibility contract

- Renderer selection is keyed by `uiRendererAdapter.useTDesign("collapse")`.
- Unknown/malformed renderer policy remains native through the existing Adapter.
- Current `AnswerCard` trace disclosure remains closed by default and keeps its
  child content mounted while closed panels are marked `hidden`.
- No route, API, storage, authority, or backend contract changes.
- `Collapse.Panel` child syntax remains a separate follow-up; this slice does
  not claim to migrate the direct TDesign `SafeTracePanel` import.

## Verification

1. Add failing native contract tests for structure, toggle, controlled state,
   accordion, disabled items, ARIA, and prop filtering.
2. Add source/key parity guards and CSS contracts.
3. Run the focused AnswerCard/retrieval trace regression, TypeScript, ESLint,
   full frontend suite and build.
4. Request an independent sub-agent review and write a handoff document.
