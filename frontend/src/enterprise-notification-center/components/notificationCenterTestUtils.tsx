import { vi } from "vitest";

import type {
  Notification,
  NotificationDetail,
  NotificationInboxItem,
  NotificationSubscription,
} from "../model/notificationModel";
import type { EnterpriseNotificationsHook } from "../hooks/useEnterpriseNotifications";

const digest = "a".repeat(64);

export const qualityRoute = {
  code: "knowledge_quality_operations" as const,
  path: "/enterprise/knowledge-base" as const,
  query: { dataset: "dataset-001", alert: "alert-001" },
  href: "/enterprise/knowledge-base?dataset=dataset-001&alert=alert-001",
};

export const approvalRoute = {
  code: "enterprise_approval" as const,
  path: "/enterprise/approvals" as const,
  query: { request: "approval-001" },
  href: "/enterprise/approvals?request=approval-001",
};

export function makeNotification(overrides: Partial<Notification> = {}): Notification {
  const base: Notification = {
    id: "notification-001",
    tenant_id: "tenant-001",
    source_kind: "quality_alert",
    source_id: "alert-001",
    source_revision: 4,
    source_dataset_id: "dataset-001",
    category: "quality",
    severity: "critical",
    action_required: true,
    mandatory: true,
    notification_key: digest,
    source_digest: digest,
    title_code: "quality_alert_opened",
    summary_code: "quality_alert_certification_expired",
    safe_facts: {
      channel_name: "生产渠道",
      release_number: 18,
      reason_code: "certification_expired",
    },
    target_route_code: "knowledge_quality_operations",
    target_route_params: { dataset_id: "dataset-001", alert_id: "alert-001" },
    route: qualityRoute,
    occurred_at: "2026-08-29T02:30:00.000000Z",
    created_at: "2026-08-29T02:30:00.000000Z",
    created_by: "system:quality-scanner",
  };
  return { ...base, ...overrides };
}

export function makeItem(overrides: Partial<NotificationInboxItem> = {}): NotificationInboxItem {
  const notification = overrides.notification ?? makeNotification();
  return {
    notification,
    recipient: {
      id: "recipient-001",
      tenant_id: notification.tenant_id,
      notification_id: notification.id,
      account_id: "account-001",
      recipient_reason: "dataset_owner",
      mandatory: notification.mandatory,
      assignment_digest: digest,
      assigned_at: "2026-08-29T02:30:00.000000Z",
    },
    receipt: {
      id: "receipt-001",
      tenant_id: notification.tenant_id,
      notification_id: notification.id,
      account_id: "account-001",
      status: "unread",
      revision: 3,
      read_at: null,
      archived_at: null,
      updated_at: "2026-08-29T02:30:00.000000Z",
    },
    ...overrides,
  };
}

export const qualityItem = makeItem();
export const approvalItem = makeItem({
  notification: makeNotification({
    id: "notification-approval-001",
    source_kind: "approval_pending_for_me",
    source_id: "approval-001",
    source_dataset_id: null,
    category: "approval",
    severity: "warning",
    action_required: true,
    mandatory: false,
    title_code: "approval_pending_for_me",
    summary_code: "approval_requires_review",
    safe_facts: { request_type: "release_promotion", workspace_name: "平台知识库" },
    target_route_code: "enterprise_approval",
    target_route_params: { approval_request_id: "approval-001" },
    route: approvalRoute,
  }),
  recipient: {
    id: "recipient-approval-001",
    tenant_id: "tenant-001",
    notification_id: "notification-approval-001",
    account_id: "account-001",
    recipient_reason: "eligible_approver",
    mandatory: false,
    assignment_digest: digest,
    assigned_at: "2026-08-29T02:30:00.000000Z",
  },
  receipt: {
    id: "receipt-approval-001",
    tenant_id: "tenant-001",
    notification_id: "notification-approval-001",
    account_id: "account-001",
    status: "read",
    revision: 2,
    read_at: "2026-08-29T02:35:00.000000Z",
    archived_at: null,
    updated_at: "2026-08-29T02:35:00.000000Z",
  },
});

