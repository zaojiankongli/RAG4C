// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  NotificationApiScope,
  NotificationDetail,
  NotificationInboxItem,
  NotificationMutationOutcome,
  NotificationPage,
  NotificationSubscription,
  NotificationSummary,
} from "../api/notificationApi";
import {
  useEnterpriseNotifications,
  type EnterpriseNotificationsApi,
} from "./useEnterpriseNotifications";

const scope: NotificationApiScope = { tenantId: "tenant-a", actorToken: "actor-token-a" };
const otherScope: NotificationApiScope = { tenantId: "tenant-b", actorToken: "actor-token-b" };
const summary: NotificationSummary = {
  state: "ready",
  tenant_id: "tenant-a",
  account_id: "account-a",
  unread_count: 2,
  unread_state: "count",
  as_of: "2026-08-29T12:00:00.000000Z",
  reason_code: null,
};
const item = {} as NotificationInboxItem;
const unreadPage: NotificationPage<NotificationInboxItem> = {
  items: [item],
  next_cursor: "unread-next",
  invalid_item_count: 0,
};
const historyPage: NotificationPage<NotificationInboxItem> = {
  items: [item],
  next_cursor: null,
  invalid_item_count: 0,
};
const detail = {} as NotificationDetail;
const subscription = {} as NotificationSubscription;
const applied: NotificationMutationOutcome = {
  state: "applied",
  operation: "notification_receipt",
  resource_id: "notification-a",
  message: "已更新",
  retryable: false,
};
const unavailable: NotificationMutationOutcome = {
  state: "unavailable",
  operation: "notification_receipt",
  resource_id: null,
  message: null,
  retryable: false,
};

function makeApi(overrides: Partial<EnterpriseNotificationsApi> = {}): EnterpriseNotificationsApi {
  return {
    fetchSummary: vi.fn().mockResolvedValue(summary),
    fetchNotifications: vi.fn().mockResolvedValue(unreadPage),
    fetchDetail: vi.fn().mockResolvedValue(detail),
    fetchSubscriptions: vi.fn().mockResolvedValue({
      items: [subscription],
      next_cursor: null,
      invalid_item_count: 0,
    }),
    markRead: vi.fn().mockResolvedValue(applied),
    markUnread: vi.fn().mockResolvedValue(applied),
    archive: vi.fn().mockResolvedValue(applied),
    bulkRead: vi.fn().mockResolvedValue(applied),
    updateSubscription: vi.fn().mockResolvedValue(applied),
    ...overrides,
  };
}

