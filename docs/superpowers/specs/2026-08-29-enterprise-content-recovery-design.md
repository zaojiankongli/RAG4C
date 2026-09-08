# Enterprise Content Recovery Center v1.0 Design

Date: 2026-08-29
Status: Approved by standing user authorization
Stage: 23
Migration revision: `0033_enterprise_content_recovery`
Down revision: `0032_enterprise_notification_center`

## 1. Problem

RAG4C already has a production-grade irreversible deletion executor in `0011_durable_document_delete`. The Documents UI can directly submit durable deletes, but the product has no enterprise recovery layer in front of that executor:

- no Document recycle bin;
- no Tenant content retention policy;
- no restore lifecycle;
- no legal hold;
- no approval-gated purge request;
- no immutable recovery event chain;
- no dedicated official-looking recovery center.

This means a normal content-management action is too close to permanent multi-store deletion. Tencent Cloud products commonly insert a recycle/retention boundary before irreversible cleanup.

## 2. Decision

Build:

```text
Enterprise Content Recovery Center v1.0
```

The system becomes:

```text
Documents UI delete
    -> recycle authority (0033)
    -> retention / legal hold / restore
    -> approval-gated purge request
    -> future approved executor
    -> existing 0011 durable delete
```

Stage 23 does not automatically consume Approval Execution Tickets and does not automatically start physical deletion. It creates the authoritative prerequisites and safe handoff only.

## 3. Alternatives

### Enterprise Search & Discovery

Deferred. Search, Query, document q filters and recent assets already exist. Saved searches and cross-KB discovery remain valuable but do not close the current destructive lifecycle gap.

### Unified Task Center

Deferred. The repository already has ingest, sync, delete, audit export, quality scan and recertification jobs. A unified envelope is valuable but has broader cross-domain coupling.

### UI-only Recycle Bin

Rejected. A visual shell without database retention, legal hold, revision fences and purge approval would be misleading and not enterprise-grade.

## 4. Scope

Included:

- Document recycle and restore;
- Tenant content retention policy;
- Document legal holds;
- approval-gated purge requests;
- immutable recovery event history;
- strict read/mutation API separation;
- Documents UI delete replacement when capability is ready;
- `/enterprise/recycle-bin` primary route;
- TDesign desktop table and mobile cards;
- retention, hold, restore and purge-request dialogs;
- read-only preflight and source-bound Playwright.

Excluded:

- QA recycle entries;
- Dataset recycle;
- external object-store restoration;
- automatic purge scheduler;
- consuming Approval Execution Tickets;
- automatic physical deletion;
- Notification source-kind expansion;
- production migration/backfill/restore/purge.

## 5. Database authority

Exactly five new tables.

### 5.1 `tenant_content_retention_policies`

Tenant singleton policy.

Columns:

```text
id
 tenant_id
 status                       active | paused
 retention_days               1..3650
 auto_purge_enabled            boolean
 purge_requires_approval       boolean
 revision                      > 0
 created_at / created_by
 updated_at / updated_by
```

Identity:

```text
UNIQUE(tenant_id)
UNIQUE(tenant_id, id)
```

Stage 23 defaults to `auto_purge_enabled=false` and `purge_requires_approval=true`.

### 5.2 `tenant_document_recycle_entries`

One current recovery authority per Tenant/Dataset/Document.

Columns:

```text
id
 tenant_id
 dataset_id
 document_id
 recycle_generation            > 0
 active_recycle_key             nullable canonical key
 status                         recycled | restoring | restored |
                                purge_requested | purged | failed
 revision                       > 0
 document_mutation_generation   >= 0
 original_lifecycle_state       active | expired
 original_retrieval_enabled     boolean
 retention_days_snapshot        1..3650
 recycled_at / recycled_by
 purge_eligible_at
 restored_at / restored_by
 purge_requested_at
 purged_at / purged_by
 safe_snapshot_json
 snapshot_digest
 created_at / updated_at
```

Canonical active identity:

