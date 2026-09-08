import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  activateServingProfile,
  createServingIdempotencyKey,
  createServingPolicyRevision,
  createServingProfile,
  fetchServingEvents,
  fetchServingProfile,
  fetchServingSnapshot,
  fetchServingSnapshots,
  fetchServingStageFacts,
  fetchServingSummary,
  previewServingPolicy,
  type ActivateServingProfileInput,
  type CreateServingPolicyRevisionInput,
  type CreateServingProfileInput,
  type PreviewServingPolicyInput,
  type ServingApi,
  type ServingApiScope,
  type ServingListQuery,
  type ServingRequestOptions,
} from "../api/servingApi";
import type {
  ServingEvent,
  ServingEventPage,
  ServingMutationOutcome,
  ServingPage,
  ServingPreview,
  ServingProfileEnvelope,
  ServingSnapshot,
  ServingSnapshotDetail,
  ServingStageFact,
  ServingSummary,
} from "../model/servingModel";

export type ServingLoadStatus =
  "idle" | "loading" | "ready" | "partial" | "empty" | "unavailable" | "error";
export interface ServingDetailValue extends ServingSnapshotDetail {
  events: ServingEvent[];
}
export interface ServingMutationState {
  status: "idle" | "saving" | "success" | "error";
  outcome: ServingMutationOutcome | null;
  error: Error | null;
}
export interface UseEnterpriseKnowledgeServingOptions {
  enabled: boolean;
  readOnly?: boolean;
  api?: ServingApi;
}
export interface ServingCollection<T> {
  status: ServingLoadStatus;
  items: T[];
  count: number | null;
  nextCursor: string | null;
  invalidItemCount: number;
  error: Error | null;
  load: (query?: ServingListQuery) => Promise<boolean>;
  reload: (query?: ServingListQuery) => Promise<boolean>;
}
export interface EnterpriseKnowledgeServingHook {
  active: boolean;
  readOnly: boolean;
  load: { status: ServingLoadStatus; error: Error | null; reload: () => Promise<boolean> };
  summary: { status: ServingLoadStatus; value: ServingSummary | null; error: Error | null };
  profile: { status: ServingLoadStatus; value: ServingProfileEnvelope | null; error: Error | null };
  snapshots: ServingCollection<ServingSnapshot>;
  stageFacts: ServingCollection<ServingStageFact>;
  activity: ServingCollection<ServingEvent>;
  detail: {
    status: ServingLoadStatus;
    value: ServingDetailValue | null;
    error: Error | null;
    load: (id: string) => Promise<boolean>;
  };
  mutation: ServingMutationState & {
    createProfile: (input: CreateServingProfileInput) => Promise<ServingMutationOutcome | null>;
    createPolicyRevision: (
      input: CreateServingPolicyRevisionInput,
    ) => Promise<ServingMutationOutcome | null>;
    activateProfile: (input: ActivateServingProfileInput) => Promise<ServingMutationOutcome | null>;
    previewPolicy: (input: PreviewServingPolicyInput) => Promise<ServingPreview | null>;
  };
}

const defaultApi: ServingApi = {
  fetchSummary: fetchServingSummary,
  fetchProfile: fetchServingProfile,
  fetchSnapshots: fetchServingSnapshots,
  fetchSnapshot: fetchServingSnapshot,
  fetchStageFacts: fetchServingStageFacts,
  fetchEvents: fetchServingEvents,
  createProfile: createServingProfile,
  createPolicyRevision: createServingPolicyRevision,
  activateProfile: activateServingProfile,
  previewPolicy: previewServingPolicy,
};
const errorOf = (value: unknown, fallback: string) =>
  value instanceof Error ? value : new Error(fallback);
const pageStatus = <T>(page: ServingPage<T>): ServingLoadStatus =>
  page.items.length === 0 && page.invalid_item_count === 0
    ? "empty"
    : page.invalid_item_count > 0
      ? "partial"
      : "ready";
