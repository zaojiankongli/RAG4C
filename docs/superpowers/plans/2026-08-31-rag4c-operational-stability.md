# RAG4C Operational Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair every Playwright-confirmed P0/P1 runtime and shared compatibility defect so all existing RAG4C workflows tell the truth and can execute against the current catalog head.

**Architecture:** Preserve route and authority boundaries. Read endpoints keep read-only engines; mutation endpoints receive explicit writable engines. Shared UI facade fixes eliminate page-specific workarounds. Recovery and Workspace capability checks become dialect- and revision-aware without weakening unknown/future fail-closed behavior.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy/Alembic, React 18, TypeScript, Vite, TDesign facade, Vitest, pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-08-31-rag4c-operational-clarity-design.md`

## Global Constraints

- Preserve all existing route URLs and safe handoff query contracts.
- Preserve Tenant, Workspace, Dataset and actor authorization boundaries.
- No database revision is added.
- No broad formatting, reset, cleanup or cumulative-worktree commit.
- Every production edit starts with a failing focused test and ends with focused verification.
- Unknown/future catalog revisions and malformed recovery evidence remain fail-closed.

---

### Task 1: Current-head Workspace compatibility

**Files:**
- Modify: `core/enterprise_workspace_control.py:70-79,609-655,823-831`
- Modify: `tests/test_stage18_backend_review_fixes.py`
- Modify or add focused assertions: `tests/test_enterprise_workspace_control.py`

**Interfaces:**
- Consumes: `core.catalog_schema._known_catalog_revisions()`, `HEAD_REVISION`, registry/release capability constants.
- Produces: `_supported_workspace_revision(revision: str) -> bool` and registry-managed primary-binding behavior for every known revision at or after 0028.

- [ ] **Step 1: Write failing tests**

```python
def test_workspace_control_accepts_current_head(tmp_path):
    engine = _current_head_engine(tmp_path)
    result = list_workspaces(engine, tenant_id="tenant-a", actor_id="owner-a")
    assert result.body["items"]


def test_current_head_primary_binding_remains_registry_managed(tmp_path):
    engine = _current_head_engine(tmp_path)
    with pytest.raises(WorkspaceConflict, match="registry"):
        remove_workspace_dataset(...)
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
uv run pytest -q tests/test_stage18_backend_review_fixes.py tests/test_enterprise_workspace_control.py -k "current_head or registry_managed"
```

Expected: current head is rejected as migration-required or registry protection is absent.

- [ ] **Step 3: Implement revision-aware support**

Use known migration ancestry rather than another finite set ending at a stage constant. Keep future/unknown revisions unavailable.

- [ ] **Step 4: Verify GREEN and regressions**

```powershell
uv run pytest -q tests/test_stage18_backend_review_fixes.py tests/test_enterprise_workspace_control.py tests/test_enterprise_workspace_api.py
uv run ruff check core/enterprise_workspace_control.py tests/test_stage18_backend_review_fixes.py tests/test_enterprise_workspace_control.py
```

- [ ] **Step 5: Checkpoint**

```powershell
git diff --check -- core/enterprise_workspace_control.py tests/test_stage18_backend_review_fixes.py tests/test_enterprise_workspace_control.py
```

### Task 2: Retrieval experiment read/write engine separation

**Files:**
- Modify: `server/retrieval_experiments_api.py:340-443,568-715`
- Modify: `server/app.py:630-810`
- Modify: `tests/test_retrieval_experiments_api.py`
- Modify: `tests/test_retrieval_experiment_runner.py` only if persistence behavior needs a regression assertion.

**Interfaces:**
- Produces: `build_retrieval_experiments_router(read_engine_provider, mutation_engine_provider, ...)` or equivalent explicit request-state providers.
- Read operations use the read engine; run/judgment mutations use the mutation engine.

- [ ] **Step 1: Write a failing real-engine test**

Mount the router with a SQLite read-only URI for reads and a writable engine for mutations. POST the default two-variant payload and assert 201 plus persisted experiments.

```python
assert response.status_code == 201
with Session(write_engine) as session:
    assert session.scalar(select(func.count()).select_from(RetrievalExperiment)) == 2