```text
active_recycle_key = dataset_id + ':' + document_id
```

for `recycled`, `restoring`, `purge_requested`; otherwise null.

Tenant-leading FKs target Tenant, Dataset and Document.

No document body, raw metadata, query, note, token, credential or external URL may be copied into `safe_snapshot_json`.

### 5.3 `tenant_document_legal_holds`

Columns:

```text
id
 tenant_id / dataset_id / document_id / recycle_entry_id
 status                         active | released
 active_hold_key                nullable canonical key
 revision
 reason_code
 safe_reason
 held_at / held_by
 released_at / released_by
 created_at / updated_at
```

Only one active hold per recycle entry and reason code.

Active hold blocks purge request and future purge execution. Restore is allowed unless policy explicitly changes in a future stage.

### 5.4 `tenant_document_purge_requests`

Columns:

```text
id
 tenant_id / dataset_id / document_id / recycle_entry_id
 status                         pending_approval | approved |
                                cancelled | expired | executed
 revision
 expected_entry_revision
 request_digest
 idempotency_key_digest
 approval_request_id
 retention_snapshot_json
 legal_hold_count_snapshot
 requested_at / requested_by
 approved_at
 cancelled_at / cancelled_by
 expires_at
 executed_at
 created_at / updated_at
```

Rules:

- request cannot be created before `purge_eligible_at`;
- active legal hold count must be zero;
- approval request action type is `document_purge`;
- client cannot submit or consume an execution ticket;
- Stage 23 never transitions to `executed` through public API.

### 5.5 `tenant_document_recovery_events`

Append-only chain.

Event types:

```text
recycled
restored
hold_applied
hold_released
purge_requested
purge_approved
purge_cancelled
```

Columns include Tenant scope, entry/document identity, sequence, previous digest, event digest, actor, request ID, safe snapshot and occurred time.

Database insert guard verifies:

- recycle entry exists;
- sequence is contiguous;
- previous digest matches;
- first event is `recycled`;
- update/delete is forbidden.

Readiness and read services recompute canonical event digests.

## 6. Document lifecycle

Extend `documents.lifecycle_state` with `recycled`.

Allowed transitions:

```text
active  -> recycled
expired -> recycled
recycled -> active
recycled -> delete_requested   # reserved for future approved purge executor
```

Invariants:

- recycled Document has `retrieval_enabled=false`;
- recycle captures original lifecycle and retrieval flag;
- restore requires the same Tenant/Dataset/Document and expected entry revision;
- restore checks current Dataset, Workspace and membership authority;
- restore increments `mutation_generation`;
- restore returns to the captured lifecycle state and retrieval flag;
- recycled Documents are excluded by default from Document Catalog, retrieval and recent assets;
- capability uncertainty never falls back to durable delete.

## 7. Approval integration

Extend Approval action type allow-list with:

```text
document_purge
```

Recovery Center creates a normal Approval Request with a strict safe snapshot:

```text
recycle_entry_id
 document_id
 dataset_id
 entry_revision
 purge_eligible_at
 retention_days_snapshot
 legal_hold_count = 0
```

Recovery Center may navigate to `/enterprise/approvals?request=<id>`. It cannot approve, reject or execute the request.

## 8. Core services

Pure authority module:

```python
canonical_recycle_key
canonical_recycle_snapshot
canonical_recovery_event
canonical_purge_request_digest
project_recovery_route
```

Read service:

```python
get_recovery_summary
list_recycle_entries
get_recycle_entry
get_retention_policy
list_legal_holds
list_purge_requests
```

Mutation service:

```python
recycle_document
restore_document
apply_legal_hold
release_legal_hold
update_content_retention_policy
request_document_purge
cancel_document_purge_request
```

All mutations are actor-only, Tenant-scoped, revision-fenced and idempotent.

## 9. HTTP API

