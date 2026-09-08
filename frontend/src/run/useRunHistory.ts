import { useCallback, useEffect, useMemo, useReducer, useRef } from "react";
import { ApiError } from "../api/client";
import {
  RunOpsApiError,
  fetchRunDetail,
  fetchRunEvents,
  fetchRunHealth,
  fetchRuns,
} from "../api/runs";
import type { BackendRunEvent } from "../types/rag";
import type {
  RunDetailDto,
  RunEventIntegrity,
  RunEventsDto,
  RunHealthDto,
  RunListDto,
  RunListFilters,
  RunListView,
  RunPersistenceStatus,
  RunSummaryDto,
} from "../types/runs";
import type { RunEventBufferSnapshot } from "./runEventBuffer";
import type { MonitoredRun } from "./runMonitorStore";
import type { RunViewState } from "./runViewState";

export type RunsCapability =
  "loading" | "available" | "disabled" | "unauthorized" | "legacy" | "unavailable";

export interface RunsHistoryApi {
  fetchRunHealth(signal?: AbortSignal): Promise<RunHealthDto>;
  fetchRuns(filters?: RunListFilters, signal?: AbortSignal): Promise<RunListDto>;
  fetchRunDetail(runId: string, signal?: AbortSignal): Promise<RunDetailDto>;
  fetchRunEvents(
    runId: string,
    options: { afterSeq: number; limit?: number; waitMs?: number; signal?: AbortSignal },
  ): Promise<RunEventsDto>;
}

export interface RunHistoryVisibilitySource {
  readonly hidden: boolean;
  addEventListener(type: "visibilitychange", listener: () => void): void;
  removeEventListener(type: "visibilitychange", listener: () => void): void;
}

export interface UseRunHistoryOptions {
  api?: RunsHistoryApi;
  liveRun: MonitoredRun | null;
  initial?: Partial<Pick<RunViewState, "runId" | "view">>;
  random?: () => number;
  visibility?: RunHistoryVisibilitySource;
}

export interface RunHistoryState {
  capability: RunsCapability;
  health: RunHealthDto | null;
  items: readonly RunSummaryDto[];
  listSource: RunListDto["source"] | null;
  retention: RunListDto["retention"] | null;
  selectedRunId: string | null;
  detail: RunDetailDto | null;
  events: readonly BackendRunEvent[];
  loading: boolean;
  reconnecting: boolean;
  expired: boolean;
  historyGap: boolean;
  legacyMode: boolean;
  view: RunListView;
  hasMore: boolean;
  maximumContiguousSeq: number;
  liveTransportDesync: boolean;
  eventIntegrity: RunEventIntegrity;
  persistenceStatus: RunPersistenceStatus | null;
  selectRun: (runId: string | null) => void;
  setView: (view: RunListView) => void;
  loadMore: () => void;
  refresh: () => void;
  retryNow: () => void;
}

interface StoreState {
  capability: RunsCapability;
  health: RunHealthDto | null;
  items: readonly RunSummaryDto[];
  listSource: RunListDto["source"] | null;
  retention: RunListDto["retention"] | null;
  selectedRunId: string | null;
  detail: RunDetailDto | null;
  events: readonly BackendRunEvent[];
  serverEvents: readonly BackendRunEvent[];
  serverAuthoritative: boolean;
  view: RunListView;
  nextCursor: string | null;
  loadingHealth: boolean;
  loadingList: boolean;
  loadingDetail: boolean;
  loadingEvents: boolean;
  loadingMore: boolean;
  reconnectHealth: boolean;
  reconnectList: boolean;
  reconnectEvents: boolean;
  expired: boolean;
  historyGap: boolean;
  liveTransportDesync: boolean;
  eventIntegrity: RunEventIntegrity;
  persistenceStatus: RunPersistenceStatus | null;
  generation: number;
  healthEpoch: number;
  listEpoch: number;
  detailEpoch: number;
  eventEpoch: number;
  loadMoreEpoch: number;
}

