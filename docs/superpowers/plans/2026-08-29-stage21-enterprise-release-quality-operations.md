# Stage 21 Enterprise Release Quality Operations Implementation Plan

**Date:** 2026-08-29  
**Design:** `docs/superpowers/specs/2026-08-29-enterprise-release-quality-operations-design.md`  
**Revision:** `0031_enterprise_release_quality_operations`  
**Down revision:** `0030_enterprise_release_quality_certification`

## Guardrails

- Do not run a production migration, SLO scan, Certification, Waiver, Approval, promotion, rollback, pin, source sync, restore or destructive operation.
- Do not modify protected Retrieval Quality paths:

```text
frontend/src/retrieval-quality/**
core/retrieval_experiment_runner.py
server/retrieval_experiments_api.py
tests/test_retrieval_experiment_runner.py
tests/test_retrieval_experiments_api.py
```

- Do not reset, checkout, clean or broadly format the cumulative worktree.
- Every production function is implemented with test-first RED -> GREEN -> REFACTOR.
- All persistent facts are Tenant-leading, body-free, secret-safe and revision/digest fenced.

## Task 1 — Research, Spec and Plan

**Files:**

- `docs/research/2026-08-29-stage21-quality-operations-db-security.md`
- `docs/research/2026-08-29-stage21-tencent-quality-operations-ui-reference.md`
- `docs/superpowers/specs/2026-08-29-enterprise-release-quality-operations-design.md`
- `docs/superpowers/plans/2026-08-29-stage21-enterprise-release-quality-operations.md`

**Acceptance:**

- Tencent Playwright patterns are translated, not copied;
- six tables, APIs, UI, security, testing and production boundaries agree;
- no TODO/TBD, contradictory state names or missing authority ownership.

## Task 2 — 0031 Migration, ORM, Catalog and Readiness

**Files:**

- `catalog_migrations/versions/0031_enterprise_release_quality_operations.py`
- `models/orm.py`
- `core/catalog_schema.py`
- `server/enterprise_readiness_api.py`
- `tests/test_enterprise_release_quality_operations_migration.py`
- `tests/test_enterprise_release_quality_operations_orm.py`
- `tests/test_catalog_schema.py`
- `tests/test_enterprise_readiness_api.py`

**RED coverage:**

- exactly six new tables;
- canonical active SLO Policy scope;
- one active Schedule per SLO Policy;
- deterministic unique Schedule/planned Run slot;
- Run lease and terminal-state CHECKs;
- immutable Observation UPDATE/DELETE guards;
- Alert canonical active identity and exact lifecycle evidence;
- one non-terminal Recertification Job authority;
- exact integer minute/count thresholds;
- Tenant-leading composite FKs and target uniques;
- safe digest/timestamp/JSON checks;
- MySQL/PostgreSQL offline DDL and SQLite offline fail closed;
- nonempty 0031 authority blocks downgrade;
- 0030 capabilities remain ready at 0031;
- readiness capability key `enterprise_release_quality_operations`.

**Verification:**

```powershell
uv run pytest -q `
  tests/test_enterprise_release_quality_operations_migration.py `
  tests/test_enterprise_release_quality_operations_orm.py `
  tests/test_catalog_schema.py `
  tests/test_enterprise_readiness_api.py
```

## Task 3 — Pure SLO Evaluation and Canonical Digests

**Files:**

- `core/enterprise_release_quality_operations.py`
- `tests/test_enterprise_release_quality_operations_core.py`

**Interfaces:**

```python
canonical_operations_digest(...)
resolve_slo_policy_projection(...)
evaluate_release_quality_slo(...)
canonical_observation(...)
```

**RED coverage:**

- exact channel -> risk tier -> global ordering;
- UTC microsecond normalization;
- exact signed integer minutes;
- healthy/warning/critical/unavailable boundaries;
- passing Certification horizons;
- active/expiring/expired Waiver;
- blocked, stale and unavailable Gate;
- low-risk `not_required` compatibility;
- malformed timestamps, booleans, digests and scope fail closed;
- no query/body/note/ticket/credential enters canonical values;
- deterministic observation and summary digests.

## Task 4 — SLO Policy and Scan Schedule Authority

**Files:**

