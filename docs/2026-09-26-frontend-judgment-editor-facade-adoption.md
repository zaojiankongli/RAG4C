# Frontend JudgmentEditor facade adoption

## Outcome

`JudgmentEditor` now consumes the shared UI facade instead of importing
controls directly from `tdesign-react`:

- `D:\program_project\python_project\RAG4C\frontend\src\retrieval-quality\components\JudgmentEditor.tsx`
- `D:\program_project\python_project\RAG4C\frontend\src\ui\index.tsx`

The migration keeps the existing judgment mutation contract and user-visible
behavior:

- relevance radio values remain `relevant`, `partial`, and `irrelevant`;
- clearing the score still produces `null`, never `NaN`;
- the note field still supports the 20,000-character limit and 2–5 row
  autosizing;
- conflict copy, owner/revision tags, read-only judgments, and save behavior
  remain unchanged;
- the conflict alert has one semantic `role="alert"` instead of a duplicate
  wrapper role.

The shared facade changes are intentionally limited to compatibility mapping:

- `Alert` accepts both the historical `theme` alias and `title`/`message`
  content, while preserving role, ARIA/data attributes, id, and style;
- `Input.TextArea` maps `maxLength` and `autoSize` to both native and TDesign
  renderers;
- facade text-area changes use an event-shaped `{ target: { value } }` payload
  consistently across renderers.

## Verification

- JudgmentEditor/UI focused suite: **7 files / 25 tests passed**
- focused TypeScript check: passed
- focused ESLint: **0 errors**
- `git diff --check`: passed
- independent sub-agent review: **PASS**, no P0–P3 findings

Final repository-wide validation:

- full frontend suite: **327/327 files passed**, manifest
  `42399be7a00d3576808d89e240230cf0d67b450de5dc4982b17ce246eed695b2`
- `npm run build`: passed, **7,127 modules transformed**
- `npm run lint`: **0 errors / 98 existing warnings**
- final `git diff --check`: passed

## Scope boundary

No route, API, backend, storage, authorization, or persisted-state contract
changed. `ExperimentHistory` and other retrieval-quality direct TDesign
consumers remain separate follow-up slices.

Design:
`D:\program_project\python_project\RAG4C\docs\plans\2026-09-26-frontend-judgment-editor-facade-adoption-design.md`