type Action =
  | { type: "health-start" }
  | { type: "health-success"; health: RunHealthDto }
  | { type: "health-error"; capability: RunsCapability; retain: boolean }
  | { type: "list-start"; append: boolean }
  | { type: "list-success"; page: RunListDto; append: boolean }
  | { type: "list-error"; append: boolean }
  | {
      type: "select";
      runId: string | null;
      generation: number;
      live: RunEventBufferSnapshot | null;
      desync: boolean;
    }
  | { type: "set-view"; view: RunListView }
  | { type: "load-more" }
  | { type: "detail-start" }
  | { type: "detail-success"; detail: RunDetailDto }
  | { type: "detail-error" }
  | { type: "events-start" }
  | { type: "events-stop" }
  | { type: "refresh"; generation: number }
  | { type: "refresh-detail" }
  | { type: "live-sync"; live: RunEventBufferSnapshot | null; desync: boolean }
  | { type: "event-page"; page: RunEventsDto; live: RunEventBufferSnapshot | null }
  | { type: "event-reconnecting"; reconnecting: boolean }
  | { type: "event-gap" }
  | { type: "event-expired" };

const DEFAULT_API: RunsHistoryApi = Object.freeze({
  fetchRunHealth,
  fetchRuns,
  fetchRunDetail,
  fetchRunEvents,
});

function matchingLiveBuffer(
  runId: string | null,
  liveRun: MonitoredRun | null,
): RunEventBufferSnapshot | null {
  return runId && liveRun?.id === runId ? (liveRun.eventBuffer ?? null) : null;
}

function contiguousSeq(events: readonly BackendRunEvent[]): number {
  const seqs = new Set(events.map(({ seq }) => seq));
  let seq = 0;
  while (seqs.has(seq + 1)) seq += 1;
  return seq;
}

function mergeItems(
  current: readonly RunSummaryDto[],
  incoming: readonly RunSummaryDto[],
): readonly RunSummaryDto[] {
  const positions = new Map(current.map((item, index) => [item.run_id, index]));
  const merged = [...current];
  for (const item of incoming) {
    const position = positions.get(item.run_id);
    if (position === undefined) {
      positions.set(item.run_id, merged.length);
      merged.push(item);
    } else {
      merged[position] = item;
    }
  }
  return merged;
}

function sortedServerMerge(
  current: readonly BackendRunEvent[],
  incoming: readonly BackendRunEvent[],
): readonly BackendRunEvent[] {
  const bySeq = new Map(current.map((item) => [item.seq, item]));
  for (const item of incoming) bySeq.set(item.seq, item);
  return Array.from(bySeq.values()).sort((left, right) => left.seq - right.seq);
}

function mergeLiveAndServer(
  live: RunEventBufferSnapshot | null,
  server: readonly BackendRunEvent[],
): readonly BackendRunEvent[] {
  const bySeq = new Map<number, BackendRunEvent>();
  for (const item of live?.events ?? []) bySeq.set(item.seq, item);
  for (const item of server) if (!bySeq.has(item.seq)) bySeq.set(item.seq, item);
  return Array.from(bySeq.values()).sort((left, right) => left.seq - right.seq);
}

function initialState(
  initial: UseRunHistoryOptions["initial"],
  liveRun: MonitoredRun | null,
): StoreState {
  const selectedRunId = initial?.runId ?? null;
  const live = matchingLiveBuffer(selectedRunId, liveRun);
  return {
    capability: "loading",
    health: null,
    items: [],
    listSource: null,
    retention: null,
    selectedRunId,
    detail: null,
    events: live?.events ?? [],
    serverEvents: [],
    serverAuthoritative: false,
    view: initial?.view ?? "recent",
    nextCursor: null,
    loadingHealth: true,
    loadingList: false,
    loadingDetail: false,
    loadingEvents: false,
    loadingMore: false,
    reconnectHealth: false,
    reconnectList: false,
    reconnectEvents: false,
    expired: false,
    historyGap: false,
    liveTransportDesync: selectedRunId === liveRun?.id ? liveRun.liveTransportDesync : false,
    eventIntegrity: live?.historyState ?? "unknown",
    persistenceStatus: null,
    generation: 0,
    healthEpoch: 0,
    listEpoch: 0,
    detailEpoch: 0,
    eventEpoch: 0,
    loadMoreEpoch: 0,
  };
}

