import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { RunOpsApiError } from "../api/runs";
import type { BackendRunEvent } from "../types/rag";
import type {
  RunDetailDto,
  RunEventsDto,
  RunHealthDto,
  RunListDto,
  RunSummaryDto,
} from "../types/runs";
import { createRunEventBuffer, mergeRunEvents } from "./runEventBuffer";
import type { MonitoredRun } from "./runMonitorStore";
import {
  useRunHistory,
  type RunHistoryState,
  type RunsHistoryApi,
  type UseRunHistoryOptions,
} from "./useRunHistory";

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

function summary(runId: string, overrides: Partial<RunSummaryDto> = {}): RunSummaryDto {
  return {
    schema_version: 1,
    run_id: runId,
    status: "running",
    outcome: "unknown",
    started_at: "2026-08-23T12:00:00Z",
    updated_at: "2026-08-23T12:00:01Z",
    finished_at: null,
    elapsed_ms: 1000,
    boot_id: "boot-1",
    worker_id: "worker-1",
    topology_id: "rag.query",
    topology_revision: "sha256:test",
    executor: "sequential_stream",
    last_seq: 1,
    event_count: 1,
    earliest_available_seq: 1,
    current_node_ids: [],
    failed_node_ids: [],
    route: "hybrid",
    degraded_count: 0,
    retry_count: 0,
    attention: [],
    event_integrity: "complete",
    persistence_status: "durable",
    interruption_reason: null,
    ...overrides,
  };
}

function list(items: RunSummaryDto[], nextCursor: string | null = null): RunListDto {
  return {
    schema_version: 1,
    items,
    next_cursor: nextCursor,
    as_of: "2026-08-23T12:00:02Z",
    source: "memory+sqlite",
    history_state: "complete",
    retention: { days: 30, max_runs: 100000 },
  };
}

function health(overrides: Partial<RunHealthDto> = {}): RunHealthDto {
  return {
    schema_version: 1,
    status: "ok",
    enabled: true,
    boot_id: "boot-1",
    worker_id: "worker-1",
    memory: { active_runs: 1, recent_runs: 1, event_count: 2, dropped_runs: 0, dropped_events: 0 },
    persistence: {
      enabled: true,
      state: "ready",
      database: "run-history.sqlite3",
      wal: true,
      writer_queue_depth: 0,
      last_commit_at: "2026-08-23T12:00:01Z",
      commit_lag_ms: 0,
      dropped_mutations: 0,
      quick_check: "ok",
    },
    heartbeat: { interval_s: 5, stale_after_s: 20, last_heartbeat_at: null },
    retention: { days: 30, max_runs: 100000, memory_terminal_ttl_s: 900 },
    scope_stability: "installation",
    ...overrides,
  };
}

function detail(runId: string, overrides: Partial<RunDetailDto> = {}): RunDetailDto {
  return {
    schema_version: 1,
    summary: summary(runId),
    topology: {
      id: "rag.query",
      revision: "sha256:test",
      executor: "sequential_stream",
      nodes: [
        {
          id: "receive",
          label: "Receive",
          group: "input",
          description: "Receive",
          optional: false,
          repeatable: false,
          available: true,
          attributes: {},
        },
      ],
      edges: [],
    },
    node_rollup: [],
    history: {
      event_integrity: "complete",
      earliest_available_seq: 1,
      last_seq: 1,
      persistence_status: "durable",
    },
    ...overrides,
  };
}

function event(runId: string, seq: number, type: BackendRunEvent["type"]): BackendRunEvent {
  return {
    schema_version: 1,
    run_id: runId,
    seq,
    occurred_at: "2026-08-23T12:00:00Z",
    elapsed_ms: seq * 100,
    topology_id: "rag.query",
    topology_revision: "sha256:test",
    type,
    attributes:
      type === "run.started"
        ? {
            topology: {
              id: "rag.query",
              revision: "sha256:test",
              executor: "sequential_stream",
              nodes: [
                {
                  id: "receive",
                  label: "Receive",
                  group: "input",
                  description: "Receive",
                  optional: false,
                  repeatable: false,
                  available: true,
                  attributes: {},
                },
              ],
              edges: [],
            },
          }
        : type === "run.completed"
          ? { outcome: "answered" }
          : {},
  };
}

