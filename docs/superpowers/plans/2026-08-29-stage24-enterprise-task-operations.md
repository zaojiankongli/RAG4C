# Stage 24 Enterprise Task Operations Center Implementation Plan

Date: 2026-08-29
Status: Completed and repository-wide verified 2026-08-30
Design: `docs/superpowers/specs/2026-08-29-enterprise-task-operations-design.md`
Revision: `0034_enterprise_task_operations`

## Safety

- No production migration, reconcile, retry, cancel or queue mutation.
- Preserve cumulative worktree and protected Retrieval Quality paths.
- Browser evidence is loopback-only and source-bound.

### Task 1: Migration / ORM / Catalog / Readiness

- [x] Create 0034 exactly five tables and Tenant-leading constraints.
- [x] Add immutable Event guards, canonical current projection/action/view identities.
- [x] MySQL/PostgreSQL offline DDL; SQLite offline fail-closed.
- [x] Downgrade blockers.
- [x] Add `enterprise_task_operations` capability; preserve 0033 readiness.

### Task 2: Pure Task Authority

- [x] Canonical source digest/projection digest/Event/action/Saved View filters/routes.
- [x] Strict source-kind/status/action allow-lists.
- [x] Reject raw payload, query, result body, note, ticket, token, credential and URLs.

### Task 3: Reconciliation Adapters and Service

- [x] Adapters for seven initial source kinds.
- [x] Stable replay-safe reconciliation and currentness fences.
- [x] Reconciliation Run evidence and concurrency tests.
- [x] Read summary/list/detail/events.

### Task 4: Safe Operator Actions

- [x] Explicit adapter registry only.
- [x] retry/cancel/acknowledge lifecycle, actor authorization and idempotency.
- [x] Unsupported pairs fail closed.
- [x] Source revision/digest revalidation before dispatch.

### Task 5: Strict API / App Mount / Preflight

- [x] Strict Pydantic contracts and read/mutation engine separation.
- [x] Mount all approved routes.
- [x] Read-only 0034 preflight and runbook.

### Task 6: Frontend Model / API / Hook

- [x] Strict Task/Action/Event/View/Reconciliation/Summary models.
- [x] AbortSignal, context fences, lazy detail/events/views, serialized mutations.
- [x] Exact partial/unavailable/read-only states.

### Task 7: TDesign Task Center

- [x] Attention Board and SOURCE -> QUEUE -> ATTEMPT -> OUTCOME rail.
- [x] Table/mobile cards, filters, Saved Views, detail/event drawer.
- [x] Retry/Cancel dialogs and reconciliation panel.
- [x] Keyboard/Escape/focus return and 375/280.

### Task 8: App Integration

- [x] Add `/enterprise/tasks` direct/hash route and primary nav.
- [x] Safe source handoff to Documents/Sources/Compliance/Quality.
- [x] Do not add generic source mutation fallback.

### Task 9: Review / Regression / Playwright

- [x] Independent security review and Critical/Important fixes.
- [x] Backend integration, frontend tests/tsc/Prettier/build.
- [x] Playwright direct/hash × light/dark × 1440/375/280.
- [x] Cover seven source kinds, actions, stale/unavailable/partial/empty/read-only/scope switch.
- [x] Zero console/page/unknown/leak/overflow; fresh result/manifest/PNG; standalone gate.
- [x] Ruff, py_compile, Alembic head and git diff check.

The plan is self-approved under the user's standing authorization.

## Integration note

Stage24-focused and repository-wide gates are green. `DocumentsPage.workspace.test.tsx` is 35/35, and the unified `npm test` runner executes all 245 frontend test files with a manifest-bound isolated process strategy. No merge, push, cleanup or production operation was performed.