```

Also assert GET never requests the mutation provider.

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest -q tests/test_retrieval_experiments_api.py -k "read_only or mutation_engine"
```

Expected: POST attempts a Dataset fence UPDATE on the read-only engine and returns 503.

- [ ] **Step 3: Implement provider split**

Build/mount the router explicitly. `_repository()` receives the selected engine; `_base_retrieval()` remains the dedicated uncached retrieval pipeline.

- [ ] **Step 4: Verify GREEN**

```powershell
uv run pytest -q tests/test_retrieval_experiments_api.py tests/test_retrieval_experiment_runner.py
uv run ruff check server/retrieval_experiments_api.py server/app.py tests/test_retrieval_experiments_api.py
```

- [ ] **Step 5: Checkpoint**

```powershell
git diff --check -- server/retrieval_experiments_api.py server/app.py tests/test_retrieval_experiments_api.py
```

### Task 3: Eval-safe Radio facade

**Files:**
- Modify: `frontend/src/ui/index.tsx:551-557`
- Add: `frontend/src/ui/Radio.compat.test.tsx`
- Add or modify: `frontend/src/pages/EvalPage.test.tsx`

**Interfaces:**
- Produces fallback `Radio.Group` supporting `value`, event-shaped `onChange`, `options`, and `Radio.Button` children.

- [ ] **Step 1: Write failing facade and page tests**

```tsx
render(<Radio.Group value="none" onChange={onChange}><Radio.Button value="none">快速演练</Radio.Button></Radio.Group>);
expect(screen.getByRole("radio", { name: "快速演练" })).toBeChecked();
await user.click(screen.getByRole("radio", { name: "真实资料评测" }));
expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ target: { value: "rag:answer_query" } }));
```

Render real `EvalPage` with empty API results and assert the initial form and “运行评测” are visible.

- [ ] **Step 2: Verify RED**

```powershell
cd frontend
npm run test:single -- src/ui/Radio.compat.test.tsx src/pages/EvalPage.test.tsx
```

Expected: the current wrapper renders direct TDesign or the page test hangs/fails.

- [ ] **Step 3: Implement native fallback**

Render a labelled radiogroup/fieldset and native radios when `canRenderTDesign()` is false. Keep TDesign for the enabled branch.

- [ ] **Step 4: Verify GREEN and browser smoke**

```powershell
npm run test:single -- src/ui/Radio.compat.test.tsx src/pages/EvalPage.test.tsx
npm run lint -- --quiet
```

Run Playwright direct `/eval` with `{report:null}` and historical report fixtures; DOM and screenshot must complete within 10 seconds.

### Task 4: Notification default subscription status

**Files:**
- Modify: `server/enterprise_notification_api.py:498-515`
- Modify: `tests/test_enterprise_notification_api.py`
- Modify: `frontend/src/enterprise-notification-center/components/NotificationCenter.test.tsx` only for empty-versus-error projection.

**Interfaces:**
- GET without `status` sends `active` to the service.

- [ ] **Step 1: Write failing HTTP test**

```python
response = client.get("/api/enterprise/notification-subscriptions?limit=50", headers=_headers())
assert response.status_code == 200
assert response.json()["items"] == []
```

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest -q tests/test_enterprise_notification_api.py -k "subscription and default"
```

Expected: 422 `status is invalid`.

- [ ] **Step 3: Normalize omitted status**

Pass `status=status or "active"`; explicit invalid enum values remain FastAPI 422.

- [ ] **Step 4: Verify GREEN**

```powershell
uv run pytest -q tests/test_enterprise_notification_api.py
```

### Task 5: Content Recovery capability normalization

**Files:**
- Modify: `core/catalog_schema.py:4590-5026`
- Modify: `tests/test_catalog_schema.py`
- Modify: `tests/test_enterprise_content_recovery_migration.py`

**Interfaces:**
- Produces private helpers `_sql_boolean(value, label) -> bool` and `_json_object(value, label) -> dict[str, Any]` or equivalent.

- [ ] **Step 1: Write failing SQLite capability test**

Seed an active retention policy, recycled entry and two canonical events through the real service. Assert:

```python
state, issues = inspect_enterprise_content_recovery_capability(engine)
assert (state, issues) == ("ready", ())
```

Add malformed JSON and digest mismatch tests that remain unavailable.

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest -q tests/test_enterprise_content_recovery_migration.py tests/test_catalog_schema.py -k "content_recovery and sqlite"
```

