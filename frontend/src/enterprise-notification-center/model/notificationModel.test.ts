import { describe, expect, it } from "vitest";

import {
  projectNotification,
  projectNotificationDetail,
  projectNotificationEvent,
  projectNotificationInboxItem,
  projectNotificationRecipient,
  projectNotificationReceipt,
  projectNotificationSubscription,
  projectNotificationSummary,
  projectNotificationRoute,
  type NotificationModelScope,
} from "./notificationModel";

const scope: NotificationModelScope = { tenantId: "tenant-a", accountId: "account-a" };
const digest = "a".repeat(64);
const eventDigest = "b".repeat(64);
const timestamp = "2026-08-29T12:00:00.000000Z";

function notificationRaw(overrides: Record<string, unknown> = {}) {
  return {
    id: "notification-a",
    tenant_id: "tenant-a",
    source_kind: "quality_alert",
    source_id: "alert-a",
    source_revision: 3,
    source_dataset_id: "dataset-a",
    category: "quality",
    severity: "critical",
    action_required: true,
    mandatory: false,
    notification_key: digest,
    source_digest: digest,
    title_code: "quality.alert.opened",
    summary_code: "quality.alert.summary",
    safe_facts_json: {
      alert_type: "certification_expired",
      release_id: "release-a",
      channel_id: "channel-production",
      revision: 3,
      severity: "critical",
    },
    target_route_code: "knowledge_quality_operations",
    target_route_params_json: {
      dataset_id: "dataset-a",
      section: "releases",
      alert_id: "alert-a",
    },
    occurred_at: timestamp,
    created_at: timestamp,
    created_by: "system:quality-scanner",
    ...overrides,
  };
}

function recipientRaw(overrides: Record<string, unknown> = {}) {
  return {
    id: "recipient-a",
    tenant_id: "tenant-a",
    notification_id: "notification-a",
    account_id: "account-a",
    recipient_reason: "dataset_owner",
    mandatory: true,
    assignment_digest: digest,
    assigned_at: timestamp,
    ...overrides,
  };
}

function receiptRaw(overrides: Record<string, unknown> = {}) {
  return {
    id: "receipt-a",
    tenant_id: "tenant-a",
    notification_id: "notification-a",
    account_id: "account-a",
    status: "unread",
    revision: 1,
    read_at: null,
    archived_at: null,
    updated_at: timestamp,
    ...overrides,
  };
}

function subscriptionRaw(overrides: Record<string, unknown> = {}) {
  return {
    id: "subscription-a",
    tenant_id: "tenant-a",
    account_id: "account-a",
    category: "quality",
    status: "active",
    preference: "subscribed",
    active_subscription_key: "account-a:quality",
    revision: 2,
    minimum_severity: "warning",
    muted_until: null,
    created_at: timestamp,
    created_by: "account-a",
    updated_at: timestamp,
    updated_by: "account-a",
    archived_at: null,
    archived_by: null,
    ...overrides,
  };
}

function eventRaw(overrides: Record<string, unknown> = {}) {
  return {
    id: "event-a",
    tenant_id: "tenant-a",
    notification_id: "notification-a",
    account_id: "account-a",
    sequence: 1,
    event_type: "materialized",
    previous_event_digest: null,
    event_digest: eventDigest,
    actor_id: "system:notification-materializer",
    request_id: "request-a",
    safe_snapshot_json: {
      receipt_status: "unread",
      source_revision: 3,
      source_digest: digest,
    },
    occurred_at: timestamp,
    ...overrides,
  };
}

