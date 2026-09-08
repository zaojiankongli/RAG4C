import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  acknowledgeQualityAlert,
  cancelRecertificationJob,
  createOperationsIdempotencyKey,
  fetchQualityAlerts,
  fetchQualityOperationsSummary,
  fetchRecertificationJobs,
  queueRecertificationJob,
  requestQualityOperationsScan,
  resolveQualityAlert,
  suppressQualityAlert,
  type AlertAcknowledgeInput,
  type AlertListQuery,
  type AlertSuppressInput,
  type CancelRecertificationInput,
  type JobListQuery,
  type OperationsApiScope,
  type OperationsMutationOutcome,
  type OperationsPage,
  type OperationsRequestOptions,
  type QueueRecertificationInput,
} from "../api/operationsApi";
import {
  sanitizeOperationsMessage,
  type QualityOperationsAlert,
  type QualityOperationsSummary,
  type RecertificationJob,
} from "../model/operationsModel";

export type OperationsLoadStatus =
  "idle" | "loading" | "ready" | "partial" | "empty" | "error" | "unavailable";
export type OperationsResourceStatus = OperationsLoadStatus;
export type OperationsMutationStatus = "idle" | "saving" | "success" | "error";

export interface ReleaseQualityOperationsApi {
  fetchSummary: typeof fetchQualityOperationsSummary;
  fetchAlerts: typeof fetchQualityAlerts;
  fetchJobs: typeof fetchRecertificationJobs;
  acknowledgeAlert: typeof acknowledgeQualityAlert;
  resolveAlert: typeof resolveQualityAlert;
  suppressAlert: typeof suppressQualityAlert;
  queueRecertification: typeof queueRecertificationJob;
  cancelRecertification: typeof cancelRecertificationJob;
  requestScan: typeof requestQualityOperationsScan;
}

export type OperationsApi = ReleaseQualityOperationsApi;

export interface UseReleaseQualityOperationsOptions {
  enabled: boolean;
  readOnly?: boolean;
  api?: ReleaseQualityOperationsApi;
  alertQuery?: AlertListQuery;
  jobQuery?: JobListQuery;
}

export type ReleaseQualityOperationsOptions = UseReleaseQualityOperationsOptions;
export type OperationsMutationOptions = Pick<OperationsRequestOptions, "idempotencyKey">;

export interface OperationsSummaryState {
  status: OperationsResourceStatus;
  value: QualityOperationsSummary | null;
  error: Error | null;
}

export interface OperationsCollectionState<T> {
  status: OperationsResourceStatus;
  items: T[];
  nextCursor: string | null;
  invalidItemCount: number;
  error: Error | null;
}

export interface OperationsLazyPlaceholder {
  status: "idle" | "unavailable";
  available: false;
  items: unknown[];
  error: Error | null;
  load: () => Promise<boolean>;
}

export interface ReleaseQualityOperationsHook {
  active: boolean;
  load: {
    status: OperationsLoadStatus;
    error: Error | null;
    reload: () => Promise<boolean>;
  };
  summary: OperationsSummaryState;
  alerts: OperationsCollectionState<QualityOperationsAlert>;
  jobs: OperationsCollectionState<RecertificationJob>;
  timeline: OperationsLazyPlaceholder;
  observations: OperationsLazyPlaceholder;
  mutation: {
    status: OperationsMutationStatus;
    outcome: OperationsMutationOutcome | null;
    error: Error | null;
    acknowledgeAlert: (
      alertId: string,
      input: AlertAcknowledgeInput,
      options?: OperationsMutationOptions,
    ) => Promise<OperationsMutationOutcome | null>;
    resolveAlert: (
      alertId: string,
      input: AlertAcknowledgeInput,
      options?: OperationsMutationOptions,
    ) => Promise<OperationsMutationOutcome | null>;
    suppressAlert: (
      alertId: string,
      input: AlertSuppressInput,
      options?: OperationsMutationOptions,
    ) => Promise<OperationsMutationOutcome | null>;
    queueRecertification: (
      input: QueueRecertificationInput,
      options?: OperationsMutationOptions,
    ) => Promise<OperationsMutationOutcome | null>;
    cancelRecertification: (
      jobId: string,
      input: CancelRecertificationInput,
      options?: OperationsMutationOptions,
    ) => Promise<OperationsMutationOutcome | null>;
    requestScan: (
      input: { reason: string },
      options?: OperationsMutationOptions,
    ) => Promise<OperationsMutationOutcome | null>;
    retry: () => Promise<OperationsMutationOutcome | null>;
  };
}

