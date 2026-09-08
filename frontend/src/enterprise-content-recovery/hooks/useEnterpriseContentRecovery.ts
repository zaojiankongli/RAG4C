import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  applyLegalHold,
  bulkRecycleDocuments,
  cancelDocumentPurgeRequest,
  createRecoveryIdempotencyKey,
  fetchLegalHolds,
  fetchPurgeRequests,
  fetchRecycleEntries,
  fetchRecycleEntry,
  fetchRecoverySummary,
  fetchRetentionPolicy,
  recycleDocument,
  releaseLegalHold,
  requestDocumentPurge,
  restoreRecycleEntry,
  updateContentRetentionPolicy,
  type ApplyLegalHoldInput,
  type BulkRecycleDocumentsInput,
  type CancelDocumentPurgeRequestInput,
  type EnterpriseContentRecoveryApi,
  type RecoveryApiScope,
  type RecoveryChildListQuery,
  type RecoveryEntryListQuery,
  type RecoveryPage,
  type RecoveryRequestOptions,
  type RecycleDocumentInput,
  type ReleaseLegalHoldInput,
  type RequestDocumentPurgeInput,
  type RestoreRecycleEntryInput,
  type UpdateContentRetentionPolicyInput,
} from "../api/recoveryApi";
import type {
  ContentRetentionPolicy,
  LegalHold,
  PurgeRequest,
  RecoveryEntry,
  RecoveryEntryDetail,
  RecoveryMutationOutcome,
  RecoverySummary,
} from "../model/recoveryModel";

export type RecoveryLoadStatus =
  "idle" | "loading" | "ready" | "partial" | "empty" | "error" | "unavailable";
export type RecoveryMutationStatus = "idle" | "saving" | "success" | "error";

export type { EnterpriseContentRecoveryApi };
export type EnterpriseContentRecoveryApiContract = EnterpriseContentRecoveryApi;

export interface UseEnterpriseContentRecoveryOptions {
  enabled: boolean;
  readOnly?: boolean;
  api?: EnterpriseContentRecoveryApi;
  entriesQuery?: RecoveryEntryListQuery;
  childQuery?: RecoveryChildListQuery;
}

export type EnterpriseContentRecoveryOptions = UseEnterpriseContentRecoveryOptions;
export type RecoveryMutationOptions = Pick<RecoveryRequestOptions, "idempotencyKey">;

export interface RecoverySummaryState {
  status: RecoveryLoadStatus;
  value: RecoverySummary | null;
  error: Error | null;
}

export interface RecoveryCollectionState<T> {
  status: RecoveryLoadStatus;
  items: T[];
  nextCursor: string | null;
  invalidItemCount: number;
  error: Error | null;
}

export interface RecoveryLazyCollectionState<T> extends RecoveryCollectionState<T> {
  entryId: string | null;
  load: (entryId: string) => Promise<boolean>;
}

export interface RecoveryDetailState {
  status: RecoveryLoadStatus;
  value: RecoveryEntryDetail | null;
  error: Error | null;
  load: (entryId: string) => Promise<boolean>;
}

export interface RecoveryPolicyState {
  status: RecoveryLoadStatus;
  value: ContentRetentionPolicy | null;
  error: Error | null;
  load: () => Promise<boolean>;
}

