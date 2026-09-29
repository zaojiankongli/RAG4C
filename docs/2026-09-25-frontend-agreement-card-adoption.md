# Frontend AgreementPanel Card facade adoption

## Outcome

`AgreementPanel` now imports `Card` from the shared UI facade rather than
`tdesign-react` directly:

- `D:\program_project\python_project\RAG4C\frontend\src\retrieval-quality\components\AgreementPanel.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\ui\index.tsx`

The shared Card Adapter now consumes the historical `header` prop without
leaking it to native DOM, preserves TDesign's `header` over `title` precedence,
and accepts arbitrary React content in the header slot. `bordered` is also
consumed: the native renderer maps `bordered={false}` to an app-owned modifier,
and TDesign receives the explicit boolean. This keeps AgreementPanel's
heading, `rq-agreement` class, metrics, and surrounding markup intact.

When the TDesign renderer is selected, root `id`, `role`, `tabIndex`,
`aria-*`, `data-*`, and DOM event props are retained on an explicit facade root
wrapper instead of being silently discarded by the dependency component.

## Verification

- Card/AgreementPanel focused suite: **4 files / 18 tests passed**
- Retrieval-quality consumer regression: **6 files / 21 tests passed**
- Focused ESLint: passed
- `npx tsc --noEmit`: passed
- `git diff --check`: passed
- Full frontend suite: **319/319 files passed**, manifest
  `98b255b21f8c38994f8278427a1d4c34ff692e163a3a55cb4636b463372dbfa9`
- `npm run build`: passed, **7,127 modules transformed**
- Full ESLint: **0 errors / 98 existing warnings**
- Independent sub-agent review: **PASS**, with the falsy-header parity finding
  fixed and covered by regression.

## Scope boundary

No route, API, backend, storage, authorization, or persisted-state contract
changed. `StrategyCard` and other retrieval-quality direct TDesign consumers
are intentionally separate follow-up slices.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-agreement-card-adoption-design.md`
