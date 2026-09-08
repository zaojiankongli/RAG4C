# Enterprise Notification Center Design

**Date:** 2026-08-29  
**Stage:** 22  
**Revision:** `0032_enterprise_notification_center`  
**Down revision:** `0031_enterprise_release_quality_operations`  
**Status:** Proposed — implementation requires explicit approval

## 1. Objective

Stage 22 turns the existing App-shell notification icon from a conditional UI placeholder into a Tenant-safe, user-specific Notification Center with authoritative unread counts, persistent read receipts, safe handoff to the underlying business resource and a Tencent-inspired enterprise console surface.

The center must answer:

> Which current Quality or Approval events require this signed-in account's attention, why is this account a recipient, has the account read the event, and where is the authoritative business record?

## 2. Evidence translated from Tencent

Fresh browser research of Tencent Cloud Message Center and ADP application publishing/operations showed these product patterns:

- a global message-center entry and top-right bell for cross-product asynchronous results;
- unread-only filtering plus partial and bulk mark-read operations;
- filters by received time, message category/subtype, title, product and source;
- subscription management structured as product/event type -> channel -> recipient;
- advanced, basic and bulk subscription editing;
- receiver management separate from the inbox;
- quiet-hour and do-not-disturb preferences;
- publication completion notification remains separate from publication history/version facts;
- trigger execution is visible both in local execution logs and consolidated application operations.

RAG4C will reuse the information architecture, not Tencent source code, branding, templates, internal APIs or current-account data.

## 3. Current RAG4C boundary

Existing reusable facts:

- Stage 21 Quality Alert lifecycle and immutable Observation timeline;
- Enterprise Approval `pending_for_me` and safe Approval deep link;
- Tenant Audit Event append-only evidence;
- WorkspaceScopeBar `onOpenNotifications` and Notification icon placeholder;
- TDesign Drawer, Tabs, PrimaryTable, Dialog, Badge, Tag, Alert, Empty and Pagination patterns;
- API AbortSignal, stale generation fence, idempotency and safe error infrastructure.

Missing authority:

- Notification envelope and global ordering;
- per-recipient assignment;
- read/unread/archived receipt;
- server unread count;
- Notification route/provider;
- subscription preference;
- safe source handoff contract.

Alert acknowledged is not Notification read. Approval status is not Notification read. Audit events are not automatically Notifications.

## 4. Approaches considered

### 4.1 Browser fan-in of Alert and Approval lists

The browser loads Stage 21 Alerts and Approval inbox in parallel and merges arrays.

Rejected because it cannot prove:

- complete global ordering and pagination;
- an exact unread count;
- cross-session/device read state;
- recipient assignment;
- partial-source correctness;
- one canonical notification identity.

It would make the Bell look finished while retaining a different, weaker product truth.

### 4.2 Minimal two-table Inbox

Create Notification and Receipt rows but resolve recipients dynamically during each read.

Advantages:

- smaller migration;
- fast initial implementation.

Rejected as the final recommendation because recipient eligibility would have no frozen evidence and changes in membership/Approval eligibility could silently rewrite notification history.

### 4.3 Durable five-table in-app Notification Center — recommended

Persist Notification, recipient assignment, receipt, subscription and immutable events.

Advantages:

- exact unread count;
- auditable recipient evidence;
- replay-safe source materialization;
- cross-session/device receipts;
- subscription and bulk operation support;
- clean future path to external delivery without pretending it already exists.

This is the recommended Stage 22 design.

## 5. Authority model

Revision `0032_enterprise_notification_center` creates five tables.

### 5.1 `tenant_notification_subscriptions`

Per-account, per-category in-app preference.

```text
id, tenant_id, account_id
category = quality | approval
status = active | archived
preference = subscribed | muted
active_subscription_key
revision
minimum_severity = info | warning | critical
muted_until
created_at/by, updated_at/by
```