function reducer(state: StoreState, action: Action): StoreState {
  switch (action.type) {
    case "health-start":
      return { ...state, loadingHealth: true };
    case "health-success": {
      const available = action.health.enabled && action.health.status !== "disabled";
      return {
        ...state,
        health: action.health,
        capability: available ? "available" : "disabled",
        loadingHealth: false,
        reconnectHealth: false,
      };
    }
    case "health-error":
      return {
        ...state,
        capability: action.retain ? state.capability : action.capability,
        loadingHealth: false,
        reconnectHealth: action.capability === "unavailable",
      };
    case "list-start":
      return action.append ? { ...state, loadingMore: true } : { ...state, loadingList: true };
    case "list-success":
      return {
        ...state,
        items: action.append ? mergeItems(state.items, action.page.items) : action.page.items,
        listSource: action.page.source,
        retention: action.page.retention,
        nextCursor: action.page.next_cursor,
        loadingList: false,
        loadingMore: false,
        reconnectList: false,
      };
    case "list-error":
      return {
        ...state,
        loadingList: false,
        loadingMore: false,
        reconnectList: state.items.length > 0,
      };
    case "select":
      return {
        ...state,
        selectedRunId: action.runId,
        detail: null,
        events: action.live?.events ?? [],
        serverEvents: [],
        serverAuthoritative: false,
        loadingDetail: false,
        loadingEvents: false,
        reconnectEvents: false,
        expired: false,
        historyGap: false,
        liveTransportDesync: action.desync,
        eventIntegrity: action.live?.historyState ?? "unknown",
        persistenceStatus: null,
        generation: action.generation,
      };
    case "set-view":
      if (state.view === action.view) return state;
      return { ...state, view: action.view, nextCursor: null, listEpoch: state.listEpoch + 1 };
    case "load-more":
      return state.nextCursor && !state.loadingMore
        ? { ...state, loadMoreEpoch: state.loadMoreEpoch + 1 }
        : state;
    case "detail-start":
      return { ...state, loadingDetail: true };
    case "detail-success":
      return {
        ...state,
        detail: action.detail,
        loadingDetail: false,
        eventIntegrity: action.detail.history.event_integrity,
        persistenceStatus: action.detail.history.persistence_status,
      };
    case "detail-error":
      return { ...state, loadingDetail: false };
    case "events-start":
      return { ...state, loadingEvents: true };
    case "events-stop":
      return { ...state, loadingEvents: false };
    case "refresh":
      return {
        ...state,
        generation: action.generation,
        healthEpoch: state.healthEpoch + 1,
        listEpoch: state.listEpoch + 1,
        detailEpoch: state.detailEpoch + 1,
        eventEpoch: state.eventEpoch + 1,
        reconnectHealth: false,
        reconnectList: false,
        reconnectEvents: false,
        expired: false,
      };
    case "refresh-detail":
      return { ...state, detailEpoch: state.detailEpoch + 1 };
    case "live-sync":
      return {
        ...state,
        events: state.serverAuthoritative
          ? state.events
          : mergeLiveAndServer(action.live, state.serverEvents),
        liveTransportDesync: action.desync,
        eventIntegrity:
          state.eventIntegrity === "partial" || action.live?.historyState === "partial"
            ? "partial"
            : state.eventIntegrity,
      };
    case "event-page": {
      const serverEvents = sortedServerMerge(state.serverEvents, action.page.events);
      const acceptedEvents = mergeLiveAndServer(action.live, serverEvents);
      const authoritative =
        action.page.history_state === "complete" &&
        action.page.earliest_available_seq === 1 &&
        contiguousSeq(acceptedEvents) >= action.page.latest_seq;
      return {
        ...state,
        serverEvents,
        serverAuthoritative: authoritative,
        events: acceptedEvents,
        loadingEvents: false,
        reconnectEvents: false,
        historyGap:
          state.historyGap ||
          action.page.history_state !== "complete" ||
          action.page.earliest_available_seq > 1,
        eventIntegrity: action.page.history_state === "partial" ? "partial" : state.eventIntegrity,
        persistenceStatus: action.page.persistence_status,
      };
    }
    case "event-reconnecting":
      return { ...state, loadingEvents: false, reconnectEvents: action.reconnecting };
    case "event-gap":
      return { ...state, loadingEvents: false, historyGap: true, eventIntegrity: "partial" };
    case "event-expired":
      return { ...state, loadingEvents: false, reconnectEvents: false, expired: true };
  }
}

function isAbort(error: unknown): boolean {
  return (
    (error instanceof ApiError && error.kind === "aborted") ||
    (error instanceof DOMException && error.name === "AbortError")
  );
}

