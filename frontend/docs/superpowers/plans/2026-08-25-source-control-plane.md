# Source Control Plane Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the authenticated Source Control Plane with strict connector CRUD, durable manual sync, cursor-based run operations, visibility-aware polling, and accurate projection-fence semantics.

**Architecture:** Keep the page as a small composition root. Put exact contracts and safe projection helpers in `src/sources-control/model`, HTTP methods in `api`, request state and persistence in `hooks`, and focused TDesign UI in `components`.

**Tech Stack:** React 18, TypeScript 5.6, TDesign React 1.18, Vitest, Testing Library, Vite, browser automation.

**Spec:** `frontend/docs/superpowers/specs/2026-08-25-source-control-plane-design.md`

## Global Constraints

- Frontend-only paths under `frontend/` and exactly one final commit.
- No Ant Design and no demo facts on production paths.
- Require authenticated tenant, dataset, and actor token before requests.
- Exact Source API paths, payloads, cursor filters, CAS fields, and 202/replayed semantics.
- Ten rows per run/item page; never expose cursor tokens.
- Never render exact credential references, arbitrary secrets, unsafe URI queries, stacks, or raw error bodies.
- No schedule controls; show the backend capability gap.
- `execution_next_attempt_at` is unavailable and must be labeled as such.
- 409 refreshes authoritative facts and never reports success.
- Poll only when route-active and document-visible; cancel on hidden/unmount/scope changes.
- TDesign-native responsive light/dark UI with focus restoration and keyboard scroll.

---

### Task 1: Models, safe projections, and durable identity

**Files:** Create `src/sources-control/model/sourceModels.ts`, `sourceProjection.ts`, tests; create `hooks/syncIntentStore.ts` and tests.

- [ ] Write failing tests for authenticated scope closure, safe connector summaries, reference truncation, known error projection, active execution classification, and generation fences.
- [ ] Run focused tests and confirm expected missing-module failures.
- [ ] Implement minimal typed contracts and projections.
- [ ] Write failing durable-intent tests for stable replay, payload/generation separation, successful clearing, and malformed-storage recovery.
- [ ] Implement the local-storage record and pass focused tests.

### Task 2: Exact Source API boundary

**Files:** Create `src/sources-control/api/sourcesApi.ts` and test.

- [ ] Write failing tests for every endpoint, auth header, status/trigger/cursor query, limit=10, CAS body, idempotency header, and retry empty body.
- [ ] Implement methods using only the shared `request` primitive and pass focused tests.

### Task 3: Source/run hooks and polling

**Files:** Create `hooks/useSourceControl.ts`, `useSourceRuns.ts`, tests.

- [ ] Write failing tests for fail-closed/offline behavior, load/mutation refresh, 409 refresh, lost-response key reuse, cursor stack, retry replay, stale fencing, active/visible polling, bounded backoff, and cancellation.
- [ ] Implement request generation fencing, abort controllers, durable sync, cursor state, and polling.
- [ ] Pass focused hook tests.

### Task 4: Source forms and source list

**Files:** Create connector form, source list/actions/sync components and tests.

- [ ] Write failing component tests for strict connector payloads, credential badge privacy, allowlist errors, CAS, confirmations, 202 queued copy, replay copy, and focus restoration.
- [ ] Implement the minimal accessible TDesign surfaces and pass tests.

### Task 5: Run history/detail/items

**Files:** Create run history/detail/items/reference components and tests.

- [ ] Write failing tests for filters, 10-row cursor navigation, separated statuses, attempts, unavailable next attempt, generations, safe IDs/URIs, current-generation retry, and replay status.
- [ ] Implement responsive tables/drawer and pass tests.

### Task 6: Page integration and responsive styling

**Files:** Replace `src/pages/KnowledgeSourcesPage.tsx`; modify `src/App.tsx`; create page/CSS tests and `sources-control.css`.

- [ ] Write failing page/route tests for authenticated scope, active prop, no projections/demo facts, capability explanations, landmarks, mobile scroll ownership, token styling, focus rings, and reduced motion.
- [ ] Implement page composition, route activity, and styles.
- [ ] Pass all Source Control focused tests.

### Task 7: Verification and delivery

- [ ] Run full Vitest with `--testTimeout=10000` and investigate failures.
- [ ] Run `npm run build` and `npm run lint`.
- [ ] Run browser light/dark checks at 375px and 1440px plus axe and keyboard-scroll checks.
- [ ] Review requirements and frontend-only diff.
- [ ] Stage only `frontend/**`, create one commit, and report SHA and changed paths.