const eventPageStatus = (page: ServingEventPage): ServingLoadStatus => pageStatus(page);
function emptyPage<T>(): ServingPage<T> {
  return { items: [], count: null, next_cursor: null, invalid_item_count: 0 };
}
function isAbort(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}
function requestContext(generation: number, controllers: Set<AbortController>) {
  const controller = new AbortController();
  controllers.add(controller);
  return {
    generation,
    controller,
    options: { signal: controller.signal } satisfies ServingRequestOptions,
  };
}

export function useEnterpriseKnowledgeServing(
  scope: ServingApiScope,
  options: UseEnterpriseKnowledgeServingOptions,
): EnterpriseKnowledgeServingHook {
  const active = options.enabled;
  const readOnly = Boolean(options.readOnly);
  const api = options.api ?? defaultApi;
  const contextKey = [
    scope.tenantId,
    scope.accountId ?? "",
    scope.datasetId,
    scope.actorToken,
    active ? "enabled" : "disabled",
    readOnly ? "readonly" : "editable",
  ].join("\u0000");
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const generationRef = useRef(0);
  const mountedRef = useRef(false);
  const controllersRef = useRef(new Set<AbortController>());
  const mutationQueueRef = useRef<Promise<unknown>>(Promise.resolve());
  const selectedSnapshotRef = useRef<string | null>(null);
  const [loadStatus, setLoadStatus] = useState<ServingLoadStatus>("idle");
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [summary, setSummary] = useState<ServingSummary | null>(null);
  const [summaryStatus, setSummaryStatus] = useState<ServingLoadStatus>("idle");
  const [summaryError, setSummaryError] = useState<Error | null>(null);
  const [profile, setProfile] = useState<ServingProfileEnvelope | null>(null);
  const [profileStatus, setProfileStatus] = useState<ServingLoadStatus>("idle");
  const [profileError, setProfileError] = useState<Error | null>(null);
  const [snapshots, setSnapshots] = useState<ServingPage<ServingSnapshot>>(emptyPage);
  const [snapshotsStatus, setSnapshotsStatus] = useState<ServingLoadStatus>("idle");
  const [snapshotsError, setSnapshotsError] = useState<Error | null>(null);
  const [stageFacts, setStageFacts] = useState<ServingPage<ServingStageFact>>(emptyPage);
  const [stageFactsStatus, setStageFactsStatus] = useState<ServingLoadStatus>("idle");
  const [stageFactsError, setStageFactsError] = useState<Error | null>(null);
  const [activity, setActivity] = useState<ServingEventPage>(emptyPage);
  const [activityStatus, setActivityStatus] = useState<ServingLoadStatus>("idle");
  const [activityError, setActivityError] = useState<Error | null>(null);
  const [detail, setDetail] = useState<ServingDetailValue | null>(null);
  const [detailStatus, setDetailStatus] = useState<ServingLoadStatus>("idle");
  const [detailError, setDetailError] = useState<Error | null>(null);
  const [mutationStatus, setMutationStatus] = useState<ServingMutationState["status"]>("idle");
  const [mutationOutcome, setMutationOutcome] = useState<ServingMutationOutcome | null>(null);
  const [mutationError, setMutationError] = useState<Error | null>(null);

  const abortInflight = useCallback(() => {
    for (const controller of controllersRef.current) controller.abort();
    controllersRef.current.clear();
  }, []);
  const resetState = useCallback(() => {
    selectedSnapshotRef.current = null;
    setLoadStatus("idle");
    setLoadError(null);
    setSummary(null);
    setSummaryStatus("idle");
    setSummaryError(null);
    setProfile(null);
    setProfileStatus("idle");
    setProfileError(null);
    setSnapshots(emptyPage());
    setSnapshotsStatus("idle");
    setSnapshotsError(null);
    setStageFacts(emptyPage());
    setStageFactsStatus("idle");
    setStageFactsError(null);
    setActivity(emptyPage());
    setActivityStatus("idle");
    setActivityError(null);
    setDetail(null);
    setDetailStatus("idle");
    setDetailError(null);
    setMutationStatus("idle");
    setMutationOutcome(null);
    setMutationError(null);
  }, []);
  const current = useCallback(
    (generation: number) => mountedRef.current && generation === generationRef.current,
    [],
  );

  const loadAuthority = useCallback(
    async (requestedGeneration = generationRef.current): Promise<boolean> => {
      if (!active || !current(requestedGeneration)) return false;
      setLoadStatus("loading");
      setLoadError(null);
      setSummaryStatus("loading");
      setProfileStatus("loading");
      setSnapshotsStatus("loading");
      setSummaryError(null);
      setProfileError(null);
      setSnapshotsError(null);
      const summaryRequest = requestContext(requestedGeneration, controllersRef.current);
      const profileRequest = requestContext(requestedGeneration, controllersRef.current);
      const snapshotsRequest = requestContext(requestedGeneration, controllersRef.current);
      const results = await Promise.allSettled([
        api.fetchSummary(scopeRef.current, summaryRequest.options),
        api.fetchProfile(scopeRef.current, profileRequest.options),
        api.fetchSnapshots(scopeRef.current, {}, snapshotsRequest.options),
      ]);
      if (!current(requestedGeneration)) return false;
      let success = true;
      const summaryResult = results[0];
      if (summaryResult?.status === "fulfilled") {
        setSummary(summaryResult.value);
        setSummaryStatus("ready");
      } else if (summaryResult?.status === "rejected" && !isAbort(summaryResult.reason)) {
        setSummary(null);
        setSummaryStatus("unavailable");
        setSummaryError(errorOf(summaryResult.reason, "Serving summary unavailable"));
        success = false;
      }
      const profileResult = results[1];
      if (profileResult?.status === "fulfilled") {
        setProfile(profileResult.value);
        setProfileStatus("ready");
      } else if (profileResult?.status === "rejected" && !isAbort(profileResult.reason)) {
        setProfile(null);
        setProfileStatus("unavailable");
        setProfileError(errorOf(profileResult.reason, "Serving profile unavailable"));
        success = false;
      }
      const snapshotsResult = results[2];
      if (snapshotsResult?.status === "fulfilled") {
        setSnapshots(snapshotsResult.value);
        setSnapshotsStatus(pageStatus(snapshotsResult.value));
      } else if (snapshotsResult?.status === "rejected" && !isAbort(snapshotsResult.reason)) {
        setSnapshots(emptyPage());
        setSnapshotsStatus("unavailable");
        setSnapshotsError(errorOf(snapshotsResult.reason, "Serving snapshots unavailable"));
        success = false;
      }
      controllersRef.current.delete(summaryRequest.controller);
      controllersRef.current.delete(profileRequest.controller);
      controllersRef.current.delete(snapshotsRequest.controller);
      if (!current(requestedGeneration)) return false;
      const hasFulfilledAuthority =
        summaryResult?.status === "fulfilled" ||
        profileResult?.status === "fulfilled" ||
        snapshotsResult?.status === "fulfilled";
      setLoadStatus(success ? "ready" : hasFulfilledAuthority ? "partial" : "unavailable");
      if (!success)
        setLoadError(
          summaryResult?.status === "rejected"
            ? errorOf(summaryResult.reason, "Serving authority unavailable")
            : profileResult?.status === "rejected"
              ? errorOf(profileResult.reason, "Serving authority unavailable")
              : errorOf(
                  snapshotsResult?.status === "rejected" ? snapshotsResult.reason : null,
                  "Serving authority unavailable",
                ),
        );
      return success;
    },
    [active, api, current],
  );

  const loadSnapshots = useCallback(
    async (list: ServingListQuery = {}): Promise<boolean> => {
      const requestedGeneration = generationRef.current;
      if (!active || !current(requestedGeneration)) return false;
      setSnapshotsStatus("loading");
      setSnapshotsError(null);
      const ctx = requestContext(requestedGeneration, controllersRef.current);
      try {
        const page = await api.fetchSnapshots(scopeRef.current, list, ctx.options);
        if (!current(requestedGeneration)) return false;
        setSnapshots(page);
        setSnapshotsStatus(pageStatus(page));
        return true;
      } catch (caught) {
        if (!current(requestedGeneration) || isAbort(caught)) return false;
        setSnapshotsStatus("unavailable");
        setSnapshotsError(errorOf(caught, "Serving snapshots unavailable"));
        return false;
      } finally {
        controllersRef.current.delete(ctx.controller);
      }
    },
    [active, api, current, scope],
  );
  const loadStageFacts = useCallback(
    async (list: ServingListQuery = {}): Promise<boolean> => {
      const requestedGeneration = generationRef.current;
      if (!active || !current(requestedGeneration)) return false;
      setStageFactsStatus("loading");
      setStageFactsError(null);
      const ctx = requestContext(requestedGeneration, controllersRef.current);
      try {
        const page = await api.fetchStageFacts(scopeRef.current, list, ctx.options);
        if (!current(requestedGeneration)) return false;
        setStageFacts(page);
        setStageFactsStatus(pageStatus(page));
        return true;
      } catch (caught) {
        if (!current(requestedGeneration) || isAbort(caught)) return false;
        setStageFactsStatus("unavailable");
        setStageFactsError(errorOf(caught, "Serving stage facts unavailable"));
        return false;
      } finally {
        controllersRef.current.delete(ctx.controller);
      }
    },
    [active, api, current, scope],
  );
  const loadActivity = useCallback(
    async (list: ServingListQuery = {}): Promise<boolean> => {
      const requestedGeneration = generationRef.current;
      if (!active || !current(requestedGeneration)) return false;
      const query =
        list.snapshotId || selectedSnapshotRef.current
          ? { ...list, snapshotId: list.snapshotId ?? selectedSnapshotRef.current ?? undefined }
          : list;
      setActivityStatus("loading");
      setActivityError(null);
      const ctx = requestContext(requestedGeneration, controllersRef.current);
      try {
        const page = await api.fetchEvents(scopeRef.current, query, ctx.options);
        if (!current(requestedGeneration)) return false;
        setActivity(page);
        setActivityStatus(eventPageStatus(page));
        return true;
      } catch (caught) {
        if (!current(requestedGeneration) || isAbort(caught)) return false;
        setActivityStatus("unavailable");
        setActivityError(errorOf(caught, "Serving activity unavailable"));
        return false;
      } finally {
        controllersRef.current.delete(ctx.controller);
      }
    },
    [active, api, current, scope],
  );
  const loadDetail = useCallback(
    async (snapshotId: string): Promise<boolean> => {
      const requestedGeneration = generationRef.current;
      if (!active || !current(requestedGeneration)) return false;
      selectedSnapshotRef.current = snapshotId;
      setDetailStatus("loading");
      setDetailError(null);
      const ctx = requestContext(requestedGeneration, controllersRef.current);
      try {
        const value = await api.fetchSnapshot(scopeRef.current, snapshotId, ctx.options);
        if (!current(requestedGeneration)) return false;
        setDetail(value);
        setDetailStatus("ready");
        return true;
      } catch (caught) {
        if (!current(requestedGeneration) || isAbort(caught)) return false;
        setDetailStatus("unavailable");
        setDetailError(errorOf(caught, "Serving snapshot detail unavailable"));
        return false;
      } finally {
        controllersRef.current.delete(ctx.controller);
      }
    },
    [active, api, current, scope],
  );

  const runMutation = useCallback(
    <T extends ServingMutationOutcome | ServingPreview>(
      invoke: (options: ServingRequestOptions) => Promise<T>,
      isPreview = false,
    ): Promise<T | null> => {
      const requestedGeneration = generationRef.current;
      if (!active || readOnly || !current(requestedGeneration)) return Promise.resolve(null);
      if (isPreview) {
        const ctx = requestContext(requestedGeneration, controllersRef.current);
        return invoke(ctx.options)
          .then((value) => (current(requestedGeneration) ? value : null))
          .catch((caught) => {
            if (!current(requestedGeneration) || isAbort(caught)) return null;
            setMutationError(errorOf(caught, "Serving preview unavailable"));
            return null;
          })
          .finally(() => {
            controllersRef.current.delete(ctx.controller);
          });
      }
      const task = mutationQueueRef.current.then(async () => {
        if (!current(requestedGeneration)) return null;
        const ctx = requestContext(requestedGeneration, controllersRef.current);
        setMutationStatus("saving");
        setMutationOutcome(null);
        setMutationError(null);
        try {
          const value = await invoke({
            ...ctx.options,
            idempotencyKey: createServingIdempotencyKey(),
          });
          if (!current(requestedGeneration)) return null;
          setMutationOutcome(value as ServingMutationOutcome);
          setMutationStatus("success");
          void loadAuthority(requestedGeneration);
          return value;
        } catch (caught) {
          if (!current(requestedGeneration) || isAbort(caught)) return null;
          setMutationStatus("error");
          setMutationError(errorOf(caught, "Serving mutation failed"));
          return null;
        } finally {
          controllersRef.current.delete(ctx.controller);
        }
      });
      mutationQueueRef.current = task.then(
        () => undefined,
        () => undefined,
      );
      return task;
    },
    [active, current, loadAuthority, readOnly],
  );

  const mutations = useMemo(
    () => ({
      createProfile: (input: CreateServingProfileInput) =>
        runMutation((requestOptions) =>
          api.createProfile(scopeRef.current, input, requestOptions),
        ) as Promise<ServingMutationOutcome | null>,
      createPolicyRevision: (input: CreateServingPolicyRevisionInput) =>
        runMutation((requestOptions) =>
          api.createPolicyRevision(scopeRef.current, input, requestOptions),
        ) as Promise<ServingMutationOutcome | null>,
      activateProfile: (input: ActivateServingProfileInput) =>
        runMutation((requestOptions) =>
          api.activateProfile(scopeRef.current, input, requestOptions),
        ) as Promise<ServingMutationOutcome | null>,
      previewPolicy: (input: PreviewServingPolicyInput) =>
        runMutation(
          (requestOptions) => api.previewPolicy(scopeRef.current, input, requestOptions),
          true,
        ) as Promise<ServingPreview | null>,
    }),
    [api, runMutation],
  );

  useLayoutEffect(() => {
    mountedRef.current = true;
    generationRef.current += 1;
    const requestedGeneration = generationRef.current;
    abortInflight();
    resetState();
    if (active) void loadAuthority(requestedGeneration);
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      abortInflight();
    };
  }, [abortInflight, active, contextKey, loadAuthority, resetState]);

  return useMemo(
    () => ({
      active,
      readOnly,
      load: { status: loadStatus, error: loadError, reload: () => loadAuthority() },
      summary: { status: summaryStatus, value: summary, error: summaryError },
      profile: { status: profileStatus, value: profile, error: profileError },
      snapshots: {
        status: snapshotsStatus,
        items: snapshots.items,
        count: snapshots.count,
        nextCursor: snapshots.next_cursor,
        invalidItemCount: snapshots.invalid_item_count,
        error: snapshotsError,
        load: loadSnapshots,
        reload: loadSnapshots,
      },
      stageFacts: {
        status: stageFactsStatus,
        items: stageFacts.items,
        count: stageFacts.count,
        nextCursor: stageFacts.next_cursor,
        invalidItemCount: stageFacts.invalid_item_count,
        error: stageFactsError,
        load: loadStageFacts,
        reload: loadStageFacts,
      },
      activity: {
        status: activityStatus,
        items: activity.items,
        count: activity.count,
        nextCursor: activity.next_cursor,
        invalidItemCount: activity.invalid_item_count,
        error: activityError,
        load: loadActivity,
        reload: loadActivity,
      },
      detail: { status: detailStatus, value: detail, error: detailError, load: loadDetail },
      mutation: {
        status: mutationStatus,
        outcome: mutationOutcome,
        error: mutationError,
        ...mutations,
      },
    }),
    [
      active,
      activity,
      activityError,
      activityStatus,
      detail,
      detailError,
      detailStatus,
      loadActivity,
      loadAuthority,
      loadError,
      loadDetail,
      loadSnapshots,
      loadStageFacts,
      loadStatus,
      mutationError,
      mutationOutcome,
      mutationStatus,
      mutations,
      profile,
      profileError,
      profileStatus,
      readOnly,
      snapshots,
      snapshotsError,
      snapshotsStatus,
      stageFacts,
      stageFactsError,
      stageFactsStatus,
      summary,
      summaryError,
      summaryStatus,
    ],
  );
}
