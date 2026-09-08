# Stage 17 Enterprise Workspace Authorization Implementation Plan

**Date:** 2026-08-27  
**Spec:** `docs/superpowers/specs/2026-08-27-enterprise-workspace-authorization-design.md`  
**Target revision:** `0027_enterprise_workspace_authorization`

## Task 1 — 0027 authorization authority

- Add `tenant_workspace_authorization_policies` and all constraints/indexes.
- Extend approval action checks with `workspace_authorization_mode_change`.
- Backfill active Workspaces to shadow and archived Workspaces to disabled.
- Update ORM, Catalog Schema, Readiness and migration tests.
- Prove long IDs, cross-tenant isolation, exact checks and downgrade behavior.

## Task 2 — Workspace authorization evaluator

- Add the fixed permission model version 1.
- Compute disabled/shadow/enforced Workspace evidence from active policies, Workspaces, members and bindings.
- Extend `DatasetAccessDecision` without changing the meaning of `enforcement_mode`.
- Union permissions only in enforced mode.
- Add immediate revocation, multi-Workspace union and partial-schema fail-closed tests.

## Task 3 — Policy lifecycle Core/API

- Add policy read and impact preview.
- Add revision-fenced, idempotent and audited mode mutation.
- Create shadow policy transactionally for new Workspaces.
- Mount strict authenticated HTTP routes.
- Add real 0027 SQLite router integration.

## Task 4 — Approval-gated mode change

- Add approval action/resource contracts and scope matching.
- Block direct mutation when an active policy requires approval.
- Add one-time execution consumer with Workspace and policy revision re-checks.
- Add retry, non-manager and stale-revision tests.

## Task 5 — TDesign Permissions Rollout Center

- Add policy authority strip, role matrix and impact preview.
- Add direct/approval mode-change dialogs and Approval Center navigation.
- Preserve honest unavailable/shadow/enforced states.
- Add desktop table, 375/280 cards, dark theme and accessibility tests.

## Task 6 — Operations and verification

- Update enterprise catalog upgrade runbook for 0027.
- Run Stage 7–17 backend regression.
- Run Ruff, format, py_compile, Alembic head, lock and diff checks.
- Run enterprise frontend tests, Prettier, ESLint, TypeScript and production build.
- Run Playwright direct/hash, desktop light/dark, 375/280, Drawer/Dialog, console/page/unknown request checks.
- Perform independent final database/security and UI reviews.

## Write-scope coordination

- Database worker: migration, ORM, Catalog Schema, Readiness and migration tests.
- Authorization worker: Dataset evaluator, policy Core/API, Workspace creation integration and tests.
- Approval worker: Approval contracts/consumer and approval-gated tests.
- Frontend worker: enterprise-workspace rollout UI and tests.
- Verification worker: runbook, Playwright evidence and read-only reviews.

## Production prohibition

No real production migration, Workspace policy backfill, Enforced activation, approval execution, restore or external publication.