```text
GET   /api/enterprise/recovery/summary
GET   /api/enterprise/recycle-bin
GET   /api/enterprise/recycle-bin/{entry_id}
POST  /api/enterprise/recycle-bin/documents/{document_id}
POST  /api/enterprise/recycle-bin/{entry_id}/restore
GET   /api/enterprise/recycle-bin/{entry_id}/holds
POST  /api/enterprise/recycle-bin/{entry_id}/holds
POST  /api/enterprise/recycle-bin/{entry_id}/holds/{hold_id}/release
GET   /api/enterprise/recycle-bin/{entry_id}/purge-requests
POST  /api/enterprise/recycle-bin/{entry_id}/purge-requests
POST  /api/enterprise/recycle-bin/{entry_id}/purge-requests/{request_id}/cancel
GET   /api/enterprise/recovery/retention-policy
PATCH /api/enterprise/recovery/retention-policy
```

Read routes use read engine only; mutation routes use mutation engine only. Every mutation requires Idempotency-Key.

## 10. Frontend

Route:

```text
/enterprise/recycle-bin
#/enterprise/recycle-bin
```

Primary navigation:

```text
知识工作台
├─ 知识概览
├─ 文档管理
├─ 回收站
├─ 知识组织
...
```

Page composition:

```text
Header + Authority Banner
Metric Strip
RECYCLED -> RETAINED -> HELD / ELIGIBLE -> RESTORE / PURGE REQUEST
Filter Toolbar
Desktop PrimaryTable / Mobile Cards
Detail Drawer
Restore Dialog
Legal Hold Dialog
Purge Approval Dialog
Retention Policy Drawer
```

DocumentsPage behavior when capability ready:

- “删除” becomes “移入回收站”;
- single and batch recycle use recovery API;
- no direct call to durable delete API;
- if recovery capability is unavailable, destructive controls are disabled with an explicit explanation;
- no fallback to permanent delete.

TDesign React and TDesign Icons are mandatory for primary UI. Uiverse/Morphicons may only fill missing decorative/non-core visuals.

## 11. Signature visual

```text
RECYCLE -> RETAIN -> HOLD / ELIGIBLE -> RESTORE / PURGE REQUEST
```

Use restrained blue/gray enterprise surfaces, amber for retention warnings, red only for purge request risk, and green for restored evidence.

## 12. Readiness and preflight

Capability:

```text
enterprise_content_recovery
```

Readiness validates:

- exactly five tables and schema contract;
- Document `recycled` lifecycle support;
- Tenant-leading FKs and canonical active keys;
- active Entry/Document lifecycle consistency;
- recycled Documents have retrieval disabled;
- legal hold lifecycle;
- purge retention and hold snapshots;
- approval action type support;
- immutable/contiguous/canonical Event chain;
- unsafe snapshot fields;
- Stage 22 remains ready at 0033.

Preflight is read-only and reports counts/blockers. It never recycles, restores, creates holds, creates approvals or starts durable deletion.

## 13. Migration and downgrade

- MySQL/PostgreSQL offline DDL supported;
- SQLite offline fails closed;
- downgrade requires online preflight;
- downgrade is blocked when any new table is nonempty or any Document remains `recycled`;
- no automatic production migration or backfill.

## 14. Verification

Backend:

- migration/ORM/catalog/readiness;
- pure canonical authority;
- lifecycle and Tenant isolation;
- legal hold and retention fences;
- Approval request integration;
- no durable delete invocation from recycle/restore routes;
- idempotency and concurrency;
- preflight full regression.

Frontend:

- strict model/API/hook;
- TDesign table/mobile cards;
- disabled permanent delete fallback;
- direct/hash routes;
- a11y, keyboard, 375/280 responsive;
- production build.

Playwright:

```text
direct/hash × light/dark × 1440/375/280
```

Scenarios include recycle, restore, legal hold, hold release, retention warning, purge blocked by time, purge blocked by hold, approval handoff, partial/unavailable/empty/read-only, scope switch, keyboard/Escape/focus return and zero leak/overflow/errors.

## 15. Safety

No real production migration, backfill, recycle, restore, hold, approval, purge or durable delete execution is permitted during implementation or acceptance.