function statusOf(error: unknown): number | undefined {
  if (error instanceof RunOpsApiError) return error.status;
  if (typeof error === "object" && error !== null && "status" in error) {
    const status = (error as { status?: unknown }).status;
    return typeof status === "number" ? status : undefined;
  }
  return undefined;
}

function retryable(error: unknown): boolean {
  if (error instanceof ApiError) return error.kind === "network" || error.kind === "timeout";
  const status = statusOf(error);
  if (status !== undefined) return status === 429 || status >= 500;
  return error instanceof Error;
}

function probeCapability(error: unknown): RunsCapability {
  const status = statusOf(error);
  if (status === 401 || status === 403) return "unauthorized";
  if (status === 404) return "legacy";
  return "unavailable";
}

function waitFor(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => finish(resolve), ms);
    function cleanup() {
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
    }
    function finish(complete: () => void) {
      if (settled) return;
      settled = true;
      cleanup();
      complete();
    }
    function abort() {
      finish(() => reject(new ApiError("aborted", "aborted")));
    }
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

function backoff(attempt: number, random: () => number): number {
  const base = [500, 1000, 2000, 5000][Math.min(attempt, 3)]!;
  return Math.round(base * (1 + Math.max(0, Math.min(1, random())) * 0.2));
}

export function useRunHistory(options: UseRunHistoryOptions): RunHistoryState {
  const apiRef = useRef(options.api ?? DEFAULT_API);
  const liveRunRef = useRef(options.liveRun);
  const randomRef = useRef(options.random ?? Math.random);
  const visibilityRef = useRef<RunHistoryVisibilitySource | undefined>(options.visibility);
  apiRef.current = options.api ?? DEFAULT_API;
  liveRunRef.current = options.liveRun;
  randomRef.current = options.random ?? Math.random;
  visibilityRef.current = options.visibility;

  const [state, dispatch] = useReducer(
    reducer,
    { initial: options.initial, liveRun: options.liveRun },
    ({ initial, liveRun }) => initialState(initial, liveRun),
  );
  const stateRef = useRef(state);
  stateRef.current = state;
  const generation = useRef(0);
  const selectionControllers = useRef(new Set<AbortController>());
  const healthController = useRef<AbortController | null>(null);
  const eventController = useRef<AbortController | null>(null);
  const handledLoadMore = useRef(0);
  const initialRunRef = useRef(options.initial?.runId ?? null);
  const initialViewRef = useRef(options.initial?.view ?? "recent");

  const abortSelectionRequests = useCallback(() => {
    for (const controller of selectionControllers.current) controller.abort();
    selectionControllers.current.clear();
  }, []);

  const selectRun = useCallback(
    (runId: string | null) => {
      generation.current += 1;
      abortSelectionRequests();
      const liveRun = liveRunRef.current;
      dispatch({
        type: "select",
        runId,
        generation: generation.current,
        live: matchingLiveBuffer(runId, liveRun),
        desync: runId !== null && liveRun?.id === runId ? liveRun.liveTransportDesync : false,
      });
    },
    [abortSelectionRequests],
  );

  const setView = useCallback((view: RunListView) => {
    dispatch({ type: "set-view", view });
  }, []);
  const loadMore = useCallback(() => dispatch({ type: "load-more" }), []);
  const refresh = useCallback(() => {
    generation.current += 1;
    abortSelectionRequests();
    healthController.current?.abort();
    dispatch({ type: "refresh", generation: generation.current });
  }, [abortSelectionRequests]);
  const retryNow = refresh;

  useEffect(() => {
    const runId = options.initial?.runId ?? null;
    if (runId !== initialRunRef.current) {
      initialRunRef.current = runId;
      selectRun(runId);
    }
  }, [options.initial?.runId, selectRun]);

  useEffect(() => {
    const view = options.initial?.view ?? "recent";
    if (view !== initialViewRef.current) {
      initialViewRef.current = view;
      setView(view);
    }
  }, [options.initial?.view, setView]);

  useEffect(() => {
    return () => {
      abortSelectionRequests();
      healthController.current?.abort();
    };
  }, [abortSelectionRequests]);

  useEffect(() => {
    const controller = new AbortController();
    healthController.current?.abort();
    healthController.current = controller;
    let attempt = 0;
    const run = async () => {
      dispatch({ type: "health-start" });
      while (!controller.signal.aborted) {
        try {
          const result = await apiRef.current.fetchRunHealth(controller.signal);
          if (!controller.signal.aborted) dispatch({ type: "health-success", health: result });
          return;
        } catch (error) {
          if (isAbort(error) || controller.signal.aborted) return;
          const capability = probeCapability(error);
          dispatch({
            type: "health-error",
            capability,
            retain: capability === "unavailable" && stateRef.current.health !== null,
          });
          if (!retryable(error)) return;
          try {
            await waitFor(backoff(attempt++, randomRef.current), controller.signal);
          } catch {
            return;
          }
        }
      }
    };
    void run();
    return () => controller.abort();
  }, [state.healthEpoch]);

  useEffect(() => {
    if (state.capability !== "available") return;
    const append = state.loadMoreEpoch > handledLoadMore.current;
    if (append) handledLoadMore.current = state.loadMoreEpoch;
    const cursor = append ? stateRef.current.nextCursor : null;
    if (append && !cursor) return;
    const controller = new AbortController();
    selectionControllers.current.add(controller);
    const requestGeneration = state.generation;
    dispatch({ type: "list-start", append });
    void apiRef.current
      .fetchRuns({ view: state.view, cursor: cursor ?? undefined }, controller.signal)
      .then((page) => {
        if (!controller.signal.aborted && generation.current === requestGeneration) {
          dispatch({ type: "list-success", page, append });
        }
      })
      .catch((error: unknown) => {
        if (
          !isAbort(error) &&
          !controller.signal.aborted &&
          generation.current === requestGeneration
        ) {
          dispatch({ type: "list-error", append });
        }
      })
      .finally(() => selectionControllers.current.delete(controller));
    return () => controller.abort();
  }, [state.capability, state.generation, state.listEpoch, state.loadMoreEpoch, state.view]);

  useEffect(() => {
    const runId = state.selectedRunId;
    if (state.capability !== "available" || !runId) return;
    const controller = new AbortController();
    selectionControllers.current.add(controller);
    const requestGeneration = state.generation;
    dispatch({ type: "detail-start" });
    void apiRef.current
      .fetchRunDetail(runId, controller.signal)
      .then((result) => {
        if (
          !controller.signal.aborted &&
          generation.current === requestGeneration &&
          stateRef.current.selectedRunId === runId
        ) {
          dispatch({ type: "detail-success", detail: result });
        }
      })
      .catch((error: unknown) => {
        if (
          !isAbort(error) &&
          !controller.signal.aborted &&
          generation.current === requestGeneration
        ) {
          dispatch({ type: "detail-error" });
        }
      })
      .finally(() => selectionControllers.current.delete(controller));
    return () => controller.abort();
  }, [state.capability, state.detailEpoch, state.generation, state.selectedRunId]);

  const liveRunId = options.liveRun?.id ?? "";
  const liveLastSeq = options.liveRun?.eventBuffer?.lastContiguousSeq ?? 0;
  const liveEventCount = options.liveRun?.eventBuffer?.events.length ?? 0;
  const liveHistoryState = options.liveRun?.eventBuffer?.historyState ?? "unknown";
  const liveDesync = options.liveRun?.liveTransportDesync ?? false;
  useEffect(() => {
    const selectedRunId = stateRef.current.selectedRunId;
    const liveRun = liveRunRef.current;
    dispatch({
      type: "live-sync",
      live: matchingLiveBuffer(selectedRunId, liveRun),
      desync:
        selectedRunId !== null && liveRun?.id === selectedRunId
          ? liveRun.liveTransportDesync
          : false,
    });
  }, [liveDesync, liveEventCount, liveHistoryState, liveLastSeq, liveRunId]);

  useEffect(() => {
    const runId = state.selectedRunId;
    if (state.capability !== "available" || !runId) return;
    const controller = new AbortController();
    eventController.current = controller;
    selectionControllers.current.add(controller);
    const requestGeneration = state.generation;
    let cursor = contiguousSeq(stateRef.current.events);
    let attempt = 0;
    let catchingUp = false;
    dispatch({ type: "events-start" });

    const poll = async () => {
      while (!controller.signal.aborted) {
        try {
          const page = await apiRef.current.fetchRunEvents(runId, {
            afterSeq: cursor,
            limit: 200,
            waitMs: catchingUp ? 0 : 25_000,
            signal: controller.signal,
          });
          if (
            controller.signal.aborted ||
            generation.current !== requestGeneration ||
            stateRef.current.selectedRunId !== runId
          ) {
            return;
          }
          if (page.history_state === "expired") {
            dispatch({ type: "event-expired" });
            return;
          }
          dispatch({
            type: "event-page",
            page,
            live: matchingLiveBuffer(runId, liveRunRef.current),
          });
          cursor = page.after_seq;
          attempt = 0;
          const caughtUp = cursor >= page.latest_seq;
          if (page.terminal && caughtUp) return;
          catchingUp = !caughtUp;
          if (visibilityRef.current?.hidden && !catchingUp) {
            await waitFor(25_000, controller.signal);
          }
        } catch (error) {
          if (isAbort(error) || controller.signal.aborted) return;
          if (
            generation.current !== requestGeneration ||
            stateRef.current.selectedRunId !== runId
          ) {
            return;
          }
          const status = statusOf(error);
          if (status === 409) {
            const earliest =
              error instanceof RunOpsApiError ? error.earliestAvailableSeq : undefined;
            dispatch({ type: "event-gap" });
            dispatch({ type: "refresh-detail" });
            cursor = Math.max(0, (earliest ?? 1) - 1);
            attempt = 0;
            catchingUp = true;
            continue;
          }
          const known =
            liveRunRef.current?.id === runId ||
            stateRef.current.detail?.summary.run_id === runId ||
            stateRef.current.items.some(({ run_id }) => run_id === runId);
          if (status === 404) {
            if (known) dispatch({ type: "event-expired" });
            else dispatch({ type: "events-stop" });
            return;
          }
          if (!retryable(error)) {
            dispatch({ type: "events-stop" });
            return;
          }
          dispatch({ type: "event-reconnecting", reconnecting: true });
          try {
            await waitFor(backoff(attempt++, randomRef.current), controller.signal);
          } catch {
            return;
          }
        }
      }
    };
    void poll().finally(() => selectionControllers.current.delete(controller));
    return () => controller.abort();
  }, [state.capability, state.eventEpoch, state.generation, state.selectedRunId]);

  useEffect(() => {
    const visibility =
      options.visibility ??
      (typeof document === "undefined"
        ? undefined
        : (document as unknown as RunHistoryVisibilitySource));
    visibilityRef.current = visibility;
    if (!visibility) return;
    const onVisibilityChange = () => {
      if (visibility.hidden || !stateRef.current.selectedRunId) return;
      generation.current += 1;
      abortSelectionRequests();
      dispatch({ type: "refresh", generation: generation.current });
    };
    visibility.addEventListener("visibilitychange", onVisibilityChange);
    return () => visibility.removeEventListener("visibilitychange", onVisibilityChange);
  }, [abortSelectionRequests, options.visibility]);

  const loading =
    state.loadingHealth ||
    state.loadingList ||
    state.loadingDetail ||
    state.loadingEvents ||
    state.loadingMore;
  const reconnecting = state.reconnectHealth || state.reconnectList || state.reconnectEvents;
  const maximumContiguousSeq = contiguousSeq(state.events);
  const legacyMode = state.capability === "disabled" || state.capability === "legacy";

  return useMemo(
    () => ({
      capability: state.capability,
      health: state.health,
      items: state.items,
      listSource: state.listSource,
      retention: state.retention,
      selectedRunId: state.selectedRunId,
      detail: state.detail,
      events: state.events,
      loading,
      reconnecting,
      expired: state.expired,
      historyGap: state.historyGap,
      legacyMode,
      view: state.view,
      hasMore: state.nextCursor !== null,
      maximumContiguousSeq,
      liveTransportDesync: state.liveTransportDesync,
      eventIntegrity: state.eventIntegrity,
      persistenceStatus: state.persistenceStatus,
      selectRun,
      setView,
      loadMore,
      refresh,
      retryNow,
    }),
    [
      legacyMode,
      loadMore,
      loading,
      maximumContiguousSeq,
      reconnecting,
      refresh,
      retryNow,
      selectRun,
      setView,
      state.capability,
      state.detail,
      state.eventIntegrity,
      state.events,
      state.expired,
      state.health,
      state.historyGap,
      state.items,
      state.listSource,
      state.retention,
      state.liveTransportDesync,
      state.nextCursor,
      state.persistenceStatus,
      state.selectedRunId,
      state.view,
    ],
  );
}