Active subscriptions use canonical key `account_id:category`; archived history clears the key. `muted_until` is valid only for a muted active preference. Critical/action-required source adapters may mark a Notification mandatory, bypassing optional mute without changing the subscription row.

Revision 0032 must first add an explicit Tenant-leading target unique:

```text
tenant_members(tenant_id, account_id)
```

because the existing identity is ordered `(account_id, tenant_id)` and cannot be used by a Tenant-leading FK without adding the matching target unique.

### 5.2 `tenant_notifications`

Immutable, body-free Notification envelope.

```text
id, tenant_id
source_kind = quality_alert | approval_pending_for_me
source_id, source_revision
source_dataset_id
category = quality | approval
severity = info | warning | critical
action_required, mandatory
notification_key
source_digest
title_code, summary_code
safe_facts_json
target_route_code, target_route_params_json
occurred_at, created_at, created_by
```

Append-only triggers block UPDATE/DELETE. `source_dataset_id` is required for `quality_alert` and must be `NULL` for `approval_pending_for_me`; the polymorphic source itself is revalidated by the service and readiness rather than represented by an unsafe polymorphic FK.

Initial route codes:

```text
knowledge_quality_operations
enterprise_approval
```

Arbitrary URLs are forbidden.

### 5.3 `tenant_notification_recipients`

Immutable evidence explaining why one account received one Notification.

```text
id, tenant_id, notification_id, account_id
recipient_reason = tenant_owner | tenant_admin | dataset_owner |
                   eligible_approver | explicit_subscription
mandatory
assignment_digest
assigned_at
```

The row never stores email, phone, external identity or credentials. It declares `UNIQUE(tenant_id, notification_id, account_id)` as the exact target identity for Receipt and Event FKs. Reads additionally revalidate that the account remains an active Tenant member; historical assignment remains immutable even when current access is lost.

### 5.4 `tenant_notification_receipts`

Mutable per-account inbox lifecycle and sole unread-count authority.

```text
id, tenant_id, notification_id, account_id
status = unread | read | archived
revision
read_at, archived_at, updated_at
```

The Receipt FK is `(tenant_id, notification_id, account_id) -> tenant_notification_recipients(...)`. No Receipt means the Notification is unavailable to the account, not unread. Bulk transitions lock Receipts in `(tenant_id, account_id, notification_id)` order and accept at most 200 Notification IDs per request.

### 5.5 `tenant_notification_events`

Immutable receipt/materialization timeline.

```text
id, tenant_id, notification_id, account_id
sequence
event_type = materialized | marked_read | marked_unread | archived
previous_event_digest, event_digest
actor_id, request_id
safe_snapshot_json
occurred_at
```

Receipt transition and Event append happen in the same transaction. Merely opening the Bell, Drawer or detail does not write an Event and does not silently mark a Notification read; only an explicit receipt mutation changes state.

## 6. Canonical identities

```text
Notification:
  source_kind + source_id + source_revision/cycle + event semantic

Recipient:
  tenant_id + notification_id + account_id

Receipt:
  tenant_id + notification_id + account_id

Subscription:
  account_id + category

Event stream:
  tenant_id + notification_id + account_id + sequence
```

All active identities use ordinary nullable-key UniqueConstraints rather than partial unique indexes.

## 7. Initial source adapters

### 7.1 `quality_alert`

Initial materialized events:

- active Alert opened;
- severity escalated;
- optionally resolved informational event.

Recipients:

- Dataset owner;
- active Tenant owner/admin accounts that currently pass Dataset `knowledge.read`;
- explicit subscribed recipients that already pass Dataset `knowledge.read`.

The Notification hands off to Quality Operations. It cannot acknowledge, suppress or resolve the Alert.

### 7.2 `approval_pending_for_me`

Materialize only after the Approval service confirms the account is currently eligible.

The Notification hands off to:

```text
/enterprise/approvals?request=<approval_request_id>
```

It does not approve/reject, store or consume an Approval ExecutionFact.

