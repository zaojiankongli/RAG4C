# TDesign Knowledge Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Ant Design with TDesign React and deliver a Tencent-inspired, RAG4C-specific knowledge workbench with safe deletion, detailed parsing telemetry, and ingestion monitoring.

**Architecture:** Migrate the design system from the provider outward, keeping product layout in application-owned CSS. Add a backend document-operations service that coordinates catalog, Milvus, graph, cache, telemetry, and aggregation APIs; then build document and monitor projections against those stable contracts.

**Tech Stack:** React 18, TypeScript 5.6, Vite 6, TDesign React 1.18.x, TDesign Icons React 0.6.x, ECharts 6, FastAPI, SQLAlchemy, Milvus, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-24-tdesign-knowledge-workbench-design.md`

## Global Constraints
- Remove all runtime and source dependencies on `antd` and `@ant-design/icons`.
- Preserve React 18, TypeScript 5.6, Vite 6, Tauri 2, ECharts 6, light/dark mode, and Chinese locale.
- Reject deletion of queued/running documents with HTTP 409.
- Batch deletion accepts at most 100 unique document IDs and returns itemized outcomes.
- Adaptive duration formatting must retain millisecond precision for values below one second.
- Do not rely on private Tencent VHTML packages or copy Tencent branding.
- The workspace has no Git metadata; skip commit steps and record verification output instead.

---

### Task 1: TDesign foundation and app shell
**Files:** Modify `frontend/package.json`, lockfile, `frontend/src/main.tsx`, `frontend/src/AppProviders.tsx`, `frontend/src/theme/tokens.ts`, `frontend/src/App.tsx`, `frontend/src/styles.css`; create `frontend/src/ui/feedback.ts` and tests.
**Produces:** TDesign locale/theme foundation, message helpers, migrated shell/navigation, no Ant provider dependency.
- [ ] Add TDesign dependencies and write failing theme/feedback tests.
- [ ] Run focused tests and confirm failures are caused by missing TDesign foundation.
- [ ] Implement locale, theme attribute, token bridge, app shell, and message helpers.
- [ ] Run focused tests, lint, and TypeScript build.

### Task 2: Shared component migration
**Files:** Modify every Ant-dependent file in `frontend/src/components/` and associated tests/styles.
**Consumes:** Task 1 theme and feedback helpers.
**Produces:** Shared cards, banners, flow controls, tables, timelines, and inspectors using TDesign and TDesign icons.
- [ ] Add/update component tests for TDesign semantics and accessible interactions.
- [ ] Migrate components in small groups, running their focused tests after each group.
- [ ] Replace component-internal `.ant-*` CSS with application-owned classes.
- [ ] Run all component tests and TypeScript build.

### Task 3: Page migration and Ant removal
**Files:** Modify `frontend/src/pages/QueryPage.tsx`, `ConfigPage.tsx`, `EvalPage.tsx`, `VisualizePage.tsx`, `MonitorPage.tsx`, current `DocumentsPage.tsx`, tests, and CSS; remove Ant packages.
**Consumes:** Tasks 1-2 shared UI.
**Produces:** All pages on TDesign; source scan finds no Ant imports/selectors.
- [ ] Update page tests/mocks to fail without Ant compatibility assumptions.
- [ ] Migrate pages and page-level icons/components.
- [ ] Remove Ant dependencies and selectors.
- [ ] Run full frontend tests, lint, build, and source scan.

### Task 4: Safe document deletion service and APIs
**Files:** Create `server/document_operations.py`; modify `server/documents.py`, `indexing/ingest.py`; add `tests/test_document_delete_api.py` and focused pipeline tests.
**Produces:** `delete_document_artifacts`, single delete endpoint, batch delete endpoint, busy-document protection, graph/vector/catalog/cache cleanup.
- [ ] Write failing tests for success, not-found, busy, graph cascade, partial external failure, quota/cache behavior, and batch itemization.
- [ ] Run focused tests and confirm expected failures.
- [ ] Implement the service and API models/routes with catalog-last idempotent cleanup.
- [ ] Run focused and related backend tests.

### Task 5: Detailed ingest telemetry and document metrics API
**Files:** Modify `indexing/ingest.py`, `indexing/state_machine.py`, `core/catalog.py`, `server/documents.py`, frontend API/types; add backend and frontend projection tests.
**Produces:** Persisted `stage_ms`/counts/total metadata, process metrics, `/api/documents/metrics`, adaptive formatter contract.
- [ ] Write failing tests for stage capture, errors, legacy metadata, percentiles, distributions, and duration formatting.
- [ ] Implement per-stage timers and state-machine metric emission.
- [ ] Implement catalog aggregation and API response.
- [ ] Run focused tests and compatibility tests.

### Task 6: Tencent-inspired document workbench
**Files:** Split `frontend/src/pages/DocumentsPage.tsx` into focused modules under `frontend/src/documents/`; modify API/types/mock/styles and add tests.
**Consumes:** Tasks 3-5.
**Produces:** Filter rail, selectable table, contextual batch bar, guided import, delete confirmations, detail drawer, timing rail, chunk preview foundation.
- [ ] Write failing interaction/projection tests for selection, busy rows, partial deletion, adaptive duration, import flow, and details.
- [ ] Implement document projections and pure formatters first.
- [ ] Implement the workbench components with TDesign.
- [ ] Run document tests, accessibility checks, lint, and build.

### Task 7: Ingestion monitoring and monitor polish
**Files:** Create `frontend/src/monitor/DocumentIngestMonitor.tsx` and projections/tests; modify `MonitorPage.tsx`, API/types, charts, and styles.
**Consumes:** Task 5 metrics API and Task 3 TDesign migration.
**Produces:** Document KPIs, stage percentile chart, distributions, slow/failure lists, refreshed monitor hierarchy.
- [ ] Write failing projection and rendering tests for real/legacy/empty/error data.
- [ ] Implement API projection and chart options.
- [ ] Integrate the TDesign monitor section and responsive styles.
- [ ] Run monitor tests, accessibility tests, lint, and build.

### Task 8: Final verification and cleanup
**Files:** All changed files, README/API documentation if contracts changed.
- [ ] Run full backend pytest with the project runtime.
- [ ] Run `npm test`, `npm run lint`, and `npm run build`.
- [ ] Scan for `antd`, `@ant-design/icons`, and `.ant-` in source and package manifests.
- [ ] Inspect generated bundle and verify TDesign replaces Ant chunks.
- [ ] Review spec coverage and record remaining future scope (chunk editing only).
