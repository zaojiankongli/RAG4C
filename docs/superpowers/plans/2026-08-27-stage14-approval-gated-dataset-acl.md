# Stage 14 Approval-Gated Dataset ACL Implementation Plan

**Date:** 2026-08-27  
**Spec:** `docs/superpowers/specs/2026-08-27-approval-gated-dataset-acl-design.md`

## Task 1 — Backend policy gate and consumer

- Add active policy resolution.
- Block direct ACL disable when a matching policy is active.
- Add action-keyed adapter registry and enrich approval adapter payload.
- Implement Dataset ACL consumer with deterministic idempotency.
- Add direct/no-policy/approved/stale/replay/audit tests.

## Task 2 — Dataset ACL approval UX

- Add policy lookup and request submission to the ACL disable dialog.
- Keep direct flow when no policy exists.
- Add request-created evidence and approval-center navigation.

## Task 3 — Approval execution UX

- Preserve final approval ticket in memory only.
- Add explicit connected-action execution dialog.
- Consume ticket and refresh execution status.
- Test ticket non-persistence and complete scope body.

## Task 4 — Verification

- Stage 7–14 backend regression.
- Enterprise approval/access frontend tests, Prettier, ESLint and build.
- Playwright desktop light/dark/375/280 and request→approval→execute mocked flow.

## Production prohibition

No real ACL mutation, production migration, backup, restore or external publication.
