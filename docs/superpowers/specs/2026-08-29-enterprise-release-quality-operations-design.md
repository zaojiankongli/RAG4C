# Enterprise Release Quality Operations Design

**Date:** 2026-08-29  
**Stage:** 21  
**Revision:** `0031_enterprise_release_quality_operations`  
**Down revision:** `0030_enterprise_release_quality_certification`  
**Status:** Approved design

## 1. Objective

Stage 21 adds a Tenant-safe operations layer over the immutable Stage 20 Release quality authority. It must answer, with durable evidence:

> Which serving or pinned Knowledge Base Releases are about to lose quality eligibility, which alerts are active, and which Releases need an operator-driven recertification workflow?

Stage 21 does not add another Retrieval Experiment runner. It observes and revalidates existing Policy, Baseline, Certification, Waiver, Release, Channel and Approval facts, writes immutable observations, and manages alert and recertification lifecycles.

## 2. Tencent patterns translated into RAG4C

Fresh Playwright review of Tencent Knowledge Base recall testing, Application evaluation, publishing and operations showed four relevant product patterns:

1. Evaluation tasks are durable objects with queue, run, pause/resume, annotation, report, download, copy and delete states.
2. Reports separate summary/performance views from complete result exports.
3. Publishing is an asynchronous background flow with completion notification and release history.
4. Operational records are available in a consolidated operations workspace, not only in one task dialog.

RAG4C translates these patterns without copying Tencent branding or source:

- Scan Schedule and Scan Run are durable operational objects.
- Observation is immutable evidence; Alert is the operator lifecycle.
- Recertification Job is a durable action queue that eventually calls the existing Stage 20 `certify_release(...)` service.
- Quality Operations is a local Releases work surface. It does not claim that a global Notification Center exists.

## 3. Non-goals and production boundary

Stage 21 does not:

- create a second Retrieval Experiment runner;
- change or synthesize Retrieval Judgments;
- create Baselines automatically;
- auto-grant Waivers;
- auto-promote, rollback or pin Releases;
- send email, SMS or webhook notifications;
- expose a global notification route before one exists;
- run a production migration, scan, recertification, source sync, restore or destructive operation during implementation or acceptance.

Code may support scheduled scans and durable jobs, but all acceptance uses controlled local fixtures only.

## 4. Authority model

Revision `0031_enterprise_release_quality_operations` creates six tables.

### 4.1 `tenant_release_quality_slo_policies`

Revision-fenced Tenant policy resolved by exact Channel, then risk tier, then global scope.

```text
id, tenant_id, name
scope_type = global | risk_tier | channel
scope_value, channel_id, active_scope_key
status = active | disabled
revision
certification_warning_minutes
certification_critical_minutes
waiver_warning_minutes
max_open_alerts
auto_queue_recertification
require_passing_certification
allow_active_waiver
policy_digest
created_at/by, updated_at/by, disabled_at/by
```

Rules:

- `certification_warning_minutes > certification_critical_minutes > 0`;
- `waiver_warning_minutes > 0`;
- `max_open_alerts` is an exact positive integer;
- active scope keys are canonical and unique per Tenant;
- disabled rows have no active scope key and retain revision history;
- scope resolution is `channel -> risk_tier -> global`.

### 4.2 `tenant_release_quality_scan_schedules`

Durable fixed-interval UTC schedule for one active SLO Policy.

```text
id, tenant_id, dataset_id, slo_policy_id
status = active | paused | archived
active_policy_slot
revision
interval_seconds
next_run_at
last_enqueued_at
created_at/by, updated_at/by
paused_at/by, archived_at/by
```

Rules:

- one active schedule per Tenant/Dataset/SLO Policy;
- `interval_seconds` is bounded and exact;
- pause/resume/archive are revision-fenced;
- due selection is ordered by `next_run_at, id`;
- no circular `last_run_id` FK; latest run is queried from the Run ledger.

### 4.3 `tenant_release_quality_scan_runs`

Durable execution ledger for one schedule slot.

```text
id, tenant_id, dataset_id, schedule_id, slo_policy_id, slo_policy_revision
status = pending | claimed | running | completed | failed | cancelled
planned_at
claim_owner, claim_lease_until, heartbeat_at
attempt_count, max_attempts, next_attempt_at
started_at, finished_at
observation_count, alert_count, recertification_job_count
idempotency_key_digest, request_hash
summary_digest
safe_error_code, safe_error
created_at, updated_at
```