export const releaseQualityOperationsApi: ReleaseQualityOperationsApi = {
  fetchSummary: fetchQualityOperationsSummary,
  fetchAlerts: fetchQualityAlerts,
  fetchJobs: fetchRecertificationJobs,
  acknowledgeAlert: acknowledgeQualityAlert,
  resolveAlert: resolveQualityAlert,
  suppressAlert: suppressQualityAlert,
  queueRecertification: queueRecertificationJob,
  cancelRecertification: cancelRecertificationJob,
  requestScan: requestQualityOperationsScan,
};

export const operationsApi = releaseQualityOperationsApi;

interface CollectionProjection<T> {
  status: OperationsResourceStatus;
  items: T[];
  nextCursor: string | null;
  invalidItemCount: number;
  error: Error | null;
}

interface MutationRequest {
  generation: number;
  idempotencyKey: string;
  invoke: (options: OperationsRequestOptions) => Promise<OperationsMutationOutcome>;
}

function scopeKey(scope: OperationsApiScope): string {
  return JSON.stringify([scope.tenantId.trim(), scope.datasetId.trim(), scope.actorToken.trim()]);
}

function hasUsableScope(scope: OperationsApiScope): boolean {
  return Boolean(scope.tenantId.trim() && scope.datasetId.trim() && scope.actorToken.trim());
}

function safeError(value: unknown, fallback: string): Error {
  const message = value instanceof Error ? value.message : null;
  return new Error(sanitizeOperationsMessage(message, fallback));
}

function unavailableError(message: string): Error {
  return new Error(sanitizeOperationsMessage(message, "Quality Operations unavailable"));
}

function classifySummary(summary: QualityOperationsSummary): OperationsResourceStatus {
  if (summary.state === "unavailable") return "unavailable";
  return summary.items.length === 0 ? "empty" : "ready";
}

function classifyCollection<T>(page: OperationsPage<T>): CollectionProjection<T> {
  if (page.invalid_item_count > 0 && page.items.length === 0) {
    return {
      status: "unavailable",
      items: [],
      nextCursor: page.next_cursor,
      invalidItemCount: page.invalid_item_count,
      error: unavailableError("Quality Operations collection items are unavailable"),
    };
  }
  if (page.invalid_item_count > 0) {
    return {
      status: "partial",
      items: page.items,
      nextCursor: page.next_cursor,
      invalidItemCount: page.invalid_item_count,
      error: unavailableError("Some Quality Operations collection items are unavailable"),
    };
  }
  return {
    status: page.items.length === 0 ? "empty" : "ready",
    items: page.items,
    nextCursor: page.next_cursor,
    invalidItemCount: 0,
    error: null,
  };
}

function aggregateLoadStatus(
  statuses: [OperationsResourceStatus, OperationsResourceStatus, OperationsResourceStatus],
  hasData: boolean,
): OperationsLoadStatus {
  if (statuses.some((status) => status === "loading")) return "loading";
  const bad = statuses.filter((status) => ["partial", "error", "unavailable"].includes(status));
  if (bad.length === 0) return hasData ? "ready" : "empty";
  const usable = statuses.some((status) => ["ready", "empty", "partial"].includes(status));
  if (usable) return "partial";
  if (bad.every((status) => status === "error")) return "error";
  if (bad.every((status) => status === "unavailable")) return "unavailable";
  return "unavailable";
}

