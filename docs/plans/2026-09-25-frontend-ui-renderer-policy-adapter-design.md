# Frontend UI renderer policy + Adapter design (2026-09-25)

## Context

`frontend/src/ui/index.tsx` is the application's compatibility facade over
TDesign.  Its fallback widgets are intentionally used today because several
TDesign popup/form paths can enter a browser render loop with the current
kept-alive page architecture.  The decision is currently encoded as one
parameterless function:

```ts
const canRenderTDesign = () => false;
```

That keeps the current behavior, but it hides the extension boundary.  A later
renderer rollout would have to edit every component branch independently, and
an unknown component could silently inherit the wrong renderer.

The existing frontend work already uses typed registries and Adapters for
navigation and theme metadata.  The UI facade should use the same boundary
without changing the rendered output in this slice.

## Decision

Add `frontend/src/ui/rendererPolicy.ts` with:

- a closed, typed set of compatibility components that have native/TDesign
  render branches;
- a `native | tdesign` renderer strategy;
- one registry entry per component, including a key-parity type check;
- a pure lookup Adapter that returns the declared renderer;
- fail-closed lookup for unknown or prototype-inherited keys.

The initial registry deliberately selects `native` for every conditional
compatibility component.  This preserves the current production path while
making a future per-component rollout a single registry decision rather than a
distributed set of branches.  The registry is not a plugin loader and does
not mutate at runtime.

`frontend/src/ui/index.tsx` will consume the Adapter with a component key at
each native/TDesign boundary.  The Adapter owns renderer selection; individual
components remain responsible for translating props and preserving their
existing behavior.

## Component scope

The registry covers only components that currently have both a native fallback
and a TDesign branch:

- alert, button, card, tooltip, popover, popconfirm;
- input, input-search, input-text-area, select, input-number, switch;
- spin, progress, tag, text, title, paragraph;
- space, col, row;
- radio-button, radio-group, segmented, tabs;
- drawer, modal.

Always-TDesign aliases such as `ConfigProvider`, `Statistic`, the layout
content slot, the current menu, skeleton, and collapse are outside this slice.
They do not currently consult the old policy function and would need a
separate native implementation before they could safely participate in the
registry.

## Failure and compatibility contract

- Unknown renderer keys resolve to `native`; they never opt into TDesign.
- Prototype names such as `toString` and `__proto__` are not accepted as
  registered components.
- Registry entries cannot omit a component or use a renderer outside the
  declared union.
- The current native renderer remains selected for every entry.
- No CSS, DOM vocabulary, event signature, route, API, storage key, or public
  UI import changes.
- The existing UI facade tests must remain green.

## Verification

1. Unit-test registry completeness, renderer values, unknown-key fail-closed
   behavior, and prototype-key rejection.
2. Add a source guard proving the facade uses keyed Adapter lookups rather than
   the old parameterless global branch.
3. Run the focused UI compatibility suite, TypeScript/build, focused ESLint,
   and `git diff --check`.
4. Request an independent sub-agent review of the registry, all call-site
   mappings, and behavior-preservation evidence.