Rules:

- unique Tenant/Dataset/Schedule/planned slot;
- raw idempotency keys are never persisted;
- claim/heartbeat/finish transitions are atomic and lease-fenced;
- terminal states clear the lease;
- summary counts and digest are server-computed;
- failed retries use bounded attempts and `next_attempt_at`.

### 4.4 `dataset_release_quality_observations`

Immutable evidence captured by one scan run for one Release/Channel authority.

```text
id, tenant_id, dataset_id, release_id, channel_id
scan_run_id
slo_policy_id, slo_policy_revision
release_role = active | pinned
gate_state, gate_reason
certification_id, certification_digest, certification_valid_until
waiver_id, waiver_digest, waiver_expires_at
minutes_to_certification_expiry
minutes_to_waiver_expiry
severity = healthy | warning | critical | unavailable
observation_digest
observed_at/by, request_id
```

Rules:

- append-only and body-free;
- one observation per scan run and Release/Channel/role identity;
- all Release, Channel, Certification, Waiver, Run and Policy references are Tenant-leading composite FKs;
- minute values are exact signed integers or null;
- Observation digest includes all authoritative fields except generated row ID;
- query text, result body, Judgment note, ticket and raw idempotency key are forbidden.

Scan targets are current active `dataset_channel_releases` plus active pinned Application references evaluated against the one active default-serving Channel. Candidate-only Releases remain in the Stage 20 Release Center until they become an active or pinned serving authority.

### 4.5 `dataset_release_quality_alerts`

Revision-fenced operator lifecycle derived from observations.

```text
id, tenant_id, dataset_id, release_id, channel_id
release_role
alert_type = certification_expiring | certification_expired |
             certification_stale | waiver_expiring | waiver_expired |
             quality_gate_blocked | quality_authority_unavailable
severity = warning | critical
status = open | acknowledged | resolved | suppressed
active_alert_key
revision
source_observation_id, source_observation_digest
occurrence_count
opened_at, last_observed_at
acknowledged_at/by/comment
resolved_at/by/comment
suppressed_until, suppressed_by/comment
created_at, updated_at
```

Rules:

- at most one active Alert per Tenant/Dataset/Release/Channel/role/type;
- active key is canonical and database-checked;
- acknowledge, resolve and suppress are revision-fenced;
- open and acknowledged Alerts retain active identity;
- resolved Alerts clear active identity and require resolution evidence;
- suppressed Alerts retain active identity and require a future suppression time;
- every lifecycle mutation writes a Tenant Audit Event;
- Observation history provides the immutable timeline behind the mutable Alert projection.

### 4.6 `dataset_release_recertification_jobs`

Durable operator-driven recertification queue.

```text
id, tenant_id, dataset_id, release_id, channel_id
release_role
baseline_id
policy_id, policy_revision
slo_policy_id, slo_policy_revision
trigger = manual | certification_warning | certification_expired |
          stale_evidence | alert_escalation
status = pending | claimed | awaiting_evidence | ready_to_certify |
         completed | failed | cancelled
active_job_key, cycle_key
expected_manifest_digest
expected_evidence_digest
expected_channel_revision
claim_owner, claim_lease_until, heartbeat_at
attempt_count, max_attempts, next_attempt_at
result_certification_id
idempotency_key_digest, request_hash
safe_error_code, safe_error
created_at/by, updated_at, completed_at, cancelled_at/by
request_id, reason
```

Rules:

- Job does not execute Retrieval Experiments or mutate Judgments;
- Job may enter `ready_to_certify` only after current Baseline, Experiment and Judgment evidence is complete;
- final certification delegates to Stage 20 `certify_release(...)`;
- all expected digests and revisions are revalidated in the same transaction;
- terminal transitions are immutable in meaning and fully audited;
- only one non-terminal Job may exist for the canonical Release/Channel/Policy authority;
- `cycle_key` separately deduplicates the immutable Manifest/Evidence authority cycle, while request idempotency remains request-scoped.

## 5. SLO evaluation

A pure evaluator receives UTC `now`, the resolved SLO Policy and a validated Stage 20 Quality Gate projection.

```text
passing Certification:
  minutes > warning       -> healthy
  warning >= minutes > critical -> warning / certification_expiring
  critical >= minutes > 0 -> critical / certification_expiring
  minutes <= 0            -> critical / certification_expired

active Waiver:
  minutes > waiver_warning -> warning or healthy according to allow_active_waiver
  0 < minutes <= warning   -> critical / waiver_expiring
  minutes <= 0             -> critical / waiver_expired

blocked Gate              -> critical / quality_gate_blocked
unavailable or malformed  -> unavailable / quality_authority_unavailable
stale Certification       -> critical / certification_stale
not_required              -> healthy only when the Channel is low risk and non-default
```