describe("useEnterpriseNotifications", () => {
  it("is inactive-safe and performs no reads or mutations until enabled", async () => {
    const api = makeApi();
    const { result, rerender } = renderHook(
      ({ enabled }) => useEnterpriseNotifications(scope, { enabled, api }),
      { initialProps: { enabled: false } },
    );

    expect(result.current.active).toBe(false);
    expect(result.current.load.status).toBe("idle");
    expect(result.current.history.status).toBe("idle");
    expect(api.fetchSummary).not.toHaveBeenCalled();
    expect(api.fetchNotifications).not.toHaveBeenCalled();

    await act(async () => {
      await expect(
        result.current.mutation.markRead("notification-a", {
          expectedRevision: 1,
          reason: "inactive",
        }),
      ).resolves.toBeNull();
    });
    expect(api.markRead).not.toHaveBeenCalled();

    rerender({ enabled: true });
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    expect(api.fetchSummary).toHaveBeenCalledTimes(1);
    expect(api.fetchNotifications).toHaveBeenCalledTimes(1);
  });

  it("loads summary and unread in parallel with one AbortSignal and leaves history lazy", async () => {
    let resolveSummary: ((value: NotificationSummary) => void) | undefined;
    let resolveUnread: ((value: NotificationPage<NotificationInboxItem>) => void) | undefined;
    const summaryPromise = new Promise<NotificationSummary>((resolve) => {
      resolveSummary = resolve;
    });
    const unreadPromise = new Promise<NotificationPage<NotificationInboxItem>>((resolve) => {
      resolveUnread = resolve;
    });
    const api = makeApi({
      fetchSummary: vi.fn().mockReturnValue(summaryPromise),
      fetchNotifications: vi.fn().mockReturnValue(unreadPromise),
    });
    const { result } = renderHook(() => useEnterpriseNotifications(scope, { enabled: true, api }));

    await waitFor(() => {
      expect(api.fetchSummary).toHaveBeenCalledTimes(1);
      expect(api.fetchNotifications).toHaveBeenCalledTimes(1);
    });
    const summarySignal = vi.mocked(api.fetchSummary).mock.calls[0]?.[1]?.signal;
    const unreadSignal = vi.mocked(api.fetchNotifications).mock.calls[0]?.[2]?.signal;
    expect(summarySignal).toBeInstanceOf(AbortSignal);
    expect(unreadSignal).toBe(summarySignal);
    expect(vi.mocked(api.fetchNotifications).mock.calls[0]?.[1]).toEqual({ status: "unread" });
    expect(result.current.history.status).toBe("idle");

    await act(async () => {
      resolveSummary?.(summary);
      resolveUnread?.(unreadPage);
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    expect(result.current.summary.value?.unread_count).toBe(2);
    expect(result.current.unread.items).toHaveLength(1);
  });

  it("loads history and detail lazily, preserving distinct empty and unavailable states", async () => {
    const api = makeApi({
      fetchNotifications: vi.fn().mockResolvedValue({
        items: [],
        next_cursor: null,
        invalid_item_count: 0,
      }),
      fetchDetail: vi.fn().mockRejectedValue(new Error("history detail unavailable")),
    });
    const { result } = renderHook(() => useEnterpriseNotifications(scope, { enabled: true, api }));
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    expect(result.current.history.status).toBe("idle");

    await act(async () => {
      await expect(result.current.history.load()).resolves.toBe(true);
    });
    expect(result.current.history.status).toBe("empty");
    expect(vi.mocked(api.fetchNotifications).mock.calls[1]?.[1]).toEqual({ status: "all" });

    await act(async () => {
      await expect(result.current.detail.load("notification-a")).resolves.toBe(false);
    });
    expect(result.current.detail.status).toBe("unavailable");
    expect(result.current.detail.value).toBeNull();
  });

  it("fences stale context responses, aborts old signals, and does not leak old data", async () => {
    let resolveOldSummary: ((value: NotificationSummary) => void) | undefined;
    const oldSummary = new Promise<NotificationSummary>((resolve) => {
      resolveOldSummary = resolve;
    });
    const nextSummary: NotificationSummary = {
      ...summary,
      tenant_id: "tenant-b",
      account_id: "account-b",
    };
    const api = makeApi({
      fetchSummary: vi.fn((nextScope: NotificationApiScope) =>
        nextScope.tenantId === "tenant-a" ? oldSummary : Promise.resolve(nextSummary),
      ),
      fetchNotifications: vi.fn((nextScope: NotificationApiScope) =>
        nextScope.tenantId === "tenant-a"
          ? new Promise<NotificationPage<NotificationInboxItem>>(() => undefined)
          : Promise.resolve(historyPage),
      ),
    });
    const { result, rerender } = renderHook(
      ({ currentScope }) => useEnterpriseNotifications(currentScope, { enabled: true, api }),
      { initialProps: { currentScope: scope } },
    );
    await waitFor(() => expect(api.fetchSummary).toHaveBeenCalledTimes(1));
    const oldSignal = vi.mocked(api.fetchSummary).mock.calls[0]?.[1]?.signal;

    rerender({ currentScope: otherScope });
    await waitFor(() => expect(result.current.summary.value?.tenant_id).toBe("tenant-b"));
    expect(oldSignal?.aborted).toBe(true);

    await act(async () => {
      resolveOldSummary?.(summary);
      await Promise.resolve();
    });
    expect(result.current.summary.value?.tenant_id).toBe("tenant-b");
  });

  it("serializes mutations, retains the caller key for retry, and blocks read-only/unavailable outcomes", async () => {
    let resolveFirst: ((value: NotificationMutationOutcome) => void) | undefined;
    const first = new Promise<NotificationMutationOutcome>((resolve) => {
      resolveFirst = resolve;
    });
    const markRead = vi.fn().mockReturnValueOnce(first).mockResolvedValueOnce(applied);
    const api = makeApi({ markRead });
    const { result } = renderHook(() => useEnterpriseNotifications(scope, { enabled: true, api }));
    await waitFor(() => expect(result.current.load.status).toBe("ready"));

    let firstResult: Promise<NotificationMutationOutcome | null>;
    let secondResult: Promise<NotificationMutationOutcome | null>;
    await act(async () => {
      firstResult = result.current.mutation.markRead(
        "notification-a",
        { expectedRevision: 1, reason: "first" },
        { idempotencyKey: "caller-key" },
      );
      secondResult = result.current.mutation.markRead(
        "notification-b",
        { expectedRevision: 1, reason: "second" },
        { idempotencyKey: "second-key" },
      );
      await Promise.resolve();
    });
    expect(markRead).toHaveBeenCalledTimes(1);
    await act(async () => {
      resolveFirst?.(applied);
      await firstResult!;
    });
    await waitFor(() => expect(markRead).toHaveBeenCalledTimes(2));
    expect(markRead.mock.calls[1]?.[3]?.idempotencyKey).toBe("second-key");
    await act(async () => {
      await secondResult!;
    });

    const retrying = makeApi({
      markRead: vi
        .fn()
        .mockRejectedValueOnce(new Error("temporary"))
        .mockResolvedValueOnce(applied),
    });
    const retryHook = renderHook(() =>
      useEnterpriseNotifications(scope, { enabled: true, api: retrying }),
    );
    await waitFor(() => expect(retryHook.result.current.load.status).toBe("ready"));
    await act(async () => {
      await retryHook.result.current.mutation.markRead(
        "notification-a",
        { expectedRevision: 1, reason: "retry" },
        { idempotencyKey: "retry-key" },
      );
    });
    expect(retryHook.result.current.mutation.status).toBe("error");
    await act(async () => {
      await retryHook.result.current.mutation.retry();
    });
    expect(vi.mocked(retrying.markRead).mock.calls[0]?.[3]?.idempotencyKey).toBe("retry-key");
    expect(vi.mocked(retrying.markRead).mock.calls[1]?.[3]?.idempotencyKey).toBe("retry-key");

    const readOnlyApi = makeApi();
    const readOnlyHook = renderHook(() =>
      useEnterpriseNotifications(scope, { enabled: true, readOnly: true, api: readOnlyApi }),
    );
    await waitFor(() => expect(readOnlyHook.result.current.load.status).toBe("ready"));
    await act(async () => {
      await expect(
        readOnlyHook.result.current.mutation.archive("notification-a", {
          expectedRevision: 1,
          reason: "blocked",
        }),
      ).resolves.toBeNull();
    });
    expect(readOnlyApi.archive).not.toHaveBeenCalled();

    const unavailableApi = makeApi({ markRead: vi.fn().mockResolvedValue(unavailable) });
    const unavailableHook = renderHook(() =>
      useEnterpriseNotifications(scope, { enabled: true, api: unavailableApi }),
    );
    await waitFor(() => expect(unavailableHook.result.current.load.status).toBe("ready"));
    await act(async () => {
      await unavailableHook.result.current.mutation.markRead(
        "notification-a",
        { expectedRevision: 1, reason: "unavailable" },
        { idempotencyKey: "unavailable-key" },
      );
    });
    expect(unavailableHook.result.current.mutation.status).toBe("error");
    expect(unavailableHook.result.current.mutation.outcome).toBeNull();
  });
});