function eventsPage(
  runId: string,
  events: BackendRunEvent[],
  options: Partial<RunEventsDto> = {},
): RunEventsDto {
  return {
    schema_version: 1,
    run_id: runId,
    events,
    after_seq: events[events.length - 1]?.seq ?? 0,
    latest_seq: events[events.length - 1]?.seq ?? 0,
    terminal: false,
    timed_out: false,
    history_state: "complete",
    earliest_available_seq: 1,
    persistence_status: "durable",
    retry_after_ms: 500,
    ...options,
  };
}

function never<T>(signal?: AbortSignal): Promise<T> {
  return new Promise<T>((_resolve, reject) => {
    signal?.addEventListener("abort", () => reject(new ApiError("aborted", "aborted")), {
      once: true,
    });
  });
}

function api(overrides: Partial<RunsHistoryApi> = {}): RunsHistoryApi {
  return {
    fetchRunHealth: vi.fn(async () => health()),
    fetchRuns: vi.fn(async () => list([])),
    fetchRunDetail: vi.fn((_, signal) => never<RunDetailDto>(signal)),
    fetchRunEvents: vi.fn((_, options) => never<RunEventsDto>(options.signal)),
    ...overrides,
  };
}

interface MountedHook {
  readonly current: RunHistoryState;
  rerender: (options: UseRunHistoryOptions) => void;
  unmount: () => void;
}

