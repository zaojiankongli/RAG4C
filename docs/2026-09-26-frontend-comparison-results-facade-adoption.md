# Frontend ComparisonResults facade adoption

## Outcome

`ComparisonResults` now consumes the shared UI facade instead of importing
`Alert`, `Card`, and `Tag` directly from `tdesign-react`:

- `D:\program_project\python_project\RAG4C\frontend\src\retrieval-quality\components\ComparisonResults.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\ui\index.tsx`

The migration intentionally keeps the result pipeline unchanged:

- empty, completed, failed, and no-hit states retain their existing copy and
  status mapping;
- the immutable generation rail, evidence alignment table, lineage strip,
  neutral note, and responsive CSS are unchanged;
- failure codes remain sanitized through `projectExperiment`;
- `SafeTracePanel` remains the existing shared `Collapse` consumer and keeps
  traces closed until explicitly opened;
- the evidence table remains named and horizontally scrollable through its
  existing semantic region.

The tests now cover native and TDesign rendering for the migrated controls,
including a TDesign trace-open path that confirms sanitized trace text rather
than the raw sensitive value.

## Verification

- ComparisonResults/UI focused suite: **7 files / 13 tests passed**
- focused TypeScript check: passed
- focused ESLint: **0 errors**
- `git diff --check`: passed
- independent sub-agent review: **PASS**, no actionable P0–P3 findings

Final repository-wide validation:

- full frontend suite: **333/333 files passed**, manifest
  `726a29af9f0bd20ddb782e6b908b8b85e9df9d161c392ec54bb75f0a7f7514f7`
- `npm run build`: passed, **7,127 modules transformed**
- `npm run lint`: **0 errors / 98 existing warnings**
- final `git diff --check`: passed

## Scope boundary

No route, API, backend, storage, authorization, or persisted-state contract
changed. `ExperimentDetailDrawer` and `RetrievalQualityCenter` still have
direct TDesign imports and remain separate follow-up slices.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-26-frontend-comparison-results-facade-adoption-design.md`