- `core/enterprise_release_quality_operation_mutations.py`
- `core/enterprise_release_quality_scheduler.py`
- `tests/test_enterprise_release_quality_slo_policy.py`
- `tests/test_enterprise_release_quality_scan_schedule.py`

**Interfaces:**

```python
create_quality_slo_policy(...)
update_quality_slo_policy(...)
list_quality_slo_policies(...)
create_quality_scan_schedule(...)
update_quality_scan_schedule(...)
pause_quality_scan_schedule(...)
resume_quality_scan_schedule(...)
archive_quality_scan_schedule(...)
```

**RED coverage:**

- owner/admin-only Tenant policy and schedule mutations;
- exact canonical scope and active uniqueness;
- warning > critical threshold validation;
- revision fences, audit and generic Tenant idempotency replay;
- one active schedule per Tenant/Dataset/policy;
- fixed interval and UTC next slot;
- disabled Policy blocks active Schedule;
- read routes use `knowledge.read` and sanitize all fields.

## Task 5 — Scan Run, Observation, Alert and Job Lifecycle

**Files:**

- `core/enterprise_release_quality_scheduler.py`
- `core/enterprise_release_quality_alerts.py`
- `core/enterprise_release_quality_recertification.py`
- `tests/test_enterprise_release_quality_scan_run.py`
- `tests/test_enterprise_release_quality_observation.py`
- `tests/test_enterprise_release_quality_alerts.py`
- `tests/test_enterprise_release_quality_recertification.py`
- concurrency tests as required

**Interfaces:**

```python
enqueue_due_quality_scans(...)
claim_quality_scan_run(...)
heartbeat_quality_scan_run(...)
execute_quality_scan_run(...)
finish_quality_scan_run(...)
acknowledge_quality_alert(...)
resolve_quality_alert(...)
suppress_quality_alert(...)
queue_recertification_job(...)
claim_recertification_job(...)
mark_recertification_ready(...)
complete_recertification_job(...)
cancel_recertification_job(...)
```

**RED coverage:**

- deterministic Dataset-scoped schedule slot replay and no duplicate Runs;
- claim lease, heartbeat, stale owner and retry behavior;
- active Channel bindings and pinned Application targets;
- Stage 20 Gate revalidation under fixed lock order;
- immutable one-per-run Observation identity and digest;
- Alert create/update/system-resolve semantics;
- resolved Alert never mutates back to open;
- suppress does not change Quality Gate or publication eligibility;
- auto-queue creates a Job only, never a Certification;
- one non-terminal Job per canonical authority plus immutable `cycle_key` coalescing;
- Job state machine, lease, retry, cancel and terminal replay;
- explicit operator completion delegates to Stage 20 `certify_release(...)`;
- cross-Tenant, stale manifest/evidence/policy/channel and forged facts fail closed;
- no raw idempotency key, query, result body, Judgment note or ticket leakage.

## Task 6 — Strict Operations API

**Files:**

- `server/enterprise_release_quality_operations_api.py`
- `server/app.py`
- `tests/test_enterprise_release_quality_operations_api.py`

**Routes:**

```text
GET/POST/PATCH /api/enterprise/release-quality/slo-policies
GET/POST/PATCH /api/enterprise/release-quality/scan-schedules
GET /api/enterprise/release-quality/scan-runs
POST /api/enterprise/release-quality/scan-runs/{run_id}/cancel
GET /api/enterprise/knowledge-bases/{dataset_id}/quality-operations/summary
GET /api/enterprise/knowledge-bases/{dataset_id}/quality-observations
GET /api/enterprise/knowledge-bases/{dataset_id}/quality-alerts
POST .../quality-alerts/{alert_id}/acknowledge
POST .../quality-alerts/{alert_id}/resolve
POST .../quality-alerts/{alert_id}/suppress
GET/POST .../recertification-jobs
POST .../recertification-jobs/{job_id}/cancel
POST .../quality-operations/scan
```

**RED coverage:**

- strict Pydantic schemas and extra-field rejection;
- exact integer/bool/timestamp validation;
- owner/admin vs Dataset manage/read permission separation;
- mutation/read engine separation;
- Idempotency-Key required on every mutation;
- stable opaque cursor and limit validation;
- safe domain error projection and unknown exception sanitization;
- explicit service HTTP statuses are preserved;
- main App route mount.

