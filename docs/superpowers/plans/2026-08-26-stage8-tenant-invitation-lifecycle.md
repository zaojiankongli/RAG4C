# Stage 8 Enterprise Invitation Lifecycle Implementation Plan

**Date:** 2026-08-26  
**Spec:** `docs/superpowers/specs/2026-08-26-tenant-invitation-lifecycle-design.md`

## Task 1 — 0020 database contract

- Add `0020_tenant_invitation_lifecycle.py` after 0019.
- Add invitation lifecycle columns, constraints, indexes, FKs, duplicate-pending preflight, and safe backfill.
- Add `tenant_control_mutation_requests` with `actor_id → accounts.id`, independent tenant FK, and tenant/actor/key uniqueness so pre-member acceptance side effects can be replayed safely.
- Update ORM, `core/catalog_schema.py`, readiness capability mapping, and enterprise upgrade runbook.
- Add migration upgrade/downgrade, offline MySQL DDL, manifest damage-attribution, and historical-data tests.

## Task 2 — Invitation mutation core and API

- Add `core/enterprise_invitation_mutations.py` and generic tenant idempotency primitives.
- Implement create, rotate/resend, revoke, and accept transactions.
- Add tenant/role/email/token/revision validation, row locks, expiry transition, audit, and replay.
- Add POST endpoints to `server/enterprise_access_graph_api.py` or a focused invitation router.
- Add strict 0020 migration gate and stable HTTP error codes.

## Task 3 — TDesign invitation frontend

- Extend invitation model/API projection with revision, send count, timestamps, and mutation responses.
- Add stable operation-key hook.
- Add create, one-time delivery, rotate-link, revoke, and accept components.
- Keep token only in transient component state.
- Add honest manual-delivery language and responsive cards/action drawer.

## Task 4 — Verification

- Migration/manifest/readiness tests.
- Backend tenant isolation, owner/admin role boundaries, duplicate pending, expiry, email match, token redaction, idempotency and rollback tests.
- Frontend API/hook/component/page tests.
- Ruff, ESLint, Prettier, build, diff check.
- Playwright desktop light/dark, 375/280, create → copy → rotate → revoke, and accept flows with zero console errors.

## Protected paths

Do not modify:

- `frontend/src/retrieval-quality/**`
- `core/retrieval_experiment_runner.py`
- `server/retrieval_experiments_api.py`
- `tests/test_retrieval_experiment_runner.py`
- `tests/test_retrieval_experiments_api.py`

## Production prohibition

Do not execute real Alembic migrations, MySQL writes, backups/restores, SMTP delivery, SSO/SCIM provisioning, or external publishing.