## 8. Materialization flow

```text
Allow-listed source lifecycle
-> deterministic source notification intent
-> immutable Notification insert/replay
-> recipient resolution
-> immutable recipient assignments
-> unread Receipt creation
-> materialized Event append
-> server unread summary
```

A source adapter revalidates the source revision/digest and recipient eligibility in the same controlled materialization operation. V1 uses a replay-safe internal reconciler that scans current active Quality Alerts and pending Approval requests, inserts missing deterministic Notification/Recipient/Receipt facts and records materialization Events. Notification list/summary routes are strictly read-only and never invoke the reconciler. The reconciler may rescan the bounded current source set because unique notification and recipient identities make replay safe; a durable watermark is deferred until source volume proves it necessary.

Stage 22 acceptance uses local fixtures only. It does not materialize production notifications.

## 9. API boundary

Read routes:

```text
GET /api/enterprise/notifications/summary
GET /api/enterprise/notifications
GET /api/enterprise/notifications/{notification_id}
GET /api/enterprise/notification-subscriptions
```

Receipt mutations:

```text
POST /api/enterprise/notifications/{notification_id}/read
POST /api/enterprise/notifications/{notification_id}/unread
POST /api/enterprise/notifications/{notification_id}/archive
POST /api/enterprise/notifications/bulk-read
```

Subscription mutation:

```text
PATCH /api/enterprise/notification-subscriptions/{subscription_id}
```

All mutations require strict schemas, Idempotency-Key, revision fences, request evidence and Tenant audit.

## 10. Permission model

- Notification list/summary: active Tenant member, limited to Recipient rows for the actor;
- Quality handoff: revalidate current Dataset `knowledge.read` before returning target params;
- Approval handoff: revalidate current approver eligibility;
- Receipt mutation: actor may mutate only the actor's Receipt;
- Subscription mutation: actor may mutate only the actor's preference; Tenant owner/admin may inspect aggregate configuration but cannot mark another user's Notification read;
- materializer: fixed system actor and allow-listed source adapters.

## 11. Frontend information architecture

### 11.1 Global Bell

Wire the existing `WorkspaceScopeBar.onOpenNotifications` placeholder only when:

- Stage 22 capability is ready;
- Tenant/actor scope is verified;
- unread summary is authoritative.

The Badge displays the server count; visual display caps at `99+`, while the accessible label retains the exact count.

### 11.2 Notification Drawer

Desktop: right-side TDesign Drawer.  
375px/280px: full-screen Drawer.

Sections:

```text
Unread | All | Quality | Approvals
```

Features:

- unread emphasis;
- category/severity/status/time filters;
- partial selection and bulk mark read;
- one safe primary handoff action per row;
- explicit empty, partial, unavailable, stale and read-only states;
- no direct Alert or Approval business mutation.

### 11.3 Full Notification Center

Route (internal enterprise route reached from Bell/View all; not a new primary side-navigation item):

```text
/enterprise/notifications
```

Local tabs:

```text
Inbox | Subscriptions | Activity
```

Desktop uses TDesign PrimaryTable; mobile uses priority cards.

### 11.4 Signature visual

The single memorable element is the **Signal Routing Rail**:

```text
SOURCE -> RECIPIENT -> RECEIPT -> HANDOFF
```

It shows which stage is authoritative/unavailable for the selected Notification. It is evidence navigation, not a marketing process graphic.

## 12. Visual system

TDesign React and TDesign Icons first:

- Badge, Drawer, Tabs, PrimaryTable, Checkbox, Tag, Alert, Empty, Loading, Pagination, Dialog, Timeline, Button, Tooltip;
- existing blue `#0052D9`, ink `#17233D`, green `#2BA471`, amber `#ED7B2F`, red `#D54941`, surface `#F5F7FA`;
- monospace only for IDs, digests, revisions and timestamps;
- no remote font, gradient KPI wall, marketing Hero or copied Tencent markup;
- Uiverse/Morphicons only if TDesign lacks a non-core visual primitive.

