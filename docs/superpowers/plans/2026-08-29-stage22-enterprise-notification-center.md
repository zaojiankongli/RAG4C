# Enterprise Notification Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax for tracking.

**Goal:** Build a Tenant-safe global in-app Notification Center with exact per-user unread counts, subscriptions, immutable source envelopes, recipient evidence, receipt lifecycle, safe deep-link handoff and a TDesign enterprise UI.

**Architecture:** Revision `0032_enterprise_notification_center` adds five notification tables and a Tenant-leading membership target unique. Allow-listed materializers transform current Stage 21 Quality Alerts and pending Approval authority into immutable Notifications, frozen Recipients, unread Receipts and immutable Events. Read routes never materialize; receipt/subscription mutations are revision-fenced, idempotent and audited. The App-shell Bell opens a TDesign Drawer and the internal `/enterprise/notifications` route provides Inbox, Subscriptions and Activity.

**Tech Stack:** Python 3.13, SQLAlchemy 2, Alembic, FastAPI/Pydantic, SQLite/MySQL/PostgreSQL DDL, React 18, TypeScript 5.6, TDesign React, TDesign Icons, Vitest, Playwright/agent-browser.

**Spec:** `docs/superpowers/specs/2026-08-29-enterprise-notification-center-design.md`

## Global Constraints

- Revision is exactly `0032_enterprise_notification_center`; down revision is `0031_enterprise_release_quality_operations`.
- Stage 22 creates exactly five Notification tables and adds `tenant_members(tenant_id, account_id)` as a Tenant-leading FK target unique.
- Initial source kinds are only `quality_alert` and `approval_pending_for_me`.
- In-app only: no email, SMS, voice, Push, Webhook or third-party robot delivery.
- Notification list/summary routes are read-only and never invoke materialization.
- Opening Bell/Drawer/detail does not mark read and does not write an Event.
- Notification Center never directly acknowledges/resolves a Quality Alert or approves/rejects an Approval request.
- TDesign React and TDesign Icons are the core UI library.
- Protected Retrieval Quality paths remain untouched.
- Do not reset, clean, checkout or broadly format the cumulative worktree.
- Do not execute production migration, materialization, receipt mutation or external delivery.
- Do not create commits in the cumulative worktree.

---

### Task 1: 0032 Migration, ORM, Catalog and Readiness

**Files:**

- Create: `catalog_migrations/versions/0032_enterprise_notification_center.py`
- Modify: `models/orm.py`
- Modify: `core/catalog_schema.py`
- Modify: `server/enterprise_readiness_api.py`
- Create: `tests/test_enterprise_notification_center_migration.py`
- Create: `tests/test_enterprise_notification_center_orm.py`
- Modify: `tests/test_catalog_schema.py`
- Modify: `tests/test_enterprise_readiness_api.py`

**Interfaces:**

- Produces ORM models:
  - `TenantNotificationSubscription`
  - `TenantNotification`
  - `TenantNotificationRecipient`
  - `TenantNotificationReceipt`
  - `TenantNotificationEvent`
- Produces `inspect_enterprise_notification_center_capability(bind)`.

- [x] Write RED tests for new head/down revision, exactly five tables and `uq_tenant_members_tenant_account`.
- [x] Write RED tests for exact columns, not-null, indexes, canonical active subscription key, Notification/Recipient/Receipt/Event identities and Tenant-leading FKs.
- [x] Write RED tests for immutable Notification/Event UPDATE/DELETE triggers.
- [x] Write RED tests for receipt lifecycle, event sequence/hash chain and subscription lifecycle checks.
- [x] Write RED tests for MySQL/PostgreSQL offline DDL and SQLite offline fail closed.
- [x] Write RED tests for clean downgrade and nonempty authority downgrade blocker.
- [x] Run focused tests and verify failure is missing 0032 behavior.
- [x] Implement migration and ORM in matching column order.
- [x] Add Catalog manifest/readiness schema and data integrity checks.
- [x] Make Stage 21 and earlier capability inspectors remain ready at 0032.
- [x] Run focused pytest, Ruff, format-check, py_compile and Alembic head verification.

**Verification:**

```powershell
uv run pytest -q `
  tests/test_enterprise_notification_center_migration.py `
  tests/test_enterprise_notification_center_orm.py `
  tests/test_catalog_schema.py `
  tests/test_enterprise_readiness_api.py
```

### Task 2: Pure Notification Envelope, Route and Digest Authority

**Files:**

- Create: `core/enterprise_notification_center.py`
- Create: `tests/test_enterprise_notification_center_core.py`

**Interfaces:**

```python
canonical_notification_digest(namespace, value)
canonical_notification_key(...)
project_notification_source(...)
project_notification_route(...)
project_notification_payload(...)
canonical_assignment_digest(...)
canonical_notification_event(...)
```

