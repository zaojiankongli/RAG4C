# Enterprise Task Operations Center v1.0 Design

Date: 2026-08-29
Status: Approved by standing user authorization
Stage: 24
Revision: `0034_enterprise_task_operations`
Down revision: `0033_enterprise_content_recovery`

## Problem

RAG4C already persists multiple enterprise task types, but operators must visit Documents, Sources, Compliance, Quality and Monitor separately. Status, attempts, leases, errors, retry/cancel rules and deep links differ. There is no single Tenant-scoped operational queue or evidence that a unified row still matches its source authority.

## Decision

Build an Enterprise Task Operations Center using a **verified projection architecture**:

```text
source task authority
    -> reconciliation adapter
    -> unified task projection
    -> Task Center read model / safe actions
```

Source domain tables remain authoritative. Stage 24 never rewrites their state through reflection or generic SQL.

## Initial source kinds

```text
document_ingest
index_operation
source_sync
document_delete
audit_export
release_quality_scan
release_recertification
```

Interactive RAG query runs remain in Monitor/Run Registry.

## Database

Exactly five tables.

### `tenant_task_projections`

Current verified task view.

Key columns:

```text
id
 tenant_id
 source_kind / source_id / source_revision / source_digest
 dataset_id / workspace_id
 category
 normalized_status            queued | running | succeeded | failed |
                              cancelled | blocked | unavailable
 action_required
 progress_percent
 attempt_number / max_attempts
 lease_owner / lease_until
 safe_error_code / safe_error
 target_route_code / target_route_params_json
 source_current
 projection_digest
 occurred_at / started_at / finished_at / updated_at
```

Identity:

```text
UNIQUE(tenant_id, source_kind, source_id)
UNIQUE(tenant_id, id)
```

### `tenant_task_operator_actions`

Actions:

```text
retry
cancel
acknowledge
```

Lifecycle:

```text
requested -> dispatched -> applied / rejected / expired
```

Contains expected source revision/digest, idempotency digest, actor/request facts and safe reason. It never stores an execution ticket or raw source payload.

### `tenant_task_events`

Immutable hash chain:

```text
materialized
status_changed
source_stale
action_requested
action_applied
action_rejected
attention_acknowledged
```

### `tenant_task_saved_views`

Per-account active/archived filter views. Allowed filters: source kind, category, normalized status, action required, Dataset, Workspace and bounded time range. No free-form backend query or URL.

### `tenant_task_reconciliation_runs`

Read-model build evidence: durable `source_kinds_json` scope, source inventory digest, per-source counts, created/updated/stale/invalid totals, started/completed/failed lifecycle and safe error. The selected source scope survives process restart and is never reconstructed from an in-memory cache.

## Reconciliation

`reconcile_enterprise_tasks()` scans each source adapter in stable order. Each adapter returns a pure source projection with source revision/digest. Reconciliation:

- validates Tenant/source scope;
- upserts current Task Projection;
- appends Event on material changes;
- marks disappeared/stale sources unavailable, never deletes history;
- records one Reconciliation Run;
- does not mutate source tables;
- is replay-safe and concurrency-safe.

## Safe action adapters

No dynamic reflection dispatch.

Initial explicit adapter candidates:

- source_sync retry;
- recertification cancel;
- audit export cancel when source service supports it;
- acknowledge is Task Center-local only.

Unsupported source/action pairs are disabled and rejected server-side.

Every action requires current source revision/digest, actor authorization, Idempotency-Key and safe reason. The source service remains final authority.

## API

```text
GET  /api/enterprise/tasks/summary
GET  /api/enterprise/tasks
GET  /api/enterprise/tasks/{task_id}
GET  /api/enterprise/tasks/{task_id}/events
POST /api/enterprise/tasks/{task_id}/retry
POST /api/enterprise/tasks/{task_id}/cancel
POST /api/enterprise/tasks/{task_id}/acknowledge
GET  /api/enterprise/task-views
POST /api/enterprise/task-views
PATCH /api/enterprise/task-views/{view_id}
POST /api/enterprise/tasks/reconcile/preview
POST /api/enterprise/tasks/reconcile
```

Production acceptance does not invoke reconcile or source actions. Playwright uses loopback in-memory adapters only.

## Frontend

Route:

```text
/enterprise/tasks
#/enterprise/tasks
```

Primary navigation under Knowledge Operations:

```text
任务中心
```

Composition:

```text
Header + authority badge
Attention Board: Running / Queued / Failed / Stale
SOURCE -> QUEUE -> ATTEMPT -> OUTCOME
All / Running / Failed / Completed / Activity
Filter toolbar + Saved Views
Desktop PrimaryTable / Mobile Cards
Task Detail Drawer
Retry / Cancel Dialog
Reconciliation status panel
```

TDesign React and TDesign Icons are mandatory for core UI.

## Readiness and preflight

Capability:

```text
enterprise_task_operations
```

Readiness validates five tables, source allow-list, Tenant-leading FKs, projection/currentness/digest, action adapter identity, Event chain, Saved View filter schema and Reconciliation Run counts. Stage 23 and earlier remain ready at 0034.

Preflight is read-only and never reconciles or dispatches an action.

## Safety

No production migration, task reconciliation, retry, cancel, queue operation, external notification or source mutation is permitted during implementation or acceptance.
