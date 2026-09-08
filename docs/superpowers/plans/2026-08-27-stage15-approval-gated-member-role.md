# Stage 15 Approval-Gated Member Role Implementation Plan

**Date:** 2026-08-27  
**Spec:** `docs/superpowers/specs/2026-08-27-approval-gated-member-role-design.md`

## Task 1 — Backend policy gate and consumer

- Add transactional policy enforcement to member role mutation.
- Add sanitized 409 policy-required response.
- Add member_role_change approval consumer and production registry.
- Cover direct/no-policy, stale precheck, approved execution, stale revision, last owner, replay and role authorization.

## Task 2 — Member Directory approval UX

- Add policy lookup for role action only.
- Preserve direct role mutation with no policy.
- Submit approval snapshot with policy evidence when required.
- Show request status and approval-center navigation without changing local role.

## Task 3 — Approval execution extension

- Allow connected member_role_change tickets in the transient execution dialog.
- Consume complete scope and keep ticket non-persistent/retry-safe.

## Task 4 — Verification

- Stage 7–15 backend regression.
- Enterprise admin/approval frontend tests, Prettier, ESLint, build.
- Playwright desktop light/dark and 375/280 request/execute flow.

## Production prohibition

No real member role mutation, production migration, backup, restore or external publication.
