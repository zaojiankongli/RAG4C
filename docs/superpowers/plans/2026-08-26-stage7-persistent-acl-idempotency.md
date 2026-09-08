# Stage 7 Persistent Dataset ACL and Idempotency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Dataset ACL enforcement durable and safe across retries by adding a persisted ACL mode source of truth, a tenant/actor-scoped idempotency ledger, and a revision-fenced tenant-owner/admin-only disable operation.

**Architecture:** `Dataset.acl_mode` is the authoritative mode whenever the 0019 schema is complete: `tenant_role` permits the existing fallback, while `dataset_acl` denies ordinary members without a matching active grant. Grant mutations lock the Dataset first, re-authorize inside the same writable transaction, enable ACL on the first grant (and keep it enabled after the last revoke), and write the grant change plus audit event plus idempotency record atomically. A separate idempotency service canonicalizes the request body, binds the key to tenant and actor, stores only a request hash and sanitized response, and replays the exact first response for an identical retry.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy ORM, Alembic, Pydantic, SQLite temporary test databases, MySQL offline DDL only.

**Spec:** User-provided Stage 7 requirements; existing `docs/superpowers/specs/2026-08-26-dataset-acl-enforcement-design.md` and `docs/superpowers/specs/2026-08-26-dataset-acl-mutations-design.md`.

## Global Constraints

- Do not modify `tests/test_dataset_acl_idempotency.py` if it exists; treat it as a protected contract.
- Do not connect to, migrate, write, back up, or restore any real database.
- Allowed production files: `core/enterprise_access_control.py`, `core/enterprise_access_mutations.py`, `server/enterprise_access_graph_api.py`, `server/knowledge_auth.py`, `server/app.py`; may add `core/enterprise_acl_idempotency.py` and the 0019 migration/ORM/schema files required by the dependency.
- Preserve retrieval-related uncommitted work and all existing Stage 4–6 behavior.
- Every ACL mutation requires a non-empty `Idempotency-Key`; `X-Request-ID` remains tracing only.
- Never persist Authorization headers, bearer tokens, cookies, JWTs, API keys, DSNs, raw request headers, or raw request bodies.
- Use Dataset-row-first locking for every ACL mutation and keep all authorization decisions tenant-scoped.

---

### Task 1: Establish Stage 7 RED contracts

**Files:**
- Create: `tests/test_dataset_acl_mode_stage7.py`
- Create: `tests/test_enterprise_acl_idempotency_stage7.py`
- Create: `tests/test_enterprise_access_graph_disable_stage7.py`
- Read-only: `tests/test_dataset_acl_idempotency.py` when present

**Interfaces:**
- Tests describe `Dataset.acl_mode`, `Dataset.acl_revision` (or the exact 0019 model contract discovered in the repository), the idempotency service, and the disable route without altering the protected test.

- [ ] Write failing tests for persistent mode truth, zero-grant deny, first-grant enable, last-revoke no fallback, explicit disable fencing/audit, mandatory idempotency, same-hash replay, different-hash conflict, tenant/actor isolation, and credential redaction.
- [ ] Run only these tests and record the expected RED failures.

### Task 2: Implement 0019 persistence contract

**Files:**
- Create/modify: `catalog_migrations/versions/0019_dataset_acl_persistence.py`
- Modify: `models/orm.py`
- Modify: `core/catalog_schema.py`
- Create/modify: migration contract tests (excluding protected idempotency test)

**Interfaces:**
- `Dataset` exposes persisted ACL mode and revision fields with database checks/defaults.
- New idempotency ledger model is tenant/actor scoped and stores canonical request hash plus replay-safe response data, never credentials.

- [ ] Add ORM and manifest fields/tables first with tests.
- [ ] Add Alembic upgrade/downgrade and offline MySQL/SQLite checks.
- [ ] Verify Alembic head is `0019_dataset_acl_persistence` without touching a real DB.

### Task 3: Add canonical idempotency ledger service

**Files:**
- Create: `core/enterprise_acl_idempotency.py`
- Test: `tests/test_enterprise_acl_idempotency_stage7.py`

**Interfaces:**
- Canonical JSON hashing is deterministic across object key order and rejects non-JSON-safe or oversized input.
- `Idempotency-Key` is required, bounded, and stored only with `(tenant_id, actor_id, key)`.
- Ledger reservation/replay/conflict decisions are transaction-local and safe under the Dataset-first lock order.

- [ ] Implement canonicalizer and SHA-256 request hash.
- [ ] Implement reserve/replay/conflict behavior with response snapshot allowlisting.
- [ ] Prove sensitive headers/body fields are never persisted.
- [ ] Run service tests to GREEN.

### Task 4: Make ACL mode durable and mutation-safe

**Files:**
- Modify: `core/enterprise_access_control.py`
- Modify: `core/enterprise_access_mutations.py`
- Test: Stage 7 ACL mode/mutation tests

**Interfaces:**
- Evaluator treats persisted `Dataset.acl_mode` as the mode source of truth when 0019 is valid.
- `dataset_acl` denies ordinary members with zero matching active grants; active tenant owner/admin and active Dataset owner retain scoped manager bypass.
- First create/any grant mutation locks Dataset and enables ACL with revision increment; revoke never implicitly disables or falls back.
- All mutations re-authorize in the same transaction and use revision fencing.

- [ ] Add mode-aware evaluator tests before implementation changes.
- [ ] Add Dataset-first lock and ACL revision updates to create/update/revoke/resume.
- [ ] Preserve same-transaction audit and rollback behavior.
- [ ] Run Stage 5/6 and Stage 7 evaluator/mutation tests.

### Task 5: Add explicit ACL disable endpoint and integration

**Files:**
- Modify: `server/enterprise_access_graph_api.py`
- Modify: `server/knowledge_auth.py`
- Modify: `server/app.py`
- Test: `tests/test_enterprise_access_graph_disable_stage7.py`

**Interfaces:**
- `POST /api/knowledge-bases/{dataset_id}/access-grants/disable` (or the established route form) requires active tenant owner/admin, `expected_revision`, non-empty reason, and `Idempotency-Key`.
- Disable updates persisted mode only with revision fencing, writes `dataset_acl.disabled` audit atomically, and returns a replayable response.
- Dataset-scoped manager permission never becomes tenant-wide enterprise management permission.

- [ ] Add route contract tests and observe RED.
- [ ] Implement resolver/dependency and error mapping.
- [ ] Verify GET routes never call writable engine; mutation provider remains lazy.

### Task 6: Full verification and security audit

**Files:**
- No new production scope unless a test exposes a required defect.

- [ ] Run protected test unchanged and all Stage 4–7 focused suites with temporary SQLite only.
- [ ] Run Ruff, Python compile, `git diff --check`, migration head, and offline MySQL DDL checks.
- [ ] Search diff and database columns for credential material.
- [ ] Confirm no `TEST_MYSQL_URL`/real database operation was used.
- [ ] Report remaining production migration/owner bootstrap blockers without claiming completion prematurely.
