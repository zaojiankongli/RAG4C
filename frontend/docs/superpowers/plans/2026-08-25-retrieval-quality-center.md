# Retrieval Quality Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an authenticated frontend-only center for pure-retrieval comparison, immutable experiment history, CAS judgments, and agreement.

**Architecture:** Keep `RetrievalLabPage` as a thin route adapter and move exact contracts, safe projections, scope-fenced hooks, native TDesign components, and CSS into `frontend/src/retrieval-quality/`. Every authority request is authenticated, abortable, generation-fenced, and never falls back to demo data.

**Tech Stack:** React 18, TypeScript 5.6, TDesign React 1.18, Vitest 3, Testing Library, Vite 6, Playwright Core, axe-core.

**Spec:** `frontend/docs/superpowers/specs/2026-08-25-retrieval-quality-center-design.md`

## Global Constraints

- Commit only `frontend/` paths; leave concurrent backend changes untouched.
- Use the approved `/retrieval-experiments/run` body and exact current OpenAPI names for existing APIs.
- Send bearer and `X-RAG4C-Tenant` headers on every call.
- Use no demo data, automatic winner, raw metadata, secrets, credential URLs, or unbounded traces.
- History uses keyset pagination with `limit=10`.
- Test first and observe RED before each production change.
- Final gates: focused/full Vitest with 10s timeout, lint, build, Playwright 375/1440 light/dark, and axe.

## File Map

- `model/contracts.ts`: backend DTOs and feature view types.
- `model/validation.ts`: query, ACL, and strategy validation.
- `model/projection.ts`: allow-listed evidence, lineage, hash, excerpt, trace, status projections.
- `api/retrievalQualityApi.ts`: exact authenticated HTTP calls.
- `hooks/`: scope-safe run, history, detail, and judgment state.
- `components/`: composer, comparison, lifeline, history, drawer, judgments, agreement.
- `RetrievalQualityCenter.tsx` and `retrieval-quality.css`: page composition and responsive visual system.
- `pages/RetrievalLabPage.tsx`: route adapter only.

---

### Task 1: Contracts, validation, and safe projection

**Files:** Create `frontend/src/retrieval-quality/model/{contracts,validation,projection}.ts` and matching tests.

**Interfaces:** Produce `RetrievalScope`, `RetrievalVariant`, `RunRetrievalRequest`, `Experiment`, `ExperimentDetail`, `RunResponse`, `Judgment`, `Agreement`, `EvidenceView`, `VariantView`; produce `validateComposer`, `sanitizeDisplayText`, `abbreviateHash`, and `projectExperiment`.

- [ ] Write failing validation tests for NFKC/collapsed query normalization, trimmed query length 1..20000, repository-normalized query hashing, 1..4 variants, 1..64 trimmed case-folded unique names, integer `top_k` 1..50, exact enums/booleans, and at most 100 safe trimmed ACL entries of 1..256 characters.

```ts
expect(validateComposer({ query: "  报销\n 流程 ", acl: [" staff ", "staff"], variants: [variant({name:" A "}), variant({name:"a"})] }).ok).toBe(false);
expect(validateComposer({ query: "Q", acl: [], variants: [] }).ok).toBe(false);
```

- [ ] Run `npm test -- src/retrieval-quality/model/validation.test.ts --testTimeout=10000`; expect missing-module RED.
- [ ] Implement minimal discriminated validation result containing sanitized request data only on success.
- [ ] Re-run validation test; expect PASS.
- [ ] Write failing projection tests proving secret/credential URL redaction, hash abbreviation, whitelist-only output, failed state, no-hit state, rank/branch/revision/excerpt/lineage mapping, and bounded collapsed trace strings.

```ts
expect(JSON.stringify(projectExperiment(experimentWithSecret))).not.toContain("api_token");
expect(projectExperiment(noHitExperiment).state).toBe("no-hit");
expect(projectExperiment(failedExperiment).state).toBe("failed");
```

