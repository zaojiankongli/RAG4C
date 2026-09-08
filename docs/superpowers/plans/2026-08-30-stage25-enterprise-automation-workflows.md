# Stage 25 Enterprise Automation & Workflow Orchestration Implementation Plan

Date: 2026-08-30  
Status: Self-approved under standing user authorization  
Design: `docs/superpowers/specs/2026-08-30-enterprise-automation-workflows-design.md`  
Revision: `0035_enterprise_automation_workflows`

## Safety

- No production migration, event observation, cursor advancement, Run, dispatch or external action.
- Preserve the cumulative worktree and protected Retrieval Quality paths.
- Browser acceptance is loopback-only with fixture adapters.

### Task 1 — Migration / ORM / Catalog / Readiness

- [ ] Create exactly six Tenant-scoped tables.
- [ ] Immutable Rule Revision and Automation Event guards.
- [ ] Canonical identities, lifecycle checks, Tenant-leading FKs and indexes.
- [ ] SQLite online only; MySQL/PostgreSQL offline DDL; nonempty downgrade blocker.
- [ ] `enterprise_automation_workflows` readiness while preserving 0034 parents.

### Task 2 — Pure automation authority

- [ ] Canonical trigger, condition, action plan, Rule Revision, Run, Action Request and Event digests.
- [ ] Exact trigger/condition/action schemas and safe route projection.
- [ ] Reject arbitrary query, expression, SQL, URL, webhook, code, prompt, body, token, credential and ticket fields.

### Task 3 — Service / adapters

- [ ] Explicit trigger adapter registry for six initial sources.
- [ ] Stable preview and replay-safe observation.
- [ ] Rule CRUD, immutable revision creation, activation/pause and summary/list/detail reads.
- [ ] Cursor lease, concurrency, idempotency and Tenant isolation.
- [ ] Generate bounded Action Requests only; do not dispatch source effects.

### Task 4 — Strict API / preflight

- [ ] Read/mutation engine separation and strict Pydantic models.
- [ ] Mount approved routes; no generic ingest/execute endpoint.
- [ ] Read-only six-table upgrade preflight and operations runbook.

### Task 5 — Frontend model/API/hook

- [ ] Strict Rule/Revision/Run/Request/Event/Summary models.
- [ ] AbortSignal, context fences, lazy detail/history, serialized mutations and retained keys.
- [ ] Exact partial/unavailable/empty/read-only states.

### Task 6 — TDesign Automation Center

- [ ] WHEN -> IF -> REQUEST -> EVIDENCE rail and Attention Board.
- [ ] Rules/Runs/Requests/Activity tabs, desktop/mobile surfaces.
- [ ] Rule Builder, preview, detail Drawer and immutable revision timeline.
- [ ] Keyboard/Escape/focus return, 375/280 and dark/light.

### Task 7 — App integration / review / Playwright

- [ ] `/enterprise/automations` direct/hash route and `自动化中心` nav.
- [ ] Safe handoff to Task/Notification/Approval/Quality/Source pages only.
- [ ] Independent security review and Critical/Important fixes.
- [ ] Backend, full frontend runner, TypeScript, Prettier, build and Alembic head.
- [ ] Playwright 12-matrix + bounded scenarios + fresh artifacts + standalone gate.

This plan is self-approved; no user approval checkpoint is required.
