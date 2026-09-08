# Parse Intervention Fix Round 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace insecure legacy chunk intervention with authenticated dataset-scoped SQL authority APIs and harden every reviewed frontend behavior.

**Architecture:** Add a dedicated authenticated Knowledge Chunks router and scoped ChunkCatalog page/detail methods. Migrate the parse workspace to exact authenticated contracts, remove legacy consumers, and redesign controller state around server paging and independent orphan drafts.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic v2, pytest, React 18, TypeScript, TDesign React, Vitest, Playwright, axe-core.

**Spec:** `frontend/docs/superpowers/specs/2026-08-25-parse-intervention-fix-round-1-design.md`

## Global constraints

- P0 first: no unauthenticated legacy chunk read/write route may remain usable.
- `off` authority mode returns structured 409 and never offers projection-dependent editing.
- PATCH uses `KNOWLEDGE_WRITE`; DELETE uses stricter `KNOWLEDGE_DELETE`.
- Search query max 256; offset 0+; limit 1..200.
- Raw chunk metadata and unsanitized URI-like values never leave the backend.
- Backend and frontend commits remain separately scoped.
- Every behavior starts with a failing test.

---

### Task 1: Authenticated Knowledge Chunks router and legacy shutdown

**Files:**
- Create: `server/knowledge_chunks_api.py`
- Create: `tests/test_knowledge_chunks_api.py`
- Modify: `server/app.py`
- Modify: `server/documents.py`

**Interfaces:**
- Produces scoped GET list/detail, PATCH, DELETE routes.
- Produces structured `_not_found`, `_conflict`, `_invalid`, and `_authority_unavailable` errors.
- Legacy routes return structured 410.

- [ ] Write failing tests for missing/invalid Bearer, missing tenant header, read/write/delete roles, cross-tenant/dataset/document concealment, OpenAPI Bearer security, and legacy 410.
- [ ] Run `pytest tests/test_knowledge_chunks_api.py -q` and verify RED.
- [ ] Implement router dependencies and scope verification using existing `KnowledgeActor` policy.
- [ ] Include router in bridge app and replace legacy handlers with 410.
- [ ] Run auth/security tests and verify GREEN.
- [ ] Commit backend-only: `feat(api): secure knowledge chunk routes`.

### Task 2: SQL page/detail repository and safe projections

**Files:**
- Modify: `core/chunk_catalog.py`
- Modify: `server/knowledge_chunks_api.py`
- Modify: `tests/test_knowledge_chunks_api.py`
- Modify: `tests/test_chunk_authority_api.py`
- Modify: `tests/test_document_chunks_api.py`

**Interfaces:**
- Produces `ChunkHeadPage`, `list_document_heads_page`, `get_head_scoped`, and parent relation facts.
- Produces one safe projection shared by list/detail/mutations.

- [ ] Write failing SQL tests with >200 rows for count, offset/limit, query reset, deterministic order, include-disabled, scoped detail, known/missing parent IDs, and no raw metadata/secret URI.
- [ ] Verify RED.
- [ ] Implement SQLAlchemy count/page/search without loading all contents.
- [ ] Implement explicit safe fields and scheme-agnostic URI sanitization.
- [ ] Implement structured `off` 409.
- [ ] Verify focused backend GREEN.
- [ ] Commit backend-only: `feat(api): page authoritative chunk heads`.

### Task 3: Exact mutation projections and conflict detail

**Files:**
- Modify: `server/knowledge_chunks_api.py`
- Modify: `server/chunk_operations.py` only if exact post-mutation head access requires it
- Modify: `tests/test_knowledge_chunks_api.py`

**Interfaces:**
- PATCH/DELETE return the same lifecycle projection as list/detail plus `authority_mode`, `operation_ids`, and optional graph summary.