- [x] Write RED tests for deterministic domain-separated digests and UTC microseconds.
- [x] Write RED tests for `quality_alert` and `approval_pending_for_me` source allow-lists.
- [x] Write RED tests for route codes `knowledge_quality_operations` and `enterprise_approval` with exact parameter schemas.
- [x] Write RED tests that raw URLs, query, result body, note, ticket, email, token and credentials fail closed.
- [x] Write RED tests for Notification/Recipient/Event canonical keys and sequence hash chain.
- [x] Implement minimal pure functions with no ORM imports.
- [x] Run focused pytest/Ruff/format/py_compile.

### Task 3: Replay-safe Notification Materializer

**Files:**

- Create: `core/enterprise_notification_materializer.py`
- Create: `tests/test_enterprise_notification_materializer.py`
- Create concurrency tests as required.

**Interfaces:**

```python
materialize_quality_alert_notifications(...)
materialize_pending_approval_notifications(...)
reconcile_notification_sources(...)
```

**Requirements:**

- Quality recipients: Dataset owner plus active Tenant owner/admin with current `knowledge.read`, and optional explicit subscribers with `knowledge.read`.
- Approval recipients: accounts currently confirmed eligible by Approval authority.
- Mandatory/action-required sources bypass optional mute.
- Materializer inserts/replays Notification, Recipient, unread Receipt and materialized Event in one transaction.

- [x] Write RED tests for deterministic source replay and no duplicates.
- [x] Write RED tests for frozen recipient reasons and current membership/permission checks.
- [x] Write RED tests for Quality source revision/digest and Approval eligibility revalidation.
- [x] Write RED tests for subscription mute, minimum severity and mandatory override.
- [x] Write RED tests for cross-Tenant/Dataset and unsafe source failure.
- [x] Implement ordered locking and replay-safe inserts.
- [x] Run focused tests including concurrent materialization.

### Task 4: Receipt and Subscription Lifecycle Services

**Files:**

- Create: `core/enterprise_notification_receipts.py`
- Create: `core/enterprise_notification_subscriptions.py`
- Create: `tests/test_enterprise_notification_receipts.py`
- Create: `tests/test_enterprise_notification_subscriptions.py`

**Interfaces:**

```python
list_notifications(...)
get_notification_summary(...)
get_notification(...)
mark_notification_read(...)
mark_notification_unread(...)
archive_notification(...)
bulk_mark_notifications_read(...)
list_notification_subscriptions(...)
update_notification_subscription(...)
```

- [x] Write RED tests for per-account Recipient/Receipt isolation and exact unread count.
- [x] Write RED tests that no Receipt is unavailable, not unread.
- [x] Write RED tests for read/unread/archive revision fences and Event append.
- [x] Write RED tests that opening/read routes cause no mutation.
- [x] Write RED tests for bulk max 200, deterministic lock order and one idempotency key replay.
- [x] Write RED tests for subscription active/archived and subscribed/muted lifecycle.
- [x] Write RED tests for stale Quality/Approval handoff and safe route projection.
- [x] Implement services using read/mutation engine separation and Tenant audit.
- [x] Run focused tests/Ruff/format/py_compile.

### Task 5: Strict Notification API and App Mount

**Files:**

- Create: `server/enterprise_notification_api.py`
- Modify: `server/app.py`
- Create: `tests/test_enterprise_notification_api.py`

**Routes:**

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

- [x] Write RED tests for strict Pydantic extra rejection and exact integers/booleans.
- [x] Write RED tests for actor-only Receipt mutation and subscription ownership.
- [x] Write RED tests for read/mutation engine separation and Idempotency-Key on every mutation.
- [x] Write RED tests for cursor/limit/filter validation and safe HTTP error projection.
- [x] Implement router with explicit service doubles and default module availability tests.
- [x] Mount router in the main App.
- [x] Run focused API tests and app mount regression.

### Task 6: Read-only Preflight and Runbook

**Files:**

- Modify: `scripts/enterprise_catalog_upgrade.py`
- Modify: `tests/test_enterprise_catalog_upgrade.py`
- Modify: `docs/operations/enterprise-catalog-upgrade.md`

- [x] Write RED tests for five table counts, canonical identities, immutable guards, recipient/receipt/event consistency and unsafe route blockers.
- [x] Implement `notification_center` read-only preflight report.
- [x] Integrate blockers into `safe_to_upgrade` without materializing Notifications.
- [x] Add 0032 manual migration/downgrade/container-smoke runbook.
- [x] Run full enterprise catalog upgrade tests.

### Task 7: Frontend Model, API and Hook

**Files:**

