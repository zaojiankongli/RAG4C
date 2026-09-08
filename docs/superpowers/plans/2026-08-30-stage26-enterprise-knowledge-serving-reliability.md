# Stage 26 Enterprise Knowledge Serving & Reliability Implementation Plan

Date: 2026-08-30  
Status: Self-approved under standing user authorization  
Design: `docs/superpowers/specs/2026-08-30-enterprise-knowledge-serving-reliability-design.md`  
Revision: `0036_enterprise_knowledge_serving_reliability`

## Safety

- No production migration, snapshot observation, source mutation, index mutation, Release mutation, query execution or external action.
- Preserve the cumulative worktree and protected Retrieval Quality paths.
- Browser acceptance is loopback-only with fixture adapters.

### Task 1 — Migration / ORM / Catalog / Readiness

- [ ] Create exactly six Tenant-scoped Serving Reliability tables.
- [ ] Add Tenant-leading FKs, current policy/snapshot pointers and exact indexes/checks.
- [ ] Add immutable Policy/Snapshot/Stage/Evidence/Event guards and Event predecessor validation.
- [ ] SQLite online upgrade/downgrade tests; MySQL/PostgreSQL offline DDL; unknown dialect fail closed.
- [ ] Add `enterprise_knowledge_serving_reliability` capability while preserving Stage25 parents.

### Task 2 — Pure serving authority

- [ ] Canonical Policy, Stage Fact, Evidence Link, Snapshot and Event digests.
- [ ] Exact five-stage ordering and derived overall state.
- [ ] Bounded safe values and internal route projection.
- [ ] Reject arbitrary URL, SQL, query, payload, prompt, token, credential and document/answer bodies.

### Task 3 — Service / adapters

- [ ] Explicit read adapter registry for Source, Parse, Chunk, Index and Serve.
- [ ] Profile CRUD, immutable policy revision, activation and strict reads.
- [ ] Internal replay-safe snapshot recording against temporary fixtures only.
- [ ] Tenant isolation, pointer fences, idempotency and concurrency tests.
- [ ] No source-domain mutation or public observe/execute endpoint.

### Task 4 — Strict API / preflight

- [ ] Strict Pydantic requests and minimal canonical wire DTOs.
- [ ] Read/mutation engine separation and approved routes only.
- [ ] Zero-write preview and no generic record/reconcile/execute surface.
- [ ] Read-only six-table preflight and operations runbook.

### Task 5 — Frontend model/API/hook

- [ ] Strict Profile/Policy/Snapshot/Stage/Evidence/Event/Summary models.
- [ ] AbortSignal, Tenant/Account/Dataset generation fences and lazy detail/history.
- [ ] Serialized mutations with retained Idempotency-Key and no stale commits.
- [ ] Exact ready/degraded/blocked/partial/unavailable/empty/read-only states.

### Task 6 — TDesign Serving Reliability Center

- [ ] Governance Evidence Strip.
- [ ] SOURCE -> PARSE -> CHUNK -> INDEX -> SERVE rail.
- [ ] Dense desktop table and 375/280 priority cards.
- [ ] Single detail Drawer with Overview/Evidence/Pipeline/Policy/History.
- [ ] Policy Dialog and zero-write Preview panel.
- [ ] Keyboard/Escape/focus return and dark/light support.

### Task 7 — Resource Shell integration / review / Playwright

- [ ] Add `serving` to Knowledge Base Resource Shell direct/hash routing.
- [ ] Safe handoff to Sources/Documents/Tasks/Releases/Quality/Retrieval Debug only.
- [ ] Independent security review and all Critical/Important fixes.
- [ ] Backend, cumulative readiness/integration, full frontend runner, build and Alembic head.
- [ ] Playwright 12-matrix + bounded scenarios + 28+ fresh artifacts + standalone gate.

This plan is self-approved; no user approval checkpoint is required.
