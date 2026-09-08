# Stage 9 Enterprise Identity Federation Implementation Plan

**Date:** 2026-08-26  
**Spec:** `docs/superpowers/specs/2026-08-26-enterprise-identity-federation-design.md`

## Task 1 — 0021 database authority

- Add domain, identity-provider, and SCIM-token tables.
- Add global domain uniqueness, one-active-provider, active-token-name, lifecycle checks/FKs/indexes.
- Update ORM, catalog manifest, readiness, upgrade tool and runbook.
- Add upgrade/downgrade/offline MySQL/preflight/damage-attribution tests.

## Task 2 — Identity control-plane backend

- Add generic identity mutation service using tenant mutation idempotency.
- Add injectable DNS TXT resolver and bounded production adapter.
- Implement domain create/verify/revoke.
- Implement IdP create/update/activate/disable with HTTPS/SSRF/secret-ref validation.
- Implement SCIM token issue/list/revoke with one-time raw token.
- Add audit, revision fencing, tenant role boundaries and stable error codes.

## Task 3 — TDesign identity center

- Add domain, IdP and SCIM model/API/hooks.
- Add Enterprise Identity Center surface and navigation.
- Add DNS challenge copy, IdP wizard, one-time SCIM token dialog and responsive cards/drawers.
- Expose honest control-plane/data-plane capability states.

## Task 4 — Verification

- Backend migration/security/regression tests.
- Frontend model/API/hook/component/page tests.
- Ruff, ESLint, Prettier, build and diff checks.
- Playwright desktop light/dark, 375/280, domain challenge, provider wizard, SCIM token issue/revoke, zero console errors and redacted evidence.

## Protected paths

Do not modify `frontend/src/retrieval-quality/**`, `core/retrieval_experiment_runner.py`, `server/retrieval_experiments_api.py`, or their protected tests.

## Production prohibition

No real production migration/write, DNS write, SSO login, SAML/OIDC callback, SCIM provisioning, SMTP or external publishing.
