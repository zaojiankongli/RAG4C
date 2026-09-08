import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  archiveNotification,
  bulkMarkNotificationsRead,
  createNotificationIdempotencyKey,
  fetchNotification,
  fetchNotificationSubscriptions,
  fetchNotifications,
  fetchNotificationSummary,
  markNotificationRead,
  markNotificationUnread,
  updateNotificationSubscription,
  type BulkReadInput,
  type NotificationApiScope,
  type NotificationDetail,
  type NotificationInboxItem,
  type NotificationListQuery,
  type NotificationMutationOutcome,
  type NotificationPage,
  type NotificationRequestOptions,
  type NotificationSubscription,
  type NotificationSummary,
  type ReceiptMutationInput,
  type SubscriptionListQuery,
  type UpdateNotificationSubscriptionInput,
} from "../api/notificationApi";

export type NotificationLoadStatus =
  "idle" | "loading" | "ready" | "partial" | "empty" | "error" | "unavailable";
export type NotificationMutationStatus = "idle" | "saving" | "success" | "error";

export interface EnterpriseNotificationsApi {
  fetchSummary: typeof fetchNotificationSummary;
  fetchNotifications: typeof fetchNotifications;
  fetchDetail: typeof fetchNotification;
  fetchSubscriptions: typeof fetchNotificationSubscriptions;
  markRead: typeof markNotificationRead;
  markUnread: typeof markNotificationUnread;
  archive: typeof archiveNotification;
  bulkRead: typeof bulkMarkNotificationsRead;
  updateSubscription: typeof updateNotificationSubscription;
}

export interface UseEnterpriseNotificationsOptions {
  enabled: boolean;
  readOnly?: boolean;
  api?: EnterpriseNotificationsApi;
  unreadQuery?: Omit<NotificationListQuery, "status">;
  historyQuery?: Omit<NotificationListQuery, "status">;
  subscriptionQuery?: SubscriptionListQuery;
}

export type EnterpriseNotificationsOptions = UseEnterpriseNotificationsOptions;
export type NotificationMutationOptions = Pick<NotificationRequestOptions, "idempotencyKey">;

export interface NotificationSummaryState {
  status: NotificationLoadStatus;
  value: NotificationSummary | null;
  error: Error | null;
}

export interface NotificationCollectionState<T> {
  status: NotificationLoadStatus;
  items: T[];
  nextCursor: string | null;
  invalidItemCount: number;
  error: Error | null;
}

export interface NotificationLazyCollectionState<T> extends NotificationCollectionState<T> {
  load: () => Promise<boolean>;
}

export interface NotificationDetailState {
  status: NotificationLoadStatus;
  value: NotificationDetail | null;
  error: Error | null;
  load: (notificationId: string) => Promise<boolean>;
}

export interface EnterpriseNotificationsHook {
  active: boolean;
  load: {
    status: NotificationLoadStatus;
    error: Error | null;
    reload: () => Promise<boolean>;
  };
  summary: NotificationSummaryState;
  unread: NotificationCollectionState<NotificationInboxItem>;
  history: NotificationLazyCollectionState<NotificationInboxItem>;
  subscriptions: NotificationLazyCollectionState<NotificationSubscription>;
  detail: NotificationDetailState;
  mutation: {
    status: NotificationMutationStatus;
    outcome: NotificationMutationOutcome | null;
    error: Error | null;
    markRead: (
      notificationId: string,
      input: ReceiptMutationInput,
      options?: NotificationMutationOptions,
    ) => Promise<NotificationMutationOutcome | null>;
    markUnread: (
      notificationId: string,
      input: ReceiptMutationInput,
      options?: NotificationMutationOptions,
    ) => Promise<NotificationMutationOutcome | null>;
    archive: (
      notificationId: string,
      input: ReceiptMutationInput,
      options?: NotificationMutationOptions,
    ) => Promise<NotificationMutationOutcome | null>;
    bulkRead: (
      input: BulkReadInput,
      options?: NotificationMutationOptions,
    ) => Promise<NotificationMutationOutcome | null>;
    updateSubscription: (
      subscriptionId: string,
      input: UpdateNotificationSubscriptionInput,
      options?: NotificationMutationOptions,
    ) => Promise<NotificationMutationOutcome | null>;
    retry: () => Promise<NotificationMutationOutcome | null>;
  };
}

