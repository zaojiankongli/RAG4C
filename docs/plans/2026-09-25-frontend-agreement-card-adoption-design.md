# Frontend AgreementPanel Card facade adoption design (2026-09-25)

## Context

`AgreementPanel` is a small retrieval-quality surface that still imports
`Card` directly from `tdesign-react`. Its usage is already within the shared
facade's supported boundary, but it uses TDesign's `header` prop while the
facade currently exposes the equivalent app-owned prop as `title`.

Migrating the consumer without addressing that alias would silently lose the
visible heading on the native renderer and would leak `header` as an
unintended DOM attribute.

## Decision

1. Extend the shared `Card` Adapter to accept the historical `header` prop as
   a first-class header-content contract.
2. Preserve TDesign's precedence: `header` content wins when present;
   otherwise the facade's `title` is used.
3. Render arbitrary `header` React nodes directly in the native header slot,
   rather than nesting them in a title-only element or leaking `header` to
   the DOM. Forward `header` to the TDesign branch unchanged.
4. Migrate `AgreementPanel` to:

   ```tsx
   import { Card } from "../../ui";
   ```

The agreement metrics, `rq-agreement` class, `bordered` prop, and existing
copy remain unchanged. This slice does not migrate the larger `StrategyCard`
or other retrieval-quality components.

## Compatibility and failure boundary

- `header` is consumed by the facade and must not reach native DOM.
- `header` wins over `title` when both are supplied, matching TDesign.
- Falsy `header` values fall back to `title` in both renderers.
- TDesign root accessibility/data/event props are retained on the facade root
  wrapper because the dependency Card does not forward arbitrary props.
- The change affects only Card rendering; no route, API, backend, storage, or
  persisted-state contract changes.
- Native Card remains the default renderer through the existing registry.

## Verification

1. Add a failing Card contract test for the `header` alias.
2. Add AgreementPanel component/source tests for native rendering, visible
   heading, metrics, and the facade import.
3. Run focused tests, TypeScript, and focused ESLint.
4. Request an independent sub-agent review, fix any in-scope findings, and
   write the handoff/progress entry.
5. Run the full frontend suite/build/lint because the shared Card boundary
   changed.
