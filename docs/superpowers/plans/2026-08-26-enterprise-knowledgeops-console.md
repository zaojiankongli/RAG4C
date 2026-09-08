# Enterprise KnowledgeOps Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first enterprise-grade RAG4C workspace wave: a Tencent-inspired shared shell, operational knowledge overview, governed taxonomy workspace, and honest database-readiness reporting.

**Architecture:** New enterprise UI primitives and page-local styles avoid expanding the global CSS monolith. Independent page slices are implemented in parallel, then the app shell and server router are integrated serially to protect shared dirty files. The backend endpoint is read-only and never migrates the live database.

**Tech Stack:** React 18, TypeScript, Vite 6, TDesign React, TDesign Icons, Vitest, Testing Library, FastAPI, SQLAlchemy/Alembic, Pytest.

**Spec:** `docs/superpowers/specs/2026-08-26-enterprise-knowledgeops-console-design.md`

## Global Constraints

- TDesign React and TDesign Icons are the primary UI system.
- Do not add a new npm dependency in this wave.
- Do not mutate or migrate the real MySQL database.
- Preserve all current uncommitted retrieval-quality and retrieval-experiment changes.
- Demo or unavailable data must be labeled honestly.
- All new behavior requires a failing test before implementation.
- Shared files `frontend/src/App.tsx`, `frontend/src/styles.css`, `frontend/src/ui/index.tsx`, and `server/app.py` are integrated serially by the controller only.

---

### Task 1: Enterprise UI primitives

**Files:**
- Create: `frontend/src/ui/enterprise/WorkspaceScopeBar.tsx`
- Create: `frontend/src/ui/enterprise/AuthorityBanner.tsx`
- Create: `frontend/src/ui/enterprise/LifecycleRail.tsx`
- Create: `frontend/src/ui/enterprise/MetricStrip.tsx`
- Create: `frontend/src/ui/enterprise/index.ts`
- Create: `frontend/src/theme/enterprise-components.css`
- Test: `frontend/src/ui/enterprise/enterprise-components.test.tsx`

**Interfaces:**
- Produces `WorkspaceScopeBar`, `AuthorityBanner`, `LifecycleRail`, and `MetricStrip` with typed props and no application-context dependency.

- [ ] Write semantic component tests for organization/knowledge-base/environment labels, authority state, lifecycle steps, and metric values.
- [ ] Run `npm --prefix frontend test -- src/ui/enterprise/enterprise-components.test.tsx` and confirm failure because the modules do not exist.
- [ ] Implement the components with TDesign controls and TDesign icons.
- [ ] Add responsive, dark-mode, focus-visible, and reduced-motion styling in the new CSS file.
- [ ] Re-run the focused test and confirm it passes.

### Task 2: Knowledge overview command center

**Files:**
- Modify: `frontend/src/pages/KnowledgeOverviewPage.tsx`
- Create: `frontend/src/pages/knowledge-overview.css`
- Modify: `frontend/src/pages/KnowledgeOverviewPage.test.tsx`
- Modify: `frontend/src/pages/KnowledgeOverviewPage.css.test.ts`

**Interfaces:**
- Consumes existing `useKnowledgeDocuments()` and `projectKnowledgeOverview()`.
- May consume Task 1 primitives only through `../ui/enterprise`.

- [ ] Extend tests to require a single page heading, lifecycle semantics, attention surface, recent asset surface, and honest demo/live label.
- [ ] Run the focused page tests and confirm they fail for the missing operational layout.
- [ ] Implement the command-center layout without adding global CSS selectors.
- [ ] Replace full-page anchor navigation with app-route-compatible navigation intents.
- [ ] Re-run focused tests and confirm they pass.

### Task 3: Governed taxonomy workspace

**Files:**
- Modify: `frontend/src/pages/KnowledgeTaxonomyPage.tsx`
- Create: `frontend/src/pages/knowledge-taxonomy.css`
- Create or modify: `frontend/src/pages/KnowledgeTaxonomyPage.test.tsx`

**Interfaces:**
- Consumes the current knowledge document projection and workspace scope.
- Produces directory, tag, and document result surfaces without backend mutation.

- [ ] Write tests for directory navigation, tag filtering, document counts, empty state, and semantic headings.
- [ ] Run the focused test and confirm it fails against the current static card layout.
- [ ] Implement the three-surface enterprise layout with TDesign controls.
- [ ] Re-run focused tests and confirm they pass.

### Task 4: Enterprise readiness endpoint

**Files:**
- Create: `server/enterprise_readiness_api.py`
- Create: `tests/test_enterprise_readiness_api.py`

**Interfaces:**
- Produces `EnterpriseReadinessResponse` and `build_enterprise_readiness_router(...)`.
- The router exposes `GET /api/enterprise/readiness` after Task 5 integration.

- [ ] Write tests for schema current, schema behind, inspection unavailable, and malformed revision states.
- [ ] Run `pytest tests/test_enterprise_readiness_api.py -q` and confirm failure because the module does not exist.
- [ ] Implement a dependency-injected, read-only inspector and FastAPI router.
- [ ] Re-run the focused backend test and confirm it passes.

### Task 5: Shared shell and router integration

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/run/appRoute.ts` only if a new enterprise administration route is introduced.
- Modify: `frontend/src/App.a11y.test.tsx`
- Modify: `frontend/src/App.mobile.test.tsx`
- Create: `frontend/src/shell/enterprise-shell.css`
- Modify: `frontend/src/main.tsx` to import new CSS files if required.
- Modify carefully: `server/app.py`
- Test: existing app tests plus `tests/test_enterprise_readiness_api.py`

**Interfaces:**
- Consumes Task 1 primitives and Task 4 router.
- Preserves existing route keys and current dirty changes in `server/app.py`.

- [ ] Extend app tests for workspace scope, grouped navigation, mobile controls, and accessible search.
- [ ] Run focused app tests and confirm expected failures.
- [ ] Integrate the workspace bar and enterprise navigation without changing page keep-alive semantics.
- [ ] Import page-local and enterprise CSS once from the frontend entry point.
- [ ] Mount the readiness router in `server/app.py` while preserving the existing retrieval-experiment diff.
- [ ] Re-run focused frontend and backend tests.

### Task 6: Full verification and Playwright QA

**Files:**
- Create screenshots only under `output/playwright/enterprise-console/`.

- [ ] Run `npm --prefix frontend test`.
- [ ] Run `npm --prefix frontend run build`.
- [ ] Run focused Python tests for enterprise readiness and affected app routing.
- [ ] Start or reuse the frontend and backend locally without modifying live database state.
- [ ] Capture 1440px and 375px screenshots for overview and taxonomy in light mode.
- [ ] Capture at least one dark-mode enterprise shell screenshot.
- [ ] Inspect screenshots for overflow, low contrast, empty dead zones, duplicated headings, and inconsistent component styles.
- [ ] Fix visual regressions with a failing style/behavior test first, then repeat verification.