Expected issues include invalid boolean flags and invalid canonical events.

- [ ] **Step 3: Normalize booleans and JSON**

Accept `False/True` and exact integer `0/1`; reject all other values. Parse JSON text to an object before canonical event validation.

- [ ] **Step 4: Verify GREEN**

```powershell
uv run pytest -q tests/test_enterprise_content_recovery_migration.py tests/test_enterprise_content_recovery_core.py tests/test_catalog_schema.py -k "content_recovery or recovery"
uv run ruff check core/catalog_schema.py tests/test_enterprise_content_recovery_migration.py
```

### Task 6: Documents recycle Dataset scope and response parity

**Files:**
- Modify: `frontend/src/enterprise-content-recovery/api/recoveryApi.ts:75-78,474-535`
- Modify: `frontend/src/enterprise-content-recovery/model/recoveryModel.ts:920-943`
- Modify: `frontend/src/pages/DocumentsPage.tsx:907-944`
- Modify: `frontend/src/enterprise-content-recovery/api/recoveryApi.test.ts`
- Modify: `frontend/src/pages/DocumentsPage.workspace.test.tsx`
- Modify: `frontend/src/enterprise-content-recovery/model/recoveryModel.test.ts`

**Interfaces:**
- `RecycleDocumentInput` includes `datasetId: string`.
- Payload includes `dataset_id`.
- Recovery mutation projector accepts/validates `route`.

- [ ] **Step 1: Write failing request and projection tests**

```ts
expect(JSON.parse(String(init.body))).toEqual({
  dataset_id: "dataset-a",
  expected_mutation_generation: 3,
  reason: "operator recycle",
});
```

Project a real server response containing `route: null` and assert state `applied`.

- [ ] **Step 2: Verify RED**

```powershell
cd frontend
npm run test:single -- src/enterprise-content-recovery/api/recoveryApi.test.ts src/enterprise-content-recovery/model/recoveryModel.test.ts src/pages/DocumentsPage.workspace.test.tsx
```

- [ ] **Step 3: Implement contract parity**

Require `datasetId`, update single/bulk callers, include `route` in exact keys, validate supplied route against the canonical approval handoff.

- [ ] **Step 4: Verify GREEN**

Run the focused tests and TypeScript:

```powershell
npm run test:single -- src/enterprise-content-recovery/api/recoveryApi.test.ts src/enterprise-content-recovery/model/recoveryModel.test.ts src/pages/DocumentsPage.workspace.test.tsx
npx tsc --noEmit
```

### Task 7: Automation drafts remain visible

**Files:**
- Modify: `frontend/src/enterprise-automation-workflows/hooks/useEnterpriseAutomation.ts`
- Modify: `frontend/src/enterprise-automation-workflows/AutomationPage.tsx` or controller component owning filters.
- Modify: `frontend/src/enterprise-automation-workflows/hooks/useEnterpriseAutomation.test.tsx`
- Modify: `frontend/src/enterprise-automation-workflows/AutomationPage.test.tsx`

**Interfaces:**
- Default rule list includes draft, active and paused non-archived records.
- Created draft becomes visible after refresh.

- [ ] **Step 1: Write failing test**

Return one draft after create and assert the rule name is visible instead of the empty state. Assert API request does not force `status=active` unless the user selected that filter.

- [ ] **Step 2: Verify RED**

