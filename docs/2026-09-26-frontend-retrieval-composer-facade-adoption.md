# Frontend RetrievalComposer facade adoption

## Outcome

`RetrievalComposer` now consumes the shared UI facade instead of importing
`Alert`, `Button`, `Card`, `Input`, `Space`, and `Textarea` directly from
`tdesign-react`:

- `D:\program_project\python_project\RAG4C\frontend\src\retrieval-quality\components\RetrievalComposer.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\retrieval-quality\retrieval-quality.css`

The migration preserves the composer contract:

- the query field uses `Input.TextArea` with the 20,000-character limit and
  3–8 row autosizing;
- query and ACL values are extracted from the facade's event-shaped payload;
- sample questions remain state-only actions and never auto-run;
- validation still blocks `onRun`, keeps existing error copy, and uses the
  shared `[aria-invalid="true"]` focus path;
- add/duplicate/remove actions, StrategyCard state ownership, and the pure
  retrieval run payload remain unchanged;
- the native `rag-space` spacer and native buttons receive the same flex and
  mobile-width treatment as their historical TDesign counterparts.

The related validation boundary was tightened:

- ACL invalid state now marks the actual shared Input with `aria-invalid`;
- Top K invalid state now marks the actual shared InputNumber with
  `aria-invalid`;
- the TDesign InputNumber adapter synchronizes `id`, `aria-*`, and `data-*`
  props to its nested real input while retaining caller `inputProps`.

## Verification

- RetrievalComposer/UI focused suite: **7 files / 19 tests passed**
- focused TypeScript check: passed
- focused ESLint: **0 errors**
- `git diff --check`: passed
- independent sub-agent review found two P2 focus/accessibility gaps; both were
  fixed with native/TDesign regression coverage
- follow-up independent review: **PASS**, no remaining P0–P3 findings

Final repository-wide validation:

- full frontend suite: **331/331 files passed**, manifest
  `3545def58dad01f9718486f8fba4bde5a3bc20f2b561f73b0aa6b220997a6cea`
- `npm run build`: passed, **7,127 modules transformed**
- `npm run lint`: **0 errors / 98 existing warnings**
- final `git diff --check`: passed

## Scope boundary

No route, API, backend, storage, authorization, or persisted-state contract
changed. `ComparisonResults` and other retrieval-quality direct TDesign
consumers remain separate follow-up slices.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-26-frontend-retrieval-composer-facade-adoption-design.md`