function normalizedQueries(
  alertQuery: AlertListQuery | undefined,
  jobQuery: JobListQuery | undefined,
): { alertKey: string; jobKey: string } {
  return {
    alertKey: JSON.stringify(alertQuery ?? {}),
    jobKey: JSON.stringify(jobQuery ?? {}),
  };
}

export function useReleaseQualityOperations(
  scope: OperationsApiScope,
  options: UseReleaseQualityOperationsOptions,
): ReleaseQualityOperationsHook;
export function useReleaseQualityOperations(
  scope: OperationsApiScope,
  enabled: boolean,
  options?: Omit<UseReleaseQualityOperationsOptions, "enabled">,
): ReleaseQualityOperationsHook;
export function useReleaseQualityOperations(
  scope: OperationsApiScope,
  optionsOrEnabled: UseReleaseQualityOperationsOptions | boolean,
  legacyOptions: Omit<UseReleaseQualityOperationsOptions, "enabled"> = {},
): ReleaseQualityOperationsHook {
  const options: UseReleaseQualityOperationsOptions =
    typeof optionsOrEnabled === "boolean"
      ? { ...legacyOptions, enabled: optionsOrEnabled }
      : optionsOrEnabled;
  const api = options.api ?? releaseQualityOperationsApi;
  const active = options.enabled && hasUsableScope(scope);
  const contextKey = scopeKey(scope);
  const { alertKey, jobKey } = normalizedQueries(options.alertQuery, options.jobQuery);

  const mountedRef = useRef(false);
  const activeRef = useRef(active);
  const readOnlyRef = useRef(options.readOnly === true);
  const scopeRef = useRef(scope);
  const apiRef = useRef(api);
  const alertQueryRef = useRef(options.alertQuery);
  const jobQueryRef = useRef(options.jobQuery);
  const generationRef = useRef(0);
  const loadRequestRef = useRef(0);
  const loadControllerRef = useRef<AbortController | null>(null);
  const controllersRef = useRef(new Set<AbortController>());
  const mutationChainRef = useRef<Promise<void>>(Promise.resolve());
  const mutationPendingRef = useRef(0);
  const retryRef = useRef<MutationRequest | null>(null);

  activeRef.current = active;
  readOnlyRef.current = options.readOnly === true;
  scopeRef.current = scope;
  apiRef.current = api;
  alertQueryRef.current = options.alertQuery;
  jobQueryRef.current = options.jobQuery;

  const [loadStatus, setLoadStatus] = useState<OperationsLoadStatus>("idle");
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [summaryStatus, setSummaryStatus] = useState<OperationsResourceStatus>("idle");
  const [summary, setSummary] = useState<QualityOperationsSummary | null>(null);
  const [summaryError, setSummaryError] = useState<Error | null>(null);
  const [alertStatus, setAlertStatus] = useState<OperationsResourceStatus>("idle");
  const [alerts, setAlerts] = useState<QualityOperationsAlert[]>([]);
  const [alertCursor, setAlertCursor] = useState<string | null>(null);
  const [alertInvalidItemCount, setAlertInvalidItemCount] = useState(0);
  const [alertError, setAlertError] = useState<Error | null>(null);
  const [jobStatus, setJobStatus] = useState<OperationsResourceStatus>("idle");
  const [jobs, setJobs] = useState<RecertificationJob[]>([]);
  const [jobCursor, setJobCursor] = useState<string | null>(null);
  const [jobInvalidItemCount, setJobInvalidItemCount] = useState(0);
  const [jobError, setJobError] = useState<Error | null>(null);
  const [timelineStatus, setTimelineStatus] = useState<"idle" | "unavailable">("idle");
  const [timelineError, setTimelineError] = useState<Error | null>(null);
  const [observationStatus, setObservationStatus] = useState<"idle" | "unavailable">("idle");
  const [observationError, setObservationError] = useState<Error | null>(null);
  const [mutationStatus, setMutationStatus] = useState<OperationsMutationStatus>("idle");
  const [mutationOutcome, setMutationOutcome] = useState<OperationsMutationOutcome | null>(null);
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
    setAlertStatus("idle");
    setAlerts([]);
    setAlertCursor(null);
    setAlertInvalidItemCount(0);
    setAlertError(null);
    setJobStatus("idle");
    setJobs([]);
    setJobCursor(null);
    setJobInvalidItemCount(0);
    setJobError(null);
    setTimelineStatus("idle");
    setTimelineError(null);
    setObservationStatus("idle");
    setObservationError(null);
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
    const alertQuery = alertQueryRef.current ? { ...alertQueryRef.current } : undefined;
    const jobQuery = jobQueryRef.current ? { ...jobQueryRef.current } : undefined;

    setLoadStatus("loading");
    setLoadError(null);
    setSummaryStatus("loading");
    setSummaryError(null);
    setAlertStatus("loading");
    setAlertError(null);
    setJobStatus("loading");
    setJobError(null);

    const isCurrent = () =>
      mountedRef.current &&
      activeRef.current &&
      generation === generationRef.current &&
      requestId === loadRequestRef.current &&
      !controller.signal.aborted;

    try {
      const [summaryResult, alertsResult, jobsResult] = await Promise.allSettled([
        operationApi.fetchSummary(operationScope, { signal: controller.signal }),
        operationApi.fetchAlerts(operationScope, alertQuery, { signal: controller.signal }),
        operationApi.fetchJobs(operationScope, jobQuery, { signal: controller.signal }),
      ]);
      if (!isCurrent()) return false;

      let nextSummaryStatus: OperationsResourceStatus;
      let nextSummary: QualityOperationsSummary | null = null;
      let nextSummaryError: Error | null = null;
      if (summaryResult.status === "fulfilled") {
        nextSummary = summaryResult.value;
        nextSummaryStatus = classifySummary(summaryResult.value);
      } else {
        nextSummaryStatus = "error";
        nextSummaryError = safeError(
          summaryResult.reason,
          "Quality Operations summary is unavailable",
        );
      }

      let nextAlertStatus: OperationsResourceStatus;
      let nextAlerts: QualityOperationsAlert[] = [];
      let nextAlertCursor: string | null = null;
      let nextAlertInvalidItemCount = 0;
      let nextAlertError: Error | null = null;
      if (alertsResult.status === "fulfilled") {
        const projection = classifyCollection(alertsResult.value);
        nextAlertStatus = projection.status;
        nextAlerts = projection.items;
        nextAlertCursor = projection.nextCursor;
        nextAlertInvalidItemCount = projection.invalidItemCount;
        nextAlertError = projection.error;
      } else {
        nextAlertStatus = "error";
        nextAlertError = safeError(alertsResult.reason, "Quality Alert inbox is unavailable");
      }

      let nextJobStatus: OperationsResourceStatus;
      let nextJobs: RecertificationJob[] = [];
      let nextJobCursor: string | null = null;
      let nextJobInvalidItemCount = 0;
      let nextJobError: Error | null = null;
      if (jobsResult.status === "fulfilled") {
        const projection = classifyCollection(jobsResult.value);
        nextJobStatus = projection.status;
        nextJobs = projection.items;
        nextJobCursor = projection.nextCursor;
        nextJobInvalidItemCount = projection.invalidItemCount;
        nextJobError = projection.error;
      } else {
        nextJobStatus = "error";
        nextJobError = safeError(jobsResult.reason, "Recertification Job queue is unavailable");
      }

      setSummaryStatus(nextSummaryStatus);
      setSummary(nextSummary);
      setSummaryError(nextSummaryError);
      setAlertStatus(nextAlertStatus);
      setAlerts(nextAlerts);
      setAlertCursor(nextAlertCursor);
      setAlertInvalidItemCount(nextAlertInvalidItemCount);
      setAlertError(nextAlertError);
      setJobStatus(nextJobStatus);
      setJobs(nextJobs);
      setJobCursor(nextJobCursor);
      setJobInvalidItemCount(nextJobInvalidItemCount);
      setJobError(nextJobError);

      const nextLoadError = nextSummaryError ?? nextAlertError ?? nextJobError;
      setLoadError(nextLoadError);
      setLoadStatus(
        aggregateLoadStatus(
          [nextSummaryStatus, nextAlertStatus, nextJobStatus],
          Boolean(nextSummary?.items.length || nextAlerts.length || nextJobs.length),
        ),
      );
      return true;
    } finally {
      if (loadControllerRef.current === controller) loadControllerRef.current = null;
      release();
    }
  }, [trackController]);

  const lazyTimeline = useCallback(async (): Promise<boolean> => {
    if (!activeRef.current || !mountedRef.current) return false;
    setTimelineStatus("unavailable");
    setTimelineError(unavailableError("Quality Operations Timeline is not available yet"));
    return false;
  }, []);

  const lazyObservations = useCallback(async (): Promise<boolean> => {
    if (!activeRef.current || !mountedRef.current) return false;
    setObservationStatus("unavailable");
    setObservationError(unavailableError("Quality Operations Observations are not available yet"));
    return false;
  }, []);

  const guardMutation = useCallback(
    (message: string): Promise<OperationsMutationOutcome | null> => {
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
    async (request: MutationRequest): Promise<OperationsMutationOutcome | null> => {
      const generation = request.generation;
      const isCurrent = () =>
        mountedRef.current && activeRef.current && generation === generationRef.current;
      if (!isCurrent()) return null;

      const controller = new AbortController();
      const release = trackController(controller);
      setMutationStatus("saving");
      setMutationError(null);
      try {
        const outcome = await request.invoke({
          idempotencyKey: request.idempotencyKey,
          signal: controller.signal,
        });
        if (!isCurrent() || controller.signal.aborted) return null;
        if (outcome.state === "unavailable") {
          throw unavailableError("Quality Operations mutation authority is unavailable");
        }
        setMutationOutcome(outcome);
        setMutationStatus("success");
        retryRef.current = null;
        return outcome;
      } catch (caught) {
        if (!isCurrent() || controller.signal.aborted) return null;
        setMutationStatus("error");
        setMutationOutcome(null);
        setMutationError(
          caught instanceof Error
            ? safeError(caught, "Quality Operations mutation failed")
            : safeError(null, "Quality Operations mutation failed"),
        );
        return null;
      } finally {
        release();
      }
    },
    [trackController],
  );

  const enqueueMutation = useCallback(
    (
      invoke: MutationRequest["invoke"],
      options: OperationsMutationOptions | undefined,
      retrying = false,
    ): Promise<OperationsMutationOutcome | null> => {
      if (!activeRef.current) return guardMutation("Quality Operations is inactive");
      if (readOnlyRef.current) return guardMutation("Quality Operations is read-only");
      const generation = generationRef.current;
      const request: MutationRequest = {
        generation,
        idempotencyKey: options?.idempotencyKey ?? createOperationsIdempotencyKey(),
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
      void task.finally(() => {
        if (generation === generationRef.current) {
          mutationPendingRef.current = Math.max(0, mutationPendingRef.current - 1);
        }
      });
      return task;
    },
    [executeMutation, guardMutation],
  );

  const makeMutationInvoke = useCallback(
    <T extends (...args: never[]) => Promise<OperationsMutationOutcome>>(
      operation: T,
      args: Parameters<T> extends [OperationsApiScope, ...infer Rest extends unknown[]]
        ? Rest
        : never,
    ) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return (requestOptions: OperationsRequestOptions) =>
        operation.apply(operationApi, [operationScope, ...args, requestOptions] as Parameters<T>);
    },
    [],
  );

  const acknowledgeAlertMutation = useCallback(
    (alertId: string, input: AlertAcknowledgeInput, options?: OperationsMutationOptions) =>
      enqueueMutation(
        makeMutationInvoke(apiRef.current.acknowledgeAlert, [alertId, input]),
        options,
      ),
    [enqueueMutation, makeMutationInvoke],
  );

  const resolveAlertMutation = useCallback(
    (alertId: string, input: AlertAcknowledgeInput, options?: OperationsMutationOptions) =>
      enqueueMutation(makeMutationInvoke(apiRef.current.resolveAlert, [alertId, input]), options),
    [enqueueMutation, makeMutationInvoke],
  );

  const suppressAlertMutation = useCallback(
    (alertId: string, input: AlertSuppressInput, options?: OperationsMutationOptions) =>
      enqueueMutation(makeMutationInvoke(apiRef.current.suppressAlert, [alertId, input]), options),
    [enqueueMutation, makeMutationInvoke],
  );

  const queueRecertificationMutation = useCallback(
    (input: QueueRecertificationInput, options?: OperationsMutationOptions) =>
      enqueueMutation(makeMutationInvoke(apiRef.current.queueRecertification, [input]), options),
    [enqueueMutation, makeMutationInvoke],
  );

  const cancelRecertificationMutation = useCallback(
    (jobId: string, input: CancelRecertificationInput, options?: OperationsMutationOptions) =>
      enqueueMutation(
        makeMutationInvoke(apiRef.current.cancelRecertification, [jobId, input]),
        options,
      ),
    [enqueueMutation, makeMutationInvoke],
  );

  const requestScanMutation = useCallback(
    (input: { reason: string }, options?: OperationsMutationOptions) =>
      enqueueMutation(makeMutationInvoke(apiRef.current.requestScan, [input]), options),
    [enqueueMutation, makeMutationInvoke],
  );

  const retry = useCallback((): Promise<OperationsMutationOutcome | null> => {
    const request = retryRef.current;
    if (!request) return Promise.resolve(null);
    return enqueueMutation(request.invoke, { idempotencyKey: request.idempotencyKey }, true);
  }, [enqueueMutation]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      invalidateContext();
    };
  }, [invalidateContext]);

  useEffect(() => {
    invalidateContext();
    resetAuthority();
    if (active) void reload();
    return () => invalidateContext();
  }, [active, alertKey, api, contextKey, invalidateContext, jobKey, reload, resetAuthority]);

  return useMemo<ReleaseQualityOperationsHook>(
    () => ({
      active,
      load: {
        status: loadStatus,
        error: loadError,
        reload,
      },
      summary: {
        status: summaryStatus,
        value: summary,
        error: summaryError,
      },
      alerts: {
        status: alertStatus,
        items: alerts,
        nextCursor: alertCursor,
        invalidItemCount: alertInvalidItemCount,
        error: alertError,
      },
      jobs: {
        status: jobStatus,
        items: jobs,
        nextCursor: jobCursor,
        invalidItemCount: jobInvalidItemCount,
        error: jobError,
      },
      timeline: {
        status: timelineStatus,
        available: false,
        items: [],
        error: timelineError,
        load: lazyTimeline,
      },
      observations: {
        status: observationStatus,
        available: false,
        items: [],
        error: observationError,
        load: lazyObservations,
      },
      mutation: {
        status: mutationStatus,
        outcome: mutationOutcome,
        error: mutationError,
        acknowledgeAlert: acknowledgeAlertMutation,
        resolveAlert: resolveAlertMutation,
        suppressAlert: suppressAlertMutation,
        queueRecertification: queueRecertificationMutation,
        cancelRecertification: cancelRecertificationMutation,
        requestScan: requestScanMutation,
        retry,
      },
    }),
    [
      active,
      acknowledgeAlertMutation,
      alertCursor,
      alertError,
      alertInvalidItemCount,
      alertStatus,
      alerts,
      cancelRecertificationMutation,
      jobCursor,
      jobError,
      jobInvalidItemCount,
      jobStatus,
      jobs,
      lazyObservations,
      lazyTimeline,
      loadError,
      loadStatus,
      mutationError,
      mutationOutcome,
      mutationStatus,
      observationError,
      observationStatus,
      queueRecertificationMutation,
      reload,
      requestScanMutation,
      resolveAlertMutation,
      retry,
      summary,
      summaryError,
      summaryStatus,
      suppressAlertMutation,
      timelineError,
      timelineStatus,
    ],
  );
}