## 13. Data behavior

- summary and first unread page load in parallel;
- all/history page loads lazily;
- actor/Tenant scope generation fences stale responses;
- unread `0` is distinct from unavailable;
- unread summary returns `as_of`, exact count and authority state; unavailable authority never becomes zero;
- source stale/currentness is projected per Notification handoff and does not mutate Receipt state;
- list pages use server opaque cursor and stable ordering;
- mutations retain one caller-owned key across retry;
- route params are projected from server allow-lists only;
- no raw source payload, query, result body, Judgment note, Approval ticket, token, email or webhook value reaches DOM/storage/console.

## 14. Readiness, preflight and downgrade

Capability:

```text
enterprise_notification_center
```

Readiness verifies schema plus:

- immutable Notification/Event guards;
- canonical keys and digests;
- recipient/receipt consistency;
- receipt lifecycle contradictions;
- orphan/cross-Tenant facts;
- unsafe route/fact payloads;
- eligible Tenant member assignment;
- unread handoff integrity.

Preflight is SELECT/metadata-only and never creates Notification/Recipient/Receipt/Event rows.

Downgrade to 0031 is blocked while any Stage 22 row exists.

## 15. Acceptance

Backend:

- SQLite online migration/downgrade;
- MySQL/PostgreSQL offline DDL, SQLite offline fail closed;
- ORM/Catalog/Readiness parity;
- deterministic source replay and recipient assignment;
- per-user isolation and exact unread count;
- read/unread/archive revision fences and bulk replay;
- subscription mute and mandatory critical override;
- source currentness and safe route handoff;
- immutable Notification/Event triggers;
- no secret/body/ticket leakage.

Frontend matrix:

```text
direct/hash x light/dark x 1440/375/280
```

Scenarios:

- unread Quality Alert;
- pending Approval;
- mixed categories and exact badge count;
- mark one read and bulk mark read;
- archived Notification;
- muted optional category;
- mandatory critical override;
- source stale/unavailable;
- partial source error;
- no notifications true empty;
- scope switch stale-response fence;
- Bell keyboard focus, Drawer Escape and focus return;
- safe deep links;
- read-only;
- zero console/page/network errors, leaks and overflow;
- source-bound fresh artifact manifest.

## 16. Explicit non-goals

- email, SMS, voice, browser Push;
- Webhook endpoints or external delivery worker;
- third-party chat robots;
- arbitrary templates or routing rules;
- notification generation from every Audit Event;
- separate Notification for every Recertification Job;
- direct approve/reject or Alert mutation inside Notification Center;
- production notification materialization during implementation or acceptance.

## 17. Protected paths

Stage 22 must not modify:

```text
frontend/src/retrieval-quality/**
core/retrieval_experiment_runner.py
server/retrieval_experiments_api.py
tests/test_retrieval_experiment_runner.py
tests/test_retrieval_experiments_api.py
```

No reset, clean, checkout or broad formatting of the cumulative worktree.


## 18.1 Design review corrections before approval

The Proposed design was tightened before implementation approval:

- added the exact Tenant-leading `tenant_members(tenant_id, account_id)` target unique;
- separated Subscription row lifecycle (`active/archived`) from user preference (`subscribed/muted`);
- bound Receipt and Event rows to the immutable Recipient identity;
- removed automatic `opened` events and `last_opened_at` writes;
- bounded bulk receipt mutations to 200 rows with deterministic lock ordering;
- declared Notification reads pure and moved source reconciliation to an internal replay-safe materializer;
- clarified that the full Notification route is Bell-driven and does not add another primary navigation resource.

## 19. Recommendation

Approve **Stage 22 — Enterprise Notification Center v1** with revision `0032_enterprise_notification_center` and the five-table in-app authority above.

It makes the global Bell, unread count, subscriptions and safe handoff real without prematurely introducing external delivery security risk or a second business state machine.