- Create: `frontend/src/enterprise-notification-center/model/notificationModel.ts`
- Create: `frontend/src/enterprise-notification-center/model/notificationModel.test.ts`
- Create: `frontend/src/enterprise-notification-center/api/notificationApi.ts`
- Create: `frontend/src/enterprise-notification-center/api/notificationApi.test.ts`
- Create: `frontend/src/enterprise-notification-center/hooks/useEnterpriseNotifications.ts`
- Create: `frontend/src/enterprise-notification-center/hooks/useEnterpriseNotifications.test.tsx`

**Interfaces:**

- strict Notification, Recipient, Receipt, Subscription, Event and Summary projectors;
- exact zero/unavailable unread distinction;
- list/summary/detail and Receipt/Subscription API methods;
- inactive safety, parallel summary/unread load, lazy history, AbortSignal and context generation fences;
- serialized mutation queue with same-key retry.

- [x] Write RED projector/API/hook tests.
- [x] Implement strict safe models and transport.
- [x] Implement hook with stale response and read-only guards.
- [x] Run focused Vitest, Prettier and TypeScript.

### Task 8: TDesign Bell, Drawer and Full Notification Center

**Files:**

- Create: `frontend/src/enterprise-notification-center/components/NotificationBell.tsx`
- Create: `frontend/src/enterprise-notification-center/components/NotificationDrawer.tsx`
- Create: `frontend/src/enterprise-notification-center/components/NotificationCenter.tsx`
- Create: `frontend/src/enterprise-notification-center/components/NotificationDetailDrawer.tsx`
- Create: `frontend/src/enterprise-notification-center/components/NotificationSubscriptionPanel.tsx`
- Create related component tests.
- Create: `frontend/src/enterprise-notification-center/notification-center.css`

**UI:**

```text
Bell -> Unread Drawer -> View all -> Inbox | Subscriptions | Activity
```

Signature:

```text
SOURCE -> RECIPIENT -> RECEIPT -> HANDOFF
```

- [x] Write RED tests for exact Badge, hidden/unavailable Bell and keyboard activation.
- [x] Write RED tests for Unread/All/Quality/Approvals, filters and bulk mark read.
- [x] Write RED tests for desktop PrimaryTable vs mobile cards without duplicate DOM.
- [x] Write RED tests for safe handoff only, no direct Alert/Approval mutation.
- [x] Write RED tests for Drawer focus trap/Escape/focus return and 375/280 behavior.
- [x] Implement TDesign UI and restrained enterprise CSS.
- [x] Run focused Vitest/Prettier/TypeScript.

### Task 9: Route and App-shell Integration

**Files:**

- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/AppProviders.tsx` if a Notification provider is required.
- Modify: `frontend/src/run/appRoute.ts`
- Modify: `frontend/src/run/appRoute.test.ts`
- Modify: `frontend/src/ui/enterprise/WorkspaceScopeBar.tsx`
- Modify related App accessibility/mobile/initial-route tests.

- [x] Write RED tests for direct/hash `/enterprise/notifications` route precedence.
- [x] Write RED tests that Bell remains hidden until capability and summary are ready.
- [x] Write RED tests for exact accessible unread label and `99+` visual cap.
- [x] Implement Bell callback/provider and internal route without adding primary navigation.
- [x] Implement safe Quality and Approval handoff navigation.
- [x] Run focused App/route/a11y/mobile tests and production build.

### Task 10: Playwright, Reviews and Final Gates

**Files:**

- Create: `output/playwright/enterprise-notification-center-stage22/**`
- Create: `docs/research/2026-08-29-enterprise-notification-center-stage22-visual-acceptance.md`

- [x] Run independent backend code review for Tenant isolation, receipts and materializer replay.
- [x] Fix all Critical/Important findings with regression tests.
- [x] Run backend Stage 22 integration suite.
- [x] Run frontend focused tests, TypeScript, Prettier and Vite build.
- [x] Run source-bound controlled Playwright matrix:

```text
direct/hash x light/dark x 1440/375/280
```

- [x] Cover exact Badge count, unread/read/bulk, Quality/Approval handoff, subscription mute/mandatory override, stale/unavailable/partial/empty/read-only and scope switch.
- [x] Require zero console/page/unknown request/errors, zero sensitive leaks and zero horizontal overflow.
- [x] Generate fresh result/manifest/PNG and standalone fail-closed gate tests.
- [x] Run `git diff --check`, Ruff, py_compile and Alembic head.

## Plan Self-review

- Spec coverage: all schema, materializer, receipt, subscription, API, Bell, Drawer, route, preflight and Playwright requirements map to Tasks 1-10.
- No implementation placeholders or unbounded external delivery work are included.
- Type consistency: Notification source/route/status enums match the design spec.
- Protected paths and production boundaries are explicit.
- The approved recommended plan is self-approved under the user's standing authorization and proceeds through subagent-driven TDD.
