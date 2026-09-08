# Enterprise Retrieval Quality Center Frontend Design

**Date:** 2026-08-25
**Scope:** Frontend only
**Status:** Approved

## 1. Objective

Replace the compressed Retrieval Lab page with an isolated enterprise Retrieval Quality Center that lets authenticated KnowledgeOps operators run one to four pure-retrieval strategies, compare immutable evidence snapshots, inspect experiment history, record rank-level judgments, and review agreement metrics.

The subsystem must preserve the RAG4C Knowledge Lifeline: every result remains traceable to the dataset serving generation, strategy snapshot, result snapshot, evidence lineage, document revision, and chunk/content revision that existed when the experiment ran.

## 2. Non-goals

- Do not edit backend code, source-control code, or consistency code.
- Do not generate answers or invoke answer-generation flows.
- Do not infer an automatic winner.
- Do not invent an experiment-to-Eval mutation when no endpoint exists.
- Do not render arbitrary metadata, credential references, secrets, or raw trace payloads.
- Do not use demo records when authentication, connectivity, or APIs are unavailable.

## 3. Current and approved contracts

### 3.1 Approved synchronous run contract

`POST /api/knowledge-bases/{dataset_id}/retrieval-experiments/run`

Request:

```json
{
  "query": "string",
  "acl": ["string"],
  "variants": [
    {
      "name": "string",
      "route_target": "auto | hybrid | vector_graph_rag | full",
      "top_k": 8,
      "hybrid_search_on": true,
      "rerank_on": true,
      "graph_retrieval_on": false,
      "sentence_window_on": false,
      "source_diversity": "off | group_only | group_mmr"
    }
  ]
}
```

The request contains one through four variants and no UI-only properties. The response contains `run_id`, `dataset_serving_generation`, and `items`. Each item contains the complete persisted experiment representation plus runtime route, latency, result-count, rerank, and degraded facts. The API adapter is the only place allowed to reconcile small naming differences if the backend OpenAPI lands during implementation.

### 3.2 Existing experiment contracts

The current backend exposes:

- `GET /api/knowledge-bases/{dataset_id}/retrieval-experiments`
- `POST /api/knowledge-bases/{dataset_id}/retrieval-experiments`
- `GET /api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}`
- `POST /api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}/judgments`
- `PATCH /api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}/judgments/{judgment_id}`
- `GET /api/knowledge-bases/{dataset_id}/retrieval-experiments/{experiment_id}/agreement`

Experiment list pagination is descending keyset pagination through `before_sequence`. Supported filters include `status`, `query_hash`, `run_id`, `created_by`, `created_from`, and `created_to`. The Retrieval Quality Center uses ten records per page. The visible query filter is exact-match only: the frontend applies the backend-compatible NFKC/whitespace normalization and computes SHA-256 for `query_hash`. It must not imply substring or fuzzy query search.

Judgment creation accepts result rank, relevance label, optional document/chunk identifiers, optional score from zero through three, and a note. Judgment updates send `expected_revision` plus at least one changed mutable field. A conflict response triggers an authoritative detail and agreement refresh.

All requests send:

- `Authorization: Bearer <actor token>`
- `X-RAG4C-Tenant: <tenant id>`
- JSON content headers for mutations

## 4. Subsystem structure

The feature lives under `frontend/src/retrieval-quality/`:

```text
api/
  retrievalQualityApi.ts
model/
  retrievalQualityContracts.ts
  retrievalQualityValidation.ts
  retrievalQualityProjection.ts
hooks/
  useRetrievalQualityScope.ts
  useRetrievalRun.ts
  useExperimentHistory.ts
  useExperimentDetail.ts
  useJudgments.ts
components/
  RetrievalComposer.tsx
  StrategyCard.tsx
  ComparisonResults.tsx
  EvidenceComparisonTable.tsx
  LineageStrip.tsx
  ExperimentHistory.tsx
  ExperimentDetailDrawer.tsx
  JudgmentEditor.tsx
  AgreementPanel.tsx
  SafeTracePanel.tsx
RetrievalQualityCenter.tsx
retrieval-quality.css
```