Persistent arithmetic uses exact integer minutes. Datetimes are stored in UTC with microsecond precision. An unavailable denominator or malformed timestamp fails closed.

## 6. Scan data flow

```text
Schedule due slot
-> deterministic idempotency key and request hash
-> create/replay Scan Run
-> claim lease
-> resolve matching Channel/Dataset targets
-> resolve and revalidate Stage 20 Quality Gate
-> evaluate SLO
-> append immutable Observation
-> create/update/resolve Alert projection
-> optionally enqueue Recertification Job
-> persist Run summary digest and terminal state
```

The scan transaction uses deterministic lock order:

```text
Tenant
-> SLO Policy
-> Schedule
-> Scan Run
-> Dataset
-> Release / Channel / binding or pinned reference
-> Stage 20 quality authority
-> existing active Alert
-> existing non-terminal Recertification Job
-> new Observation
```

Scanning is idempotent by schedule slot. A replay never duplicates Observation, Alert or Job facts.

## 7. Alert behavior

- A matching active Alert increments `occurrence_count`, advances `last_observed_at`, and updates the source Observation under an expected revision.
- A resolved Alert is not silently reopened. A later breach creates a new Alert row.
- Healthy observations resolve matching open/acknowledged Alerts only through the scan service, with an explicit `resolved_by=system-quality-scanner` fact.
- Suppression affects alert presentation and job auto-queueing only. It never changes the Stage 20 Quality Gate or allows publication.
- The Stage 21 Alert Inbox is the notification surface for this stage. No global bell or notification route is advertised.

## 8. Recertification behavior

Automatic queueing means creating a durable Job, not running Certification immediately.

```text
pending
-> claimed
-> awaiting_evidence when Baseline or current judgments are incomplete
-> ready_to_certify when Stage 20 evidence can be revalidated
-> explicit operator certify action
-> completed with result_certification_id
```

A failed or cancelled Job never changes a Release binding. Waiver requests remain a separate Stage 20 Approval flow.

## 9. API boundary

Strict authenticated routes:

```text
GET/POST/PATCH /api/enterprise/release-quality/slo-policies
GET/POST/PATCH /api/enterprise/release-quality/scan-schedules
GET /api/enterprise/release-quality/scan-runs
POST /api/enterprise/release-quality/scan-runs/{run_id}/cancel

GET /api/enterprise/knowledge-bases/{dataset_id}/quality-operations/summary
GET /api/enterprise/knowledge-bases/{dataset_id}/quality-observations
GET /api/enterprise/knowledge-bases/{dataset_id}/quality-alerts
POST /api/enterprise/knowledge-bases/{dataset_id}/quality-alerts/{alert_id}/acknowledge
POST /api/enterprise/knowledge-bases/{dataset_id}/quality-alerts/{alert_id}/resolve
POST /api/enterprise/knowledge-bases/{dataset_id}/quality-alerts/{alert_id}/suppress

GET/POST /api/enterprise/knowledge-bases/{dataset_id}/recertification-jobs
POST /api/enterprise/knowledge-bases/{dataset_id}/recertification-jobs/{job_id}/cancel
POST /api/enterprise/knowledge-bases/{dataset_id}/quality-operations/scan
```

Permissions:

- read routes require `knowledge.read`;
- Dataset Alert/Job mutations require `knowledge.manage`;
- Tenant SLO Policy and Schedule mutations require owner/admin;
- scheduler claim/run APIs are internal system services, not public actor routes.

All mutations require strict schemas, exact integers/booleans, revision fences, Idempotency-Key, actor/request evidence and sanitized replay payloads.

## 10. Frontend information architecture

Stage 21 remains inside the Knowledge Base Releases resource. It adds local tabs rather than a new primary navigation item:

```text
Release Center | Quality Operations
```

The Quality Operations surface contains:

1. authority header with Tenant, Dataset, Workspace, last completed scan and read-only state;
2. SLO Horizon Rail;
3. TDesign filters for Channel, severity, alert type, Job status and search;
4. desktop PrimaryTable or mobile priority cards;
5. one coordinated Detail Drawer;
6. Alert acknowledge/resolve/suppress Dialogs;
7. Recertification Job queue and operator action;
8. explicit unavailable, empty and partial-error states.

