# Stage 22 Enterprise Notification Center Database / Security Research

> **Research only / no implementation**  
> **Date:** 2026-08-29  
> **Scope:** Global in-app Notification Center, per-user unread authority, safe source handoff, subscriptions, audit and readiness.  
> **Production boundary:** no migration, no notification materialization, no read receipt mutation, no webhook/email delivery and no external network action.

## 1. Executive conclusion

RAG4C currently has three durable business authorities that resemble notifications but cannot safely be merged in the browser:

- Stage 21 `dataset_release_quality_alerts` is a Dataset-scoped quality lifecycle;
- Enterprise Approval is a Tenant-scoped business task and approver eligibility authority;
- `tenant_audit_events` is append-only evidence, not a user inbox.

A trustworthy Notification Center therefore needs its own durable, per-recipient authority. `Alert acknowledged`, `Approval approved` and `Notification read` are different facts and must remain separate.

The recommended Stage 22 boundary is **in-app Notification Center first**. It may define future channel vocabulary, but it must not send email, SMS, Push or Webhook in this stage. External delivery requires secret storage, SSRF controls, signing, delivery leases and production network governance and should remain a later stage.

## 2. Recommended revision and tables

```text
Revision: 0032_enterprise_notification_center
Down revision: 0031_enterprise_release_quality_operations
```

Recommended five-table authority:

```text
tenant_notification_subscriptions
tenant_notifications
tenant_notification_recipients
tenant_notification_receipts
tenant_notification_events
```

This is deliberately smaller than a full multi-channel delivery platform while still proving unread counts, recipient assignment, subscriptions, audit and safe navigation.

## 3. `tenant_notification_subscriptions`

Per-account in-app preference. It does not decide business eligibility; it filters optional notifications after the source adapter has produced an eligible recipient.

```text
id, tenant_id, account_id
category = quality | approval
status = subscribed | muted
active_subscription_key
revision
minimum_severity = info | warning | critical
muted_until
created_at/by, updated_at/by
```

Rules:

- `UNIQUE(tenant_id, account_id, category, id)` target identity;
- `UNIQUE(tenant_id, active_subscription_key)` ordinary nullable-active identity;
- active key is `account_id:category` while subscribed/muted and `NULL` only for archived history if archival is added later;
- `muted_until` is required only for bounded mute;
- critical, action-required Notifications may be declared mandatory by the source adapter and ignore user mute;
- subscription cannot grant Dataset access or Approval eligibility.

FKs:

```text
(tenant_id, account_id)
  -> tenant_members(tenant_id, account_id)
```

The FK target order must match an existing Tenant-leading unique identity. If the current `tenant_members` unique is ordered differently, add the target unique explicitly rather than introducing a non-Tenant-leading FK.

## 4. `tenant_notifications`

Immutable, body-free notification envelope created from an allow-listed source adapter.

```text
id, tenant_id
source_kind = quality_alert | approval_pending_for_me
source_id, source_revision
source_dataset_id
category = quality | approval
severity = info | warning | critical
action_required
notification_key
source_digest
title_code, summary_code
safe_facts_json
target_route_code, target_route_params_json
occurred_at, created_at, created_by
```

Rules:

- append-only UPDATE/DELETE trigger;
- `UNIQUE(tenant_id, id)`;
- `UNIQUE(tenant_id, notification_key)`;
- notification key includes source kind, source ID, source revision/cycle and event semantic;
- raw query, result body, Judgment note, Approval execution ticket, email address, access token, Webhook URL and arbitrary route URL are forbidden;
- title and summary use server-owned codes plus bounded safe facts; arbitrary source text is not copied;
- route code is an enum, never an arbitrary URL.

Initial route allow-list:

```text
knowledge_quality_operations
enterprise_approval
```

Expected route params:

```text
knowledge_quality_operations:
  tenant_id, dataset_id, release_id, channel_id, alert_id

enterprise_approval:
  tenant_id, approval_request_id
```

## 5. `tenant_notification_recipients`

Immutable recipient assignment proving who was eligible when the notification was materialized.

```text
id, tenant_id, notification_id, account_id
recipient_reason = tenant_owner | tenant_admin | dataset_owner |
                   eligible_approver | explicit_subscription
mandatory
assignment_digest
assigned_at
```

Rules:

- append-only;
- `UNIQUE(tenant_id, notification_id, account_id)`;
- Tenant-leading FKs to Notification and Tenant member;
- quality recipient resolution is limited to the Dataset owner plus active Tenant owner/admin accounts that still have `knowledge.read` at materialization time;
- approval recipient resolution uses the Approval service's current eligible-approver projection;
- recipient assignment does not persist account email, phone or external identity.

## 6. `tenant_notification_receipts`

Mutable per-user inbox lifecycle. This table is the only unread-count authority.

```text
id, tenant_id, notification_id, account_id
status = unread | read | archived
revision
read_at
archived_at
updated_at
last_opened_at
```

Rules:

- `UNIQUE(tenant_id, notification_id, account_id)`;
- composite FK must match a recipient assignment;
- unread: `read_at IS NULL`, `archived_at IS NULL`;
- read: `read_at IS NOT NULL`, `archived_at IS NULL`;
- archived: `archived_at IS NOT NULL`; read evidence may be retained;
- mark-read, mark-unread and archive are revision-fenced and idempotent;
- bulk mutation owns one caller idempotency key and one request hash;
- no receipt row means the notification is unavailable to that account, not unread;
- unread count is a server query over authorized receipt rows and never a frontend array length.

## 7. `tenant_notification_events`

Immutable receipt and materialization timeline.

```text
id, tenant_id, notification_id, account_id
sequence
event_type = materialized | marked_read | marked_unread | opened | archived
previous_event_digest, event_digest
actor_id, request_id
safe_snapshot_json
occurred_at
```

