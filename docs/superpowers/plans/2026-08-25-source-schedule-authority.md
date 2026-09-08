# Enterprise Source Schedule Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add tenant/dataset/source-scoped, audited fixed-interval UTC scheduling that atomically creates durable scheduled source-sync runs and is recovered by the existing dispatcher.

**Architecture:** A new `source_schedules` authority table stores one revisioned schedule row per source. A focused `SourceScheduleRepository` owns scoped CAS writes, DB-clock next-slot calculation, and transactional due-run creation; the existing API exposes typed schedule operations and `SourceDispatchRuntime` invokes due enqueueing before reserving pending runs. Scheduled runs reuse the current durable execution outbox and generation fences, so no external submission occurs inside the schedule transaction.

**Tech Stack:** Python 3.10+, SQLAlchemy 2, Alembic, FastAPI/Pydantic v2, pytest, SQLite with MySQL/MariaDB/PostgreSQL-compatible DDL and locking.

**Spec:** User-approved bounded design in the August 25, 2026 task prompt.

## Global Constraints

- Fixed UTC intervals only; `interval_seconds` is constrained to `300..604800`.
- Do not claim or expose cron/calendar capability.
- One durable schedule authority row per source, status `active|paused|archived`, positive revision CAS.
- API and scheduler decisions use database UTC, not process wall clock.
- Scheduled run creation, schedule advancement, and audit append are one transaction; dispatch happens only after commit.
- Lock order is Dataset → Source → Schedule; SQLite uses `BEGIN IMMEDIATE`; MySQL/PostgreSQL use `SKIP LOCKED` where supported.
- Missed windows coalesce to one run and advance to the first future slot.
- Do not edit retrieval, frontend, or consistency modules.

---

### Task 1: Migration, ORM, and catalog manifest

**Files:**
- Create: `catalog_migrations/versions/0014_source_schedules.py`
- Modify: `models/orm.py`
- Modify: `core/catalog_schema.py`
- Create: `tests/test_source_schedule_migration.py`

**Interfaces:**
- Produces: `models.orm.SourceSchedule` and catalog head `0014_source_schedules`.
- Produces: portable `DATETIME(6)` schedule timestamps, scope FKs, checks, unique source authority, due/status/scope indexes.

- [ ] Write migration tests for SQLite upgrade/downgrade/re-upgrade, reflected constraints/indexes/FKs, DB checks, MySQL offline DDL, MariaDB datetime type, and populated round-trip.
- [ ] Run the new migration tests and confirm failure because revision `0014_source_schedules` and `SourceSchedule` do not exist.
- [ ] Implement the minimal migration, ORM model/export, and manifest additions.
- [ ] Run migration and catalog tests until green.
- [ ] Commit the schema slice without staging unrelated files.

### Task 2: Schedule repository CAS and due-run transaction

**Files:**
- Create: `core/source_schedules.py`
- Create: `tests/test_source_schedules.py`

**Interfaces:**
- Produces: `SourceScheduleRepository.get_scoped`, `put`, `pause`, `resume`, `archive`, and `enqueue_due`.
- Produces: `SourceScheduleConflict`, `SourceScheduleNotFound`, and `DueScheduleRun` result records.
- Consumes: `AuditContext`, `read_db_utc`, `SourceSyncRun`, and current source/dataset generation fields.

- [ ] Write failing tests for create/update CAS, revision conflicts, interval/status validation, scoped lookup, inactive source/dataset gates, atomic audit rollback, and DB-clock timestamps.
- [ ] Implement scoped writes with Dataset → Source → Schedule locks, positive revision increments, DB-time next-slot reset, archive durability, and secret-safe audit snapshots.
- [ ] Write failing tests for one-run-per-slot concurrency, repeated-poll idempotency, missed-window coalescing, revision-key separation, rollback without schedule advancement, inactive/paused/archived suppression, and crash/restart recovery.
- [ ] Implement due candidate scanning and per-schedule transactions, stable schedule id/revision/planned-at key/hash, generation fences, planned metadata in `cursor_before`, schedule advancement, last-run fields, and scheduler audit.
- [ ] Run repository tests until green and commit the authority slice.

### Task 3: Typed schedule API and OpenAPI contract

**Files:**
- Modify: `server/knowledge_sources_api.py`
- Modify: `tests/test_knowledge_sources_api.py`

**Interfaces:**
- Consumes: `SourceScheduleRepository`.
- Produces: GET/PUT/DELETE `/{source_id}/schedule` and POST pause/resume endpoints under the existing source router.

- [ ] Write failing API tests for READ vs MANAGE RBAC, tenant/dataset/source scoping, strict bodies, create/update CAS, pause/resume/archive behavior, inactive gates, typed responses/errors, audit atomicity, and OpenAPI fixed-interval capability wording.
- [ ] Implement strict Pydantic request/response models and route handlers with existing bearer/tenant auth and error envelopes.
- [ ] Run API tests until green and commit the API slice.

### Task 4: Dispatcher integration

**Files:**
- Modify: `server/source_dispatcher.py`
- Modify: `tests/test_source_dispatch_runtime.py`
- Modify: `tests/test_source_dispatch_lifecycle.py` only if the existing lifespan contract requires a focused assertion.

**Interfaces:**
- Consumes: `SourceScheduleRepository.enqueue_due(limit)` returning committed run IDs.
- Preserves: existing `SourceSyncLedger.reserve_dispatch_batch` and durable execution flow.

- [ ] Write failing runtime tests proving due schedules are enqueued before pending dispatch, committed runs survive submit failure/restart, repeated polls do not duplicate, and startup probes schedule authority.
- [ ] Integrate schedule enqueueing at the start of each poll cycle, then reserve/submit pending runs through the normal outbox.
- [ ] Run runtime and lifespan tests until green and commit the dispatcher slice.

### Task 5: Verification and review

**Files:**
- Review all touched paths; do not modify excluded subsystems.

- [ ] Run focused schedule migration/repository/API/runtime tests.
- [ ] Run full source-control, source-ledger, dispatcher, lifespan, catalog schema/integrity regressions.
- [ ] Run SQLite downgrade/re-upgrade tests and optional live MySQL test when configured.
- [ ] Run Ruff on touched Python files and the repository-configured lint target.
- [ ] Inspect `git diff`, verify unrelated retrieval changes remain unstaged, and perform a requirement-by-requirement review.
- [ ] Create review-ready commit(s) and report SHA(s), paths, tests, and the limitation that cron/calendar schedules remain future work.