- [ ] Run `npm test -- src/retrieval-quality/model/projection.test.ts --testTimeout=10000`; expect RED.
- [ ] Implement allow-listed projection without returning raw snapshots.
- [ ] Run `npm test -- src/retrieval-quality/model --testTimeout=10000`; expect PASS.
- [ ] Commit explicit model paths with `feat(frontend): model retrieval quality contracts`.

### Task 2: Exact authenticated API adapter

**Files:** Create `frontend/src/retrieval-quality/api/retrievalQualityApi.ts` and `.test.ts`.

**Interfaces:** Produce `runRetrievalComparison`, `fetchExperiments`, `fetchExperiment`, `createJudgment`, `patchJudgment`, `fetchAgreement`, and `hashNormalizedQuery`.

- [ ] Reinspect `server` and `tests` for a newly landed run endpoint. Copy exact names if present; otherwise use the approved contract verbatim. Do not edit backend or accept undocumented aliases.
- [ ] Write failing fetch tests for encoded paths, bearer/tenant headers, abort signal, exact POST run body, `limit=10`, supported `status/run_id/query_hash/before_sequence`, judgment POST, CAS PATCH, detail GET, and agreement GET.

```ts
expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/dataset%2Fa/retrieval-experiments/run"), expect.objectContaining({method:"POST", signal}));
expect(JSON.parse(init.body as string)).toEqual({query:"Q",acl:[],variants:[approvedVariant]});
```

- [ ] Run `npm test -- src/retrieval-quality/api/retrievalQualityApi.test.ts --testTimeout=10000`; expect RED.
- [ ] Implement using `request`/`ApiError`; hash exact normalized queries with Web Crypto SHA-256; serialize only supported filters.
- [ ] Re-run API test; expect PASS.
- [ ] Commit explicit API/model paths with `feat(frontend): add retrieval quality API client`.

### Task 3: Scope-safe run and history hooks

**Files:** Create `hooks/scope.ts`, `useRetrievalRun.ts`, `useExperimentHistory.ts`, and tests.

**Interfaces:** Produce `requireRetrievalScope`, `decodeActorSubject`, `retrievalScopeKey`; run hook returns `{status,error,response,run,reset,pending}`; history hook returns items, filters, cursors, refresh/next/previous, and selection state.

- [ ] Write failing tests for missing signed scope, synchronous clearing on scope change, aborting replaced runs, ignoring late scope-A completion, and pending state ending in `finally`.
- [ ] Write a fake-timer test proving history polling happens only while the run POST is pending and stops after settle/scope change/unmount.
- [ ] Run `npm test -- src/retrieval-quality/hooks/useRetrievalRun.test.tsx --testTimeout=10000`; expect RED.
- [ ] Implement key+generation refs, one controller per request, synchronous visible-key checks, and pending-only poll generation.
- [ ] Re-run run tests; expect PASS.
- [ ] Write failing history tests for ten-row newest page, `next_before_sequence`, local previous cursor stack, filter reset, exact query hashing, stale completion fences, and no idle polling.
- [ ] Run `npm test -- src/retrieval-quality/hooks/useExperimentHistory.test.tsx --testTimeout=10000`; expect RED.
- [ ] Implement keyset/history hook with abortable loads and bounded pending polling.
- [ ] Run `npm test -- src/retrieval-quality/hooks --testTimeout=10000`; expect PASS.
- [ ] Commit explicit hook paths with `feat(frontend): fence retrieval quality requests`.

### Task 4: Detail, judgment CAS, and agreement hooks

**Files:** Create `hooks/useExperimentDetail.ts`, `hooks/useJudgmentMutation.ts`, and tests.

**Interfaces:** Detail hook returns `{detail,agreement,status,error,refresh}`. Judgment hook returns actor-owned drafts, saving/conflict ranks, and `save(rank,draft): Promise<boolean>`.

