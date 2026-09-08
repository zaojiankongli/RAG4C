# Stage 23 Enterprise Content Recovery Center Implementation Plan

Date: 2026-08-29
Status: Approved by standing user authorization
Design: `docs/superpowers/specs/2026-08-29-enterprise-content-recovery-design.md`
Revision: `0033_enterprise_content_recovery`

## Safety and write boundaries

- Do not run a real production migration, recycle, restore, legal hold, Approval execution, purge or durable delete.
- Do not reset, clean, broadly format or commit the cumulative worktree.
- Protected Retrieval Quality paths remain untouched.
- All browser evidence must be loopback-only and source-bound.

### Task 1: 0033 Migration, ORM, Catalog and Readiness

Files:

- Create `catalog_migrations/versions/0033_enterprise_content_recovery.py`
- Modify `models/orm.py`
- Modify `core/catalog_schema.py`
- Modify `server/enterprise_readiness_api.py`
- Add focused tests.

- [x] Write RED tests for exactly five tables, revision chain and Document lifecycle update.
- [x] Test Tenant-leading FKs, canonical keys, immutable Event guards and downgrade blockers.
- [x] Implement migration and ORM.
- [x] Add `enterprise_content_recovery` readiness with canonical data checks.
- [x] Keep Stage 22 and earlier capabilities ready at 0033.
- [x] Run pytest/Ruff/format/py_compile/Alembic head.

### Task 2: Pure Recovery Authority

Files:

- Create `core/enterprise_content_recovery.py`
- Create `tests/test_enterprise_content_recovery_core.py`

- [x] Write RED tests for recycle keys, snapshots, purge request digest and recovery Event hash chain.
- [x] Reject raw content, metadata, URL, query, note, ticket, token and credentials.
- [x] Implement pure functions without ORM imports.
- [x] Run focused tests and static checks.

### Task 3: Recovery Read and Mutation Services

Files:

- Create `core/enterprise_content_recovery_service.py`
- Create focused lifecycle tests.

- [x] Test Tenant/Dataset/Document isolation.
- [x] Test recycle/restore lifecycle and mutation_generation fences.
- [x] Test retrieval disable/restore behavior.
- [x] Test legal hold apply/release.
- [x] Test retention and purge eligibility fences.
- [x] Test idempotency, concurrency and atomic Event append.
- [x] Prove recycle/restore never invoke durable delete.

### Task 4: Approval-gated Purge Request

Files:

- Extend Approval action allow-lists with `document_purge`.
- Add purge Approval integration tests.

- [x] Create strict safe Approval Request snapshot.
- [x] Reject active holds and pre-retention requests.
- [x] Reject client execution tickets.
- [x] Support safe Approval Center handoff only.
- [x] Do not execute physical purge in Stage 23.

### Task 5: Strict Recovery API and App Mount

Files:

- Create `server/enterprise_content_recovery_api.py`
- Modify `server/app.py`
- Add API tests.

- [x] Strict Pydantic extra rejection and exact integers/booleans.
- [x] Read/mutation engine separation.
- [x] Actor-only mutation and Idempotency-Key on every mutation.
- [x] Safe HTTP error projection.
- [x] Mount all approved routes and run app regression.

### Task 6: Read-only Preflight and Runbook

Files:

- Modify `scripts/enterprise_catalog_upgrade.py`
- Modify `tests/test_enterprise_catalog_upgrade.py`
- Modify `docs/operations/enterprise-catalog-upgrade.md`

- [x] Add five-table counts and lifecycle blockers.
- [x] Add recycled Document/retrieval/hold/purge/Event consistency checks.
- [x] Integrate into `safe_to_upgrade` without mutation.
- [x] Document online/offline/downgrade boundaries.

### Task 7: Frontend Model, API and Hook

Files:

- Create `frontend/src/enterprise-content-recovery/model/**`
- Create `frontend/src/enterprise-content-recovery/api/**`
- Create `frontend/src/enterprise-content-recovery/hooks/**`

- [x] Strict Entry/Policy/Hold/Purge/Event/Summary projectors.
- [x] Safe route projection and exact unavailable/partial states.
- [x] Stale response and scope generation fences.
- [x] Serialized actor-only mutations and read-only guards.
- [x] Focused Vitest/Prettier/TypeScript.

### Task 8: TDesign Recovery Center

Files:

- Create `frontend/src/enterprise-content-recovery/components/**`
- Create `frontend/src/enterprise-content-recovery/content-recovery.css`

- [x] Header, Metric Strip and Recovery Lifecycle Rail.
- [x] Desktop table vs mobile cards without duplicate DOM.
- [x] Entry Detail Drawer and immutable Event timeline.
- [x] Restore, Legal Hold, Hold Release and Purge Approval dialogs.
- [x] Retention Policy panel.
- [x] Explicit partial/unavailable/empty/read-only states.
- [x] Keyboard/Escape/focus return and 375/280 tests.

### Task 9: Documents and App-shell Integration

Files:

- Modify `frontend/src/App.tsx`
- Modify `frontend/src/run/appRoute.ts`
- Modify `frontend/src/pages/DocumentsPage.tsx`
- Modify related tests.

- [x] Add direct/hash `/enterprise/recycle-bin` route and primary nav entry.
- [x] Replace UI permanent delete with “移入回收站” when capability ready.
- [x] Disable destructive action when recovery capability is unavailable.
- [x] Never fall back to durable delete.
- [x] Add restore and Approval handoff navigation.
- [x] Run App/a11y/mobile/Documents tests and build.

### Task 10: Reviews, Playwright and Final Gates

- [x] Independent backend security review.
- [x] Fix all Critical/Important findings with regression tests.
- [x] Backend Stage 23 integration suite.
- [x] Frontend focused tests, TypeScript, Prettier and build.
- [x] Source-bound Playwright matrix:

```text
direct/hash × light/dark × 1440/375/280
```

- [x] Cover recycle/restore/hold/release/retention/purge approval/stale/unavailable/partial/empty/read-only/scope switch.
- [x] Zero console/page/unknown request/errors, sensitive leaks and horizontal overflow.
- [x] Fresh result/manifest/PNG and standalone fail-closed gate tests.
- [x] `git diff --check`, Ruff, py_compile and Alembic head.

## Self-review

- The plan builds a recovery governance layer in front of existing durable deletion rather than duplicating it.
- Permanent purge remains approval-gated and unexecuted in Stage 23.
- Database, API, UI and Playwright requirements map to explicit tasks.
- The plan is self-approved under the user's standing authorization and proceeds with subagent-driven TDD.
