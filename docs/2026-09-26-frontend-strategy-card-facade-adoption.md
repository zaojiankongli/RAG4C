# Frontend StrategyCard facade adoption

## Outcome

`StrategyCard` now uses the shared UI facade instead of importing controls
directly from `tdesign-react`:

- `D:\program_project\python_project\RAG4C\frontend\src\retrieval-quality\components\StrategyCard.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\ui\index.tsx`

The migration preserves the existing strategy editor contract:

- Card header and route label;
- accessible field labels and validation messages;
- route/diversity options;
- boolean strategy switches;
- duplicate/remove callbacks;
- native input semantics and retrieval composer state ownership.

The shared facade compatibility boundary now also:

- maps `Input`/`Input.Search` `maxLength` to TDesign `maxlength`;
- consumes Tag `theme`, `variant`, `size`, and `bordered` metadata;
- normalizes unknown Tag themes to `default`;
- preserves native visual modifiers for light/outline/small tags;
- maps cleared Top K to `0`, allowing existing validation to report the
  invalid range rather than persisting `NaN`.

## Verification

- StrategyCard/UI focused suite: **7 files / 28 tests passed**
- Full frontend suite: **323/323 files passed**, manifest
  `817c63bdc857c36ea3fa7c2c76201322ae9689fcf129e3553f24bd8b253ab821`
- `npm run build`: passed, **7,127 modules transformed**
- Full ESLint: **0 errors / 98 existing warnings**
- `npx tsc --noEmit`: passed
- `git diff --check`: passed
- Independent sub-agent review: **PASS**, with all P1/P2 findings fixed and
  covered by regression tests.

## Scope boundary

No route, API, backend, storage, authorization, or persisted-state contract
changed. Other retrieval-quality direct TDesign consumers remain separate
follow-up slices.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-26-frontend-strategy-card-facade-adoption-design.md`
