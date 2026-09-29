# Frontend StrategyCard facade adoption design (2026-09-26)

## Context

`StrategyCard` is the main retrieval-quality editor surface that still imports
all of its controls directly from `tdesign-react`. The shared UI facade already
has native adapters for Card, Input, InputNumber, Select, Switch, Button, and
Tag, but the historical TDesign props used by this component are not yet
normalized at the feature boundary:

- `Input` uses `maxLength` and event-shaped `onChange` in the facade;
- `Switch` uses `checked`, not TDesign's `value`;
- native `Button` uses the facade's `type`/`danger` vocabulary;
- native `Tag` must consume TDesign `theme`/`variant` instead of leaking them
  to DOM.

## Decision

Migrate `StrategyCard` to `../../ui` and make the smallest shared Adapter
normalizations required by this consumer:

- `Tag` accepts `theme`, `variant`, and `size` as compatibility metadata and
  maps them to app-owned modifier classes;
- `Input` and `Input.Search` map facade `maxLength` to TDesign's lowercase
  `maxlength` contract;
- `StrategyCard` uses event-shaped Input handlers, `checked` Switch state, and
  facade Button props; clearing Top K stores `0`, allowing existing validation
  to report the invalid range without introducing `NaN`;
- existing Card header content, field labels, validation messages, options,
  and action semantics remain unchanged.

No backend/API/storage/route contract changes are included. `RetrievalComposer`
continues to own the strategy collection state; `StrategyCard` remains a
presentational editor with explicit callbacks.

## Failure and compatibility boundary

- Unknown Tag themes fall back to the neutral native appearance.
- Framework-only Tag props are consumed and never reach native DOM.
- Strategy controls preserve accessible labels and native input semantics.
- The TDesign renderer branch retains explicit mapping for `theme`, `variant`,
  and `size`; the default registry remains native.
- Other retrieval-quality direct TDesign consumers remain separate slices.

## Verification

1. Add failing StrategyCard behavior/source tests and Tag prop-leakage
   contracts.
2. Implement the smallest Adapter and consumer changes.
3. Run focused StrategyCard/RetrievalComposer/UI tests, TypeScript, and
   focused ESLint.
4. Request an independent sub-agent review and fix valid findings.
5. Write the handoff/progress entry, then run the full frontend suite/build/lint
   because the shared Tag facade changes.
