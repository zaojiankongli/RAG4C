# Stage 12 OIDC SSO Runtime Implementation Plan

**Date:** 2026-08-26  
**Spec:** `docs/superpowers/specs/2026-08-26-oidc-sso-runtime-design.md`

## Task 1 — 0024 database authority
- Add OIDC login transaction, subject-link and SSO-session tables.
- Add digest/encryption/session lifecycle constraints and FKs.
- Update ORM, catalog manifest, readiness, runbook and migration tests.

## Task 2 — OIDC runtime backend
- Add Fernet/domain-separated key helper and injectable OIDC client.
- Implement start/callback/session revoke.
- Add active provider/domain/member checks, state/nonce/PKCE, subject binding, actor-token issue and audit.
- Add optional identity dependencies for Authlib/httpx if needed.

## Task 3 — TDesign runtime frontend
- Add OIDC runtime evidence, Start Login and callback result page.
- Clear code/state URL evidence immediately.
- Keep SAML runtime honestly not connected.

## Task 4 — Verification
- Security/migration/backend/frontend regression.
- Playwright start/callback/success/failure, desktop/dark/375/280, zero console errors and redacted evidence.

## Protected paths
Do not modify protected retrieval-quality or retrieval experiment files.

## Production prohibition
No real IdP login, production migration, unknown-account JIT provisioning, refresh-token storage, or external publishing.