Tests are colocated with their unit or integration surface. `frontend/src/pages/RetrievalLabPage.tsx` remains only as a route adapter so the existing App lazy import and route tests remain stable.

## 5. Authenticated scope and concurrency model

The page consumes `KnowledgeWorkspaceContext`, connection state, and the stored KnowledgeOps actor token. A scope is valid only when tenant ID, dataset ID, and actor token are all non-empty. The token's signed payload may be decoded locally to display the actor subject and identify that actor's editable judgment, but server authorization remains authoritative.

The stable request key is:

```text
<tenant id> NUL <dataset id> NUL <actor token>
```

Every hook follows these rules:

1. Resolve and validate scope synchronously.
2. Project no authority facts when scope is invalid or offline.
3. Clear old-scope data synchronously when the key changes.
4. Abort active requests on scope change, replacement, unmount, or close.
5. Capture the scope key and a monotonically increasing generation before each request.
6. Ignore completion unless the controller is live and both key and generation still match.
7. Never replace an error or offline state with demo data.

The synchronous run POST is the only active-run request. History polling may run at a bounded interval while that POST remains pending and only for the same scope. Polling stops immediately when the POST settles, aborts, the scope changes, or the page unmounts. There is no idle background polling.

## 6. Composer

The composer includes:

- Query textarea.
- Optional ACL values represented as trimmed, deduplicated strings.
- Sample-question controls that populate but never auto-run.
- One through four strategy cards.
- Duplicate, add, and remove actions with deterministic focus restoration.
- Primary action labeled `运行纯检索对比（不生成答案）`.

Only approved knobs are editable:

- `name`
- `route_target`
- `top_k`
- `hybrid_search_on`
- `rerank_on`
- `graph_retrieval_on`
- `sentence_window_on`
- `source_diversity`

Validation applies before network work:

- Query run input: trimmed, one through 20,000 characters, preserving internal whitespace exactly as the run API does. Exact history filtering separately applies the repository's NFKC and collapsed-whitespace query normalization before SHA-256.
- Variants: one through four.
- Name: trimmed, one through 64 characters, and unique under case-folded comparison.
- Route target: `auto`, `hybrid`, `vector_graph_rag`, or `full`.
- Top K: integer from 1 through 50.
- ACL: at most 100 trimmed entries; every entry is one through 256 characters and excludes quotes, apostrophes, backslashes, and control characters.
- Boolean fields: actual booleans.
- Source diversity: `off`, `group_only`, or `group_mmr`.

The first invalid field receives focus after submission. No request is issued for invalid input.

## 7. Results comparison

The desktop comparison uses aligned variant columns sharing a rank axis. Each variant summary presents:

- Strategy name.
- Completed or failed status.
- Actual route.
- Degraded state.
- Whether reranking took effect.
- Latency.
- Result count.

Failed variants stay in the comparison and display the failure truthfully. Completed variants with zero results display a no-hit state and never borrow evidence from another variant.

Evidence rows render only safe projected fields:

- rank
- score
- branch, when supplied by the snapshot
- document ID
- chunk ID
- document revision
- content or chunk revision
- abbreviated content hash
- sanitized excerpt, when supplied
- lineage citation facts

Unknown snapshot fields are ignored. Missing fields display a neutral em dash or an explicit unavailable message.

There is no automatic winner. When at least two variants contain comparable operator judgments, the UI may show a neutral comparison of mean scores and relevance-label counts. It must not label a variant as the winner without a real explicit operator/backend decision.

## 8. Knowledge Lifeline and immutable snapshots

A serving-generation rail is the page's signature visual element. It ties the run-level `dataset_serving_generation` to each experiment's strategy snapshot, result snapshot, and evidence lineage snapshot.

The explanation states:

- The serving generation identifies the dataset projection queried by this run.
- Experiment snapshots are immutable historical records.
- Later document edits or index generations do not rewrite past results.
- Revision and hash facts allow an operator to trace evidence back through the Knowledge Lifeline.