### 10.1 SLO Horizon Rail

The signature visual is one quiet time horizon, not a card wall:

```text
EXPIRED | 24 HOURS | 7 DAYS | 30 DAYS | HEALTHY
```

Each segment displays authoritative counts and acts as a filter. Severity color is reserved for evidence state: red critical, amber warning, green healthy, blue selected control.

### 10.2 Desktop and mobile

- 1440px uses a dense PrimaryTable with one row per active/pinned Release authority.
- 375px uses priority cards with Gate, expiry, Alert and Job state.
- 280px uses one-column cards and full-width primary actions.
- Desktop tables are not compressed into mobile horizontal scrolling.

### 10.3 Detail Drawer

```text
Overview | Timeline | Certification | Alert | Recertification
```

Timeline is backed by immutable Observations and Audit facts:

```text
Observed -> Warning opened -> Alert acknowledged -> Job queued
-> Certification completed -> Alert resolved
```

No nested Drawer. Dialogs use TDesign focus management, Escape isolation and focus return.

### 10.4 Components and tokens

Use TDesign React and TDesign Icons first:

- Tabs, PrimaryTable, Tag, Alert, Drawer, Dialog, Select, DateRangePicker,
  Timeline, Steps, Button, Loading, Empty and Pagination;
- existing system typography and CSS variables;
- blue `#0052D9`, ink `#17233D`, green `#2BA471`, amber `#ED7B2F`, red `#D54941`, surface `#F5F7FA`;
- monospace only for IDs, revisions, digests and timestamps;
- no remote font, marketing Hero, generic gradient metric wall or copied Tencent markup.

## 11. Frontend data behavior

- summary, Alerts and first page of Jobs load in parallel;
- Observations and Timeline load lazily;
- malformed authority projects to unavailable, never empty or healthy;
- mutations own one Idempotency-Key across retry;
- context generation fences prevent stale Dataset/Release results;
- read-only hides or disables all lifecycle mutations;
- expiry is displayed as absolute UTC-derived local time plus bounded relative minutes;
- no raw safe_error is displayed without the shared secret/credential sanitizer.

## 12. Readiness, preflight and downgrade

New capability key:

```text
enterprise_release_quality_operations
```

Readiness verifies:

- exact revision and all six tables;
- Tenant-leading composite FKs and target uniques;
- canonical active Policy/Schedule/Alert/Job identities;
- immutable Observation guards;
- lifecycle CHECKs;
- digest and safe JSON constraints;
- duplicate schedule slots, active Alerts and non-terminal Jobs;
- orphan or cross-Tenant authority;
- schedule/run lease consistency.

`enterprise_catalog_upgrade.py preflight` is read-only and reports all blockers. It never creates Policies, Schedules, Runs, Observations, Alerts or Jobs.

Downgrade to 0030 is blocked while any Stage 21 row exists. SQLite offline SQL remains unsupported; MySQL/PostgreSQL offline SQL is generated for manual review only.

## 13. Acceptance

Backend acceptance includes:

- SQLite online migration and clean downgrade;
- MySQL/PostgreSQL offline DDL;
- ORM/Catalog/Readiness parity;
- pure SLO arithmetic and unavailable handling;
- deterministic schedule slot/idempotency;
- Run claim/lease/retry;
- immutable Observation digest;
- Alert lifecycle and active identity;
- Recertification Job state machine;
- Tenant isolation, audit, replay and stale revision rejection;
- no secret/body/note/ticket leakage.

Frontend acceptance matrix:

```text
direct/hash x light/dark x 1440/375/280
```

Flows:

- healthy Certification;
- warning and critical expiry horizons;
- expired/stale Certification;
- expiring Waiver;
- blocked/unavailable authority;
- Alert acknowledge/resolve/suppress;
- recertification Job queue/cancel/ready state;
- Timeline/history;
- read-only and keyboard focus;
- zero console/page/network errors, leaks and viewport overflow;
- source-bound fresh artifact manifest.

## 14. Implementation boundaries

Protected Retrieval Quality paths remain untouched:

```text
frontend/src/retrieval-quality/**
core/retrieval_experiment_runner.py
server/retrieval_experiments_api.py
tests/test_retrieval_experiment_runner.py
tests/test_retrieval_experiments_api.py
```

No implementation or test may reset, clean or broadly format the cumulative worktree.
