# RAG4C Operational Clarity Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply one compact, Chinese-first, responsive operations design system across the RAG4C shell, high-frequency Knowledge workflows and enterprise/governance pages.

**Architecture:** Reuse the existing React route/page modules and TDesign facade. Add shared CSS/component contracts instead of rewriting domain pages. Desktop and mobile receive separate presentations where dense tables or graphs cannot be shrunk safely.

**Tech Stack:** React 18, TypeScript, Vite, existing TDesign/app-owned facade, CSS tokens, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-31-rag4c-operational-clarity-design.md`

## Global Constraints

- Execute only after the Operational Stability plan is green.
- Preserve all routes, APIs, authorization checks and handoff contracts.
- Keep the blue/green trust palette and Knowledge Lifeline signature.
- Chinese product language is primary; technical IDs are secondary detail.
- No general-purpose gradients, decorative shadow system or new UI framework.
- Desktop and mobile must not duplicate interactive DOM for the same control.
- No broad formatting or cleanup of unrelated cumulative changes.

---

### Task 1: Shared Operational Clarity primitives

**Files:**
- Modify: `frontend/src/components/PageTopbar.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/theme/tokens.ts`
- Create: `frontend/src/components/OperationalPageHeader.tsx`
- Create: `frontend/src/components/OperationalEmptyState.tsx`
- Create: `frontend/src/components/OperationalMetricStrip.tsx`
- Add focused component/style tests.

**Interfaces:**
- `OperationalPageHeader({ icon, title, description, technicalMeta?, actions? })`
- `OperationalEmptyState({ title, description, primaryAction?, secondaryAction?, reason? })`
- `OperationalMetricStrip({ items, emptySummary? })`

- [ ] **Step 1: Write failing semantic tests**

Assert one H1, no more than two primary header actions, non-zero metric prioritization, and action-bearing empty states.

- [ ] **Step 2: Verify RED**

```powershell
cd frontend
npm run test:single -- src/components/OperationalPageHeader.test.tsx src/components/OperationalEmptyState.test.tsx src/components/OperationalMetricStrip.test.tsx
```

- [ ] **Step 3: Implement primitives and tokens**

Use flat surfaces, structural borders, existing font stack and semantic color variables. Technical metadata is visually subordinate.

- [ ] **Step 4: Verify GREEN**

Run component tests, style tests and ESLint.

### Task 2: Compact global shell and keyboard order

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/App.layout.css.test.ts`
- Modify: `frontend/src/App.mobile.test.tsx`
- Modify: `frontend/src/App.a11y.test.tsx`

**Interfaces:**
- Desktop shell zones: tenant/environment, Workspace/search, identity/notifications.
- Mobile shell: one identity row plus one Workspace/search trigger row; maximum target height 104px.

- [ ] **Step 1: Write failing layout tests**

Assert mobile shell does not render permanent full-width search and Workspace stacks simultaneously; first Tab remains skip-link; names have title/tooltip; hidden desktop controls are not mobile tabbable.

- [ ] **Step 2: Verify RED**

```powershell
cd frontend
npm run test:single -- src/App.layout.css.test.ts src/App.mobile.test.tsx src/App.a11y.test.tsx
```

- [ ] **Step 3: Implement compact shell**

Add mobile search sheet/overlay trigger, one-line truncation and stable status vocabulary. Preserve Ctrl/⌘K.

- [ ] **Step 4: Verify GREEN and screenshots**

Run focused tests; capture 1440, 1366, 390 and 375 shell screenshots in light/dark themes.

### Task 3: Query workspace clarity

**Files:**
- Modify: `frontend/src/pages/QueryPage.tsx`
- Modify: `frontend/src/styles.css` query sections
- Modify: `frontend/src/components/PhaseStatus.tsx`
- Add or modify Query tests.

**Interfaces:**
- Welcome content collapses after first message.
- Terminal state only after citation verification completes.
- Mobile composer remains compact and never covers navigation/latest response.

- [ ] **Step 1: Write failing tests**

Test welcome collapse, streaming versus terminal status, Stop disappearance, source count, verified citation summary and mobile composer layout classes.

- [ ] **Step 2: Verify RED**

```powershell
cd frontend
npm run test:single -- src/pages/QueryPage.test.tsx src/components/PhaseStatus.test.tsx
```

- [ ] **Step 3: Implement layout/state presentation**

Do not alter query orchestration. Recompose existing state into compact status and evidence sections.

- [ ] **Step 4: Verify GREEN and E2E**

Run tests; import the audit document and verify `AURORA-20260830`, terminal Stop removal and 1/1 citation.

### Task 4: Documents and Knowledge Base shell responsiveness

**Files:**
- Modify: `frontend/src/enterprise-knowledge-base-shell/KnowledgeBaseResourceShell.tsx`
- Modify: related shell CSS
- Modify: `frontend/src/pages/DocumentsPage.tsx`
- Modify: `frontend/src/documents/KnowledgeDocumentTable.tsx`
- Modify: Documents mobile/workspace tests.

**Interfaces:**
- Desktop table remains in a named horizontal-scroll region.
- Mobile renders document cards/action menus, not the fixed-width table.
- Authority header collapses to title, state and “权威详情”.

- [ ] **Step 1: Write failing responsive tests**

Assert one desktop table, one mobile card list, no duplicate actions, server-local path explanation and compact authority summary.

- [ ] **Step 2: Verify RED**