export function makeDetail(item: NotificationInboxItem = qualityItem): NotificationDetail {
  return {
    ...item,
    events: [
      {
        id: "event-001",
        tenant_id: item.notification.tenant_id,
        notification_id: item.notification.id,
        account_id: item.recipient.account_id,
        sequence: 1,
        event_type: "materialized",
        previous_event_digest: null,
        event_digest: digest,
        actor_id: "system:quality-scanner",
        request_id: "request-001",
        safe_snapshot: { severity: item.notification.severity },
        occurred_at: "2026-08-29T02:30:00.000000Z",
      },
      {
        id: "event-002",
        tenant_id: item.notification.tenant_id,
        notification_id: item.notification.id,
        account_id: item.recipient.account_id,
        sequence: 2,
        event_type: "marked_read",
        previous_event_digest: digest,
        event_digest: "b".repeat(64),
        actor_id: "account-001",
        request_id: "request-002",
        safe_snapshot: { status: "read" },
        occurred_at: "2026-08-29T02:35:00.000000Z",
      },
    ],
  };
}

export const mutedSubscription: NotificationSubscription = {
  id: "subscription-quality-001",
  tenant_id: "tenant-001",
  account_id: "account-001",
  category: "quality",
  status: "active",
  preference: "muted",
  active_subscription_key: digest,
  revision: 6,
  minimum_severity: "warning",
  muted_until: "2026-09-05T02:30:00.000000Z",
  created_at: "2026-08-01T00:00:00.000000Z",
  created_by: "account-001",
  updated_at: "2026-08-29T02:30:00.000000Z",
  updated_by: "account-001",
  archived_at: null,
  archived_by: null,
};

export const subscribedApproval: NotificationSubscription = {
  ...mutedSubscription,
  id: "subscription-approval-001",
  category: "approval",
  preference: "subscribed",
  minimum_severity: "critical",
  muted_until: null,
};

export const appliedOutcome = {
  state: "applied" as const,
  operation: "notification.update",
  resource_id: "resource-001",
  message: null,
  retryable: false,
};

export function makeHook(
  overrides: Partial<EnterpriseNotificationsHook> = {},
): EnterpriseNotificationsHook {
  const defaultHook: EnterpriseNotificationsHook = {
    active: true,
    load: {
      status: "ready",
      error: null,
      reload: vi.fn().mockResolvedValue(true),
    },
    summary: {
      status: "ready",
      value: {
        state: "ready",
        tenant_id: "tenant-001",
        account_id: "account-001",
        unread_count: 12,
        unread_state: "count",
        as_of: "2026-08-29T02:40:00.000000Z",
        reason_code: null,
      },
      error: null,
    },
    unread: {
      status: "ready",
      items: [qualityItem],
      nextCursor: null,
      invalidItemCount: 0,
      error: null,
    },
    history: {
      status: "ready",
      items: [qualityItem, approvalItem],
      nextCursor: null,
      invalidItemCount: 0,
      error: null,
      load: vi.fn().mockResolvedValue(true),
    },
    subscriptions: {
      status: "ready",
      items: [mutedSubscription, subscribedApproval],
      nextCursor: null,
      invalidItemCount: 0,
      error: null,
      load: vi.fn().mockResolvedValue(true),
    },
    detail: {
      status: "idle",
      value: null,
      error: null,
      load: vi.fn().mockResolvedValue(true),
    },
    mutation: {
      status: "idle",
      outcome: null,
      error: null,
      markRead: vi.fn().mockResolvedValue(appliedOutcome),
      markUnread: vi.fn().mockResolvedValue(appliedOutcome),
      archive: vi.fn().mockResolvedValue(appliedOutcome),
      bulkRead: vi.fn().mockResolvedValue(appliedOutcome),
      updateSubscription: vi.fn().mockResolvedValue(appliedOutcome),
      retry: vi.fn().mockResolvedValue(appliedOutcome),
    },
  };
  return { ...defaultHook, ...overrides };
}