## Task 7 — Frontend Model, API and Hook

**Files:**

- `frontend/src/enterprise-release-quality-operations/model/**`
- `frontend/src/enterprise-release-quality-operations/api/**`
- `frontend/src/enterprise-release-quality-operations/hooks/**`

**Interfaces:**

- strict SLO Policy, Schedule, Run, Observation, Alert, Job and Summary projectors;
- API methods for all public operations routes;
- `useReleaseQualityOperations` with inactive safety, parallel first load and lazy Timeline;
- mutation serialization, AbortSignal/context generation fencing and same-key retry;
- malformed declared authority becomes unavailable, not empty/healthy;
- shared secret and credential sanitizer;
- direct/hash Approval and Release deep links.

**RED coverage:**

- zero and null facts remain distinct;
- nested Tenant/Dataset/Release/Channel scope consistency;
- signed minute projection;
- stale response ignored after scope/filter change;
- `unavailable` mutation is an error;
- history errors differ from empty lists;
- no body/note/ticket/idempotency leak.

## Task 8 — TDesign Quality Operations Center

**Files:**

- `frontend/src/enterprise-release-quality-operations/components/**`
- `frontend/src/enterprise-release-quality-operations/release-quality-operations.css`
- Stage 19/20 Release workspace integration files only
- related component/style tests

**UI:**

```text
Release Center | Quality Operations
```

Quality Operations contains:

- governed authority header;
- SLO Horizon Rail;
- filters;
- desktop PrimaryTable and 375/280 priority cards;
- one coordinated Detail Drawer;
- Overview / Timeline / Certification / Alert / Recertification tabs;
- acknowledge, resolve, suppress, queue and cancel Dialogs;
- durable Scan/Job state and safe mutation receipts;
- read-only, partial error, empty and unavailable surfaces.

**RED coverage:**

- no marketing Hero or metric card wall;
- TDesign React and TDesign Icons first;
- Horizon Rail drives filters and exposes authoritative counts;
- desktop/mobile DOM does not duplicate priority surfaces;
- keyboard Tabs, Dialog focus trap, Escape isolation and focus return;
- one Drawer at a time;
- 1440/375/280, light/dark and reduced motion;
- expiry absolute time plus relative minutes;
- no global Notification Center button is fabricated;
- safe text and no secret authority leakage.

## Task 9 — Preflight, Runbook and Controlled Playwright

**Files:**

- `scripts/enterprise_catalog_upgrade.py`
- `docs/operations/enterprise-catalog-upgrade.md`
- `tests/test_enterprise_catalog_upgrade.py`
- `output/playwright/enterprise-release-quality-operations-stage21/**`
- `docs/research/2026-08-29-enterprise-release-quality-operations-stage21-visual-acceptance.md`

**Preflight:**

- read-only 0031 schema/capability report;
- canonical Policy/Schedule/Alert/Job identity;
- lease/state integrity;
- orphan/cross-Tenant facts;
- Observation digest/current Stage 20 authority consistency;
- expired/stale Alert and Job blockers;
- no automatic action or repair.

**Playwright matrix:**

```text
direct/hash x light/dark x 1440/375/280
```

Flows:

- healthy/warning/critical horizon;
- expired/stale/unavailable authority;
- Alert acknowledge/resolve/suppress;
- recertification queue/cancel/ready;
- Timeline lazy load;
- read-only;
- keyboard/focus;
- zero console/page/network errors, leaks and viewport overflow;
- fresh source-bound artifact manifest.

## Task 10 — Regression and Independent Reviews

- Stage 16–21 affected backend batches;
- Stage 19–21 frontend batches plus production build;
- Ruff, format, py_compile, ESLint, Prettier and TypeScript;
- Alembic head/lock/offline/diff checks;
- direct import smoke;
- controlled Playwright and artifact gates;
- independent Luna-max Database/Security review;
- independent Luna-max TDesign/UI review;
- resolve every P0/P1/P2 before declaring Stage 21 complete;
- keep the long-term enterprise Knowledge Base `/goal` active for the next stage.