The visual uses existing RAG4C primary blue, graph violet, revision borders, and monospace utility labels. It works in light and dark themes without introducing another token system.

## 9. Safe rendering

The frontend never serializes arbitrary snapshots into the DOM. Projection functions read allow-listed keys and return typed display records.

Excerpts and traces:

- Render as text, never HTML.
- Collapse whitespace.
- Remove control characters.
- Redact secret/vault credential URLs and common credential-bearing URL forms.
- Redact values adjacent to secret-like labels.
- Apply bounded length limits.
- Keep traces collapsed by default.
- Do not expose raw metadata JSON.

Content hashes are shown only as abbreviated identifiers. Credential references and URLs are never made clickable.

## 10. History and detail drawer

History provides:

- Ten experiments per page.
- Server-side status and run-ID filters.
- Exact normalized query filtering through `query_hash`.
- A cursor stack for previous-page navigation.
- Next-page navigation using `next_before_sequence`.
- Manual refresh.
- A detail drawer loaded through the experiment detail endpoint.

Changing filters clears the cursor stack and returns to the newest page. The drawer stores its opener and restores focus when closed. Detail requests are aborted if the selected experiment, scope, or drawer state changes.

## 11. Judgments and agreement

Each evidence rank includes an operator judgment editor:

- relevance: relevant, partial, or irrelevant
- score: zero through three or unset
- note
- owner
- revision

If the current actor has no judgment for the rank, save uses POST. If an owned judgment exists, save uses PATCH with its current `expected_revision` and only changed fields. Other operators' judgments are visible but not editable.

On HTTP 409:

1. Keep the user's unsaved editor values.
2. Refetch experiment detail.
3. Refetch agreement.
4. Show a conflict notice with refreshed owner/revision facts.
5. Require the operator to review and save again against the new revision.

Agreement shows judged results, judgment count, multi-judged results, unanimous and conflicting counts, exact agreement rate, relevance-label counts, and mean score.

## 12. Responsive and accessibility behavior

At 1440px, the composer and comparison use available horizontal space and evidence remains aligned across variants. At 375px, Composer, Results, and History become native TDesign tabs; strategy cards stack and result variants use a keyboard-operable variant tab switcher.

Accessibility requirements:

- Semantic headings and regions.
- Semantic evidence/history tables with captions and column headers.
- Labeled horizontal-scroll regions with keyboard focus.
- Visible focus indicators in both themes.
- Status and error live regions.
- Focus restoration after drawers, variant removal, and failed validation.
- Reduced-motion overrides for feature transitions.
- No color-only status communication.

## 13. Testing strategy

Production behavior is implemented through red-green-refactor cycles. Tests cover:

1. API paths, authentication headers, request bodies, query parameters, and abort signals.
2. Run-contract response adaptation without invented authority fields.
3. Missing scope, offline behavior, aborts, scope races, generation fences, and bounded polling.
4. Query and strategy validation, including one-to-four variants and duplicate normalized names.
5. Neutral comparison behavior and absence of winner language.
6. Failed and no-hit experiments.
7. Safe evidence, lineage, excerpt, hash, and trace projection.
8. History cursor next/previous transitions and filter resets.
9. Judgment create, patch, owner handling, CAS conflicts, draft preservation, and refresh.
10. Agreement rendering.
11. Responsive tab/stack behavior, semantic tables, focus restoration, and reduced motion.

Verification commands include focused and full Vitest runs with a ten-second test timeout, ESLint, TypeScript/Vite build, Playwright at 375px and 1440px in light and dark themes, and axe audits for the required states.

## 14. Delivery constraints

- All committed paths remain under `frontend/`.
- No backend, source-control, or consistency source is edited.
- The design specification is committed before implementation.
- Implementation receives its own frontend-only commit.
- Final reporting includes commit SHA or SHAs, changed paths, tests, build/lint results, Playwright screenshots, and axe results.

