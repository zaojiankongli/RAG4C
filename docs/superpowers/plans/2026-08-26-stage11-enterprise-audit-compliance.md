# Stage 11 Enterprise Audit Compliance Implementation Plan

**Date:** 2026-08-26  
**Spec:** `docs/superpowers/specs/2026-08-26-enterprise-audit-compliance-design.md`

## Task 1 — 0023 database authority

- Add retention policy, legal hold and export job tables.
- Add lifecycle, bounds, unique/FK/index contracts.
- Update ORM, catalog manifest, readiness, upgrade tool/runbook and migration tests.

## Task 2 — Compliance backend

- Implement policy read/update.
- Implement read-only retention preview and owner-only fingerprint-fenced execute.
- Implement legal hold create/list/release.
- Implement NDJSON/CSV export generation, integrity verification and download.
- Add audit, idempotency, path containment, CSV injection and rollback tests.

## Task 3 — TDesign compliance center

- Add model/API/hooks and Enterprise Compliance Center.
- Add policy, preview, legal hold and export workspaces.
- Add dangerous retention confirmation and responsive cards/drawers.

## Task 4 — Verification

- Stage 7–11 regression, Ruff/lint/build/diff.
- Playwright desktop light/dark, 375/280, export integrity and legal hold evidence.

## Protected paths

Do not modify protected retrieval-quality or retrieval experiment files.

## Production prohibition

No real production deletion, migration, backup/restore, scheduled cleanup, external storage write or publishing.
