# Frontend ExperimentHistory facade adoption

## Outcome

`ExperimentHistory` now consumes the shared UI facade instead of importing
`Button`, `Card`, `Input`, `Select`, and `Tag` directly from `tdesign-react`:

- `D:\program_project\python_project\RAG4C\frontend\src\retrieval-quality\components\ExperimentHistory.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\ui\index.tsx`

The migration preserves the history surface contract:

- the native semantic table, keyset pagination, loading/error/empty states,
  date formatting, `projectExperiment`, and ARIA labels remain unchanged;
- historical outline actions use the facade default button, the filter action
  uses `type="primary"`, and detail actions use `type="text"`;
- filter text is read from the facade's event-shaped `{ target: { value } }`
  payload, so native and TDesign renderers submit real strings;
- the `Select` status values and `Tag` success/danger themes remain unchanged;
- the `RefObject<HTMLButtonElement>` detail opener still receives the actual
  focusable button.

The shared `Input` adapter was hardened because the independent review found
that TDesign places ARIA/data props on its wrapper rather than the inner
`<input>`. The adapter now:

- copies `id`, `aria-*`, and `data-*` props to the actual TDesign input;
- removes those framework-bound props from the wrapper;
- removes stale attributes when props change;
- preserves the existing TDesign ref and event-shaped change contract.

## Verification

- ExperimentHistory/UI focused suite: **7 files / 29 tests passed**
- focused TypeScript check: passed
- focused ESLint: **0 errors**
- `git diff --check`: passed
- independent sub-agent review initially found one P1 accessibility defect;
  after the adapter fix and new native/TDesign regressions, follow-up review:
  **PASS**, no remaining P0–P3 findings

Final repository-wide validation:

- full frontend suite: **329/329 files passed**, manifest
  `d27e37ab435b01f5cb8ffb9022823e939cc5e79ed94337d95a13aa305937d11f`
- `npm run build`: passed, **7,127 modules transformed**
- `npm run lint`: **0 errors / 98 existing warnings**
- final `git diff --check`: passed

## Scope boundary

No route, API, backend, storage, authorization, or persisted-state contract
changed. `RetrievalComposer` and `ComparisonResults` remain separate direct
TDesign follow-up slices.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-26-frontend-experiment-history-facade-adoption-design.md`
