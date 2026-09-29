# Frontend Knowledge Serving state notices facade adoption

Date: 2026-09-27

## Outcome

Migrated the Knowledge Serving state-notice consumers to the shared UI
facade, after first extending the shared native `Alert` renderer:

- `ui/index.tsx` native `Alert` branch now renders `operation` content
  (`rag-alert-operation` slot); the TDesign branch retains and forwards the
  `operation` prop, so retryable `LoadState` keeps its retry action on both
  renderers.
- `servingUi.tsx` state `Alert` and `Tag` consumers now use shared
  `Alert`/`Tag`; the direct TDesign `Button` inside the retry operation is
  retained as the design allows.
- `KnowledgeServingCenter`'s inactive capability `Alert` uses shared
  `Alert`; its unrelated TDesign `Button`/`Input`/`Select`/`Tag` controls
  stay on TDesign per the scope boundary.
- `ServingTable` loading migrated from TDesign `Loading` to shared `Spin`
  with explicit copy via `Spin.tip`; `Button`/`PrimaryTable` stay on TDesign.
- Outer duplicate `role="alert"` wrappers in `servingUi.tsx` state wrap were
  removed where shared `Alert` owns the single accessible alert role.
- Loading/unavailable/error/partial/empty copy, retry behavior, and
  authority fail-closed semantics are unchanged; no controller, API, model,
  mutation, or backend contract changed.

## Verification

- Focused state-notice + Knowledge Serving suite:
  `vitest run src/enterprise-knowledge-serving src/ui/alert.nativeOperation.test.tsx`
  → **10 files / 57 tests passed** (19.68s).
- `npx tsc --noEmit`: passed (exit 0).
- Focused ESLint (`src/ui/index.tsx`, `src/ui/alert.nativeOperation.test.tsx`,
  `src/enterprise-knowledge-serving`): **0 errors / 17 warnings** (existing
  `react-hooks/exhaustive-deps` etc., not introduced by this slice).
- `npm run build`: passed (Vite built in ~19.7s).
- `git diff --check`: passed.
- Independent sub-agent review: **PASS_WITH_NITS**, no P0/P1/P2 findings.
  NITs recorded (not blocking): the state-notices source-test import regex
  is looser than per-import matching; the TDesign branch's forwarded
  `operation` prop is not pinned by a dedicated compat test; the shared
  working tree carries parallel facade changes in `ui/index.tsx` from other
  slices (the four consumer files' diffs are clean and state-notice only).

## Scope boundary

This slice only extends the shared native Alert `operation` rendering and
replaces Knowledge Serving state-notice renderer consumers. It does not
migrate Knowledge Serving drawer/table/filter/policy controls beyond the
listed state-notice controls, and does not change state ownership or
backend behavior.

Design:
`docs/plans/2026-09-27-frontend-knowledge-serving-state-notices-facade-adoption-design.md`
