# RAG4C KnowledgeOps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement RAG4C KnowledgeOps as a trusted, revisioned, multi-tenant knowledge-engineering platform with MySQL authority and revision-fenced Milvus/graph projections.

**Architecture:** Deepen the existing Catalog, ChunkCatalog, source ledger, ingest ledger, and index-operation modules instead of adding parallel truth stores. Establish a shared frontend knowledge-workspace module and migrate compatibility data through shadow/backfill stages before activating new authority modes.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy 2, Alembic, MySQL/SQLite, Milvus, graph store, Redis, React 18, TypeScript, TDesign tokens, Vitest, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-24-rag4c-knowledgeops-database-design.md`

## Global Constraints
- MySQL is the production knowledge authority; Milvus and graph storage are projections.
- Keep `chunk_authority_mode=shadow` until backfill and drift checks pass.
- Production failures never silently switch to demo data.
- Every mutation is tenant scoped, revision fenced where applicable, and audited.
- Existing API contracts remain available during migration through compatibility adapters.

---

### Task 1: Authority-safe chunk mutation
**Files:** Modify `server/chunk_operations.py`, `server/documents.py`, `core/chunk_catalog.py`, `core/index_operations.py`; add focused tests.
**Produces:** optimistic edit/delete through ChunkCatalog and durable projection operations.
- [ ] Write failing tests for expected revision, immutable revision creation, 409 conflict, enqueue-only projection, and deletion tombstone behavior.
- [ ] Implement the minimal deep module interface and route integration.
- [ ] Run focused tests, Ruff, and compatibility tests.

### Task 2: Shared frontend knowledge workspace
**Files:** Create `frontend/src/knowledge/KnowledgeWorkspaceContext.tsx` and tests; modify providers and knowledge pages.
**Produces:** one data snapshot, explicit error/demo semantics, mutation invalidation.
- [ ] Write failing provider tests for request deduplication, invalidation, failure visibility, and explicit demo.
- [ ] Implement provider and migrate overview/taxonomy/sources/documents consumers.
- [ ] Run Vitest, ESLint, build, and browser checks.

### Task 3: Backfill and reconcile
**Files:** Create/modify rollout scripts under `scripts/`; add tests.
**Produces:** resumable document/chunk backfill, read-only drift report, explicit repair enqueue.
- [ ] Write failing tests for resumability, hash report, stale revisions, parent chunks, and disabled chunks.
- [ ] Implement dry-run/report by default and explicit repair mode.
- [ ] Exercise isolated SQLite/MySQL migration and rollback drills.

### Task 4: Knowledge governance schema
**Files:** Add Alembic migrations and ORM/Catalog modules for folders, tags, versions, QA, and audit.
**Produces:** authoritative governance entities and compatibility projections.
- [ ] Write migration and repository tests first.
- [ ] Implement constraints, indexes, tenant/dataset scoping, CRUD, and backfill adapters.
- [ ] Run upgrade/downgrade and duplicate audits.

### Task 5: Governance APIs and frontend
**Files:** Add focused routers/modules and split frontend documents/knowledge pages.
**Produces:** knowledge-base switcher, folder/tag CRUD, QA review, expiry, version history, audit viewer.
- [ ] Add API contract and permission tests.
- [ ] Implement focused modules and shared projections.
- [ ] Add Playwright flows and accessibility checks.

### Task 6: Source operations
**Files:** Productize source-ledger APIs and frontend; add safe web/read-only DB adapters later in the task.
**Produces:** source CRUD, sync runs/items, schedules, retries, and real source status.
- [ ] Add ledger/API tests and connector security tests.
- [ ] Implement source control plane and UI.
- [ ] Verify interruption/resume and no false deletions.

### Task 7: Retrieval and consistency center
**Files:** Add pure retrieval experiments, judgments, evidence lineage, and consistency APIs/UI.
**Produces:** strategy A/B, save-to-eval, revision drift and repair console.
- [ ] Add deterministic retrieval experiment tests.
- [ ] Implement APIs and frontend modules.
- [ ] Verify metrics and CI quality gates.

### Task 8: Release audit
- [ ] Run full backend/frontend suites and migrations.
- [ ] Run Playwright/axe and responsive screenshots.
- [ ] Run tenant security and consistency checks.
- [ ] Review diff against the spec and leave worktree clean.