export const enterpriseNotificationsApi: EnterpriseNotificationsApi = {
  fetchSummary: fetchNotificationSummary,
  fetchNotifications,
  fetchDetail: fetchNotification,
  fetchSubscriptions: fetchNotificationSubscriptions,
  markRead: markNotificationRead,
  markUnread: markNotificationUnread,
  archive: archiveNotification,
  bulkRead: bulkMarkNotificationsRead,
  updateSubscription: updateNotificationSubscription,
};

export const notificationCenterApi = enterpriseNotificationsApi;

interface MutationRequest {
  generation: number;
  idempotencyKey: string;
  invoke: (options: NotificationRequestOptions) => Promise<NotificationMutationOutcome>;
}

function hasNotificationControlCharacters(value: string): boolean {
  for (const character of value) {
    const code = character.charCodeAt(0);
    if (code === 0 || code === 10 || code === 13) return true;
  }
  return false;
}

function safeError(error: unknown, fallback: string): Error {
  const message = error instanceof Error ? error.message : null;
  const safeMessage =
    typeof message === "string" &&
    message.length <= 512 &&
    !hasNotificationControlCharacters(message) &&
    !/(?:password|secret|credential|authorization|token|ticket|query|body|content|email|webhook|idempotency[_-]?key)\s*[:=]/i.test(
      message,
    )
      ? message.trim()
      : "";
  return new Error(safeMessage || fallback);
}

function unavailableError(message: string): Error {
  return new Error(message);
}

function isUnavailableMessage(error: unknown): boolean {
  return error instanceof Error && /unavailable|authority|not available/i.test(error.message);
}

function summaryStatus(value: NotificationSummary): NotificationLoadStatus {
  return value.state === "unavailable" ? "unavailable" : "ready";
}

function pageStatus<T>(page: NotificationPage<T>): NotificationLoadStatus {
  if (page.invalid_item_count > 0) return "partial";
  return page.items.length ? "ready" : "empty";
}

function aggregateStatus(
  summary: NotificationLoadStatus,
  unread: NotificationLoadStatus,
  summaryValue: NotificationSummary | null,
  unreadItems: NotificationInboxItem[],
): NotificationLoadStatus {
  const states = [summary, unread];
  if (states.includes("loading")) return "loading";
  const unavailable = states.includes("unavailable");
  const failed = states.includes("error");
  if (unavailable || failed) {
    const hasUsefulAuthority = Boolean(summaryValue) || unreadItems.length > 0;
    if (hasUsefulAuthority) return "partial";
    return unavailable ? "unavailable" : "error";
  }
  if (
    summary === "empty" &&
    unread === "empty" &&
    summaryValue?.state === "ready" &&
    summaryValue.unread_count === 0
  ) {
    return "empty";
  }
  return "ready";
}