function mountHook(options: UseRunHistoryOptions): MountedHook {
  let current: RunHistoryState | undefined;
  let renderer: ReactTestRenderer;
  function Probe({ value }: { value: UseRunHistoryOptions }) {
    current = useRunHistory(value);
    return null;
  }
  act(() => {
    renderer = create(<Probe value={options} />);
  });
  return {
    get current() {
      if (!current) throw new Error("hook did not render");
      return current;
    },
    rerender(value) {
      act(() => renderer.update(<Probe value={value} />));
    },
    unmount() {
      act(() => renderer.unmount());
    },
  };
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function liveRun(
  runId: string,
  events: BackendRunEvent[],
  overrides: Partial<MonitoredRun> = {},
): MonitoredRun {
  const eventBuffer = mergeRunEvents(createRunEventBuffer(runId), events);
  return {
    id: runId,
    provisionalId: runId,
    query: "SENTINEL_QUERY",
    startedAt: 1,
    phase: null,
    phaseStartedAt: 1,
    phaseDurations: {},
    seq: eventBuffer.lastContiguousSeq,
    retrievalAttempt: 0,
    source: "live_stream",
    status: eventBuffer.terminal ? "completed" : "running",
    topologySource: "typed-events",
    syncStatus: "synchronized",
    liveTransportDesync: false,
    eventBuffer,
    ...overrides,
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useRunHistory capability and request lifecycle", () => {
  it.each([
    [health({ status: "ok" }), "available"],
    [health({ status: "degraded" }), "available"],
    [health({ status: "disabled", enabled: false }), "disabled"],
  ] as const)("maps health responses to capability", async (result, capability) => {
    const mounted = mountHook({
      api: api({ fetchRunHealth: vi.fn(async () => result) }),
      liveRun: null,
    });
    await flush();
    expect(mounted.current.capability).toBe(capability);
    expect(mounted.current.legacyMode).toBe(capability === "disabled");
    mounted.unmount();
  });

  it.each([
    [new RunOpsApiError("no auth", 401, "ops_unauthorized"), "unauthorized"],
    [new RunOpsApiError("forbidden", 403, "ops_forbidden"), "unauthorized"],
    [new RunOpsApiError("old backend", 404, "run_not_found"), "legacy"],
    [new ApiError("offline", "network"), "unavailable"],
    [new RunOpsApiError("down", 503, "run_ops_unavailable"), "unavailable"],
  ] as const)("maps probe failures without injecting demo history", async (error, capability) => {
    vi.useFakeTimers();
    const mounted = mountHook({
      api: api({ fetchRunHealth: vi.fn(async () => Promise.reject(error)) }),
      liveRun: null,
      random: () => 0,
    });
    await flush();
    expect(mounted.current.capability).toBe(capability);
    expect(mounted.current.items).toEqual([]);
    expect(mounted.current.legacyMode).toBe(capability === "legacy");
    mounted.unmount();
  });

  it("retains successful data when a later health refresh fails", async () => {
    const fetchRunHealth = vi
      .fn<RunsHistoryApi["fetchRunHealth"]>()
      .mockResolvedValueOnce(health())
      .mockRejectedValueOnce(new ApiError("offline", "network"));
    const fetchRuns = vi
      .fn<RunsHistoryApi["fetchRuns"]>()
      .mockResolvedValue(list([summary("run-a")]));
    const mounted = mountHook({ api: api({ fetchRunHealth, fetchRuns }), liveRun: null });
    await flush();
    act(() => mounted.current.refresh());
    await flush();
    expect(mounted.current.capability).toBe("available");
    expect(mounted.current.health?.status).toBe("ok");
    expect(mounted.current.items.map(({ run_id }) => run_id)).toEqual(["run-a"]);
    expect(mounted.current.reconnecting).toBe(true);
    mounted.unmount();
  });

  it("rejects stale list/detail responses and aborts events after run switch and unmount", async () => {
    const firstList = deferred<RunListDto>();
    const secondList = deferred<RunListDto>();
    const details = new Map<string, Deferred<RunDetailDto>>();
    const eventSignals: AbortSignal[] = [];
    const fetchRuns = vi
      .fn<RunsHistoryApi["fetchRuns"]>()
      .mockReturnValueOnce(firstList.promise)
      .mockReturnValueOnce(secondList.promise);
    const fetchRunDetail = vi.fn<RunsHistoryApi["fetchRunDetail"]>((runId) => {
      const request = deferred<RunDetailDto>();
      details.set(runId, request);
      return request.promise;
    });
    const fetchRunEvents = vi.fn<RunsHistoryApi["fetchRunEvents"]>((_runId, options) => {
      if (options.signal) eventSignals.push(options.signal);
      return never(options.signal);
    });
    const mounted = mountHook({
      api: api({ fetchRuns, fetchRunDetail, fetchRunEvents }),
      liveRun: null,
      initial: { runId: "run-a", view: "recent" },
    });
    await flush();
    act(() => mounted.current.selectRun("run-b"));
    await flush();
    expect(eventSignals[0]?.aborted).toBe(true);
    details.get("run-a")?.resolve(detail("run-a"));
    firstList.resolve(list([summary("stale")]));
    details.get("run-b")?.resolve(detail("run-b"));
    secondList.resolve(list([summary("fresh")]));
    await flush();
    expect(mounted.current.selectedRunId).toBe("run-b");
    expect(mounted.current.detail?.summary.run_id).toBe("run-b");
    expect(mounted.current.items.map(({ run_id }) => run_id)).toEqual(["fresh"]);
    mounted.unmount();
    expect(eventSignals[eventSignals.length - 1]?.aborted).toBe(true);
  });

  it("initializes URL selection/view and synchronizes later external URL changes", async () => {
    const options: UseRunHistoryOptions = {
      api: api(),
      liveRun: null,
      initial: { runId: "run-a", view: "errors" },
    };
    const mounted = mountHook(options);
    await flush();
    expect(mounted.current.selectedRunId).toBe("run-a");
    expect(mounted.current.view).toBe("errors");
    mounted.rerender({ ...options, initial: { runId: "run-b", view: "slow" } });
    await flush();
    expect(mounted.current.selectedRunId).toBe("run-b");
    expect(mounted.current.view).toBe("slow");
    act(() => mounted.current.setView("stuck"));
    expect(mounted.current.view).toBe("stuck");
    mounted.unmount();
  });
});

describe("useRunHistory list and event state machine", () => {
  it("paginates with cursor and deduplicates repeated run ids", async () => {
    const fetchRuns = vi
      .fn<RunsHistoryApi["fetchRuns"]>()
      .mockResolvedValueOnce(list([summary("run-a"), summary("run-b")], "cursor-2"))
      .mockResolvedValueOnce(list([summary("run-b", { status: "completed" }), summary("run-c")]));
    const mounted = mountHook({ api: api({ fetchRuns }), liveRun: null });
    await flush();
    expect(mounted.current.hasMore).toBe(true);
    act(() => mounted.current.loadMore());
    await flush();
    expect(fetchRuns.mock.calls[1]?.[0]).toEqual(expect.objectContaining({ cursor: "cursor-2" }));
    expect(mounted.current.items.map(({ run_id }) => run_id)).toEqual(["run-a", "run-b", "run-c"]);
    expect(mounted.current.items[1]?.status).toBe("completed");
    expect(mounted.current.hasMore).toBe(false);
    mounted.unmount();
  });

  it("shows same-run live events first and replaces duplicates when server history is complete", async () => {
    const liveSecond = { ...event("run-a", 2, "degraded"), attributes: { source: "live" } };
    const current = liveRun("run-a", [event("run-a", 1, "run.started"), liveSecond]);
    const fetchRunEvents = vi
      .fn<RunsHistoryApi["fetchRunEvents"]>()
      .mockResolvedValueOnce(
        eventsPage("run-a", [event("run-a", 3, "run.completed")], {
          terminal: true,
          latest_seq: 3,
          after_seq: 3,
        }),
      );
    const mounted = mountHook({
      api: api({ fetchRunDetail: vi.fn(async () => detail("run-a")), fetchRunEvents }),
      liveRun: current,
      initial: { runId: "run-a" },
    });
    expect(mounted.current.events.map(({ seq }) => seq)).toEqual([1, 2]);
    await flush();
    expect(fetchRunEvents.mock.calls[0]?.[1].afterSeq).toBe(2);
    expect(mounted.current.events.map(({ seq }) => seq)).toEqual([1, 2, 3]);
    expect(mounted.current.events[1]?.attributes).toEqual({ source: "live" });
    expect(mounted.current.maximumContiguousSeq).toBe(3);
    expect(mounted.current.historyGap).toBe(false);
    mounted.unmount();
  });

  it("immediately repolls a timeout and stops after terminal", async () => {
    vi.useFakeTimers();
    const fetchRunEvents = vi
      .fn<RunsHistoryApi["fetchRunEvents"]>()
      .mockResolvedValueOnce(eventsPage("run-a", [], { timed_out: true }))
      .mockResolvedValueOnce(
        eventsPage("run-a", [event("run-a", 1, "run.started")], {
          terminal: true,
          after_seq: 1,
          latest_seq: 1,
        }),
      );
    const mounted = mountHook({
      api: api({ fetchRunDetail: vi.fn(async () => detail("run-a")), fetchRunEvents }),
      liveRun: null,
      initial: { runId: "run-a" },
    });
    await flush();
    expect(fetchRunEvents).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetchRunEvents).toHaveBeenCalledTimes(2);
    mounted.unmount();
  });

  it("backs off 500, 1000, 2000, and 5000 ms then resumes from the accepted cursor", async () => {
    vi.useFakeTimers();
    const fetchRunEvents = vi
      .fn<RunsHistoryApi["fetchRunEvents"]>()
      .mockRejectedValueOnce(new ApiError("offline", "network"))
      .mockRejectedValueOnce(new RunOpsApiError("down", 503, "run_ops_unavailable"))
      .mockRejectedValueOnce(new ApiError("offline", "network"))
      .mockRejectedValueOnce(new ApiError("offline", "network"))
      .mockResolvedValueOnce(eventsPage("run-a", [], { terminal: true }));
    const mounted = mountHook({
      api: api({ fetchRunDetail: vi.fn(async () => detail("run-a")), fetchRunEvents }),
      liveRun: liveRun("run-a", [event("run-a", 1, "run.started"), event("run-a", 2, "degraded")]),
      initial: { runId: "run-a" },
      random: () => 0,
    });
    await flush();
    expect(fetchRunEvents).toHaveBeenCalledTimes(1);
    for (const [delay, expectedCalls] of [
      [500, 2],
      [1000, 3],
      [2000, 4],
      [5000, 5],
    ] as const) {
      await vi.advanceTimersByTimeAsync(delay - 1);
      expect(fetchRunEvents).toHaveBeenCalledTimes(expectedCalls - 1);
      await vi.advanceTimersByTimeAsync(1);
      expect(fetchRunEvents).toHaveBeenCalledTimes(expectedCalls);
    }
    const lastCall = fetchRunEvents.mock.calls[fetchRunEvents.mock.calls.length - 1];
    expect(lastCall?.[1].afterSeq).toBe(2);
    expect(mounted.current.reconnecting).toBe(false);
    mounted.unmount();
  });
});