export interface EnterpriseContentRecoveryHook {
  active: boolean;
  load: {
    status: RecoveryLoadStatus;
    error: Error | null;
    reload: () => Promise<boolean>;
  };
  summary: RecoverySummaryState;
  entries: RecoveryCollectionState<RecoveryEntry>;
  detail: RecoveryDetailState;
  holds: RecoveryLazyCollectionState<LegalHold>;
  purgeRequests: RecoveryLazyCollectionState<PurgeRequest>;
  policy: RecoveryPolicyState;
  retentionPolicy: RecoveryPolicyState;
  mutation: {
    status: RecoveryMutationStatus;
    outcome: RecoveryMutationOutcome | null;
    error: Error | null;
    recycle: (
      documentId: string,
      input: RecycleDocumentInput,
      options?: RecoveryMutationOptions,
    ) => Promise<RecoveryMutationOutcome | null>;
    bulkRecycle: (
      input: BulkRecycleDocumentsInput,
      options?: RecoveryMutationOptions,
    ) => Promise<RecoveryMutationOutcome[] | null>;
    restore: (
      entryId: string,
      input: RestoreRecycleEntryInput,
      options?: RecoveryMutationOptions,
    ) => Promise<RecoveryMutationOutcome | null>;
    applyHold: (
      entryId: string,
      input: ApplyLegalHoldInput,
      options?: RecoveryMutationOptions,
    ) => Promise<RecoveryMutationOutcome | null>;
    releaseHold: (
      entryId: string,
      holdId: string,
      input: ReleaseLegalHoldInput,
      options?: RecoveryMutationOptions,
    ) => Promise<RecoveryMutationOutcome | null>;
    requestPurge: (
      entryId: string,
      input: RequestDocumentPurgeInput,
      options?: RecoveryMutationOptions,
    ) => Promise<RecoveryMutationOutcome | null>;
    cancelPurge: (
      entryId: string,
      requestId: string,
      input: CancelDocumentPurgeRequestInput,
      options?: RecoveryMutationOptions,
    ) => Promise<RecoveryMutationOutcome | null>;
    updatePolicy: (
      input: UpdateContentRetentionPolicyInput,
      options?: RecoveryMutationOptions,
    ) => Promise<RecoveryMutationOutcome | null>;
    retry: () => Promise<RecoveryMutationOutcome | null>;
  };
}

export const enterpriseContentRecoveryApi: EnterpriseContentRecoveryApi = {
  bulkRecycle: bulkRecycleDocuments,
  fetchSummary: fetchRecoverySummary,
  fetchEntries: fetchRecycleEntries,
  fetchDetail: fetchRecycleEntry,
  fetchHolds: fetchLegalHolds,
  fetchPurgeRequests,
  fetchPolicy: fetchRetentionPolicy,
  recycle: recycleDocument,
  restore: restoreRecycleEntry,
  applyHold: applyLegalHold,
  releaseHold: releaseLegalHold,
  requestPurge: requestDocumentPurge,
  cancelPurge: cancelDocumentPurgeRequest,
  updatePolicy: updateContentRetentionPolicy,
};

export const recoveryCenterApi = enterpriseContentRecoveryApi;

interface MutationRequest<T = RecoveryMutationOutcome> {
  generation: number;
  idempotencyKey: string;
  invoke: (options: RecoveryRequestOptions) => Promise<T>;
}

function hasControlCharacters(value: string): boolean {
  for (const character of value) {
    const code = character.charCodeAt(0);
    if (code < 32 || code === 127) return true;
  }
  return false;
}

function safeError(error: unknown, fallback: string): Error {
  const message = error instanceof Error ? error.message.trim() : "";
  const safe =
    message.length > 0 &&
    message.length <= 512 &&
    !hasControlCharacters(message) &&
    !/(?:password|passwd|secret|credential|authorization|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|client[_ -]?secret|idempotency[_ -]?key|ticket|token)\s*[:=]|bearer\s+|(?:https?|ftp|file|mailto|javascript|data):\S+/i.test(
      message,
    )
      ? message
      : "";
  return new Error(safe || fallback);
}