- [ ] Write failing detail tests proving experiment A cannot overwrite B and that detail/agreement requests begin in parallel and abort on close/scope change.
- [ ] Run `npm test -- src/retrieval-quality/hooks/useExperimentDetail.test.tsx --testTimeout=10000`; expect RED.
- [ ] Implement parallel `Promise.allSettled` authority loads with selection and scope fences.
- [ ] Re-run detail test; expect PASS.
- [ ] Write failing judgment tests: POST when current actor owns no rank judgment; PATCH only actor-owned changed fields with `expected_revision`; never edit another owner; on HTTP 409 preserve the draft, mark conflict, and await detail+agreement refresh.

```ts
expect(api.patchJudgment).toHaveBeenCalledWith(scope,"exp-1","j-owned",expect.objectContaining({expected_revision:4,note:"updated"}),expect.anything());
expect(result.current.draftFor(1)).toEqual(draftAfterConflict);
```

- [ ] Run `npm test -- src/retrieval-quality/hooks/useJudgmentMutation.test.tsx --testTimeout=10000`; expect RED.
- [ ] Implement owner-aware create/patch and conflict refresh without false success.
- [ ] Run both Task 4 tests; expect PASS.
- [ ] Commit explicit hook paths with `feat(frontend): add retrieval judgment authority`.

### Task 5: Composer and neutral comparison UI

**Files:** Create `components/StrategyCard.tsx`, `RetrievalComposer.tsx`, `LineageStrip.tsx`, `SafeTracePanel.tsx`, `EvidenceComparisonTable.tsx`, `ComparisonResults.tsx`, and tests.

**Interfaces:** Components consume validated model values and callbacks only; they own no fetch calls.

- [ ] Write failing composer tests for add/duplicate/remove, one-card minimum, four-card cap, sample questions, exact request output, duplicate-name blocking, first-invalid-field focus, and the exact action label `运行纯检索对比（不生成答案）`.
- [ ] Run `npm test -- src/retrieval-quality/components/RetrievalComposer.test.tsx --testTimeout=10000`; expect RED.
- [ ] Implement native TDesign Card/Textarea/Input/InputNumber/Select/Switch/Button/Alert controls. Keep UI card IDs out of payload and restore focus after removal.
- [ ] Re-run composer test; expect PASS.
- [ ] Write failing comparison tests for neutral language, no `winner`/`获胜`, serving generation, aligned semantic evidence table, route/degraded/rerank/latency/count, score/branch/revisions/hash/excerpt/lineage, failed variants, no-hit variants, and collapsed sanitized traces.

```tsx
expect(screen.getByRole("table", {name:/证据排名对比/})).toBeTruthy();
expect(document.body.textContent).not.toContain("获胜");
expect(screen.getByText("没有召回结果")).toBeTruthy();
```

- [ ] Run `npm test -- src/retrieval-quality/components/ComparisonResults.test.tsx --testTimeout=10000`; expect RED.
- [ ] Implement the serving-generation rail and rank union across all variant columns using projected data only.
- [ ] Run `npm test -- src/retrieval-quality/components --testTimeout=10000`; expect PASS.
- [ ] Commit explicit component paths with `feat(frontend): add retrieval comparison workspace`.

### Task 6: History, detail drawer, judgments, and agreement UI

**Files:** Create `components/ExperimentHistory.tsx`, `ExperimentDetailDrawer.tsx`, `JudgmentEditor.tsx`, `AgreementPanel.tsx`, and tests.

**Interfaces:** History receives server items/filters/cursors and callbacks. Drawer receives authoritative detail/agreement plus judgment mutation callbacks and opener reference.

