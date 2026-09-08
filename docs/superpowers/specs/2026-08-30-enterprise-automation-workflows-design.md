# Enterprise Automation & Workflow Orchestration Center v1.0 Design

Date: 2026-08-30  
Status: Self-approved under standing user authorization  
Stage: 25  
Revision: `0035_enterprise_automation_workflows`  
Down revision: `0034_enterprise_task_operations`

## Problem

RAG4C already has durable Task, Notification, Approval, Source Sync, Release Quality and Compliance authorities. Operators can observe and act in each domain, but there is no Tenant-scoped rule authority that can correlate an immutable source event with a bounded condition and produce an auditable action request. The legacy `workflows` table is app-scoped, stores arbitrary `config` JSON, has no Tenant-leading identity, revision fence, immutable definition history, execution evidence or safe action allow-list; it must not become the enterprise automation authority.

## Decision

Build a verified request-orchestration architecture:

```text
source authority event
    -> explicit trigger adapter
    -> immutable rule revision
    -> pure condition evaluation
    -> bounded action request
    -> specialized domain adapter / human approval
    -> immutable automation event evidence
```

A rule engine never imports a function by name, reflects over ORM classes, executes arbitrary SQL, calls a user-supplied URL/webhook, evaluates code, or mutates a source-domain row directly.

## Initial triggers

```text
task_failed
task_source_stale
source_sync_failed
release_quality_alert_opened
release_recertification_blocked
approval_request_terminal
```

Each trigger adapter reads one explicit source authority and returns a canonical, Tenant-scoped safe event envelope. Notification events are not v1 triggers to avoid self-amplifying notification loops.

## Initial conditions

```text
always
status_is
action_required
severity_at_least
attempt_exhausted
source_is_stale
```

Conditions accept only bounded scalar parameters validated by trigger/condition-specific schemas. No query language or expression evaluator exists.

## Initial actions

```text
notify_operator
request_approval
open_task_attention
pause_rule
```

All non-local actions create a durable `requested` action row. Stage25 acceptance never delivers a notification, executes an Approval, retries/cancels a task, starts a source sync, invokes an external webhook or changes a source-domain record. `pause_rule` is the only local metadata action and remains revision-fenced.

## Database — exactly six new tables

### `tenant_automation_rules`

Current operator-owned rule metadata.

Key facts:

```text
id / tenant_id
name / normalized_name
status                     draft | active | paused | archived
active_rule_key
revision
current_revision_id
workspace_id / dataset_id
priority
created_at/by / updated_at/by / archived_at/by
```

Canonical identities:

```text
UNIQUE(tenant_id, id)
UNIQUE(tenant_id, active_rule_key)
```

### `tenant_automation_rule_revisions`

Immutable, canonical rule definitions.

```text
id / tenant_id / rule_id / revision
trigger_code
condition_code / condition_params_json
action_plan_json           bounded 1..4 allow-listed action steps
definition_digest
created_at / created_by
```

`UNIQUE(tenant_id, rule_id, revision)`. Update/delete guards are mandatory.

### `tenant_automation_source_cursors`

Replay and lease fence for one rule/source stream.

```text
id / tenant_id / rule_id
source_kind / source_stream_id
last_sequence / last_event_digest
status                     idle | claimed | blocked
lease_owner / lease_until
revision / updated_at
```

### `tenant_automation_runs`

One evaluation attempt against one source event and one immutable rule revision.

```text
id / tenant_id / rule_id / rule_revision_id
trigger_event_id / trigger_event_digest
status                     started | not_matched | requested | completed | failed | blocked
condition_matched
action_count / requested_count / rejected_count
idempotency_digest
started_at / completed_at
safe_error_code / safe_error
created_at / updated_at
```

### `tenant_automation_action_requests`

Durable, bounded output request.

```text
id / tenant_id / run_id / rule_id
step_index / action_code
status                     requested | dispatched | applied | rejected | expired
target_kind / target_id / target_revision / target_digest
idempotency_key_digest
safe_params_json / safe_reason
approval_request_id / notification_id / task_id
requested_at / dispatched_at / applied_at / rejected_at / expires_at
created_at / updated_at
```

No ticket, token, credential, raw payload, URL, SQL, prompt, document body or arbitrary metadata column is allowed.

### `tenant_automation_events`

Immutable hash chain per Rule/Run.

```text
id / tenant_id / rule_id / run_id
sequence / event_type
previous_event_digest / event_digest
actor_id / request_id
safe_snapshot_json / occurred_at
```

Event types:

```text
rule_created
revision_created
revision_activated
rule_paused
trigger_observed
condition_not_matched
run_started
action_requested
action_rejected
run_completed
run_failed
```

## Concurrency and replay

- Stable adapter order and stable source-event ordering.
- `(tenant_id, rule_id, trigger_event_digest)` replay identity.
- Idempotency-Key for every mutation.
- Rule revision and current definition digest fences.
- Cursor lease compare-and-swap.
- Action request uniqueness by Tenant/Run/step.
- A repeated source event returns replay evidence and never creates duplicate action requests or Events.

## API

```text
GET  /api/enterprise/automations/summary
GET  /api/enterprise/automations/rules
POST /api/enterprise/automations/rules
GET  /api/enterprise/automations/rules/{rule_id}
PATCH /api/enterprise/automations/rules/{rule_id}
GET  /api/enterprise/automations/rules/{rule_id}/revisions
POST /api/enterprise/automations/rules/{rule_id}/revisions
POST /api/enterprise/automations/rules/{rule_id}/preview
POST /api/enterprise/automations/rules/{rule_id}/activate
POST /api/enterprise/automations/rules/{rule_id}/pause
GET  /api/enterprise/automations/runs
GET  /api/enterprise/automations/runs/{run_id}
GET  /api/enterprise/automations/action-requests
GET  /api/enterprise/automations/events
```

There is no public generic event-ingest or execute endpoint. Source observation is an internal explicit-adapter operation.

## Frontend

Route:

```text
/enterprise/automations
#/enterprise/automations
```

Primary navigation under Knowledge Operations: `自动化中心`.

Signature composition:

```text
Header + verified authority badge
Automation Attention Board
WHEN -> IF -> REQUEST -> EVIDENCE rail
Rules / Runs / Requests / Activity
Desktop table / mobile cards
Rule detail Drawer
Rule builder Dialog
Preview result panel
Revision and immutable Event timeline
```

TDesign React and TDesign Icons are mandatory for core UI. The builder is a dense form-driven console, not an unrestricted drag-and-drop canvas. Uiverse/Morphicons may only supplement non-core decorative states.

## Readiness / preflight

Capability: `enterprise_automation_workflows`. Readiness validates six tables, Tenant-leading FKs, active identity, immutable revision/Event guards, definition digest, cursor lease, run/action lifecycle, Event chain and parent Task/Notification/Approval/Quality authorities. Preflight is read-only and never observes source events, advances a cursor, creates a Run or Action Request, dispatches an adapter or pauses a Rule.

## Safety

No production migration, source observation, cursor advancement, automation run, action dispatch, notification delivery, Approval execution, task retry/cancel, source sync, external webhook or dynamic code execution is permitted during implementation or acceptance.