function isAbortError(error: unknown): boolean {
  return (
    (typeof DOMException !== "undefined" &&
      error instanceof DOMException &&
      error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

function isUnavailableError(error: unknown): boolean {
  return error instanceof Error && /unavailable|authority|not available/i.test(error.message);
}

function summaryStatus(value: RecoverySummary): RecoveryLoadStatus {
  if (value.state === "unavailable") return "unavailable";
  if (value.state === "error") return "error";
  if (value.state === "partial") return "partial";
  return "ready";
}

function pageStatus<T>(page: RecoveryPage<T>): RecoveryLoadStatus {
  if (page.invalid_item_count > 0) return "partial";
  return page.items.length > 0 ? "ready" : "empty";
}

function aggregateStatus(
  summaryStatusValue: RecoveryLoadStatus,
  entriesStatus: RecoveryLoadStatus,
  summary: RecoverySummary | null,
  entries: RecoveryEntry[],
): RecoveryLoadStatus {
  if (summaryStatusValue === "loading" || entriesStatus === "loading") return "loading";
  const failed = summaryStatusValue === "error" || entriesStatus === "error";
  const unavailable = summaryStatusValue === "unavailable" || entriesStatus === "unavailable";
  if (summaryStatusValue === "partial" || entriesStatus === "partial") return "partial";
  if (failed || unavailable) {
    if (summary !== null || entries.length > 0) return "partial";
    return unavailable ? "unavailable" : "error";
  }
  if (
    summaryStatusValue === "ready" &&
    entriesStatus === "empty" &&
    summary?.state === "ready" &&
    summary.recycled_count === 0
  ) {
    return "empty";
  }
  return "ready";
}

export function useEnterpriseContentRecovery(
  scope: RecoveryApiScope,
  options: UseEnterpriseContentRecoveryOptions,
): EnterpriseContentRecoveryHook {
  const active = options.enabled === true;
  const readOnly = options.readOnly === true;
  const api = options.api ?? enterpriseContentRecoveryApi;

  const activeRef = useRef(active);
  const readOnlyRef = useRef(readOnly);
  const scopeRef = useRef(scope);
  const apiRef = useRef(api);
  const entriesQueryRef = useRef(options.entriesQuery);
  const childQueryRef = useRef(options.childQuery);
  const mountedRef = useRef(false);
  const generationRef = useRef(0);
  const loadRequestRef = useRef(0);
  const detailRequestRef = useRef(0);
  const holdsRequestRef = useRef(0);
  const purgeRequestRef = useRef(0);
  const policyRequestRef = useRef(0);
  const controllersRef = useRef(new Set<AbortController>());
  const mutationChainRef = useRef<Promise<void>>(Promise.resolve());
  const retryRef = useRef<MutationRequest | null>(null);

  activeRef.current = active;
  readOnlyRef.current = readOnly;
  scopeRef.current = scope;
  apiRef.current = api;
  entriesQueryRef.current = options.entriesQuery;
  childQueryRef.current = options.childQuery;

  const [loadStatus, setLoadStatus] = useState<RecoveryLoadStatus>("idle");
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [summaryStatusValue, setSummaryStatus] = useState<RecoveryLoadStatus>("idle");
  const [summary, setSummary] = useState<RecoverySummary | null>(null);
  const [summaryError, setSummaryError] = useState<Error | null>(null);
  const [entriesStatus, setEntriesStatus] = useState<RecoveryLoadStatus>("idle");
  const [entries, setEntries] = useState<RecoveryEntry[]>([]);
  const [entriesCursor, setEntriesCursor] = useState<string | null>(null);
  const [entriesInvalidCount, setEntriesInvalidCount] = useState(0);
  const [entriesError, setEntriesError] = useState<Error | null>(null);
  const [detailStatus, setDetailStatus] = useState<RecoveryLoadStatus>("idle");
  const [detail, setDetail] = useState<RecoveryEntryDetail | null>(null);
  const [detailError, setDetailError] = useState<Error | null>(null);
  const [holdsStatus, setHoldsStatus] = useState<RecoveryLoadStatus>("idle");
  const [holds, setHolds] = useState<LegalHold[]>([]);
  const [holdsCursor, setHoldsCursor] = useState<string | null>(null);
  const [holdsInvalidCount, setHoldsInvalidCount] = useState(0);
  const [holdsError, setHoldsError] = useState<Error | null>(null);
  const [holdsEntryId, setHoldsEntryId] = useState<string | null>(null);
  const [purgeStatus, setPurgeStatus] = useState<RecoveryLoadStatus>("idle");
  const [purgeRequests, setPurgeRequests] = useState<PurgeRequest[]>([]);
  const [purgeCursor, setPurgeCursor] = useState<string | null>(null);
  const [purgeInvalidCount, setPurgeInvalidCount] = useState(0);
  const [purgeError, setPurgeError] = useState<Error | null>(null);
  const [purgeEntryId, setPurgeEntryId] = useState<string | null>(null);
  const [policyStatus, setPolicyStatus] = useState<RecoveryLoadStatus>("idle");
  const [policy, setPolicy] = useState<ContentRetentionPolicy | null>(null);
  const [policyError, setPolicyError] = useState<Error | null>(null);
  const [mutationStatus, setMutationStatus] = useState<RecoveryMutationStatus>("idle");
  const [mutationOutcome, setMutationOutcome] = useState<RecoveryMutationOutcome | null>(null);
  const [mutationError, setMutationError] = useState<Error | null>(null);

  const trackController = useCallback(() => {
    const controller = new AbortController();
    controllersRef.current.add(controller);
    return controller;
  }, []);

  const releaseController = useCallback((controller: AbortController) => {
    controllersRef.current.delete(controller);
  }, []);

  const invalidateContext = useCallback(() => {
    generationRef.current += 1;
    loadRequestRef.current += 1;
    detailRequestRef.current += 1;
    holdsRequestRef.current += 1;
    purgeRequestRef.current += 1;
    policyRequestRef.current += 1;
    for (const controller of controllersRef.current) controller.abort();
    controllersRef.current.clear();
  }, []);

  const resetAuthority = useCallback(() => {
    setLoadStatus("idle");
    setLoadError(null);
    setSummaryStatus("idle");
    setSummary(null);
    setSummaryError(null);
    setEntriesStatus("idle");
    setEntries([]);
    setEntriesCursor(null);
    setEntriesInvalidCount(0);
    setEntriesError(null);
    setDetailStatus("idle");
    setDetail(null);
    setDetailError(null);
    setHoldsStatus("idle");
    setHolds([]);
    setHoldsCursor(null);
    setHoldsInvalidCount(0);
    setHoldsError(null);
    setHoldsEntryId(null);
    setPurgeStatus("idle");
    setPurgeRequests([]);
    setPurgeCursor(null);
    setPurgeInvalidCount(0);
    setPurgeError(null);
    setPurgeEntryId(null);
    setPolicyStatus("idle");
    setPolicy(null);
    setPolicyError(null);
    setMutationStatus("idle");
    setMutationOutcome(null);
    setMutationError(null);
    retryRef.current = null;
  }, []);

  const reload = useCallback(async (): Promise<boolean> => {
    if (!activeRef.current || !mountedRef.current) return false;
    const generation = generationRef.current;
    const requestId = ++loadRequestRef.current;
    const controller = trackController();
    setLoadStatus("loading");
    setLoadError(null);
    setSummaryStatus("loading");
    setSummaryError(null);
    setEntriesStatus("loading");
    setEntriesError(null);
    setEntries([]);
    setEntriesCursor(null);
    setEntriesInvalidCount(0);
    const operationScope = { ...scopeRef.current };
    const operationApi = apiRef.current;
    const summaryPromise = Promise.resolve().then(() =>
      operationApi.fetchSummary(operationScope, { signal: controller.signal }),
    );
    const entriesPromise = Promise.resolve().then(() =>
      operationApi.fetchEntries(operationScope, entriesQueryRef.current ?? {}, {
        signal: controller.signal,
      }),
    );
    const [summaryResult, entriesResult] = await Promise.allSettled([
      summaryPromise,
      entriesPromise,
    ]);
    releaseController(controller);
    if (
      !mountedRef.current ||
      !activeRef.current ||
      generation !== generationRef.current ||
      requestId !== loadRequestRef.current
    ) {
      return false;
    }

    let nextSummaryStatus: RecoveryLoadStatus;
    let nextEntriesStatus: RecoveryLoadStatus;
    let nextSummary: RecoverySummary | null = null;
    let nextEntries: RecoveryEntry[] = [];
    if (summaryResult.status === "fulfilled") {
      nextSummary = summaryResult.value;
      nextSummaryStatus = summaryStatus(nextSummary);
      setSummary(nextSummary);
      setSummaryStatus(nextSummaryStatus);
      setSummaryError(null);
    } else if (isAbortError(summaryResult.reason)) {
      return false;
    } else {
      nextSummaryStatus = isUnavailableError(summaryResult.reason) ? "unavailable" : "error";
      const error = safeError(summaryResult.reason, "Recovery summary is unavailable");
      setSummary(null);
      setSummaryStatus(nextSummaryStatus);
      setSummaryError(error);
    }
    if (entriesResult.status === "fulfilled") {
      const page = entriesResult.value;
      nextEntries = page.items;
      nextEntriesStatus = pageStatus(page);
      setEntries(nextEntries);
      setEntriesCursor(page.next_cursor);
      setEntriesInvalidCount(page.invalid_item_count);
      setEntriesStatus(nextEntriesStatus);
      setEntriesError(null);
    } else if (isAbortError(entriesResult.reason)) {
      return false;
    } else {
      nextEntriesStatus = isUnavailableError(entriesResult.reason) ? "unavailable" : "error";
      const error = safeError(entriesResult.reason, "Recycle bin entries are unavailable");
      setEntries([]);
      setEntriesCursor(null);
      setEntriesInvalidCount(0);
      setEntriesStatus(nextEntriesStatus);
      setEntriesError(error);
    }
    const aggregate = aggregateStatus(
      nextSummaryStatus,
      nextEntriesStatus,
      nextSummary,
      nextEntries,
    );
    setLoadStatus(aggregate);
    setLoadError(
      nextSummaryStatus === "error"
        ? safeError(
            summaryResult.status === "rejected" ? summaryResult.reason : null,
            "Recovery summary is unavailable",
          )
        : nextEntriesStatus === "error"
          ? safeError(
              entriesResult.status === "rejected" ? entriesResult.reason : null,
              "Recycle bin entries are unavailable",
            )
          : null,
    );
    return summaryResult.status === "fulfilled" && entriesResult.status === "fulfilled";
  }, [releaseController, trackController]);

  const loadDetail = useCallback(
    async (entryId: string): Promise<boolean> => {
      if (!activeRef.current || !mountedRef.current) return false;
      const generation = generationRef.current;
      const requestId = ++detailRequestRef.current;
      const controller = trackController();
      setDetailStatus("loading");
      setDetailError(null);
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      try {
        const value = await operationApi.fetchDetail(operationScope, entryId, {
          signal: controller.signal,
        });
        if (
          !mountedRef.current ||
          !activeRef.current ||
          generation !== generationRef.current ||
          requestId !== detailRequestRef.current
        )
          return false;
        setDetail(value);
        setDetailStatus("ready");
        setDetailError(null);
        return true;
      } catch (error) {
        if (
          isAbortError(error) ||
          !mountedRef.current ||
          !activeRef.current ||
          generation !== generationRef.current ||
          requestId !== detailRequestRef.current
        )
          return false;
        setDetail(null);
        setDetailStatus(isUnavailableError(error) ? "unavailable" : "error");
        setDetailError(safeError(error, "Recycle bin entry is unavailable"));
        return false;
      } finally {
        releaseController(controller);
      }
    },
    [releaseController, trackController],
  );

  const loadHolds = useCallback(
    async (entryId: string): Promise<boolean> => {
      if (!activeRef.current || !mountedRef.current) return false;
      const generation = generationRef.current;
      const requestId = ++holdsRequestRef.current;
      const controller = trackController();
      setHoldsEntryId(entryId);
      setHoldsStatus("loading");
      setHoldsError(null);
      try {
        const page = await apiRef.current.fetchHolds(
          { ...scopeRef.current },
          entryId,
          childQueryRef.current ?? {},
          { signal: controller.signal },
        );
        if (
          !mountedRef.current ||
          !activeRef.current ||
          generation !== generationRef.current ||
          requestId !== holdsRequestRef.current
        )
          return false;
        setHolds(page.items);
        setHoldsCursor(page.next_cursor);
        setHoldsInvalidCount(page.invalid_item_count);
        setHoldsStatus(pageStatus(page));
        setHoldsError(null);
        return true;
      } catch (error) {
        if (
          isAbortError(error) ||
          !mountedRef.current ||
          !activeRef.current ||
          generation !== generationRef.current ||
          requestId !== holdsRequestRef.current
        )
          return false;
        setHolds([]);
        setHoldsCursor(null);
        setHoldsInvalidCount(0);
        setHoldsStatus(isUnavailableError(error) ? "unavailable" : "error");
        setHoldsError(safeError(error, "Legal holds are unavailable"));
        return false;
      } finally {
        releaseController(controller);
      }
    },
    [releaseController, trackController],
  );

  const loadPurgeRequests = useCallback(
    async (entryId: string): Promise<boolean> => {
      if (!activeRef.current || !mountedRef.current) return false;
      const generation = generationRef.current;
      const requestId = ++purgeRequestRef.current;
      const controller = trackController();
      setPurgeEntryId(entryId);
      setPurgeStatus("loading");
      setPurgeError(null);
      try {
        const page = await apiRef.current.fetchPurgeRequests(
          { ...scopeRef.current },
          entryId,
          childQueryRef.current ?? {},
          { signal: controller.signal },
        );
        if (
          !mountedRef.current ||
          !activeRef.current ||
          generation !== generationRef.current ||
          requestId !== purgeRequestRef.current
        )
          return false;
        setPurgeRequests(page.items);
        setPurgeCursor(page.next_cursor);
        setPurgeInvalidCount(page.invalid_item_count);
        setPurgeStatus(pageStatus(page));
        setPurgeError(null);
        return true;
      } catch (error) {
        if (
          isAbortError(error) ||
          !mountedRef.current ||
          !activeRef.current ||
          generation !== generationRef.current ||
          requestId !== purgeRequestRef.current
        )
          return false;
        setPurgeRequests([]);
        setPurgeCursor(null);
        setPurgeInvalidCount(0);
        setPurgeStatus(isUnavailableError(error) ? "unavailable" : "error");
        setPurgeError(safeError(error, "Purge requests are unavailable"));
        return false;
      } finally {
        releaseController(controller);
      }
    },
    [releaseController, trackController],
  );

  const loadPolicy = useCallback(async (): Promise<boolean> => {
    if (!activeRef.current || !mountedRef.current) return false;
    const generation = generationRef.current;
    const requestId = ++policyRequestRef.current;
    const controller = trackController();
    setPolicyStatus("loading");
    setPolicyError(null);
    try {
      const value = await apiRef.current.fetchPolicy(
        { ...scopeRef.current },
        { signal: controller.signal },
      );
      if (
        !mountedRef.current ||
        !activeRef.current ||
        generation !== generationRef.current ||
        requestId !== policyRequestRef.current
      )
        return false;
      setPolicy(value);
      setPolicyStatus("ready");
      setPolicyError(null);
      return true;
    } catch (error) {
      if (
        isAbortError(error) ||
        !mountedRef.current ||
        !activeRef.current ||
        generation !== generationRef.current ||
        requestId !== policyRequestRef.current
      )
        return false;
      setPolicy(null);
      setPolicyStatus(isUnavailableError(error) ? "unavailable" : "error");
      setPolicyError(safeError(error, "Content retention policy is unavailable"));
      return false;
    } finally {
      releaseController(controller);
    }
  }, [releaseController, trackController]);

  const executeMutation = useCallback(
    async <T>(request: MutationRequest<T>): Promise<T | null> => {
      if (
        !activeRef.current ||
        !mountedRef.current ||
        readOnlyRef.current ||
        request.generation !== generationRef.current
      )
        return null;
      const controller = trackController();
      try {
        const value = await request.invoke({
          signal: controller.signal,
          idempotencyKey: request.idempotencyKey,
        });
        if (
          mountedRef.current &&
          activeRef.current &&
          !readOnlyRef.current &&
          request.generation === generationRef.current &&
          isRecoveryMutationOutcome(value)
        ) {
          setMutationStatus("success");
          setMutationOutcome(value);
          setMutationError(null);
          if (retryRef.current === request) retryRef.current = null;
        }
        return value;
      } catch (error) {
        if (
          mountedRef.current &&
          activeRef.current &&
          request.generation === generationRef.current &&
          !isAbortError(error)
        ) {
          setMutationStatus("error");
          setMutationError(safeError(error, "Recovery mutation is unavailable"));
          setMutationOutcome(null);
        }
        throw safeError(error, "Recovery mutation is unavailable");
      } finally {
        releaseController(controller);
      }
    },
    [releaseController, trackController],
  );

  const enqueueMutation = useCallback(
    <T>(
      invoke: (options: RecoveryRequestOptions) => Promise<T>,
      optionsValue?: RecoveryMutationOptions,
      retrying = false,
    ): Promise<T | null> => {
      if (!activeRef.current || !mountedRef.current || readOnlyRef.current)
        return Promise.resolve(null);
      const request: MutationRequest<T> = {
        generation: generationRef.current,
        idempotencyKey: optionsValue?.idempotencyKey ?? createRecoveryIdempotencyKey(),
        invoke,
      };
      if (!retrying) retryRef.current = request as MutationRequest;
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
      return task;
    },
    [executeMutation],
  );

  const recycle = useCallback(
    (documentId: string, input: RecycleDocumentInput, optionsValue?: RecoveryMutationOptions) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) => operationApi.recycle(operationScope, documentId, input, requestOptions),
        optionsValue,
      );
    },
    [enqueueMutation],
  );

  const bulkRecycle = useCallback(
    (input: BulkRecycleDocumentsInput, optionsValue?: RecoveryMutationOptions) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) =>
          operationApi.bulkRecycle?.(operationScope, input, requestOptions) ?? Promise.resolve([]),
        optionsValue,
      );
    },
    [enqueueMutation],
  );

  const restore = useCallback(
    (entryId: string, input: RestoreRecycleEntryInput, optionsValue?: RecoveryMutationOptions) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) => operationApi.restore(operationScope, entryId, input, requestOptions),
        optionsValue,
      );
    },
    [enqueueMutation],
  );

  const applyHold = useCallback(
    (entryId: string, input: ApplyLegalHoldInput, optionsValue?: RecoveryMutationOptions) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) => operationApi.applyHold(operationScope, entryId, input, requestOptions),
        optionsValue,
      );
    },
    [enqueueMutation],
  );

  const releaseHold = useCallback(
    (
      entryId: string,
      holdId: string,
      input: ReleaseLegalHoldInput,
      optionsValue?: RecoveryMutationOptions,
    ) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) =>
          operationApi.releaseHold(operationScope, entryId, holdId, input, requestOptions),
        optionsValue,
      );
    },
    [enqueueMutation],
  );

  const requestPurge = useCallback(
    (entryId: string, input: RequestDocumentPurgeInput, optionsValue?: RecoveryMutationOptions) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) =>
          operationApi.requestPurge(operationScope, entryId, input, requestOptions),
        optionsValue,
      );
    },
    [enqueueMutation],
  );

  const cancelPurge = useCallback(
    (
      entryId: string,
      requestId: string,
      input: CancelDocumentPurgeRequestInput,
      optionsValue?: RecoveryMutationOptions,
    ) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) =>
          operationApi.cancelPurge(operationScope, entryId, requestId, input, requestOptions),
        optionsValue,
      );
    },
    [enqueueMutation],
  );

  const updatePolicy = useCallback(
    (input: UpdateContentRetentionPolicyInput, optionsValue?: RecoveryMutationOptions) => {
      const operationScope = { ...scopeRef.current };
      const operationApi = apiRef.current;
      return enqueueMutation(
        (requestOptions) => operationApi.updatePolicy(operationScope, input, requestOptions),
        optionsValue,
      );
    },
    [enqueueMutation],
  );

  const retry = useCallback((): Promise<RecoveryMutationOutcome | null> => {
    const request = retryRef.current;
    if (!request || !activeRef.current || !mountedRef.current || readOnlyRef.current) {
      return Promise.resolve(null);
    }
    return enqueueMutation(
      request.invoke,
      { idempotencyKey: request.idempotencyKey },
      true,
    ) as Promise<RecoveryMutationOutcome | null>;
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

  return useMemo<EnterpriseContentRecoveryHook>(
    () => ({
      active,
      load: { status: loadStatus, error: loadError, reload },
      summary: { status: summaryStatusValue, value: summary, error: summaryError },
      entries: {
        status: entriesStatus,
        items: entries,
        nextCursor: entriesCursor,
        invalidItemCount: entriesInvalidCount,
        error: entriesError,
      },
      detail: { status: detailStatus, value: detail, error: detailError, load: loadDetail },
      holds: {
        status: holdsStatus,
        items: holds,
        nextCursor: holdsCursor,
        invalidItemCount: holdsInvalidCount,
        error: holdsError,
        entryId: holdsEntryId,
        load: loadHolds,
      },
      purgeRequests: {
        status: purgeStatus,
        items: purgeRequests,
        nextCursor: purgeCursor,
        invalidItemCount: purgeInvalidCount,
        error: purgeError,
        entryId: purgeEntryId,
        load: loadPurgeRequests,
      },
      policy: { status: policyStatus, value: policy, error: policyError, load: loadPolicy },
      retentionPolicy: {
        status: policyStatus,
        value: policy,
        error: policyError,
        load: loadPolicy,
      },
      mutation: {
        status: mutationStatus,
        outcome: mutationOutcome,
        error: mutationError,
        recycle,
        bulkRecycle,
        restore,
        applyHold,
        releaseHold,
        requestPurge,
        cancelPurge,
        updatePolicy,
        retry,
      },
    }),
    [
      active,
      applyHold,
      bulkRecycle,
      cancelPurge,
      detail,
      detailError,
      detailStatus,
      entries,
      entriesCursor,
      entriesError,
      entriesInvalidCount,
      entriesStatus,
      holds,
      holdsCursor,
      holdsEntryId,
      holdsError,
      holdsInvalidCount,
      holdsStatus,
      loadDetail,
      loadError,
      loadHolds,
      loadPurgeRequests,
      loadPolicy,
      loadStatus,
      mutationError,
      mutationOutcome,
      mutationStatus,
      policy,
      policyError,
      policyStatus,
      purgeCursor,
      purgeEntryId,
      purgeError,
      purgeInvalidCount,
      purgeRequests,
      purgeStatus,
      recycle,
      reload,
      releaseHold,
      requestPurge,
      restore,
      retry,
      summary,
      summaryError,
      summaryStatusValue,
      updatePolicy,
    ],
  );
}

function isRecoveryMutationOutcome(value: unknown): value is RecoveryMutationOutcome {
  return typeof value === "object" && value !== null && "state" in value && "operation" in value;
}