describe("Stage 22 Notification Center projectors", () => {
  it("projects a strict Notification envelope and derives a safe handoff route", () => {
    const projected = projectNotification(notificationRaw(), scope);

    expect(projected).toMatchObject({
      id: "notification-a",
      tenant_id: "tenant-a",
      source_kind: "quality_alert",
      source_dataset_id: "dataset-a",
      category: "quality",
      severity: "critical",
      action_required: true,
      mandatory: false,
      source_digest: digest,
    });
    expect(projected.route).toEqual({
      code: "knowledge_quality_operations",
      path: "/enterprise/knowledge-base",
      query: { dataset: "dataset-a", section: "releases", alert: "alert-a" },
      href: "/enterprise/knowledge-base?dataset=dataset-a&section=releases&alert=alert-a",
    });
    expect(projected.safe_facts).toEqual({
      alert_type: "certification_expired",
      release_id: "release-a",
      channel_id: "channel-production",
      revision: 3,
      severity: "critical",
    });
  });

  it("projects the two source-specific safe route schemas and rejects arbitrary URLs", () => {
    const approval = projectNotificationRoute(
      {
        target_route_code: "enterprise_approval",
        target_route_params_json: { request_id: "request-a" },
        source_kind: "approval_pending_for_me",
        source_dataset_id: null,
      },
      scope,
    );
    expect(approval).toEqual({
      code: "enterprise_approval",
      path: "/enterprise/approvals",
      query: { request: "request-a" },
      href: "/enterprise/approvals?request=request-a",
    });

    expect(() =>
      projectNotificationRoute(
        {
          target_route_code: "knowledge_quality_operations",
          target_route_params_json: {
            dataset_id: "dataset-a",
            section: "releases",
            href: "https://evil.example",
          },
          source_kind: "quality_alert",
          source_dataset_id: "dataset-a",
        },
        scope,
      ),
    ).toThrow(/route|unsafe|invalid/i);
  });

  it("rejects cross-tenant, extra, malformed, and source/route-inconsistent authority", () => {
    expect(() => projectNotification(notificationRaw({ tenant_id: "tenant-b" }), scope)).toThrow(
      /scope|tenant/i,
    );
    expect(() =>
      projectNotification(notificationRaw({ unexpected: "must-not-be-accepted" }), scope),
    ).toThrow(/unexpected|field|authority/i);
    expect(() => projectNotification(notificationRaw({ action_required: 1 }), scope)).toThrow(
      /boolean|action_required/i,
    );
    expect(() =>
      projectNotification(
        notificationRaw({
          source_kind: "approval_pending_for_me",
          source_dataset_id: "dataset-a",
          category: "approval",
          target_route_code: "enterprise_approval",
          target_route_params_json: { request_id: "request-a" },
        }),
        scope,
      ),
    ).toThrow(/dataset|scope|source/i);
  });

  it("projects Recipient, Receipt, Subscription and Event with lifecycle invariants", () => {
    const recipient = projectNotificationRecipient(recipientRaw(), scope);
    const receipt = projectNotificationReceipt(receiptRaw(), scope);
    const subscription = projectNotificationSubscription(subscriptionRaw(), scope);
    const event = projectNotificationEvent(eventRaw(), scope);

    expect(recipient.account_id).toBe("account-a");
    expect(receipt.status).toBe("unread");
    expect(subscription.active_subscription_key).toBe("account-a:quality");
    expect(event.previous_event_digest).toBeNull();

    expect(() => projectNotificationReceipt(receiptRaw({ status: "read" }), scope)).toThrow(
      /read_at|lifecycle/i,
    );
    expect(() =>
      projectNotificationSubscription(
        subscriptionRaw({ preference: "muted", muted_until: null }),
        scope,
      ),
    ).toThrow(/muted_until|lifecycle/i);
    expect(() =>
      projectNotificationEvent(eventRaw({ sequence: 2, previous_event_digest: null }), scope),
    ).toThrow(/previous|hash|sequence/i);
  });

  it("keeps unread zero authoritative and never converts unavailable into zero", () => {
    const zero = projectNotificationSummary(
      {
        tenant_id: "tenant-a",
        account_id: "account-a",
        state: "ready",
        unread_count: 0,
        as_of: timestamp,
      },
      scope,
    );
    expect(zero).toMatchObject({
      state: "ready",
      unread_count: 0,
      unread_state: "zero",
      as_of: timestamp,
    });

    const unavailable = projectNotificationSummary(
      {
        tenant_id: "tenant-a",
        account_id: "account-a",
        state: "unavailable",
        unread_count: null,
        as_of: null,
        reason_code: "notification_authority_unavailable",
      },
      scope,
    );
    expect(unavailable).toMatchObject({
      state: "unavailable",
      unread_count: null,
      unread_state: "unavailable",
      as_of: null,
    });
    expect(() =>
      projectNotificationSummary(
        {
          tenant_id: "tenant-a",
          account_id: "account-a",
          state: "ready",
          unread_count: null,
          as_of: timestamp,
        },
        scope,
      ),
    ).toThrow(/unread|count/i);
  });

  it("projects compound inbox/detail facts and excludes unsafe fields", () => {
    const item = projectNotificationInboxItem(
      { notification: notificationRaw(), recipient: recipientRaw(), receipt: receiptRaw() },
      scope,
    );
    const detail = projectNotificationDetail(
      {
        notification: notificationRaw(),
        recipient: recipientRaw(),
        receipt: receiptRaw({ status: "read", revision: 2, read_at: timestamp }),
        events: [
          eventRaw(),
          eventRaw({ id: "event-b", sequence: 2, previous_event_digest: eventDigest }),
        ],
      },
      scope,
    );

    expect(item.notification.id).toBe("notification-a");
    expect(item.receipt.status).toBe("unread");
    expect(detail.events).toHaveLength(2);
    expect(JSON.stringify(detail)).not.toContain("ticket");
    expect(() =>
      projectNotificationDetail(
        {
          notification: notificationRaw({ safe_facts_json: { ticket: "ticket=secret" } }),
          recipient: recipientRaw(),
          receipt: receiptRaw(),
          events: [],
        },
        scope,
      ),
    ).toThrow(/safe|ticket|fact/i);
  });
});