class VisibilitySource {
  hidden = true;
  private listeners = new Set<() => void>();
  addEventListener(_type: "visibilitychange", listener: () => void) {
    this.listeners.add(listener);
  }
  removeEventListener(_type: "visibilitychange", listener: () => void) {
    this.listeners.delete(listener);
  }
  show() {
    this.hidden = false;
    for (const listener of this.listeners) listener();
  }
}

describe("useRunHistory recovery and independent integrity signals", () => {
  it("waits 25 seconds while hidden and requests immediately when visible", async () => {
    vi.useFakeTimers();
    const visibility = new VisibilitySource();
    const fetchRunEvents = vi
      .fn<RunsHistoryApi["fetchRunEvents"]>()
      .mockResolvedValueOnce(eventsPage("run-a", [], { timed_out: true }))
      .mockResolvedValueOnce(eventsPage("run-a", [], { terminal: true }));
    const mounted = mountHook({
      api: api({ fetchRunDetail: vi.fn(async () => detail("run-a")), fetchRunEvents }),
      liveRun: null,
      initial: { runId: "run-a" },
      visibility,
    });
    await flush();
    expect(fetchRunEvents).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(24_999);
    expect(fetchRunEvents).toHaveBeenCalledTimes(1);
    act(() => visibility.show());
    await flush();
    expect(fetchRunEvents).toHaveBeenCalledTimes(2);
    expect(fetchRunEvents.mock.calls[0]?.[1].waitMs).toBe(25_000);
    mounted.unmount();
  });

  it("handles a 409 by marking a gap, refreshing detail, and restarting at earliest seq", async () => {
    const detailCalls = vi.fn<RunsHistoryApi["fetchRunDetail"]>(async () => detail("run-a"));
    const fetchRunEvents = vi
      .fn<RunsHistoryApi["fetchRunEvents"]>()
      .mockRejectedValueOnce(new RunOpsApiError("gap", 409, "run_event_gap", undefined, 18, 42))
      .mockResolvedValueOnce(
        eventsPage("run-a", [event("run-a", 18, "degraded")], {
          after_seq: 18,
          latest_seq: 18,
          terminal: true,
          history_state: "partial",
          earliest_available_seq: 18,
        }),
      );
    const mounted = mountHook({
      api: api({ fetchRunDetail: detailCalls, fetchRunEvents }),
      liveRun: null,
      initial: { runId: "run-a" },
    });
    await flush();
    expect(fetchRunEvents.mock.calls[1]?.[1].afterSeq).toBe(17);
    expect(detailCalls.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(mounted.current.historyGap).toBe(true);
    expect(mounted.current.events.map(({ seq }) => seq)).toEqual([18]);
    mounted.unmount();
  });

  it("marks a previously known 404 run expired without enabling legacy or demo mode", async () => {
    const current = liveRun("run-a", [event("run-a", 1, "run.started")]);
    const mounted = mountHook({
      api: api({
        fetchRunDetail: vi.fn(async () => detail("run-a")),
        fetchRunEvents: vi.fn(async () => {
          throw new RunOpsApiError("gone", 404, "run_not_found");
        }),
      }),
      liveRun: current,
      initial: { runId: "run-a" },
    });
    await flush();
    expect(mounted.current.expired).toBe(true);
    expect(mounted.current.capability).toBe("available");
    expect(mounted.current.legacyMode).toBe(false);
    expect(JSON.stringify(mounted.current)).not.toContain("SENTINEL_QUERY");
    mounted.unmount();
  });

  it("keeps live desync, registry partial, persistence unavailable, and reconnecting independent", async () => {
    vi.useFakeTimers();
    const current = liveRun("run-a", [event("run-a", 1, "run.started")], {
      liveTransportDesync: true,
      syncStatus: "desynchronized",
    });
    const partialDetail = detail("run-a", {
      summary: summary("run-a", {
        event_integrity: "partial",
        persistence_status: "unavailable",
      }),
      history: {
        event_integrity: "partial",
        earliest_available_seq: 1,
        last_seq: 1,
        persistence_status: "unavailable",
      },
    });
    const mounted = mountHook({
      api: api({
        fetchRunDetail: vi.fn(async () => partialDetail),
        fetchRunEvents: vi.fn(async () => {
          throw new ApiError("offline", "network");
        }),
      }),
      liveRun: current,
      initial: { runId: "run-a" },
      random: () => 0,
    });
    await flush();
    expect(mounted.current.liveTransportDesync).toBe(true);
    expect(mounted.current.eventIntegrity).toBe("partial");
    expect(mounted.current.persistenceStatus).toBe("unavailable");
    expect(mounted.current.reconnecting).toBe(true);
    expect(mounted.current.historyGap).toBe(false);
    mounted.unmount();
  });

  it("retryNow cancels the pending generation and issues fresh detail/events requests", async () => {
    const signals: AbortSignal[] = [];
    const fetchRunEvents = vi.fn<RunsHistoryApi["fetchRunEvents"]>((_runId, options) => {
      if (options.signal) signals.push(options.signal);
      return never(options.signal);
    });
    const fetchRunDetail = vi.fn<RunsHistoryApi["fetchRunDetail"]>((_runId, signal) =>
      never(signal),
    );
    const mounted = mountHook({
      api: api({ fetchRunDetail, fetchRunEvents }),
      liveRun: null,
      initial: { runId: "run-a" },
    });
    await flush();
    act(() => mounted.current.retryNow());
    await flush();
    expect(signals[0]?.aborted).toBe(true);
    expect(fetchRunDetail.mock.calls.length).toBeGreaterThanOrEqual(2);
    expect(fetchRunEvents.mock.calls.length).toBeGreaterThanOrEqual(2);
    mounted.unmount();
  });
});

describe("useRunHistory fix round 1 regressions", () => {
  it("continues terminal pagination until after_seq catches latest_seq", async () => {
    const allEvents = Array.from({ length: 1000 }, (_, index) => {
      const seq = index + 1;
      return event(
        "run-long",
        seq,
        seq === 1 ? "run.started" : seq === 1000 ? "run.completed" : "degraded",
      );
    });
    const fetchRunEvents = vi.fn<RunsHistoryApi["fetchRunEvents"]>(async (_runId, options) => {
      const pageEvents = allEvents.slice(options.afterSeq, options.afterSeq + 200);
      const afterSeq = pageEvents[pageEvents.length - 1]?.seq ?? options.afterSeq;
      return eventsPage("run-long", pageEvents, {
        after_seq: afterSeq,
        latest_seq: 1000,
        terminal: true,
      });
    });
    const mounted = mountHook({
      api: api({ fetchRunDetail: vi.fn(async () => detail("run-long")), fetchRunEvents }),
      liveRun: null,
      initial: { runId: "run-long" },
    });
    await flush();
    expect(fetchRunEvents.mock.calls.map(([, options]) => options.afterSeq)).toEqual([
      0, 200, 400, 600, 800,
    ]);
    expect(fetchRunEvents.mock.calls.map(([, options]) => options.waitMs)).toEqual([
      25_000, 0, 0, 0, 0,
    ]);
    expect(mounted.current.events).toHaveLength(1000);
    expect(mounted.current.maximumContiguousSeq).toBe(1000);
    mounted.unmount();
  });

  it("starts polling after the same-run live maximum contiguous sequence", async () => {
    const fetchRunEvents = vi
      .fn<RunsHistoryApi["fetchRunEvents"]>()
      .mockResolvedValue(eventsPage("run-a", [], { after_seq: 2, latest_seq: 2, terminal: true }));
    const mounted = mountHook({
      api: api({ fetchRunDetail: vi.fn(async () => detail("run-a")), fetchRunEvents }),
      liveRun: liveRun("run-a", [event("run-a", 1, "run.started"), event("run-a", 2, "degraded")]),
      initial: { runId: "run-a" },
    });
    await flush();
    expect(fetchRunEvents.mock.calls[0]?.[1].afterSeq).toBe(2);
    mounted.unmount();
  });

  it.each([
    [new RunOpsApiError("no auth", 401, "ops_unauthorized"), "unauthorized"],
    [new RunOpsApiError("forbidden", 403, "ops_forbidden"), "unauthorized"],
    [new RunOpsApiError("old backend", 404, "run_not_found"), "legacy"],
  ] as const)(
    "updates an available capability after a later permanent probe failure",
    async (error, expected) => {
      const fetchRunHealth = vi
        .fn<RunsHistoryApi["fetchRunHealth"]>()
        .mockResolvedValueOnce(health())
        .mockRejectedValueOnce(error);
      const mounted = mountHook({
        api: api({ fetchRunHealth, fetchRuns: vi.fn(async () => list([summary("run-a")])) }),
        liveRun: null,
      });
      await flush();
      act(() => mounted.current.refresh());
      await flush();
      expect(mounted.current.capability).toBe(expected);
      expect(mounted.current.items.map(({ run_id }) => run_id)).toEqual(["run-a"]);
      expect(mounted.current.reconnecting).toBe(false);
      mounted.unmount();
    },
  );

  it("removes the retry abort listener after timer resolution and abort", async () => {
    vi.useFakeTimers();
    const removeSpies: Array<ReturnType<typeof vi.spyOn>> = [];
    const fetchRunEvents = vi
      .fn<RunsHistoryApi["fetchRunEvents"]>()
      .mockImplementationOnce(async (_runId, options) => {
        removeSpies.push(vi.spyOn(options.signal!, "removeEventListener"));
        throw new ApiError("offline", "network");
      })
      .mockResolvedValueOnce(eventsPage("run-a", [], { terminal: true }));
    const mounted = mountHook({
      api: api({ fetchRunDetail: vi.fn(async () => detail("run-a")), fetchRunEvents }),
      liveRun: null,
      initial: { runId: "run-a" },
      random: () => 0,
    });
    await flush();
    await vi.advanceTimersByTimeAsync(500);
    expect(removeSpies[0]).toHaveBeenCalled();
    mounted.unmount();

    const abortRemoves: Array<ReturnType<typeof vi.spyOn>> = [];
    const aborting = mountHook({
      api: api({
        fetchRunDetail: vi.fn(async () => detail("run-b")),
        fetchRunEvents: vi.fn(async (_runId, options) => {
          abortRemoves.push(vi.spyOn(options.signal!, "removeEventListener"));
          throw new ApiError("offline", "network");
        }),
      }),
      liveRun: null,
      initial: { runId: "run-b" },
      random: () => 0,
    });
    await flush();
    aborting.unmount();
    expect(abortRemoves[0]).toHaveBeenCalled();
  });
});
it.each(["memory", "sqlite", "memory+sqlite"] as const)("preserves list source %s and retention", async (source) => {
  const mounted = mountHook({ api: api({ fetchRuns: vi.fn(async () => ({ ...list([summary("run-a")]), source, retention: { days: 7, max_runs: 77 } })) }), liveRun: null });
  await flush();
  expect(mounted.current.listSource).toBe(source);
  expect(mounted.current.retention).toEqual({ days: 7, max_runs: 77 });
  mounted.unmount();
});