- [ ] Write failing active/shadow/off tests.
- [ ] Assert active pending derives from head lifecycle, not operation IDs.
- [ ] Assert shadow copy facts identify synchronous legacy projection semantics.
- [ ] Assert tombstone detail remains readable to authorized operators but mutation is rejected.
- [ ] Implement minimal exact mutation projection.
- [ ] Verify and commit backend-only: `fix(api): align chunk mutation projections`.

### Task 4: Authenticated frontend API and server pagination

**Files:**
- Modify: `frontend/src/parse-intervention/model/parseInterventionModel.ts`
- Modify: `frontend/src/parse-intervention/model/parseInterventionModel.test.ts`
- Rewrite: `frontend/src/parse-intervention/api/parseInterventionApi.ts`
- Rewrite: `frontend/src/parse-intervention/api/parseInterventionApi.test.ts`
- Modify: `frontend/src/parse-intervention/hooks/useParseIntervention.ts`
- Modify: `frontend/src/parse-intervention/hooks/useParseIntervention.test.tsx`
- Modify: `frontend/src/types/rag.ts`
- Remove: `frontend/src/documents/DocumentWorkspace.tsx`
- Remove: `frontend/src/documents/DocumentWorkspace.test.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/client.documents.test.ts`

**Interfaces:**
- `ParseScope` adds `actorToken`.
- API sends Bearer and `X-RAG4C-Tenant`.
- Controller exposes loaded/total/hasMore/loadMore and server-backed query.

- [ ] Write failing auth-header, missing-scope, page append/dedupe, total, load-more, query reset, abort, and stale-scope tests.
- [ ] Verify RED.
- [ ] Implement scoped API and paged controller.
- [ ] Remove all old frontend legacy chunk consumers/methods.
- [ ] Verify and commit frontend-only: `feat(frontend): use authenticated chunk authority`.

### Task 5: Navigation, mode semantics, URI/relation, orphan conflicts

**Files:**
- Modify: `frontend/src/pages/DocumentsPage.tsx`
- Modify: `frontend/src/pages/DocumentsPage.workspace.test.tsx`
- Modify: `frontend/src/documents/documentParseRoute.ts`
- Modify: `frontend/src/documents/documentParseRoute.test.ts`
- Modify: parse intervention model/hook/components/tests

**Interfaces:**
- Origin markers work for direct/hash openings.
- Direct deep-link close uses replace.
- `OrphanDraft` is selection/page independent.

- [ ] Write failing A→B dirty guard and direct/hash history tests.
- [ ] Write failing off/shadow/active copy and receipt tests.
- [ ] Write failing missing/tombstone/outside-page conflict tests.
- [ ] Write failing URI scheme and parent known/missing/unknown tests.
- [ ] Implement minimal behavior and verify GREEN.
- [ ] Commit frontend-only: `fix(frontend): harden parse intervention recovery`.

### Task 6: ARIA tabs and listbox focus

**Files:**
- Modify: `frontend/src/parse-intervention/ParseInterventionWorkspace.tsx`
- Modify: `frontend/src/parse-intervention/components/ChunkListPane.tsx`
- Modify: corresponding tests/CSS

- [ ] Write failing tab IDs/controls/labelledby/roving-arrow tests.
- [ ] Write failing listbox single-focus and mounted-active-descendant tests.
- [ ] Implement complete ARIA patterns.
- [ ] Verify and commit frontend-only: `fix(frontend): complete parse workspace accessibility`.

### Task 7: Full verification and review

- [ ] Run auth/security and focused backend tests.
- [ ] Run full backend suite appropriate to the repository.
- [ ] Run focused/full frontend Vitest with 10s timeout.
- [ ] Run lint and production build.
- [ ] Run Playwright 1440/375 light/dark, dirty navigation, paging, conflict, reduced motion, containment.
- [ ] Run axe and fix feature-owned confirmed violations.
- [ ] Normalize EOL/EOF, verify normal numstat equals ignore-EOL, ensure root `node_modules` absent.
- [ ] Commit any backend/frontend verification fixes separately.