- [ ] Write failing history tests for supported filters only, exact-query copy, semantic caption `检索实验历史，每页十条`, next/previous controls, failed/no-hit rows, and detail opener labeling.
- [ ] Run `npm test -- src/retrieval-quality/components/ExperimentHistory.test.tsx --testTimeout=10000`; expect RED.
- [ ] Implement native TDesign filters around a semantic keyboard-scrollable table; never render raw snapshots.
- [ ] Re-run history test; expect PASS.
- [ ] Write failing drawer tests for immutable snapshot explanation, owner/revision text, relevant/partial/irrelevant, score 0..3, note, conflict alert, agreement counts/rate/mean, read-only foreign judgments, Eval capability note without action, and focus restoration to opener.
- [ ] Run `npm test -- src/retrieval-quality/components/ExperimentDetailDrawer.test.tsx --testTimeout=10000`; expect RED.
- [ ] Implement native TDesign Drawer/Radio/InputNumber/Textarea/Button/Tag/Alert and focus restoration in close handling.
- [ ] Run both Task 6 tests; expect PASS.
- [ ] Commit explicit component paths with `feat(frontend): add retrieval history judgments`.

### Task 7: Page integration, responsive CSS, and accessibility contracts

**Files:** Create `RetrievalQualityCenter.tsx`, `.test.tsx`, `retrieval-quality.css`, `retrieval-quality.styles.test.ts`; replace `frontend/src/pages/RetrievalLabPage.tsx` with a thin adapter.

**Interfaces:** Consume KnowledgeWorkspace scope, actor token, ConnectionContext, and Tasks 1-6. Produce the routed page.

- [ ] Write failing page tests for missing actor scope (zero API calls), truthful offline state (no demo text), authenticated ready composition, pending run/history integration, and pure-retrieval action label.
- [ ] Run `npm test -- src/retrieval-quality/RetrievalQualityCenter.test.tsx --testTimeout=10000`; expect RED.
- [ ] Implement page composition with PageTopbar, authority/generation explanation, composer, results, history, detail, and mobile TDesign Tabs.
- [ ] Re-run page test; expect PASS.
- [ ] Write failing CSS/DOM contract tests requiring `@media (max-width: 600px)`, desktop variant grid using `--rq-variant-count`, `:focus-visible`, dark-theme rules, semantic scroll owners, and `@media (prefers-reduced-motion: reduce)`.

```ts
expect(css).toContain("@media (max-width: 600px)");
expect(css).toContain("--rq-variant-count");
expect(css).toContain(":focus-visible");
expect(css).toContain("prefers-reduced-motion: reduce");
```

- [ ] Run style/page tests; expect RED until styles exist.
- [ ] Implement RAG4C blue/violet serving-generation rail, 1440 aligned comparison, 375 stack/tabs, light/dark, focus, and reduced motion without decorative gradients.
- [ ] Run `npm test -- src/retrieval-quality --testTimeout=10000`; expect PASS.
- [ ] Commit explicit feature and route paths with `feat(frontend): integrate retrieval quality center`.

### Task 8: Full verification and browser quality gate

**Files:** Retain screenshots/axe JSON under `frontend/.shots/` only if useful; add regression tests before fixes.

- [ ] Run focused tests: `npm test -- src/retrieval-quality --testTimeout=10000`; require zero failures/unhandled warnings.
- [ ] Run full tests: `npm test -- --testTimeout=10000`; require zero failures.
- [ ] Run `npm run lint`; require exit 0.
- [ ] Run `npm run build`; require exit 0.
- [ ] Use the Playwright skill with intercepted contract-shaped APIs and authenticated localStorage; do not depend on demo data or mutate backend.
- [ ] At 375px light and dark, verify Composer/Results/History tabs, two-variant run, failed/no-hit pass, drawer focus restoration, screenshot, and axe with zero serious/critical violations.
- [ ] At 1440px light and dark, verify aligned comparison, serving-generation rail, immutable explanation, route/degraded/rerank/latency/count, evidence revisions/hash/lineage/traces, no winner language, screenshot, and axe with zero serious/critical violations.
- [ ] Audit `git log --oneline --name-only 1f71580..HEAD` and confirm every committed path begins with `frontend/`. Do not stage or alter concurrent backend files.
- [ ] For any defect, add a focused failing test, implement the fix, and rerun affected plus full gates.
- [ ] Commit only retained frontend artifacts/fixes with `test(frontend): verify retrieval quality center`; skip if unnecessary.