```powershell
cd frontend
npm run test:single -- src/enterprise-automation-workflows/hooks/useEnterpriseAutomation.test.tsx src/enterprise-automation-workflows/AutomationPage.test.tsx
```

- [ ] **Step 3: Implement all-non-archived default/filter**

Keep summary counts unchanged. Expose explicit Draft/Active/Paused filters and select the created rule.

- [ ] **Step 4: Verify GREEN**

Run focused tests and `npx tsc --noEmit`.

### Task 8: CORS, skip link and shared DOM-prop cleanup

**Files:**
- Modify: `server/app.py:641-653`
- Modify: `frontend/src/App.tsx:863-1018`
- Modify: `frontend/src/ui/index.tsx:377-448,518-520`
- Modify: `frontend/src/App.a11y.test.tsx`
- Modify: `frontend/src/App.mobile.test.tsx`
- Add: `frontend/src/ui/FallbackProps.test.tsx`
- Add backend CORS regression test in the existing app test module.

**Interfaces:**
- Loopback allowlist contains both localhost and 127.0.0.1.
- Skip link is first tabbable.
- Fallback Select/Space/Progress consume framework-only props.

- [ ] **Step 1: Write failing tests**

Assert OPTIONS from both approved origins returns 200 with exact allow-origin; an unapproved origin does not.

Assert first Tab focuses `跳到主内容`, Enter focuses `#main-content`.

Render multiple Select and Progress/Space fallbacks and assert zero React console errors.

- [ ] **Step 2: Verify RED**

```powershell
uv run pytest -q tests -k "cors and vite"
cd frontend
npm run test:single -- src/App.a11y.test.tsx src/App.mobile.test.tsx src/ui/FallbackProps.test.tsx
```

- [ ] **Step 3: Implement minimal shared fixes**

Move skip link before repeated navigation in DOM order. Map facade props to native semantics/classes and do not spread unknown props.

- [ ] **Step 4: Verify GREEN**

Run focused backend/frontend tests, ESLint and TypeScript.

### Task 9: Stability integration gates

**Files:**
- Update audit evidence only under `output/playwright/full-ui-audit-20260830/`.
- Update `.planning/2026-08-30-full-ui-playwright-audit/progress.md`.

- [ ] **Step 1: Run backend focused suite**

```powershell
uv run pytest -q tests/test_enterprise_workspace_control.py tests/test_enterprise_workspace_api.py tests/test_retrieval_experiments_api.py tests/test_retrieval_experiment_runner.py tests/test_enterprise_notification_api.py tests/test_enterprise_content_recovery_migration.py tests/test_enterprise_content_recovery_api.py
```

- [ ] **Step 2: Run frontend focused suite**

```powershell
cd frontend
npm run test:single -- src/pages/EvalPage.test.tsx src/ui/Radio.compat.test.tsx src/ui/FallbackProps.test.tsx src/enterprise-content-recovery/api/recoveryApi.test.ts src/enterprise-content-recovery/model/recoveryModel.test.ts src/pages/DocumentsPage.workspace.test.tsx src/enterprise-automation-workflows/AutomationPage.test.tsx src/App.a11y.test.tsx
npm run lint
npx tsc --noEmit
npm run build
```

- [ ] **Step 3: Playwright stability proof**

Prove:

- `/eval` DOM and screenshot complete;
- retrieval comparison returns 201 and appears in history;
- notification subscriptions return empty/real state, not error;
- Documents recycle and Recovery restore complete in UI;
- automation draft remains visible;
- both dev origins connect;
- Query run emits zero React warnings.

- [ ] **Step 4: Review checkpoint**

```powershell
git diff --check
```

Record exact results and remaining interface work in the audit progress file.

## Self-review

- All ten functional stabilization requirements in the spec map to Tasks 1–8.
- Task 9 proves user-visible behavior rather than source text.
- Tests use writable/read-only real engines where the bug depends on engine capability.
- No database migration or unrelated rewrite is introduced.
- No placeholder, deferred error handling or ambiguous interface remains.
