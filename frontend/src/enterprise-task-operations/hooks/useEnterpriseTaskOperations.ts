import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  acknowledgeTask,
  cancelTask,
  createTaskIdempotencyKey,
  createTaskSavedView,
  fetchReconciliationRuns,
  fetchTask,
  fetchTaskEvents,
  fetchTaskSummary,
  fetchTaskViews,
  fetchTasks,
  previewTaskReconciliation,
  reconcileTasks,
  retryTask,
  updateTaskSavedView,
  type TaskActionInput,
  type TaskApi,
  type TaskApiScope,
  type TaskEventQuery,
  type TaskPageQuery,
  type TaskRequestOptions,
  type TaskSavedViewInput,
  type TaskViewQuery,
  type TaskReconciliationInput,
  type UpdateTaskSavedViewInput,
} from "../api/taskApi";
import type {
  TaskActionOutcome,
  TaskDetail,
  TaskEvent,
  TaskPage,
  TaskProjection,
  TaskReconciliationRun,
  TaskSavedView,
  TaskSummary,
} from "../model/taskModel";

export type TaskLoadStatus =
  "idle" | "loading" | "ready" | "partial" | "empty" | "error" | "unavailable";
export type TaskMutationStatus = "idle" | "saving" | "success" | "error";
export interface TaskResourceState<T> {
  status: TaskLoadStatus;
  value: T | null;
  error: Error | null;
}
export interface TaskCollectionState<T> {
  status: TaskLoadStatus;
  items: T[];
  nextCursor: string | null;
  invalidItemCount: number;
  error: Error | null;
  reload: () => Promise<boolean>;
}
export interface TaskLazyCollectionState<T> extends TaskCollectionState<T> {
  load: () => Promise<boolean>;
}
export interface TaskDetailState {
  status: TaskLoadStatus;
  value: TaskDetail | null;
  error: Error | null;
  load: (taskId: string) => Promise<boolean>;
}
export interface TaskMutationOptions {
  idempotencyKey?: string;
}
export type EnterpriseTaskOperationsApi = TaskApi;
export interface UseEnterpriseTaskOperationsOptions {
  enabled: boolean;
  readOnly?: boolean;
  api?: EnterpriseTaskOperationsApi;
  taskQuery?: TaskPageQuery;
  eventQuery?: TaskEventQuery;
  viewQuery?: TaskViewQuery;
}
export interface EnterpriseTaskOperationsHook {
  active: boolean;
  readOnly: boolean;
  load: { status: TaskLoadStatus; error: Error | null; reload: () => Promise<boolean> };
  summary: TaskResourceState<TaskSummary>;
  operations: TaskCollectionState<TaskProjection>;
  tasks: TaskCollectionState<TaskProjection>;
  detail: TaskDetailState;
  activity: TaskCollectionState<TaskEvent> & { load: (taskId: string) => Promise<boolean> };
  events: TaskCollectionState<TaskEvent> & { load: (taskId: string) => Promise<boolean> };
  savedViews: TaskLazyCollectionState<TaskSavedView> & {
    activeId: string | null;
    select: (view: TaskSavedView) => void;
  };
  reconciliation: TaskLazyCollectionState<TaskReconciliationRun>;
  mutation: {
    status: TaskMutationStatus;
    outcome: TaskActionOutcome | null;
    error: Error | null;
    retry: (
      task: TaskProjection,
      options?: TaskMutationOptions,
    ) => Promise<TaskActionOutcome | null>;
    cancel: (
      task: TaskProjection,
      options?: TaskMutationOptions,
    ) => Promise<TaskActionOutcome | null>;
    acknowledge: (
      task: TaskProjection,
      options?: TaskMutationOptions,
    ) => Promise<TaskActionOutcome | null>;
    retryLast: () => Promise<TaskActionOutcome | null>;
    retryLastMutation: () => Promise<TaskActionOutcome | null>;
    createView: (
      input: TaskSavedViewInput,
      options?: TaskMutationOptions,
    ) => Promise<TaskActionOutcome | null>;
    updateView: (
      viewId: string,
      input: UpdateTaskSavedViewInput,
      options?: TaskMutationOptions,
    ) => Promise<TaskActionOutcome | null>;
    previewReconciliation: (
      input: TaskReconciliationInput,
      options?: TaskMutationOptions,
    ) => Promise<TaskActionOutcome | null>;
    reconcile: (
      input: TaskReconciliationInput,
      options?: TaskMutationOptions,
    ) => Promise<TaskActionOutcome | null>;
  };
}

