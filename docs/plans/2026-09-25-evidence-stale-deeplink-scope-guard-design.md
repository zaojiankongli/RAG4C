# Evidence stale deep-link scope guard design (2026-09-25)

## Context

The answer evidence panel can link a stale citation into the chunk workbench.
The link must include both the document and its knowledge-base scope. The
current `EvidenceChunk` frontend type has no `dataset_id`, and cross-dataset
queries may legitimately return hits from multiple datasets. Reusing the
current workspace dataset or the query request scope would fabricate
provenance when the backend evidence item does not identify its own dataset.

The upstream Java query service owns the evidence item and is the only
component that can authoritatively attach that dataset. This repository cannot
invent that value.

## Decision

- Extend the frontend `EvidenceChunk` contract with optional `dataset_id`.
- `RetrievalTrace` only builds a stale chunk deep link when both `doc_id` and
  an explicit non-empty `dataset_id` are present on the evidence item.
- When either scope component is absent, render an explanatory non-link:
  “缺少文档或知识库归属，无法直达”.
- Do not fall back to the active workspace dataset, query request dataset, or
  local storage. This is an Adapter-style boundary guard: evidence provenance
  must come from the evidence record itself.
- Keep the cross-repository Java payload change separate; this slice does not
  claim the upstream contract is complete.

## Compatibility

- Existing evidence without `dataset_id` remains visible and searchable but
  loses only the unsafe stale deep-link action.
- Evidence carrying `dataset_id` generates the same workbench route with the
  dataset query parameter.
- QA evidence and non-stale citations are unchanged.

## Verification

- Add model/component tests for scoped links and missing document/dataset
  scope.
- Run the focused RetrievalTrace/AnswerCard evidence tests, TypeScript,
  ESLint, and the relevant frontend test suite.
- Request an independent sub-agent review after implementation.
