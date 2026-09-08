# Stage 10 SCIM Provisioning Data Plane Implementation Plan

**Date:** 2026-08-26  
**Spec:** `docs/superpowers/specs/2026-08-26-scim-provisioning-data-plane-design.md`

## Task 1 — 0022 database authority

- Add SCIM user/group link tables and token usage evidence.
- Add tenant-leading unique/FK/check/index contracts.
- Update ORM, catalog manifest, readiness, upgrade tool/runbook and migration tests.

## Task 2 — SCIM v2 backend

- Add SCIM bearer-token dependency and scope checks.
- Add ServiceProviderConfig, ResourceTypes and Schemas.
- Add Users and Groups list/create/get/patch/delete.
- Add exact filter parser, pagination, SCIM errors, ETags and If-Match.
- Add audit, token-use evidence and rollback tests.

## Task 3 — Identity-center runtime evidence

- Update model/API/hooks/UI for `scim_data_plane_ready`.
- Show endpoint, resources, scopes and token usage evidence.
- Add desktop/mobile/dark states and copy actions.

## Task 4 — Verification

- Migration/backend protocol/security tests.
- Stage 7–10 regression.
- Frontend tests/build/lint.
- Playwright desktop light/dark and 375/280, zero console errors and redacted evidence.

## Protected paths

Do not modify protected retrieval-quality files or retrieval experiment backend/tests.

## Production prohibition

No real production migration/write, external SCIM provider calls, SSO login, SMTP, backup/restore or external publishing.