```powershell
cd frontend
npm run test:single -- src/pages/DocumentsPage.workspace.test.tsx src/documents/KnowledgeDocumentTable.test.tsx
```

- [ ] **Step 3: Implement responsive composition**

Reuse document model and handlers; change only presentation and action placement.

- [ ] **Step 4: Verify GREEN and Playwright**

Capture empty/populated/import/recycle states at 1440, 1366, 390 and 375.

### Task 5: Overview, Sources and empty-state next actions

**Files:**
- Modify: `frontend/src/pages/KnowledgeOverviewPage.tsx`
- Modify: `frontend/src/pages/knowledge-overview.css`
- Modify: `frontend/src/pages/KnowledgeSourcesPage.tsx`
- Modify: source/overview tests.

**Interfaces:**
- Empty Overview shows one compact asset summary and primary “导入文档”.
- Full Lifeline appears when assets exist.
- Sources exposes a real action or explicit supported handoff; no dead-end instructional copy.

- [ ] **Step 1: Write failing empty-state tests**

Assert zero-only metric walls are absent and the next action is keyboard reachable.

- [ ] **Step 2: Verify RED**

```powershell
cd frontend
npm run test:single -- src/pages/KnowledgeOverviewPage.test.tsx src/pages/KnowledgeSourcesPage.test.tsx
```

- [ ] **Step 3: Implement progressive disclosure**

Keep authoritative counts and source boundaries; do not invent unsupported source mutations.

- [ ] **Step 4: Verify GREEN**

Run tests and desktop/mobile screenshots.

### Task 6: Visualize and Monitor mobile modes

**Files:**
- Modify: `frontend/src/pages/VisualizePage.tsx`
- Modify: Visualize CSS/components
- Modify: `frontend/src/pages/MonitorPage.tsx`
- Modify: monitor/visualize tests.

**Interfaces:**
- Mobile Visualize defaults to node/status list and timeline; topology is optional detail.
- Mobile Monitor renders one selected attention category, not all columns.
- Desktop behavior and deep links remain unchanged.

- [ ] **Step 1: Write failing mobile tests**

Assert mobile list/timeline presence, topology disclosure control, attention category selector and one visible category.

- [ ] **Step 2: Verify RED**

Run existing Visualize/Monitor focused Vitest suites.

- [ ] **Step 3: Implement responsive modes**

Reuse run/event projections. No new API requests.

- [ ] **Step 4: Verify GREEN and graph evidence**

Capture recent/completed/failed run views at desktop and mobile. Confirm React Flow attribution policy.

### Task 7: Enterprise and governance consistency pass

**Files:**
- Modify existing CSS/components under:
  - `frontend/src/enterprise-task-operations/`
  - `frontend/src/enterprise-automation-workflows/`
  - `frontend/src/enterprise-notification-center/`
  - `frontend/src/enterprise-knowledge-base/`
  - `frontend/src/enterprise-knowledge-base-shell/`
  - `frontend/src/enterprise-content-recovery/`
  - `frontend/src/pages/EnterpriseAdminPage.tsx`
  - `frontend/src/pages/ConsistencyPage.tsx`
  - `frontend/src/pages/KnowledgeGovernancePage.tsx`
  - `frontend/src/pages/ConfigPage.tsx`
- Modify focused page/style tests only for touched behavior.

**Interfaces:**
- Shared Chinese-first header and attention/evidence patterns.
- Draft/paused/empty states expose actions.
- Technical IDs move to detail surfaces.
- Config search shows result count and section landmarks.

- [ ] **Step 1: Add page-level characterization tests**

For each touched page, assert primary title/copy, one next action, technical metadata demotion and mobile table/scroll semantics.

- [ ] **Step 2: Verify RED**

Run focused existing suites for Tasks, Automations, Notifications, Registry, Recovery, Enterprise, Consistency, Governance and Config.

- [ ] **Step 3: Implement page consistency**

Refactor presentation only; keep hooks/services/models unchanged unless the Stability plan explicitly changed a contract.

- [ ] **Step 4: Verify GREEN**

Run all touched suites, ESLint, TypeScript and Prettier checks.

### Task 8: Full UI release gates

**Files:**
- Update screenshots/reports in `output/playwright/full-ui-audit-20260830/`.
- Update audit findings/progress.

- [ ] **Step 1: Frontend automated gates**

```powershell
cd frontend
npm test
npm run lint
npx tsc --noEmit
npm run build
```

- [ ] **Step 2: Playwright route matrix**

Run all 19 routes at 1440×900 and 390×844, plus critical pages at 1366×768 and 375×812. Capture light/dark shell, mobile drawer, keyboard and reduced motion.

- [ ] **Step 3: Real isolated write flows**

Prove document import, terminal Query/citation, retrieval comparison, automation draft visibility, recycle and restore UI, notification subscriptions and Eval dry-run.

- [ ] **Step 4: Final quality assertions**

Require zero unexplained console warnings/page errors/4xx/5xx, no document-level overflow, clear mobile interaction, and no sensitive text in artifacts.

- [ ] **Step 5: Review checkpoint**

```powershell
git diff --check
```

Record exact test counts, build output and artifact paths.

## Self-review

- Shared primitives precede page migrations.
- Every visual finding maps to Tasks 1–7.
- High-frequency workflows are delivered before the broad enterprise consistency pass.
- Desktop and mobile have explicit separate acceptance behavior.
- No route/API/domain rewrite or new framework is required.
- No placeholder or ambiguous implementation boundary remains.
