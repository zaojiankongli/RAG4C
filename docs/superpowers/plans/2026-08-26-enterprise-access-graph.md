# Enterprise Access Graph Implementation Plan

**Date:** 2026-08-26

## Goal

Add a real, tenant-isolated, read-only enterprise access graph to RAG4C while preserving the existing fixed-role security boundary and production database safety gates.

## Task 1 — 0017 schema

- Add organization units, groups, group members, dataset grants, and invitations.
- Mirror every column, constraint, index, and foreign key in ORM and schema manifest.
- Verify SQLite 0016 → 0017 → 0016 → 0017.
- Keep real MySQL untouched.

## Task 2 — API contract tests

- Establish authenticated, tenant-isolated GET contracts.
- Verify keyset pagination and bounded filters.
- Verify cross-tenant denial and 0017-missing 503.
- Verify invitation token hashes never leave the server.

## Task 3 — backend projection

- Implement read-only SQLAlchemy projections in a dedicated core module.
- Build an injected read-engine router.
- Mount it with the same engine used by KnowledgeActor authorization.
- Update capability projection and readiness to describe read-only support honestly.

## Task 4 — frontend access graph

- Add a TDesign tabbed enterprise surface for organization, groups, dataset ACL, and invitations.
- Add authoritative banner, compact counts, filters, tables/cards, and keyset load-more.
- Do not request unavailable capabilities and do not fabricate data.
- Verify 1440, 375, 280, light, and dark layouts.

## Task 5 — integration and audit

- Run focused frontend and backend suites, lint, build, Alembic heads, Ruff, and diff checks.
- Capture Playwright screenshots and inspect overflow/contrast.
- Record remaining mutation, SSO, secure token storage, and real migration blockers.
