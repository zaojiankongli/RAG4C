import { beforeEach, describe, expect, it, vi } from "vitest";

import { request } from "../../api/client";
import {
  archiveNotification,
  bulkMarkNotificationsRead,
  fetchNotification,
  fetchNotificationSubscriptions,
  fetchNotifications,
  fetchNotificationSummary,
  markNotificationRead,
  markNotificationUnread,
  updateNotificationSubscription,
  type NotificationApiScope,
} from "./notificationApi";

vi.mock("../../api/client", () => ({ request: vi.fn() }));

const mockedRequest = vi.mocked(request);
const scope: NotificationApiScope = { tenantId: "tenant-a", actorToken: "actor-token" };
const digest = "a".repeat(64);
const time = "2026-08-29T12:00:00.000000Z";

function notificationRaw() {
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
    safe_facts_json: { alert_type: "certification_expired", release_id: "release-a" },
    target_route_code: "knowledge_quality_operations",
    target_route_params_json: { dataset_id: "dataset-a", section: "releases", alert_id: "alert-a" },
    occurred_at: time,
    created_at: time,
    created_by: "system:quality-scanner",
  };
}

function pageItem() {
  return {
    notification: notificationRaw(),
    recipient: {
      id: "recipient-a",
      tenant_id: "tenant-a",
      notification_id: "notification-a",
      account_id: "account-a",
      recipient_reason: "dataset_owner",
      mandatory: true,
      assignment_digest: digest,
      assigned_at: time,
    },
    receipt: {
      id: "receipt-a",
      tenant_id: "tenant-a",
      notification_id: "notification-a",
      account_id: "account-a",
      status: "unread",
      revision: 1,
      read_at: null,
      archived_at: null,
      updated_at: time,
    },
  };
}

function summaryRaw() {
  return {
    tenant_id: "tenant-a",
    account_id: "account-a",
    state: "ready",
    unread_count: 1,
    as_of: time,
  };
}

function mutationRaw() {
  return {
    state: "applied",
    operation: "notification_receipt",
    resource_id: "notification-a",
    message: "已更新",
    retryable: false,
  };
}

