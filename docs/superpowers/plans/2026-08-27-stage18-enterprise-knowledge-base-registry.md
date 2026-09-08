# Stage 18 Enterprise Knowledge Base Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-class enterprise Knowledge Base registry with authoritative Workspace ownership, durable Application references, dependency-aware archive safety and a TDesign resource center.

**Architecture:** Revision `0028_enterprise_knowledge_base_registry` adds ownership and Application reference authority while preserving Stage 17 shared Workspace associations and additive authorization. New Core/API modules expose tenant-scoped registry, dependency and mutation contracts; the frontend consumes only these authenticated facts and unifies Workspace/Dataset scope.

**Tech Stack:** Python 3.13, SQLAlchemy 2, Alembic, FastAPI, Pydantic, SQLite/MySQL schema tests, React 18, TypeScript, TDesign React, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-27-enterprise-knowledge-base-registry-design.md`

## Global Constraints

- Target revision is exactly `0028_enterprise_knowledge_base_registry` with down revision `0027_enterprise_workspace_authorization`.
- Work in the current cumulative workspace; do not reset, checkout, clean or format the whole tree.
- Protect `frontend/src/retrieval-quality/**`, `core/retrieval_experiment_runner.py`, `server/retrieval_experiments_api.py` and their tests.
- TDesign React and TDesign Icons are the primary UI libraries.
- Ownership, association, Application reference and permission are separate facts.
- Shared Workspace bindings remain associations and are never backfilled as ownership.
- All modern mutations require actor identity, Tenant scope, revision fences, Idempotency-Key, audit and atomic rollback.
- No production migration, production write, ownership transfer, App reference mutation, approval execution, restore or external publication.

---

### Task 1: 0028 Registry Database Authority

**Files:**
- Create: `catalog_migrations/versions/0028_enterprise_knowledge_base_registry.py`
- Modify: `models/orm.py`
- Modify: `core/catalog_schema.py`
- Modify: `server/enterprise_readiness_api.py`
- Test: `tests/test_enterprise_knowledge_base_registry_migration.py`
- Test: `tests/test_catalog_schema.py`
- Test: `tests/test_enterprise_readiness_api.py`

**Interfaces:**
- Produces ORM models `DatasetWorkspaceOwnership` and `AppDatasetReference`.
- Produces revision constants and complete 0028 Catalog capability evidence for later tasks.

- [ ] Write RED migration tests for tables, columns, exact CHECK SQL, unique/index/FK contracts, long IDs and cross-Tenant failures.
- [ ] Add RED tests for ownership backfill from active primary bindings and refusal to treat shared bindings as ownership.
- [ ] Add RED tests for approval action `dataset_workspace_transfer`, downgrade blockers and offline MySQL DDL.
- [ ] Implement the 0028 migration and deterministic backfill.
- [ ] Add ORM relationships and tenant-safe App uniqueness.
- [ ] Extend Catalog Schema and Readiness with exact 0028 capability checks.
- [ ] Run migration/Catalog/Readiness tests, Ruff, format and py_compile.

### Task 2: Registry Read Core and HTTP API

**Files:**
- Create: `core/enterprise_knowledge_base_registry.py`
- Create: `server/enterprise_knowledge_base_registry_api.py`
- Modify: `server/app.py`
- Test: `tests/test_enterprise_knowledge_base_registry_core.py`
- Test: `tests/test_enterprise_knowledge_base_registry_api.py`

**Interfaces:**
- Consumes `DatasetWorkspaceOwnership`, `AppDatasetReference` and the 0028 capability gate.
- Produces `list_knowledge_bases`, `get_knowledge_base`, `get_knowledge_base_dependencies` and strict authenticated routes.

- [ ] Write RED tests for tenant-scoped keyset list filters and null-vs-zero evidence.
- [ ] Write RED tests for detail/dependency projections and active App reference archive blockers.
- [ ] Implement fail-closed schema/actor/Tenant validation and aggregate queries.
- [ ] Mount strict GET routes and safe error mapping.
- [ ] Add real 0028 SQLite router integration tests.
- [ ] Run Core/API tests and static checks.

### Task 3: Application Reference and Ownership Mutations

**Files:**
- Modify: `core/enterprise_knowledge_base_registry.py`
- Modify: `core/knowledge_datasets.py`
- Modify: `server/enterprise_knowledge_base_registry_api.py`
- Test: `tests/test_enterprise_knowledge_base_registry_mutations.py`
- Test: `tests/test_knowledge_datasets.py`
- Test: `tests/test_knowledge_dataset_api.py`

**Interfaces:**
- Produces idempotent `create_app_reference`, `remove_app_reference` and `transfer_dataset_ownership` services.
- Adds a hard archive blocker to the existing Dataset lifecycle repository at 0028.

- [ ] Write RED tests for reference replay/conflict, removal revision fences and cross-Tenant isolation.
- [ ] Write RED tests for ownership transfer lock order, binding compatibility projection and revision conflicts.
- [ ] Write RED tests proving active App references hard-block archive and approval cannot bypass it.
- [ ] Implement mutations using the generic Tenant idempotency ledger and one transaction.
- [ ] Implement audit snapshots without secrets or opaque keys.
- [ ] Add strict POST/DELETE transfer/reference routes.
- [ ] Run mutation, Dataset lifecycle and API regressions.

### Task 4: Approval-Gated Dataset Workspace Transfer

**Files:**
- Modify: `core/enterprise_approval_control.py`
- Modify: `server/enterprise_approval_api.py`
- Modify: `server/enterprise_approval_consumers.py`
- Modify: `core/enterprise_tenant_idempotency.py` only if an operation allowlist is required
- Test: `tests/test_approval_gated_dataset_workspace_transfer.py`
- Test: `tests/test_enterprise_approval_api.py`

**Interfaces:**
- Consumes `transfer_dataset_ownership`.
- Produces action/resource contracts and a one-time execution consumer for `dataset_workspace_transfer:knowledge_base`.

- [ ] Write RED tests for direct approval-required behavior and exact scope matching.
- [ ] Write RED tests for forged fact, stale Dataset/ownership/Workspace revisions, replay and non-manager rejection.
- [ ] Implement Approval request snapshot and internal execution fact validation.
- [ ] Register the consumer without exposing raw tickets.
- [ ] Run Stage 13–18 Approval regressions and security checks.

### Task 5: TDesign Enterprise Knowledge Base Center

**Files:**
- Create: `frontend/src/enterprise-knowledge-base/enterpriseKnowledgeBaseModel.ts`
- Create: `frontend/src/enterprise-knowledge-base/enterpriseKnowledgeBaseValidation.ts`
- Create: `frontend/src/enterprise-knowledge-base/knowledgeBaseRoute.ts`
- Create: `frontend/src/enterprise-knowledge-base/api/enterpriseKnowledgeBaseApi.ts`
- Create: `frontend/src/enterprise-knowledge-base/hooks/useEnterpriseKnowledgeBaseCenter.ts`
- Create: `frontend/src/enterprise-knowledge-base/components/EnterpriseKnowledgeBaseCenter.tsx`
- Create: `frontend/src/enterprise-knowledge-base/enterprise-knowledge-base.css`
- Create corresponding tests in the same directory
- Create: `frontend/src/pages/EnterpriseKnowledgeBasePage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/run/appRoute.ts`
- Modify: `frontend/src/knowledge/KnowledgeWorkspaceContext.tsx`
- Modify: `frontend/src/ui/enterprise/WorkspaceScopeBar.tsx` only as needed for verified scope callbacks
- Modify direct tests for the touched files

**Interfaces:**
- Consumes the authenticated Registry API.
- Produces direct/hash Knowledge Base routes, Dataset deep links and verified Workspace -> Dataset scope updates.

- [ ] Write RED model/API/route tests for strict projection, direct/hash deep links and stable idempotency keys.
- [ ] Write RED component tests for authority strip, dense table, mobile cards and dependency rail.
- [ ] Write RED tests for detail tabs, Application reference flows, ownership transfer direct/approval UX and archive blocker copy.
- [ ] Write RED tests proving Workspace selection changes KnowledgeWorkspaceContext only through server-returned ownership/bindings.
- [ ] Implement TDesign desktop, 375px and 280px surfaces with honest null/unavailable states.
- [ ] Add App navigation without duplicating existing Documents/Taxonomy/Sources/Governance workspaces.
- [ ] Run enterprise Knowledge Base, App route/mobile/a11y, ESLint, TypeScript, Prettier and build.

### Task 6: Operations, Playwright and Final Review

**Files:**
- Modify: `docs/operations/enterprise-catalog-upgrade.md`
- Modify: `tests/test_enterprise_catalog_upgrade.py`
- Create: `docs/research/2026-08-27-enterprise-knowledge-base-registry-stage18-visual-acceptance.md`
- Create: `output/playwright/enterprise-knowledge-base-registry-stage18/**`

**Interfaces:**
- Consumes the complete Stage 18 backend and frontend.
- Produces fail-closed visual evidence, runbook contracts and final independent reviews.

- [ ] Extend the manual upgrade contract from 0007 to 0028 with ownership/reference preflight.
- [ ] Build a controlled Playwright fixture for Registry, dependencies, App references, transfer and archive blockers.
- [ ] Run exact direct/hash × light/dark × 1440/375/280 matrix.
- [ ] Require zero console/page/unknown/unexpected request errors and zero horizontal overflow.
- [ ] Verify keyboard Tab/Enter/Escape, Drawer/Dialog focus return and raw secret/ticket absence.
- [ ] Run Stage 7–18 backend regression, frontend enterprise regression, Ruff/format/py_compile, Alembic head, lock and diff checks.
- [ ] Request independent Database/Security and TDesign/UI reviews; resolve all P0/P1/P2 before completion.

## Write-Scope Coordination

- Database worker: migration, ORM, Catalog Schema, Readiness and migration tests.
- Registry worker: new Registry Core/API, Dataset archive integration and mutation tests.
- Approval worker: Approval contracts/consumer and approval-gated tests.
- Frontend worker: `enterprise-knowledge-base/**`, page, App route and verified scope integration.
- Verification worker: runbook, Playwright artifacts and read-only final reviews.

## Production Prohibition

No real production migration, ownership backfill, Dataset ownership transfer, Application reference mutation, Dataset archive/delete, approval execution, restore, source sync, document ingestion, external publication or protected Retrieval Quality modification is performed automatically.
