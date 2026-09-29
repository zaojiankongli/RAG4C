# Evidence stale deep-link scope guard handoff (2026-09-25)

## What changed

- Extended the frontend `EvidenceChunk` type with optional `dataset_id`.
- Updated `RetrievalTrace` so stale citation links are created only when the
  evidence item itself contains both a non-empty document id and a non-empty
  dataset id.
- When either scope component is absent, the UI keeps the stale verification
  result visible but renders an explicit non-link:
  “缺少文档或知识库归属，无法直达”.
- The frontend never falls back to the active workspace dataset, query
  request dataset, or local storage. This prevents a cross-dataset evidence
  item from linking into the wrong knowledge base.

## Contract boundary

The upstream Java query service still needs to authoritatively include
`dataset_id` on each evidence item. This slice deliberately does not stamp the
Python request scope onto evidence, because an empty query dataset means
cross-dataset retrieval and would make that value false provenance.

Evidence with a valid `dataset_id` now produces a workbench URL containing the
dataset query parameter. Existing QA evidence, non-stale citations, and
evidence rendering remain unchanged.

## Verification

- Focused frontend evidence tests: **15 passed** across model, panel, and
  `RetrievalTrace`.
- Frontend full gate: **292/292 test files passed**.
- `frontend` TypeScript/build passed; production build transformed **7,123
  modules**.
- ESLint passed with the existing baseline warnings and **0 errors**.
- Independent sub-agent review: **PASS**, no actionable findings.

## Boundaries

- No backend or cross-repository Java payload change was made.
- This slice closes the frontend fail-closed guard only; the cross-repository
  evidence contract remains an explicit follow-up.

Design record:
`docs/plans/2026-09-25-evidence-stale-deeplink-scope-guard-design.md`.