export function useEnterpriseNotifications(
  scope: NotificationApiScope,
  options: UseEnterpriseNotificationsOptions,
): EnterpriseNotificationsHook {
  const active = options.enabled === true;
  const api = options.api ?? enterpriseNotificationsApi;
  const activeRef = useRef(active);
  const readOnlyRef = useRef(options.readOnly === true);
  const scopeRef = useRef(scope);
  const apiRef = useRef(api);
  const unreadQueryRef = useRef(options.unreadQuery);
  const historyQueryRef = useRef(options.historyQuery);
  const subscriptionQueryRef = useRef(options.subscriptionQuery);
  const mountedRef = useRef(false);
  const generationRef = useRef(0);
  const loadRequestRef = useRef(0);
  const historyRequestRef = useRef(0);
  const subscriptionRequestRef = useRef(0);
  const detailRequestRef = useRef(0);
  const loadControllerRef = useRef<AbortController | null>(null);
  const controllersRef = useRef(new Set<AbortController>());
  const mutationChainRef = useRef<Promise<void>>(Promise.resolve());
  const mutationPendingRef = useRef(0);
  const retryRef = useRef<MutationRequest | null>(null);

  activeRef.current = active;
  readOnlyRef.current = options.readOnly === true;
  scopeRef.current = scope;
  apiRef.current = api;
  unreadQueryRef.current = options.unreadQuery;
  historyQueryRef.current = options.historyQuery;
  subscriptionQueryRef.current = options.subscriptionQuery;

  const [loadStatus, setLoadStatus] = useState<NotificationLoadStatus>("idle");
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [summaryStatusValue, setSummaryStatus] = useState<NotificationLoadStatus>("idle");
  const [summary, setSummary] = useState<NotificationSummary | null>(null);
  const [summaryError, setSummaryError] = useState<Error | null>(null);
  const [unreadStatus, setUnreadStatus] = useState<NotificationLoadStatus>("idle");
  const [unread, setUnread] = useState<NotificationInboxItem[]>([]);
  const [unreadCursor, setUnreadCursor] = useState<string | null>(null);
  const [unreadInvalidItemCount, setUnreadInvalidItemCount] = useState(0);
  const [unreadError, setUnreadError] = useState<Error | null>(null);
  const [historyStatus, setHistoryStatus] = useState<NotificationLoadStatus>("idle");
  const [history, setHistory] = useState<NotificationInboxItem[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [historyInvalidItemCount, setHistoryInvalidItemCount] = useState(0);
  const [historyError, setHistoryError] = useState<Error | null>(null);
  const [subscriptionStatus, setSubscriptionStatus] = useState<NotificationLoadStatus>("idle");
  const [subscriptions, setSubscriptions] = useState<NotificationSubscription[]>([]);
  const [subscriptionCursor, setSubscriptionCursor] = useState<string | null>(null);
  const [subscriptionInvalidItemCount, setSubscriptionInvalidItemCount] = useState(0);
  const [subscriptionError, setSubscriptionError] = useState<Error | null>(null);
  const [detailStatus, setDetailStatus] = useState<NotificationLoadStatus>("idle");
  const [detail, setDetail] = useState<NotificationDetail | null>(null);
  const [detailError, setDetailError] = useState<Error | null>(null);
  const [mutationStatus, setMutationStatus] = useState<NotificationMutationStatus>("idle");
  const [mutationOutcome, setMutationOutcome] = useState<NotificationMutationOutcome | null>(null);
  const [mutationError, setMutationError] = useState<Error | null>(null);

  const trackController = useCallback((controller: AbortController) => {
    controllersRef.current.add(controller);
    return () => controllersRef.current.delete(controller);
  }, []);

  const abortActiveRequests = useCallback(() => {
    loadControllerRef.current?.abort();
    loadControllerRef.current = null;
    controllersRef.current.forEach((controller) => controller.abort());
    controllersRef.current.clear();
  }, []);

  const invalidateContext = useCallback(() => {
    generationRef.current += 1;
    loadRequestRef.current += 1;
    historyRequestRef.current += 1;
    subscriptionRequestRef.current += 1;
    detailRequestRef.current += 1;
    abortActiveRequests();
    mutationChainRef.current = Promise.resolve();
    mutationPendingRef.current = 0;
    retryRef.current = null;
  }, [abortActiveRequests]);

  const resetAuthority = useCallback(() => {
    setLoadStatus("idle");
    setLoadError(null);
    setSummaryStatus("idle");
    setSummary(null);
    setSummaryError(null);
    setUnreadStatus("idle");
    setUnread([]);
    setUnreadCursor(null);
    setUnreadInvalidItemCount(0);
    setUnreadError(null);
    setHistoryStatus("idle");
    setHistory([]);
    setHistoryCursor(null);
    setHistoryInvalidItemCount(0);
    setHistoryError(null);
    setSubscriptionStatus("idle");
    setSubscriptions([]);
    setSubscriptionCursor(null);
    setSubscriptionInvalidItemCount(0);
    setSubscriptionError(null);
    setDetailStatus("idle");
    setDetail(null);
    setDetailError(null);
    setMutationStatus("idle");
    setMutationOutcome(null);
    setMutationError(null);
  }, []);

  const reload = useCallback(async (): Promise<boolean> => {
    if (!activeRef.current || !mountedRef.current) return false;
    const generation = generationRef.current;
    const requestId = ++loadRequestRef.current;
    loadControllerRef.current?.abort();
    const controller = new AbortController();
    loadControllerRef.current = controller;
    const release = trackController(controller);
    const operationScope = { ...scopeRef.current };
    const operationApi = apiRef.current;
    const unreadQuery = { ...(unreadQueryRef.current ?? {}), status: "unread" as const };

    setLoadStatus("loading");
    setLoadError(null);
    setSummaryStatus("loading");
    setSummaryError(null);
    setUnreadStatus("loading");
    setUnreadError(null);

    const isCurrent = () =>
      mountedRef.current &&
      activeRef.current &&
      generation === generationRef.current &&
      requestId === loadRequestRef.current &&
      !controller.signal.aborted;

    try {
      const summaryPromise = Promise.resolve().then(() =>
        operationApi.fetchSummary(operationScope, { signal: controller.signal }),
      );
      const unreadPromise = Promise.resolve().then(() =>
        operationApi.fetchNotifications(operationScope, unreadQuery, { signal: controller.signal }),
      );
      const [summaryResult, unreadResult] = await Promise.allSettled([
        summaryPromise,
        unreadPromise,
      ]);
      if (!isCurrent()) return false;

      let nextSummary: NotificationSummary | null = null;
      let nextSummaryStatus: NotificationLoadStatus = "error";
      let nextSummaryError: Error | null = null;
      if (summaryResult.status === "fulfilled") {
        nextSummary = summaryResult.value;
        nextSummaryStatus = summaryStatus(summaryResult.value);
        if (nextSummaryStatus === "unavailable") {
          nextSummaryError = unavailableError(
            summaryResult.value.reason_code ?? "Notification unread summary is unavailable",
          );
        }
      } else {
        nextSummaryError = safeError(
          summaryResult.reason,
          "Notification unread summary is unavailable",
        );
        nextSummaryStatus = isUnavailableMessage(summaryResult.reason) ? "unavailable" : "error";
      }

      let nextUnread: NotificationInboxItem[] = [];
      let nextUnreadCursor: string | null = null;
      let nextUnreadInvalidItemCount = 0;
      let nextUnreadStatus: NotificationLoadStatus = "error";
      let nextUnreadError: Error | null = null;
      if (unreadResult.status === "fulfilled") {
        nextUnread = unreadResult.value.items;
        nextUnreadCursor = unreadResult.value.next_cursor;
        nextUnreadInvalidItemCount = unreadResult.value.invalid_item_count;
        nextUnreadStatus = pageStatus(unreadResult.value);
      } else {
        nextUnreadError = safeError(unreadResult.reason, "Notification inbox is unavailable");
        nextUnreadStatus = isUnavailableMessage(unreadResult.reason) ? "unavailable" : "error";
      }

      setSummaryStatus(nextSummaryStatus);
      setSummary(nextSummary);
      setSummaryError(nextSummaryError);
      setUnreadStatus(nextUnreadStatus);
      setUnread(nextUnread);
      setUnreadCursor(nextUnreadCursor);
      setUnreadInvalidItemCount(nextUnreadInvalidItemCount);
      setUnreadError(nextUnreadError);
      setLoadError(nextSummaryError ?? nextUnreadError);
      setLoadStatus(aggregateStatus(nextSummaryStatus, nextUnreadStatus, nextSummary, nextUnread));
      return true;
    } finally {
      if (loadControllerRef.current === controller) loadControllerRef.current = null;
      release();
    }
  }, [trackController]);

  const loadHistory = useCallback(async (): Promise<boolean> => {
    if (!activeRef.current || !mountedRef.current) return false;
    const generation = generationRef.current;
    const requestId = ++historyRequestRef.current;
    const controller = new AbortController();
    const release = trackController(controller);
    const operationScope = { ...scopeRef.current };
    const operationApi = apiRef.current;
    const historyQuery = { ...(historyQueryRef.current ?? {}), status: "all" as const };
    setHistoryStatus("loading");
    setHistoryError(null);
    try {
      const page = await operationApi.fetchNotifications(operationScope, historyQuery, {
        signal: controller.signal,
      });
      if (
        !mountedRef.current ||
        !activeRef.current ||
        generation !== generationRef.current ||
        requestId !== historyRequestRef.current ||
        controller.signal.aborted
      ) {
        return false;
      }
      setHistory(page.items);
      setHistoryCursor(page.next_cursor);
      setHistoryInvalidItemCount(page.invalid_item_count);
      setHistoryStatus(pageStatus(page));
      return true;
    } catch (error) {
      if (
        controller.signal.aborted ||
        generation !== generationRef.current ||
        !mountedRef.current
      ) {
        return false;
      }
      setHistoryError(safeError(error, "Notification history is unavailable"));
      setHistoryStatus(isUnavailableMessage(error) ? "unavailable" : "error");
      return false;
    } finally {
      release();
    }
  }, [trackController]);

  const loadSubscriptions = useCallback(async (): Promise<boolean> => {
    if (!activeRef.current || !mountedRef.current) return false;
    const generation = generationRef.current;
    const requestId = ++subscriptionRequestRef.current;
    const controller = new AbortController();
    const release = trackController(controller);
    const operationScope = { ...scopeRef.current };
    const operationApi = apiRef.current;
    const query = { ...(subscriptionQueryRef.current ?? {}) };
    setSubscriptionStatus("loading");
    setSubscriptionError(null);
    try {
      const page = await operationApi.fetchSubscriptions(operationScope, query, {
        signal: controller.signal,
      });
      if (
        !mountedRef.current ||
        !activeRef.current ||
        generation !== generationRef.current ||
        requestId !== subscriptionRequestRef.current ||
        controller.signal.aborted
      ) {
        return false;
      }
      setSubscriptions(page.items);
      setSubscriptionCursor(page.next_cursor);
      setSubscriptionInvalidItemCount(page.invalid_item_count);
      setSubscriptionStatus(pageStatus(page));
      return true;
    } catch (error) {
      if (
        controller.signal.aborted ||
        generation !== generationRef.current ||
        !mountedRef.current
      ) {
        return false;
      }
      setSubscriptionError(safeError(error, "Notification subscriptions are unavailable"));
      setSubscriptionStatus(isUnavailableMessage(error) ? "unavailable" : "error");
      return false;
    } finally {
      release();
    }
  }, [trackController]);

  const loadDetail = useCallback(
    async (notificationId: string): Promise<boolean> => {
      if (!activeRef.current || !mountedRef.current) return false;
      const generation = generationRef.current;
      const requestId = ++detailRequestRef.current;
      const controller = new AbortController();
      const release = trackController(controller);
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      setDetailStatus("loading");
      setDetailError(null);
      try {
        const value = await operationApi.fetchDetail(operationScope, notificationId, {
          signal: controller.signal,
        });
        if (
          !mountedRef.current ||
          !activeRef.current ||
          generation !== generationRef.current ||
          requestId !== detailRequestRef.current ||
          controller.signal.aborted
        ) {
          return false;
        }
        setDetail(value);
        setDetailStatus("ready");
        return true;
      } catch (error) {
        if (
          controller.signal.aborted ||
          generation !== generationRef.current ||
          !mountedRef.current
        ) {
          return false;
        }
        setDetail(null);
        setDetailError(safeError(error, "Notification detail is unavailable"));
        setDetailStatus(isUnavailableMessage(error) ? "unavailable" : "error");
        return false;
      } finally {
        release();
      }
    },
    [trackController],
  );

  const guardMutation = useCallback(
    (message: string): Promise<NotificationMutationOutcome | null> => {
      if (mountedRef.current) {
        setMutationStatus("error");
        setMutationOutcome(null);
        setMutationError(new Error(message));
      }
      return Promise.resolve(null);
    },
    [],
  );

  const executeMutation = useCallback(
    async (request: MutationRequest): Promise<NotificationMutationOutcome | null> => {
      if (
        !mountedRef.current ||
        !activeRef.current ||
        request.generation !== generationRef.current
      ) {
        return null;
      }
      const controller = new AbortController();
      const release = trackController(controller);
      try {
        const outcome = await request.invoke({
          signal: controller.signal,
          idempotencyKey: request.idempotencyKey,
        });
        if (
          !mountedRef.current ||
          !activeRef.current ||
          request.generation !== generationRef.current ||
          controller.signal.aborted
        ) {
          return null;
        }
        if (outcome.state === "unavailable") {
          setMutationStatus("error");
          setMutationOutcome(null);
          setMutationError(new Error(outcome.message ?? "Notification mutation is unavailable"));
          return null;
        }
        setMutationStatus("success");
        setMutationOutcome(outcome);
        setMutationError(null);
        if (retryRef.current?.idempotencyKey === request.idempotencyKey) retryRef.current = null;
        return outcome;
      } catch (error) {
        if (
          controller.signal.aborted ||
          request.generation !== generationRef.current ||
          !mountedRef.current
        ) {
          return null;
        }
        setMutationStatus("error");
        setMutationOutcome(null);
        setMutationError(safeError(error, "Notification mutation failed"));
        return null;
      } finally {
        release();
      }
    },
    [trackController],
  );

  const enqueueMutation = useCallback(
    (
      invoke: (options: NotificationRequestOptions) => Promise<NotificationMutationOutcome>,
      options: NotificationMutationOptions | undefined,
      retrying = false,
    ): Promise<NotificationMutationOutcome | null> => {
      if (!activeRef.current || !mountedRef.current) return Promise.resolve(null);
      if (readOnlyRef.current) return guardMutation("Notification Center is read-only");
      const request: MutationRequest = {
        generation: generationRef.current,
        idempotencyKey: options?.idempotencyKey ?? createNotificationIdempotencyKey(),
        invoke,
      };
      if (!retrying) retryRef.current = request;
      mutationPendingRef.current += 1;
      setMutationStatus("saving");
      setMutationError(null);
      setMutationOutcome(null);
      const task = mutationChainRef.current.then(
        () => executeMutation(request),
        () => executeMutation(request),
      );
      mutationChainRef.current = task.then(
        () => undefined,
        () => undefined,
      );
      void task.then(
        () => {
          if (request.generation === generationRef.current) {
            mutationPendingRef.current = Math.max(0, mutationPendingRef.current - 1);
          }
        },
        () => {
          if (request.generation === generationRef.current) {
            mutationPendingRef.current = Math.max(0, mutationPendingRef.current - 1);
          }
        },
      );
      return task;
    },
    [executeMutation, guardMutation],
  );

  const markRead = useCallback(
    (
      notificationId: string,
      input: ReceiptMutationInput,
      options?: NotificationMutationOptions,
    ) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) =>
          operationApi.markRead(operationScope, notificationId, input, requestOptions),
        options,
      );
    },
    [enqueueMutation],
  );

  const markUnread = useCallback(
    (
      notificationId: string,
      input: ReceiptMutationInput,
      options?: NotificationMutationOptions,
    ) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) =>
          operationApi.markUnread(operationScope, notificationId, input, requestOptions),
        options,
      );
    },
    [enqueueMutation],
  );

  const archive = useCallback(
    (
      notificationId: string,
      input: ReceiptMutationInput,
      options?: NotificationMutationOptions,
    ) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) =>
          operationApi.archive(operationScope, notificationId, input, requestOptions),
        options,
      );
    },
    [enqueueMutation],
  );

  const bulkRead = useCallback(
    (input: BulkReadInput, options?: NotificationMutationOptions) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) => operationApi.bulkRead(operationScope, input, requestOptions),
        options,
      );
    },
    [enqueueMutation],
  );

  const updateSubscription = useCallback(
    (
      subscriptionId: string,
      input: UpdateNotificationSubscriptionInput,
      options?: NotificationMutationOptions,
    ) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) =>
          operationApi.updateSubscription(operationScope, subscriptionId, input, requestOptions),
        options,
      );
    },
    [enqueueMutation],
  );

  const retry = useCallback((): Promise<NotificationMutationOutcome | null> => {
    const request = retryRef.current;
    if (!request || !activeRef.current || !mountedRef.current || readOnlyRef.current) {
      return Promise.resolve(null);
    }
    return enqueueMutation(request.invoke, { idempotencyKey: request.idempotencyKey }, true);
  }, [enqueueMutation]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      invalidateContext();
    };
  }, [invalidateContext]);

  const contextKey = scope.tenantId + "\u0000" + scope.actorToken;
  useEffect(() => {
    invalidateContext();
    resetAuthority();
    if (active) void reload();
    return () => invalidateContext();
  }, [active, api, contextKey, invalidateContext, reload, resetAuthority]);

  return useMemo<EnterpriseNotificationsHook>(
    () => ({
      active,
      load: { status: loadStatus, error: loadError, reload },
      summary: { status: summaryStatusValue, value: summary, error: summaryError },
      unread: {
        status: unreadStatus,
        items: unread,
        nextCursor: unreadCursor,
        invalidItemCount: unreadInvalidItemCount,
        error: unreadError,
      },
      history: {
        status: historyStatus,
        items: history,
        nextCursor: historyCursor,
        invalidItemCount: historyInvalidItemCount,
        error: historyError,
        load: loadHistory,
      },
      subscriptions: {
        status: subscriptionStatus,
        items: subscriptions,
        nextCursor: subscriptionCursor,
        invalidItemCount: subscriptionInvalidItemCount,
        error: subscriptionError,
        load: loadSubscriptions,
      },
      detail: { status: detailStatus, value: detail, error: detailError, load: loadDetail },
      mutation: {
        status: mutationStatus,
        outcome: mutationOutcome,
        error: mutationError,
        markRead,
        markUnread,
        archive,
        bulkRead,
        updateSubscription,
        retry,
      },
    }),
    [
      active,
      archive,
      bulkRead,
      detail,
      detailError,
      detailStatus,
      loadDetail,
      history,
      historyCursor,
      historyError,
      historyInvalidItemCount,
      historyStatus,
      loadHistory,
      loadError,
      loadSubscriptions,
      loadStatus,
      markRead,
      markUnread,
      mutationError,
      mutationOutcome,
      mutationStatus,
      retry,
      subscriptionCursor,
      subscriptionError,
      subscriptionInvalidItemCount,
      subscriptionStatus,
      subscriptions,
      summary,
      summaryError,
      summaryStatusValue,
      unread,
      unreadCursor,
      unreadError,
      unreadInvalidItemCount,
      unreadStatus,
      updateSubscription,
      reload,
    ],
  );
}