describe("Stage 22 Notification API", () => {
  beforeEach(() => vi.clearAllMocks());
  it("sends scoped read requests and projects summary, unread page and detail", async () => {
    mockedRequest
      .mockResolvedValueOnce(summaryRaw())
      .mockResolvedValueOnce({
        items: [pageItem()],
        next_cursor: "cursor-next",
        invalid_item_count: 0,
      })
      .mockResolvedValueOnce({
        notification: notificationRaw(),
        recipient: pageItem().recipient,
        receipt: pageItem().receipt,
        events: [],
      });

    const controller = new AbortController();
    const summary = await fetchNotificationSummary(scope, { signal: controller.signal });
    const page = await fetchNotifications(
      scope,
      {
        status: "unread",
        category: "quality",
        severity: "critical",
        cursor: "cursor-a",
        limit: 20,
      },
      { signal: controller.signal },
    );
    const detail = await fetchNotification(scope, "notification/a", { signal: controller.signal });

    expect(summary.unread_count).toBe(1);
    expect(page.items[0]?.notification.route.code).toBe("knowledge_quality_operations");
    expect(page.next_cursor).toBe("cursor-next");
    expect(detail.events).toEqual([]);
    expect(mockedRequest).toHaveBeenNthCalledWith(
      1,
      "/api/enterprise/notifications/summary",
      expect.objectContaining({ method: "GET", signal: controller.signal }),
    );
    expect(mockedRequest).toHaveBeenNthCalledWith(
      2,
      "/api/enterprise/notifications?category=quality&cursor=cursor-a&limit=20&severity=critical&status=unread",
      expect.objectContaining({ method: "GET", signal: controller.signal }),
    );
    expect(mockedRequest).toHaveBeenNthCalledWith(
      3,
      "/api/enterprise/notifications/notification%2Fa",
      expect.objectContaining({ method: "GET", signal: controller.signal }),
    );
    const init = mockedRequest.mock.calls[0]?.[1];
    expect(init?.headers).toEqual({
      "Content-Type": "application/json",
      "X-RAG4C-Tenant": "tenant-a",
      Authorization: "Bearer actor-token",
    });
  });

  it("fetches subscriptions with strict query validation", async () => {
    mockedRequest.mockResolvedValueOnce({
      items: [
        {
          id: "subscription-a",
          tenant_id: "tenant-a",
          account_id: "account-a",
          category: "quality",
          status: "active",
          preference: "subscribed",
          active_subscription_key: "account-a:quality",
          revision: 1,
          minimum_severity: "warning",
          muted_until: null,
          created_at: time,
          created_by: "account-a",
          updated_at: time,
          updated_by: "account-a",
          archived_at: null,
          archived_by: null,
        },
      ],
      next_cursor: null,
      invalid_item_count: 0,
    });
    const result = await fetchNotificationSubscriptions(scope, { limit: 10 });
    expect(result.items).toHaveLength(1);
    expect(result.items[0]?.category).toBe("quality");
    expect(mockedRequest).toHaveBeenCalledWith(
      "/api/enterprise/notification-subscriptions?limit=10",
      expect.objectContaining({ method: "GET" }),
    );
    await expect(fetchNotifications(scope, { limit: 0 })).rejects.toThrow(/limit/i);
  });

  it("requires revision evidence and Idempotency-Key for every receipt/subscription mutation", async () => {
    mockedRequest
      .mockResolvedValueOnce(mutationRaw())
      .mockResolvedValueOnce(mutationRaw())
      .mockResolvedValueOnce(mutationRaw())
      .mockResolvedValueOnce(mutationRaw())
      .mockResolvedValueOnce(mutationRaw());
    const options = { idempotencyKey: "same-key", signal: new AbortController().signal };
    const input = { expectedRevision: 1, reason: "operator reviewed" };

    await markNotificationRead(scope, "notification-a", input, options);
    await markNotificationUnread(scope, "notification-a", input, options);
    await archiveNotification(scope, "notification-a", input, options);
    await bulkMarkNotificationsRead(
      scope,
      {
        items: [
          { notificationId: "notification-a", expectedRevision: 1 },
          { notificationId: "notification-b", expectedRevision: 2 },
        ],
        reason: "bulk review",
      },
      options,
    );
    await updateNotificationSubscription(
      scope,
      "subscription-a",
      {
        expectedRevision: 2,
        preference: "muted",
        minimumSeverity: "critical",
        mutedUntil: "2026-08-30T12:00:00.000000Z",
        reason: "temporary maintenance",
      },
      options,
    );

    expect(mockedRequest).toHaveBeenNthCalledWith(
      1,
      "/api/enterprise/notifications/notification-a/read",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Idempotency-Key": "same-key" }),
        body: JSON.stringify({ expected_revision: 1, reason: "operator reviewed" }),
      }),
    );
    expect(mockedRequest.mock.calls[3]?.[1]?.body).toBe(
      JSON.stringify({
        items: [
          { notification_id: "notification-a", expected_revision: 1 },
          { notification_id: "notification-b", expected_revision: 2 },
        ],
        reason: "bulk review",
      }),
    );
    expect(mockedRequest.mock.calls[4]?.[1]?.body).toBe(
      JSON.stringify({
        expected_revision: 2,
        preference: "muted",
        minimum_severity: "critical",
        muted_until: "2026-08-30T12:00:00.000000Z",
        reason: "temporary maintenance",
      }),
    );
    await expect(markNotificationRead(scope, "notification-a", input)).rejects.toThrow(
      /idempotency/i,
    );
    expect(() =>
      bulkMarkNotificationsRead(
        scope,
        {
          items: Array.from({ length: 201 }, (_, index) => ({
            notificationId: `notification-${index}`,
            expectedRevision: 1,
          })),
          reason: "too many",
        },
        { idempotencyKey: "bulk-key" },
      ),
    ).toThrow(/200|bulk/i);
  });
});