Rules:

- append-only trigger;
- `UNIQUE(tenant_id, notification_id, account_id, sequence)`;
- hash chain per recipient notification stream;
- event snapshot contains only status, revision, source kind/code and route code;
- receipt mutation and event append happen in the same transaction;
- `opened` is optional telemetry and must not silently mark read unless the API explicitly requests that transition.

## 8. Source adapters

### 8.1 Quality Alert

Source:

```text
dataset_release_quality_alerts
```

Materialize only server-confirmed lifecycle events:

```text
alert opened
severity escalated
alert acknowledged by operator (optional informational)
alert resolved (optional informational)
```

Stage 22 MVP should materialize `open`/escalated action-required Notifications first. The Notification directs the user to Stage 21 Quality Operations; it never acknowledges, suppresses or resolves the Alert itself.

Currentness must revalidate:

- Tenant/Dataset/Release/Channel scope;
- source Observation digest;
- Alert revision and active identity;
- recipient Dataset read permission.

### 8.2 Approval pending for me

Source:

```text
tenant_approval_requests + current eligible approver projection
```

Materialize when the Approval service confirms the account is currently eligible. Notification handoff routes to:

```text
/enterprise/approvals?request=<opaque-id>
```

The Notification does not persist or consume Approval execution tickets and does not approve/reject within the inbox.

## 9. Materialization model

Preferred initial model:

```text
source lifecycle mutation
-> same-transaction or durable outbox marker
-> notification materializer
-> immutable notification
-> recipient assignments
-> unread receipts
-> notification events
```

Do not build a browser-side fan-in of Alert and Approval lists. It cannot provide global ordering, complete pagination, exact unread counts or cross-session receipts.

If same-transaction coupling is too invasive, create a deterministic materializer scan over source revisions with a durable watermark. The materializer must be replay-safe through `notification_key` and recipient unique constraints.

## 10. Security requirements

- all Notification/Recipient/Receipt/Event FKs are Tenant-leading;
- source adapters use explicit source-kind allow-lists;
- source envelopes contain codes and safe facts only;
- target navigation uses route codes plus strict parameter schemas;
- Dataset notification reads revalidate `knowledge.read` and current Tenant membership;
- Approval notifications revalidate approver eligibility before handoff;
- no raw source payload, query, result body, note, ticket, token, password, Webhook URL or database URL;
- actor-supplied mark-read reason/comment is unnecessary and should not be accepted;
- list and unread-count APIs use read-only engines;
- receipt mutations use mutation engines, revision fences, Tenant idempotency and Tenant audit;
- source disappearance produces stale/unavailable projection, never a fabricated valid notification.

## 11. Readiness and preflight

Capability key:

```text
enterprise_notification_center
```

Readiness verifies:

- revision and five tables;
- exact columns, not-null, uniques, FKs, checks and indexes;
- Notification and Event immutable triggers;
- canonical notification/subscription keys;
- duplicate recipient/receipt/event identities;
- orphan or cross-Tenant notification facts;
- receipt lifecycle contradictions;
- recipient assignment without current Tenant member;
- unsafe facts/route params;
- unread receipts whose source handoff is malformed.

Read-only preflight must report counts and blockers and never materialize Notifications or Receipts.

Downgrade to 0031 is blocked while any Stage 22 row exists.

## 12. Cross-database notes

- use nullable active keys rather than partial unique indexes;
- use SQLAlchemy expressions for cross-dialect canonical concatenation;
- use `DATETIME(6)` on MySQL/MariaDB and microsecond UTC normalization elsewhere;
- SQLite offline SQL remains unsupported;
- MySQL/PostgreSQL offline DDL is review-only;
- Notification/Event append-only guards require SQLite triggers, MySQL `SIGNAL SQLSTATE`, PostgreSQL trigger functions;
- do not claim cross-database production readiness until real MySQL/PostgreSQL container smoke validates triggers, composite FKs and concurrent receipt mutation.

## 13. API boundary recommendation

```text
GET  /api/enterprise/notifications/summary
GET  /api/enterprise/notifications
GET  /api/enterprise/notifications/{notification_id}
POST /api/enterprise/notifications/{notification_id}/read
POST /api/enterprise/notifications/{notification_id}/unread
POST /api/enterprise/notifications/{notification_id}/archive
POST /api/enterprise/notifications/bulk-read

GET  /api/enterprise/notification-subscriptions
PATCH /api/enterprise/notification-subscriptions/{subscription_id}
```

All receipt/subscription mutations require `Idempotency-Key`.

## 14. UI implications

- App shell bell is hidden until capability and unread count are authoritative;
- badge shows server unread count, with `99+` visual cap only;
- bell opens one global TDesign Drawer;
- full route `/enterprise/notifications` provides Inbox and Subscription tabs;
- list supports All/Unread, Quality/Approval, severity and time filters;
- bulk mark read displays selected count;
- Notification row has one safe primary handoff action;
- no direct Alert or Approval business mutation inside the Notification Center;
- mobile uses cards and full-screen detail, not compressed desktop tables.

## 15. Explicit non-goals for Stage 22

- email, SMS, voice, browser Push;
- Webhook endpoint storage or external delivery worker;
- third-party chat robots;
- arbitrary routing rules or templates;
- notification generation from every Audit Event;
- Recertification Job as a separate notification source;
- copying business payloads into Notification rows;
- real production Notification materialization during implementation or acceptance.

## 16. Recommended decision

Proceed with **five-table in-app Notification Center v1**, sourcing only `quality_alert` and `approval_pending_for_me`, with per-user receipts and safe deep-link handoff. Defer multi-channel delivery and general routing rules until this authority is proven through migration, readiness, API, UI and Playwright evidence.
