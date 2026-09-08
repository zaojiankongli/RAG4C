# Stage 27 Implementation Plan — Enterprise Knowledge Operations & Feedback Center

Date: 2026-08-30
Revision: `0037_enterprise_knowledge_operations_feedback`
Approval: self-approved recommended design under standing user authorization

## Task 1 — Tencent and codebase evidence

- [ ] Complete Tencent Application Operations Playwright evidence.
- [ ] Record UI reference, database security and codebase gap analysis.
- [ ] Freeze Stage27 vocabulary and safety boundaries.

## Task 2 — Pure authority core (TDD)

- [ ] Canonical profile/session/query/feedback/review/event/candidate projectors.
- [ ] Domain-separated digests and event chains.
- [ ] Safe preview mapping with no raw content, URI, SQL, prompt or credential.
- [ ] State and route allow-lists.

## Task 3 — Migration / ORM / Catalog / Readiness (TDD)

- [ ] Add exactly seven tables in 0037.
- [ ] Add Tenant/Profile/Dataset ownership constraints.
- [ ] Add immutable guards and PostgreSQL/MySQL/SQLite parity.
- [ ] Add Catalog manifest and readiness data validation.
- [ ] Add guarded downgrade.

## Task 4 — Service / API / query adapter (TDD)

- [ ] Build read operations and review/candidate mutations.
- [ ] Add idempotency and revision fences.
- [ ] Add internal zero-content query fact adapter after delivery commit.
- [ ] Add authenticated API and readiness gate.
- [ ] Wire app and enterprise context capability.

## Task 5 — Frontend strict model/API/Hook (TDD)

- [ ] Strict DTOs and safe display projection.
- [ ] AbortSignal/generation/context reset.
- [ ] Read-only and capability fail-closed behavior.
- [ ] Exact internal handoffs.

## Task 6 — TDesign enterprise UI (TDD)

- [ ] Resource Shell `operations` section.
- [ ] Evidence Strip and Operational Learning Rail.
- [ ] Desktop PrimaryTable / mobile cards / Filter Drawer.
- [ ] Review Drawer and Candidate decision Dialog.
- [ ] Focus return, keyboard tabs, dark/light and reduced motion.

## Task 7 — Verification

- [ ] Independent security review Critical 0 / Important 0.
- [ ] Focused backend/frontend suites.
- [ ] Cumulative Catalog/Readiness and enterprise core suites.
- [ ] Full frontend manifest runner and production build.
- [ ] Alembic head / Ruff / py_compile / Prettier / tsc / diff-check.
- [ ] Fresh Playwright matrix, scenarios and standalone gate.
