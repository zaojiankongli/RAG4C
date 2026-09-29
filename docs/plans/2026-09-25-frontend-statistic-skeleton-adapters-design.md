# Frontend Statistic/Skeleton native Adapter design (2026-09-25)

## Context

The previous UI renderer policy slice moved 27 conditional compatibility
components behind `uiRendererAdapter`. Two small facade exports still bypass
that boundary:

- `Skeleton` always renders TDesign;
- `Statistic` is a direct TDesign alias.

Their current consumers are narrow and known:

- `Skeleton` is used by the streaming answer placeholder;
- `Statistic` is used by the Eval metric cards.

Keeping these two aliases outside the renderer policy leaves the facade with
two different extension rules and keeps a TDesign-only rendering dependency in
the most visible loading/metrics surfaces.

## Decision

Add `skeleton` and `statistic` to the typed renderer registry and implement
native fallback Adapters in `frontend/src/ui/index.tsx`.

The registry continues to select `native` for all entries. This preserves the
application's current compatibility strategy while making the two exports
participate in the same fail-closed renderer contract.

### Skeleton Adapter

The native branch renders:

- one title placeholder when `title !== false`;
- `paragraph.rows` placeholder lines, defaulting to three;
- zero paragraph lines when callers explicitly pass `paragraph={false}`;
- `rag-skeleton` classes and an active shimmer class;
- forwarded safe `aria-*`/`data-*` attributes without leaking framework props.

`active={false}` keeps the same placeholder structure but disables shimmer
animation. The TDesign branch retains the historical forced `gradient`
animation behavior; the policy currently selects native.

The structure is intentionally presentation-only; it does not invent a
loading lifecycle or change the parent streaming state.

### Statistic Adapter

The native branch renders:

- a title block;
- prefix/value/suffix in a tabular numeric content block;
- `precision` formatting for numeric values;
- `formatter` when supplied;
- `valueStyle` on the value block;
- safe DOM/ARIA attributes on the root.

The native output keeps the existing Eval value semantics (`—`, percentages,
one decimal place, and metric colors) and does not add a new domain model.
If a component is later opted into TDesign, the Adapter maps the compatibility
vocabulary explicitly: `precision` to `decimalPlaces`, `formatter` to `format`,
and `valueStyle.color` to TDesign `color`, while merging remaining style
properties.

## Failure and compatibility contract

- Both renderer decisions are keyed registry lookups.
- Unknown, prototype, malformed, or throwing policy inputs remain
  fail-closed to `native`.
- Existing `Skeleton` and `Statistic` callers keep their public props.
- `Skeleton` framework-only props do not reach native DOM nodes.
- No API, route, storage, authority, or backend contract changes.
- The always-TDesign `Collapse`, `Menu`, `Layout`, `ConfigProvider`, and
  `Radio` root aliases remain separate follow-up boundaries because they have
  broader shell or child-component semantics.

## Verification

1. Add red tests for native Statistic value formatting/style/ARIA and Skeleton
   placeholder structure/prop filtering.
2. Extend source guards so both declarations are paired with their renderer
   keys.
3. Add focused CSS contracts for Skeleton motion and Statistic layout.
4. Run focused UI tests, TypeScript, ESLint, full frontend suite/build when
   the shared facade change is stable.
5. Request an independent sub-agent review and write a handoff document.
