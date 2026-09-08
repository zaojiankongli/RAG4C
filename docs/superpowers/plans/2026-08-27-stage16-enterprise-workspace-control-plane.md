# Stage 16 Enterprise Workspace Control Plane Implementation Plan

**Date:** 2026-08-27  
**Spec:** `docs/superpowers/specs/2026-08-27-enterprise-workspace-control-plane-design.md`

## Task 1 — 0026 workspace authority

- Add workspace/member/dataset binding tables and constraints.
- Backfill deterministic default workspaces, members and Dataset primary bindings.
- Update ORM, catalog manifest, readiness and migration tests.

## Task 2 — Workspace lifecycle backend

- Implement read models and create/update/archive/member/binding mutations.
- Add tenant-safe permissions, last owner, revision, idempotency and audit rollback.
- Mount production router.

## Task 3 — TDesign Workspace Center

- Add enterprise-workspace model/API/hooks/components/CSS.
- Add route, evidence strip, table/cards, detail drawer and dialogs.
- Integrate honest workspace selector and authorization-not-enforced state.

## Task 4 — Verification

- Update runbook for 0026 and 19 migration steps.
- Stage 7–16 backend regression, Ruff, lock and migration checks.
- Frontend tests, Prettier, ESLint, build.
- Playwright desktop light/dark and 375/280.

## Production prohibition

No real production migration, workspace backfill, membership change, Dataset rebinding or external publication.
