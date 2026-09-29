# Frontend SafeTracePanel Collapse facade adoption

## Outcome

`SafeTracePanel` now uses the shared UI facade instead of importing
`Collapse` from `tdesign-react` directly:

- `D:\program_project\python_project\RAG4C\frontend\src\retrieval-quality\components\SafeTracePanel.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\ui\index.tsx`

The component keeps the existing empty-state behavior, trace heading/count,
ordered list, `rq-traces` styling, `borderless` modifier, and closed default
state. The single trace disclosure is expressed through the facade `items`
API.

## Adapter boundary

The shared Collapse facade remains the only rendering boundary for this
consumer. Its native branch keeps the current default renderer. The TDesign
branch now also goes through a compatibility wrapper that:

- reuses normalized item/key/value data;
- preserves accordion and controlled/uncontrolled behavior;
- forwards normalized collection values and the TDesign event context;
- adds stable trigger/panel IDs and disclosure ARIA links;
- supports Enter/Space activation for the TDesign header wrapper;
- keeps the panel mounted and marks it `hidden` when closed.

This avoids making the SafeTracePanel migration depend on TDesign child-only
`Collapse.Panel` syntax or on dependency-specific markup.

## Verification

- SafeTracePanel + Collapse + renderer registry focused suite: **8 files / 28 tests passed**
- Retrieval-quality consumer regression: **4 files / 8 tests passed**
- Focused ESLint: passed
- `npx tsc --noEmit`: passed
- `git diff --check`: passed
- Full frontend suite: **316/316 files passed**, manifest
  `0fced59d899e8b22908557cec1f00a8e42288853e307b5c270468c3122f91a54`
- `npm run build`: passed, **7,127 modules transformed**
- Full ESLint: **0 errors / 98 existing warnings**
- Independent sub-agent review: **PASS** for runtime, accessibility, and state behavior; documentation bookkeeping findings were corrected in this handoff and the progress ledger.

## Scope boundary

This slice changes no route, API, backend, storage, authorization, or
persisted-state contract. Other retrieval-quality components still have direct
TDesign imports for controls outside this Collapse adoption. The shared UI
facade's remaining always-TDesign namespaces are separate follow-up slices.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-25-frontend-safe-trace-collapse-adoption-design.md`