export type EnterpriseTaskOperationsOptions = UseEnterpriseTaskOperationsOptions;
export const enterpriseTaskOperationsApi: EnterpriseTaskOperationsApi = {
  fetchSummary: fetchTaskSummary,
  fetchTasks,
  fetchTask,
  fetchTaskEvents,
  fetchViews: fetchTaskViews,
  fetchReconciliationRuns,
  retryTask,
  cancelTask,
  acknowledgeTask,
  createView: createTaskSavedView,
  updateView: updateTaskSavedView,
  previewReconciliation: previewTaskReconciliation,
  reconcile: reconcileTasks,
};
export const taskOperationsApi = enterpriseTaskOperationsApi;

interface PendingMutation {
  generation: number;
  idempotencyKey: string;
  invoke: (options: TaskRequestOptions) => Promise<TaskActionOutcome>;
}
function safeError(error: unknown, fallback: string): Error {
  const message = error instanceof Error ? error.message.trim() : "";
  return new Error(message && message.length <= 512 ? message : fallback);
}
function isUnavailable(error: unknown): boolean {
  return /(?:authority|service|capability|endpoint|api).*unavailable|not configured|not ready|暂不可用/i.test(
    error instanceof Error ? error.message : String(error),
  );
}
function fallbackTaskInput(task: TaskProjection): TaskActionInput {
  return {
    expectedSourceRevision: task.source_revision,
    expectedSourceDigest: task.source_digest,
    reason: "task operation requested",
  };
}
export function useEnterpriseTaskOperations(
  scope: TaskApiScope,
  options: UseEnterpriseTaskOperationsOptions,
): EnterpriseTaskOperationsHook {
  const active = options.enabled;
  const readOnly = Boolean(options.readOnly);
  const api = options.api ?? enterpriseTaskOperationsApi;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const apiRef = useRef<EnterpriseTaskOperationsApi>(api);
  apiRef.current = api;
  const taskQueryRef = useRef<TaskPageQuery>(options.taskQuery ?? {});
  taskQueryRef.current = options.taskQuery ?? {};
  const eventQueryRef = useRef<TaskEventQuery>(options.eventQuery ?? {});
  eventQueryRef.current = options.eventQuery ?? {};
  const viewQueryRef = useRef<TaskViewQuery>(options.viewQuery ?? { status: "active" });
  viewQueryRef.current = options.viewQuery ?? { status: "active" };
  const activeRef = useRef(active);
  activeRef.current = active;
  const readOnlyRef = useRef(readOnly);
  readOnlyRef.current = readOnly;
  const mountedRef = useRef(false);
  const generationRef = useRef(0);
  const readControllerRef = useRef<AbortController | null>(null);
  const lazyControllersRef = useRef(new Set<AbortController>());
  const mutationControllersRef = useRef(new Set<AbortController>());
  const mutationChainRef = useRef<Promise<void>>(Promise.resolve());
  const retryRef = useRef<PendingMutation | null>(null);
  const lastEventTaskIdRef = useRef<string | null>(null);

  const [loadStatus, setLoadStatus] = useState<TaskLoadStatus>("idle");
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [summaryStatus, setSummaryStatus] = useState<TaskLoadStatus>("idle");
  const [summaryValue, setSummaryValue] = useState<TaskSummary | null>(null);
  const [summaryError, setSummaryError] = useState<Error | null>(null);
  const [operationsStatus, setOperationsStatus] = useState<TaskLoadStatus>("idle");
  const [operationsPage, setOperationsPage] = useState<TaskPage<TaskProjection> | null>(null);
  const [operationsError, setOperationsError] = useState<Error | null>(null);
  const [detailStatus, setDetailStatus] = useState<TaskLoadStatus>("idle");
  const [detailValue, setDetailValue] = useState<TaskDetail | null>(null);
  const [detailError, setDetailError] = useState<Error | null>(null);
  const [activityStatus, setActivityStatus] = useState<TaskLoadStatus>("idle");
  const [activityPage, setActivityPage] = useState<TaskPage<TaskEvent> | null>(null);
  const [activityError, setActivityError] = useState<Error | null>(null);
  const [viewsStatus, setViewsStatus] = useState<TaskLoadStatus>("idle");
  const [viewsPage, setViewsPage] = useState<TaskPage<TaskSavedView> | null>(null);
  const [viewsError, setViewsError] = useState<Error | null>(null);
  const [activeViewId, setActiveViewId] = useState<string | null>(null);
  const [reconciliationStatus, setReconciliationStatus] = useState<TaskLoadStatus>("idle");
  const [reconciliationPage, setReconciliationPage] =
    useState<TaskPage<TaskReconciliationRun> | null>(null);
  const [reconciliationError, setReconciliationError] = useState<Error | null>(null);
  const [mutationStatus, setMutationStatus] = useState<TaskMutationStatus>("idle");
  const [mutationOutcome, setMutationOutcome] = useState<TaskActionOutcome | null>(null);
  const [mutationError, setMutationError] = useState<Error | null>(null);

  const isCurrent = useCallback(
    (generation: number): boolean =>
      mountedRef.current && activeRef.current && generation === generationRef.current,
    [],
  );
  const abortInFlight = useCallback(() => {
    readControllerRef.current?.abort();
    readControllerRef.current = null;
    for (const controller of lazyControllersRef.current) controller.abort();
    lazyControllersRef.current.clear();
    for (const controller of mutationControllersRef.current) controller.abort();
    mutationControllersRef.current.clear();
  }, []);
  const resetAuthority = useCallback(() => {
    setLoadStatus("idle");
    setLoadError(null);
    setSummaryStatus("idle");
    setSummaryValue(null);
    setSummaryError(null);
    setOperationsStatus("idle");
    setOperationsPage(null);
    setOperationsError(null);
    setDetailStatus("idle");
    setDetailValue(null);
    setDetailError(null);
    setActivityStatus("idle");
    setActivityPage(null);
    setActivityError(null);
    setViewsStatus("idle");
    setViewsPage(null);
    setViewsError(null);
    setActiveViewId(null);
    setReconciliationStatus("idle");
    setReconciliationPage(null);
    setReconciliationError(null);
    setMutationStatus("idle");
    setMutationOutcome(null);
    setMutationError(null);
    retryRef.current = null;
    lastEventTaskIdRef.current = null;
  }, []);
  const invalidateContext = useCallback(() => {
    generationRef.current += 1;
    abortInFlight();
    retryRef.current = null;
  }, [abortInFlight]);

  const reload = useCallback(async (): Promise<boolean> => {
    if (!activeRef.current || !mountedRef.current) return false;
    const generation = generationRef.current;
    readControllerRef.current?.abort();
    const controller = new AbortController();
    readControllerRef.current = controller;
    setLoadStatus("loading");
    setLoadError(null);
    setSummaryStatus("loading");
    setSummaryError(null);
    setOperationsStatus("loading");
    setOperationsError(null);
    const requestScope = { ...scopeRef.current };
    const requestApi = apiRef.current;
    const results = await Promise.allSettled([
      requestApi.fetchSummary(requestScope, { signal: controller.signal }),
      requestApi.fetchTasks(requestScope, taskQueryRef.current, { signal: controller.signal }),
    ]);
    if (!isCurrent(generation)) return false;
    const summaryResult = results[0];
    const operationsResult = results[1];
    let summaryOk = false;
    let operationsOk = false;
    let nextSummaryError: Error | null = null;
    let nextOperationsError: Error | null = null;
    if (summaryResult?.status === "fulfilled") {
      summaryOk = true;
      setSummaryValue(summaryResult.value);
      setSummaryStatus(summaryResult.value.state === "unavailable" ? "unavailable" : "ready");
    } else {
      nextSummaryError = safeError(summaryResult?.reason, "Task summary is unavailable");
      setSummaryValue(null);
      setSummaryError(nextSummaryError);
      setSummaryStatus(isUnavailable(summaryResult?.reason) ? "unavailable" : "error");
    }
    if (operationsResult?.status === "fulfilled") {
      operationsOk = true;
      setOperationsPage(operationsResult.value);
      setOperationsStatus(
        operationsResult.value.items.length === 0 && operationsResult.value.invalid_item_count === 0
          ? "empty"
          : operationsResult.value.invalid_item_count > 0
            ? "partial"
            : "ready",
      );
    } else {
      nextOperationsError = safeError(operationsResult?.reason, "Task list is unavailable");
      setOperationsPage(null);
      setOperationsError(nextOperationsError);
      setOperationsStatus(isUnavailable(operationsResult?.reason) ? "unavailable" : "error");
    }
    setLoadError(nextSummaryError ?? nextOperationsError);
    setLoadStatus(
      summaryOk && operationsOk
        ? operationsResult.status === "fulfilled" && operationsResult.value.invalid_item_count > 0
          ? "partial"
          : "ready"
        : summaryOk || operationsOk
          ? "partial"
          : isUnavailable(nextSummaryError) && isUnavailable(nextOperationsError)
            ? "unavailable"
            : "error",
    );
    if (readControllerRef.current === controller) readControllerRef.current = null;
    return summaryOk || operationsOk;
  }, [isCurrent]);
  const loadDetail = useCallback(
    async (taskId: string): Promise<boolean> => {
      if (!activeRef.current || !mountedRef.current) return false;
      const generation = generationRef.current;
      const controller = new AbortController();
      lazyControllersRef.current.add(controller);
      setDetailStatus("loading");
      setDetailError(null);
      const requestScope = { ...scopeRef.current };
      const requestApi = apiRef.current;
      try {
        const value = await requestApi.fetchTask(requestScope, taskId, {
          signal: controller.signal,
        });
        if (!isCurrent(generation)) return false;
        setDetailValue(value);
        setDetailStatus("ready");
        return true;
      } catch (error) {
        if (!isCurrent(generation)) return false;
        const projected = safeError(error, "Task detail is unavailable");
        setDetailValue(null);
        setDetailError(projected);
        setDetailStatus(isUnavailable(error) ? "unavailable" : "error");
        return false;
      } finally {
        lazyControllersRef.current.delete(controller);
      }
    },
    [isCurrent],
  );

  const loadActivity = useCallback(
    async (taskId: string): Promise<boolean> => {
      if (!activeRef.current || !mountedRef.current) return false;
      const generation = generationRef.current;
      const controller = new AbortController();
      lazyControllersRef.current.add(controller);
      lastEventTaskIdRef.current = taskId;
      setActivityStatus("loading");
      setActivityError(null);
      const requestScope = { ...scopeRef.current };
      const requestApi = apiRef.current;
      try {
        const value = await requestApi.fetchTaskEvents(
          requestScope,
          taskId,
          eventQueryRef.current,
          { signal: controller.signal },
        );
        if (!isCurrent(generation)) return false;
        setActivityPage(value);
        setActivityStatus(
          value.items.length === 0 && value.invalid_item_count === 0
            ? "empty"
            : value.invalid_item_count > 0
              ? "partial"
              : "ready",
        );
        return true;
      } catch (error) {
        if (!isCurrent(generation)) return false;
        const projected = safeError(error, "Task events are unavailable");
        setActivityPage(null);
        setActivityError(projected);
        setActivityStatus(isUnavailable(error) ? "unavailable" : "error");
        return false;
      } finally {
        lazyControllersRef.current.delete(controller);
      }
    },
    [isCurrent],
  );

  const loadViews = useCallback(async (): Promise<boolean> => {
    if (!activeRef.current || !mountedRef.current) return false;
    const generation = generationRef.current;
    const controller = new AbortController();
    lazyControllersRef.current.add(controller);
    setViewsStatus("loading");
    setViewsError(null);
    const requestScope = { ...scopeRef.current };
    const requestApi = apiRef.current;
    try {
      const value = await requestApi.fetchViews(requestScope, viewQueryRef.current, {
        signal: controller.signal,
      });
      if (!isCurrent(generation)) return false;
      setViewsPage(value);
      setViewsStatus(
        value.items.length === 0 && value.invalid_item_count === 0
          ? "empty"
          : value.invalid_item_count > 0
            ? "partial"
            : "ready",
      );
      if (activeViewId === null)
        setActiveViewId(value.items.find((item) => item.status === "active")?.id ?? null);
      return true;
    } catch (error) {
      if (!isCurrent(generation)) return false;
      const projected = safeError(error, "Task views are unavailable");
      setViewsPage(null);
      setViewsError(projected);
      setViewsStatus(isUnavailable(error) ? "unavailable" : "error");
      return false;
    } finally {
      lazyControllersRef.current.delete(controller);
    }
  }, [activeViewId, isCurrent]);

  const loadReconciliation = useCallback(async (): Promise<boolean> => {
    if (!activeRef.current || !mountedRef.current) return false;
    const generation = generationRef.current;
    const controller = new AbortController();
    lazyControllersRef.current.add(controller);
    setReconciliationStatus("loading");
    setReconciliationError(null);
    const requestScope = { ...scopeRef.current };
    const requestApi = apiRef.current;
    try {
      const value = await requestApi.fetchReconciliationRuns(
        requestScope,
        {},
        { signal: controller.signal },
      );
      if (!isCurrent(generation)) return false;
      setReconciliationPage(value);
      setReconciliationStatus(
        value.items.length === 0 && value.invalid_item_count === 0
          ? "empty"
          : value.invalid_item_count > 0
            ? "partial"
            : "ready",
      );
      return true;
    } catch (error) {
      if (!isCurrent(generation)) return false;
      const projected = safeError(error, "Task reconciliation history is unavailable");
      setReconciliationPage(null);
      setReconciliationError(projected);
      setReconciliationStatus(isUnavailable(error) ? "unavailable" : "error");
      return false;
    } finally {
      lazyControllersRef.current.delete(controller);
    }
  }, [isCurrent]);

  const enqueueMutation = useCallback(
    (
      invoke: PendingMutation["invoke"],
      requestedKey?: string,
      isRetry = false,
    ): Promise<TaskActionOutcome | null> => {
      if (!activeRef.current || !mountedRef.current || readOnlyRef.current)
        return Promise.resolve(null);
      const generation = generationRef.current;
      const idempotencyKey = requestedKey ?? createTaskIdempotencyKey();
      const pending: PendingMutation = { generation, idempotencyKey, invoke };
      const task = mutationChainRef.current.then(async () => {
        if (!isCurrent(generation)) return null;
        const controller = new AbortController();
        mutationControllersRef.current.add(controller);
        setMutationStatus("saving");
        setMutationError(null);
        try {
          const outcome = await invoke({ signal: controller.signal, idempotencyKey });
          if (!isCurrent(generation)) return null;
          setMutationOutcome(outcome);
          setMutationStatus("success");
          setMutationError(null);
          if (!isRetry) retryRef.current = null;
          else retryRef.current = null;
          return outcome;
        } catch (error) {
          if (isCurrent(generation)) {
            const projected = safeError(error, "Task operation is unavailable");
            setMutationError(projected);
            setMutationStatus("error");
            retryRef.current = pending;
          }
          throw error;
        } finally {
          mutationControllersRef.current.delete(controller);
        }
      });
      mutationChainRef.current = task.then(
        () => undefined,
        () => undefined,
      );
      return task;
    },
    [isCurrent],
  );
  const runTaskAction = useCallback(
    (
      action: "retryTask" | "cancelTask" | "acknowledgeTask",
      task: TaskProjection,
      optionsValue?: TaskMutationOptions,
    ): Promise<TaskActionOutcome | null> => {
      const requestScope = { ...scopeRef.current };
      const requestApi = apiRef.current;
      const input = fallbackTaskInput(task);
      return enqueueMutation(
        (requestOptions) => requestApi[action](requestScope, task.id, input, requestOptions),
        optionsValue?.idempotencyKey,
      );
    },
    [enqueueMutation],
  );
  const retry = useCallback(
    (task: TaskProjection, optionsValue?: TaskMutationOptions) =>
      runTaskAction("retryTask", task, optionsValue),
    [runTaskAction],
  );
  const cancel = useCallback(
    (task: TaskProjection, optionsValue?: TaskMutationOptions) =>
      runTaskAction("cancelTask", task, optionsValue),
    [runTaskAction],
  );
  const acknowledge = useCallback(
    (task: TaskProjection, optionsValue?: TaskMutationOptions) =>
      runTaskAction("acknowledgeTask", task, optionsValue),
    [runTaskAction],
  );
  const retryLast = useCallback((): Promise<TaskActionOutcome | null> => {
    const pending = retryRef.current;
    if (!pending || !activeRef.current || !mountedRef.current || readOnlyRef.current)
      return Promise.resolve(null);
    return enqueueMutation(pending.invoke, pending.idempotencyKey, true);
  }, [enqueueMutation]);
  const createView = useCallback(
    (
      input: TaskSavedViewInput,
      optionsValue?: TaskMutationOptions,
    ): Promise<TaskActionOutcome | null> => {
      const requestScope = { ...scopeRef.current };
      const requestApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) => requestApi.createView(requestScope, input, requestOptions),
        optionsValue?.idempotencyKey,
      );
    },
    [enqueueMutation],
  );
  const updateView = useCallback(
    (
      viewId: string,
      input: UpdateTaskSavedViewInput,
      optionsValue?: TaskMutationOptions,
    ): Promise<TaskActionOutcome | null> => {
      const requestScope = { ...scopeRef.current };
      const requestApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) => requestApi.updateView(requestScope, viewId, input, requestOptions),
        optionsValue?.idempotencyKey,
      );
    },
    [enqueueMutation],
  );
  const previewReconciliation = useCallback(
    (
      input: TaskReconciliationInput,
      optionsValue?: TaskMutationOptions,
    ): Promise<TaskActionOutcome | null> => {
      const requestScope = { ...scopeRef.current };
      const requestApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) => requestApi.previewReconciliation(requestScope, input, requestOptions),
        optionsValue?.idempotencyKey,
      );
    },
    [enqueueMutation],
  );
  const reconcile = useCallback(
    (
      input: TaskReconciliationInput,
      optionsValue?: TaskMutationOptions,
    ): Promise<TaskActionOutcome | null> => {
      const requestScope = { ...scopeRef.current };
      const requestApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) => requestApi.reconcile(requestScope, input, requestOptions),
        optionsValue?.idempotencyKey,
      );
    },
    [enqueueMutation],
  );
  const selectView = useCallback(
    (view: TaskSavedView) => {
      if (isCurrent(generationRef.current)) setActiveViewId(view.id);
    },
    [isCurrent],
  );

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      invalidateContext();
    };
  }, [invalidateContext]);
  const contextKey = `${scope.tenantId}\u0000${scope.actorToken}\u0000${scope.accountId ?? ""}`;
  const taskQueryKey = JSON.stringify(options.taskQuery ?? {});
  const eventQueryKey = JSON.stringify(options.eventQuery ?? {});
  const viewQueryKey = JSON.stringify(options.viewQuery ?? { status: "active" });
  useEffect(() => {
    invalidateContext();
    resetAuthority();
    if (active) void reload();
    return () => invalidateContext();
  }, [
    active,
    api,
    contextKey,
    eventQueryKey,
    invalidateContext,
    reload,
    resetAuthority,
    taskQueryKey,
    viewQueryKey,
  ]);

  return useMemo<EnterpriseTaskOperationsHook>(() => {
    const operations: TaskCollectionState<TaskProjection> = {
      status: operationsStatus,
      items: operationsPage?.items ?? [],
      nextCursor: operationsPage?.next_cursor ?? null,
      invalidItemCount: operationsPage?.invalid_item_count ?? 0,
      error: operationsError,
      reload,
    };
    const activity: TaskCollectionState<TaskEvent> & {
      load: (taskId: string) => Promise<boolean>;
    } = {
      status: activityStatus,
      items: activityPage?.items ?? [],
      nextCursor: activityPage?.next_cursor ?? null,
      invalidItemCount: activityPage?.invalid_item_count ?? 0,
      error: activityError,
      reload: () => loadActivity(lastEventTaskIdRef.current ?? ""),
      load: loadActivity,
    };
    const views: TaskLazyCollectionState<TaskSavedView> & {
      activeId: string | null;
      select: (view: TaskSavedView) => void;
    } = {
      status: viewsStatus,
      items: viewsPage?.items ?? [],
      nextCursor: viewsPage?.next_cursor ?? null,
      invalidItemCount: viewsPage?.invalid_item_count ?? 0,
      error: viewsError,
      reload: loadViews,
      load: loadViews,
      activeId: activeViewId,
      select: selectView,
    };
    const reconciliation: TaskLazyCollectionState<TaskReconciliationRun> = {
      status: reconciliationStatus,
      items: reconciliationPage?.items ?? [],
      nextCursor: reconciliationPage?.next_cursor ?? null,
      invalidItemCount: reconciliationPage?.invalid_item_count ?? 0,
      error: reconciliationError,
      reload: loadReconciliation,
      load: loadReconciliation,
    };
    return {
      active,
      readOnly,
      load: { status: loadStatus, error: loadError, reload },
      summary: { status: summaryStatus, value: summaryValue, error: summaryError },
      operations,
      tasks: operations,
      detail: { status: detailStatus, value: detailValue, error: detailError, load: loadDetail },
      activity,
      events: activity,
      savedViews: views,
      reconciliation,
      mutation: {
        status: mutationStatus,
        outcome: mutationOutcome,
        error: mutationError,
        retry,
        cancel,
        acknowledge,
        retryLast,
        retryLastMutation: retryLast,
        createView,
        updateView,
        previewReconciliation,
        reconcile,
      },
    };
  }, [
    active,
    activeViewId,
    acknowledge,
    activityError,
    activityPage,
    activityStatus,
    cancel,
    createView,
    detailError,
    detailStatus,
    detailValue,
    loadActivity,
    loadDetail,
    loadError,
    loadReconciliation,
    loadStatus,
    loadViews,
    mutationError,
    mutationOutcome,
    mutationStatus,
    operationsError,
    operationsPage,
    operationsStatus,
    readOnly,
    reconciliationError,
    reconciliationPage,
    reconciliationStatus,
    reload,
    retry,
    retryLast,
    selectView,
    summaryError,
    summaryStatus,
    summaryValue,
    viewsError,
    viewsPage,
    viewsStatus,
  ]);
}

export const useEnterpriseTasks = useEnterpriseTaskOperations;
