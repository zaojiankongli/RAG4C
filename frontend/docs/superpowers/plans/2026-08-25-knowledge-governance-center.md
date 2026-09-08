# Knowledge Governance Center Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the lazy, authenticated Knowledge Governance Center with exact typed API contracts, revision-fenced mutations, truthful states, and responsive accessible native-TDesign UI.

**Architecture:** Keep `KnowledgeGovernancePage` as a composition root. Put contracts and safe projections in `src/governance/model`, HTTP boundaries in `src/governance/api`, request state in `src/governance/hooks`, and focused surfaces in `src/governance/components`.

**Tech Stack:** React 18, TypeScript 5.6, TDesign React 1.18, Vitest, Testing Library, Vite.

**Spec:** `frontend/docs/superpowers/specs/2026-08-25-knowledge-governance-center-design.md`

## Global Constraints

- Frontend-only changes and commit paths under `frontend/`.
- No Ant Design and no compatibility facade expansion.
- No demo facts in offline/error states.
- Exact existing backend paths, payloads, filters, and revision fields.
- No optimistic mutation success; 409 must refresh authoritative facts.
- Never render secrets; credential references are badges only.
- Ten QA rows per UI page and a keyboard-owned horizontal table at 375px.

---

### Task 1: Route and menu

**Files:**
- Modify: `frontend/src/run/appRoute.ts`
- Modify: `frontend/src/run/appRoute.test.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.initialRoute.test.tsx`
- Create: `frontend/src/pages/KnowledgeGovernancePage.tsx`

**Interfaces:** Produces the `governance` `PageKey`, lazy page registration, and “知识治理” menu entry.

- [ ] Add failing route/menu/lazy-mount tests.
- [ ] Run focused tests and confirm failure because `governance` is unknown.
- [ ] Add the minimal route, menu, lazy import, and placeholder composition root.
- [ ] Run focused tests and confirm pass.

### Task 2: Exact models, safe projections, and API client

**Files:**
- Create: `frontend/src/governance/model/governanceModel.ts`
- Create: `frontend/src/governance/model/governanceModel.test.ts`
- Create: `frontend/src/governance/api/governanceApi.ts`
- Create: `frontend/src/governance/api/governanceApi.test.ts`

**Interfaces:** Produces typed dataset, QA, alternative, version, patch/create payloads; `requireGovernanceScope`; safe policy summaries; sanitized error projection; and exact HTTP functions.

- [ ] Add failing model tests for scope closure, status projection, secret omission, credential-reference badges, and safe ApiError messages.
- [ ] Run model tests and confirm missing-module failure.
- [ ] Implement minimal models/projections and pass tests.
- [ ] Add failing API tests covering every method, path, query, auth header, filter, body, and 204 response.
- [ ] Run API tests and confirm missing functions.
- [ ] Implement exact API functions using `request` and pass tests.

### Task 3: Governance request hooks

**Files:**
- Create: `frontend/src/governance/hooks/useDatasetGovernance.ts`
- Create: `frontend/src/governance/hooks/useDatasetGovernance.test.tsx`
- Create: `frontend/src/governance/hooks/useQAGovernance.ts`
- Create: `frontend/src/governance/hooks/useQAGovernance.test.tsx`
- Create: `frontend/src/governance/hooks/useDocumentVersions.ts`
- Create: `frontend/src/governance/hooks/useDocumentVersions.test.tsx`

**Interfaces:** Produces authoritative profile/QA/version state, filters, ten-row pagination, mutation methods, loading/error/conflict state, refresh, and request-generation fencing.

- [ ] Add failing hook tests for fail-closed scope, offline truth, initial loads, filter/refetch behavior, ten-row pagination, successful mutation refetch, 409 conflict refresh, and stale-request fencing.
- [ ] Run each focused test and confirm the expected failure.
- [ ] Implement the smallest hook behavior needed for each passing slice.
- [ ] Refactor shared request/error helpers only after green.

### Task 4: Profile and authority surfaces

**Files:**
- Create: `frontend/src/governance/components/GovernanceAuthorityBanner.tsx`
- Create: `frontend/src/governance/components/DatasetProfilePanel.tsx`
- Create: `frontend/src/governance/components/DatasetProfileEditor.tsx`
- Create: `frontend/src/governance/components/DatasetLifecycleActions.tsx`
- Create: `frontend/src/governance/components/DatasetProfilePanel.test.tsx`

**Interfaces:** Consumes the dataset hook and renders safe profile facts, edit CAS, confirmations, and stale-revision refresh.

- [ ] Add failing component tests for safe summaries, accessible controls, expected revision, confirmation, success refetch, and conflict state.
- [ ] Run focused tests and confirm failure.
- [ ] Implement native-TDesign components and pass tests.

### Task 5: QA lifecycle surfaces

**Files:**
- Create: `frontend/src/governance/components/QAGovernancePanel.tsx`
- Create: `frontend/src/governance/components/QAEditorDialog.tsx`
- Create: `frontend/src/governance/components/QAAlternativesPanel.tsx`
- Create: `frontend/src/governance/components/QAGovernancePanel.test.tsx`

**Interfaces:** Consumes the QA hook and renders filters, ten-row pages, separate statuses, CRUD/review/lifecycle actions, and alternatives CAS.

- [ ] Add failing tests for filters, pagination, create/edit payloads, approve/reject, expire/restore confirmations, alternatives add/delete, and accurate combined status display.
- [ ] Run focused tests and confirm failure.
- [ ] Implement native-TDesign components and pass tests.

### Task 6: Document-version inspector and page integration

**Files:**
- Create: `frontend/src/governance/components/DocumentVersionInspector.tsx`
- Create: `frontend/src/governance/components/DocumentVersionInspector.test.tsx`
- Modify: `frontend/src/pages/KnowledgeGovernancePage.tsx`
- Create: `frontend/src/pages/KnowledgeGovernancePage.test.tsx`

**Interfaces:** Produces explicit document inspection, immutable timeline, advanced exact create form, and the complete page composition.

- [ ] Add failing inspector/page tests for explicit document ID, ordered immutable revisions, exact create payload, offline/scope/error/empty states, and section landmarks.
- [ ] Run focused tests and confirm failure.
- [ ] Implement the inspector and page integration and pass tests.

### Task 7: Responsive styling and verification

**Files:**
- Create: `frontend/src/governance/governance.css`
- Create: `frontend/src/governance/governance.css.test.ts`
- Modify: `frontend/src/pages/KnowledgeGovernancePage.tsx`

**Interfaces:** Produces 375px horizontal ownership, responsive modal sizing, token-only light/dark styles, focus visibility, and reduced-motion behavior.

- [ ] Add failing CSS behavior tests for table ownership, narrow modal layout, focus ring, tokens, and reduced motion.
- [ ] Run focused CSS tests and confirm failure.
- [ ] Implement styles and pass tests.
- [ ] Run all focused governance tests, full Vitest, build, and lint.
- [ ] Run browser checks at 375px and 1440px in light/dark plus axe.
- [ ] Review the frontend-only diff, stage only `frontend/**`, commit, and report SHA and paths.
