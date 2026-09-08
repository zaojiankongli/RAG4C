import { describe, expect, it } from "vitest";
import type { BackendRunEvent, BackendRunTopology, QueryResponse } from "../types/rag";
import { initialRunMonitorState, runMonitorReducer } from "./runMonitorStore";

const response: QueryResponse = {
  result: {
    query: "问题",
    answer: "回答",
    citations: [],
    verdict: {},
    abstained: false,
    route: "hybrid",
    traces: ["search:12ms", "generate:20ms"],
  },
  using_mock: false,
  duration_ms: 32,
};

const topology: BackendRunTopology = {
  id: "rag.query",
  revision: "sha256:typed",
  executor: "sequential_stream",
  nodes: [
    {
      id: "receive",
      label: "Receive query",
      group: "input",
      description: "Accept the query.",
      optional: false,
      repeatable: false,
      available: true,
      attributes: {},
    },
    {
      id: "search",
      label: "Search",
      group: "retrieve",
      description: "Retrieve evidence.",
      optional: false,
      repeatable: true,
      available: true,
      attributes: {},
    },
    {
      id: "verify",
      label: "Verify",
      group: "verify",
      description: "Verify evidence.",
      optional: false,
      repeatable: true,
      available: true,
      attributes: {},
    },
    {
      id: "finalize",
      label: "Finalize",
      group: "output",
      description: "Return the result.",
      optional: false,
      repeatable: false,
      available: true,
      attributes: {},
    },
  ],
  edges: [
    { id: "receive.search", source: "receive", target: "search", kind: "dependency" },
    { id: "search.verify", source: "search", target: "verify", kind: "dependency" },
    { id: "verify.search", source: "verify", target: "search", kind: "retry" },
    { id: "verify.finalize", source: "verify", target: "finalize", kind: "dependency" },
  ],
};

function typedEvent(
  type: BackendRunEvent["type"],
  seq: number,
  overrides: Partial<BackendRunEvent> = {},
): BackendRunEvent {
  return {
    schema_version: 1,
    run_id: "run-backend",
    seq,
    occurred_at: `2026-08-23T00:00:0${Math.min(seq, 9)}Z`,
    elapsed_ms: seq * 100,
    topology_id: topology.id,
    topology_revision: topology.revision,
    type,
    attributes: {},
    ...overrides,
  };
}

function typedStarted() {
  const provisional = runMonitorReducer(initialRunMonitorState, {
    type: "start",
    id: "run-provisional",
    query: "问题",
    startedAt: 1000,
  });
  return runMonitorReducer(provisional, {
    type: "run-event",
    provisionalRunId: "run-provisional",
    event: typedEvent("run.started", 1, { attributes: { topology } }),
  });
}

describe("runMonitorReducer", () => {
  it("replaces the provisional ID and installs the backend topology once", () => {
    const started = typedStarted();
    const conflicting = runMonitorReducer(started, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("run.started", 2, {
        attributes: { topology: { ...topology, revision: "sha256:changed" } },
      }),
    });

    expect(started.current).toEqual(
      expect.objectContaining({
        id: "run-backend",
        seq: 1,
        topologyId: "rag.query",
        topologyRevision: "sha256:typed",
        topologySource: "typed-events",
      }),
    );
    expect(started.current?.typedProjection).toEqual(
      expect.objectContaining({
        id: "run-backend",
        topologySource: "typed-events",
        topologyId: "rag.query",
        topologyRevision: "sha256:typed",
      }),
    );
    expect(started.current?.typedProjection?.nodes.map((node) => node.id)).toEqual([
      "receive",
      "search",
      "verify",
      "finalize",
    ]);
    expect(conflicting).toBe(started);
  });

  it("attaches a sanitized event buffer only after canonical run.started", () => {
    const provisional = runMonitorReducer(initialRunMonitorState, {
      type: "start",
      id: "local-1",
      query: "SENTINEL_QUERY",
      startedAt: 1000,
    });
    const started = runMonitorReducer(provisional, {
      type: "run-event",
      provisionalRunId: "local-1",
      event: typedEvent("run.started", 1, { attributes: { topology } }),
    });
    const completed = runMonitorReducer(started, {
      type: "complete",
      runId: "local-1",
      seq: 2,
      source: "rest_fallback",
      finishedAt: 1100,
      response: {
        ...response,
        result: { ...response.result, answer: "SENTINEL_ANSWER" },
      },
    });

    expect(provisional.current?.eventBuffer).toBeUndefined();
    expect(started.current?.eventBuffer).toEqual(
      expect.objectContaining({
        runId: "run-backend",
        lastContiguousSeq: 1,
        historyState: "complete",
      }),
    );
    const serializedBuffer = JSON.stringify(completed.current?.eventBuffer ?? {});
    expect(serializedBuffer).not.toContain("SENTINEL_QUERY");
    expect(serializedBuffer).not.toContain("SENTINEL_ANSWER");
    expect(completed.current?.query).toBe("SENTINEL_QUERY");
  });

  it("rejects a sensitive live event and marks live integrity partial", () => {
    const started = typedStarted();
    const invalid = runMonitorReducer(started, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("degraded", 2, { attributes: { token: "SECRET_TOKEN" } }),
    });

    expect(invalid.current?.eventBuffer?.events).toHaveLength(1);
    expect(invalid.current?.eventBuffer?.historyState).toBe("partial");
    expect(invalid.current?.liveTransportDesync).toBe(true);
    expect(JSON.stringify(invalid.current?.eventBuffer)).not.toContain("SECRET_TOKEN");
  });

  it("keeps the first conflicting sequence event and rejects events from another run", () => {
    const started = typedStarted();
    const accepted = runMonitorReducer(started, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("node.started", 2, { node_id: "search", attempt: 1 }),
    });
    const regression = runMonitorReducer(accepted, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("node.completed", 2, {
        node_id: "search",
        attempt: 1,
        duration_ms: 30,
      }),
    });
    const wrongRun = runMonitorReducer(accepted, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("node.completed", 3, {
        run_id: "run-other",
        node_id: "search",
        attempt: 1,
      }),
    });

    expect(regression.current).toEqual(
      expect.objectContaining({
        seq: 2,
        eventBuffer: expect.objectContaining({ historyState: "partial" }),
      }),
    );
    expect(regression.current?.eventBuffer?.events[1]?.type).toBe("node.started");
    expect(wrongRun).toBe(accepted);
  });

  it("ignores coarse phases after typed mode activates", () => {
    const started = typedStarted();
    const coarse = runMonitorReducer(started, {
      type: "phase",
      runId: "run-backend",
      seq: 2,
      phase: "generating",
      at: 1500,
    });

    expect(coarse).toBe(started);
  });

  it("keeps the existing coarse projection for servers without run events", () => {
    const started = runMonitorReducer(initialRunMonitorState, {
      type: "start",
      id: "run-legacy",
      query: "问题",
      startedAt: 1000,
    });
    const generating = runMonitorReducer(started, {
      type: "phase",
      runId: "run-legacy",
      seq: 1,
      phase: "generating",
      at: 1200,
    });

    expect(generating.current).toEqual(
      expect.objectContaining({
        id: "run-legacy",
        phase: "generating",
        topologySource: "coarse-phase",
      }),
    );
  });

  it("projects node lifecycle, route, retry, degradation, and terminal facts", () => {
    let state = typedStarted();
    const apply = (event: BackendRunEvent) => {
      state = runMonitorReducer(state, {
        type: "run-event",
        provisionalRunId: "run-provisional",
        event,
      });
    };
    apply(typedEvent("node.started", 2, { node_id: "search", attempt: 1 }));
    apply(
      typedEvent("route.selected", 3, {
        node_id: "search",
        attempt: 1,
        attributes: { route: "hybrid" },
      }),
    );
    apply(
      typedEvent("node.completed", 4, {
        node_id: "search",
        attempt: 1,
        duration_ms: 35,
      }),
    );
    apply(
      typedEvent("retry.started", 5, {
        node_id: "search",
        attempt: 1,
        attributes: { target_attempt: 2 },
      }),
    );
    apply(
      typedEvent("degraded", 6, {
        node_id: "search",
        attempt: 2,
        attributes: { reason: "reranker_unavailable" },
      }),
    );
    apply(
      typedEvent("retry.completed", 7, {
        node_id: "search",
        attempt: 1,
        duration_ms: 12,
      }),
    );
    apply(typedEvent("run.completed", 8, { attributes: { outcome: "answered" } }));

    const projection = state.current?.typedProjection;
    expect(projection).toEqual(
      expect.objectContaining({
        route: "hybrid",
        status: "degraded",
        durationMs: 800,
      }),
    );
    expect(projection?.nodes.find((node) => node.id === "search")).toEqual(
      expect.objectContaining({ status: "degraded", durationMs: 35, attempt: 2 }),
    );
    expect(projection?.edges.find((edge) => edge.kind === "retry")).toEqual(
      expect.objectContaining({ status: "completed" }),
    );
  });

  it("makes a typed terminal run immutable", () => {
    const started = typedStarted();
    const completed = runMonitorReducer(started, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("run.completed", 2),
    });
    const late = runMonitorReducer(completed, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("node.started", 3, { node_id: "search", attempt: 2 }),
    });

    expect(completed.current?.status).toBe("completed");
    expect(late).toBe(completed);
  });

  it("allows a local REST fallback to complete a still-running typed run", () => {
    const started = typedStarted();
    const active = runMonitorReducer(started, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("node.started", 2, { node_id: "search", attempt: 1 }),
    });
    const completed = runMonitorReducer(active, {
      type: "complete",
      runId: "run-provisional",
      seq: 99,
      response,
      source: "rest_fallback",
      finishedAt: 1500,
    });
    const lateTypedTerminal = runMonitorReducer(completed, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("run.completed", 3),
    });

    expect(completed.current).toEqual(
      expect.objectContaining({
        id: "run-backend",
        status: "completed",
        source: "rest_fallback",
        response,
        topologySource: "typed-events",
      }),
    );
    expect(completed.current?.typedProjection).toEqual(
      expect.objectContaining({ status: "completed", topologySource: "typed-events" }),
    );
    expect(
      completed.current?.typedProjection?.nodes.find((node) => node.id === "search")?.status,
    ).toBe("completed");
    expect(
      completed.current?.typedProjection?.nodes.find((node) => node.id === "verify")?.status,
    ).toBe("skipped");
    expect(lateTypedTerminal).toBe(completed);
  });

  it("allows user abort to cancel a still-running typed run", () => {
    const started = typedStarted();
    const active = runMonitorReducer(started, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("node.started", 2, { node_id: "search", attempt: 1 }),
    });
    const cancelled = runMonitorReducer(active, {
      type: "cancel",
      runId: "run-provisional",
      seq: 99,
      finishedAt: 1400,
    });

    expect(cancelled.current).toEqual(
      expect.objectContaining({
        id: "run-backend",
        status: "cancelled",
        topologySource: "typed-events",
      }),
    );
    expect(
      cancelled.current?.typedProjection?.nodes.find((node) => node.id === "search")?.status,
    ).toBe("cancelled");
    expect(
      cancelled.current?.typedProjection?.nodes.find((node) => node.id === "verify")?.status,
    ).toBe("skipped");
  });

  it("keeps an authoritative typed terminal immutable against local fallback", () => {
    const started = typedStarted();
    const authoritative = runMonitorReducer(started, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("run.completed", 2),
    });
    const localComplete = runMonitorReducer(authoritative, {
      type: "complete",
      runId: "run-provisional",
      seq: 99,
      response,
      source: "rest_fallback",
      finishedAt: 1600,
    });
    const localCancel = runMonitorReducer(authoritative, {
      type: "cancel",
      runId: "run-provisional",
      seq: 100,
      finishedAt: 1700,
    });

    expect(localComplete).toBe(authoritative);
    expect(localCancel).toBe(authoritative);
  });

  it("marks an exact typed buffer overflow marker desynchronized and permits local completion", () => {
    const started = typedStarted();
    const marker = {
      type: "run_event_desync",
      run_id: "run-backend",
      expected_seq: 2,
      reason: "typed_buffer_overflow",
    } as const;
    const desynchronized = runMonitorReducer(started, {
      type: "run-event-desync",
      provisionalRunId: "run-provisional",
      marker,
    });
    const laterTyped = runMonitorReducer(desynchronized, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("node.started", 2, { node_id: "search", attempt: 1 }),
    });
    const completed = runMonitorReducer(desynchronized, {
      type: "complete",
      runId: "run-provisional",
      seq: 99,
      response,
      source: "rest_fallback",
      finishedAt: 1700,
    });

    expect(desynchronized.current).toEqual(
      expect.objectContaining({
        status: "running",
        seq: 1,
        syncStatus: "desynchronized",
        liveTransportDesync: true,
        syncWarning: expect.stringMatching(/2.*typed_buffer_overflow/),
      }),
    );
    expect(desynchronized.current?.eventBuffer).toBe(started.current?.eventBuffer);
    expect(desynchronized.current?.eventBuffer?.historyState).toBe("complete");
    expect(laterTyped).toBe(desynchronized);
    expect(completed.current).toEqual(
      expect.objectContaining({
        status: "completed",
        syncStatus: "desynchronized",
        topologySource: "typed-events",
      }),
    );
  });

  it("ignores overflow markers for unrelated and provisional-only runs", () => {
    const provisional = runMonitorReducer(initialRunMonitorState, {
      type: "start",
      id: "run-provisional",
      query: "问题",
      startedAt: 1000,
    });
    const provisionalMarker = runMonitorReducer(provisional, {
      type: "run-event-desync",
      provisionalRunId: "run-provisional",
      marker: {
        type: "run_event_desync",
        run_id: "run-provisional",
        expected_seq: 1,
        reason: "typed_buffer_overflow",
      },
    });
    const started = typedStarted();
    const unrelatedMarker = runMonitorReducer(started, {
      type: "run-event-desync",
      provisionalRunId: "run-provisional",
      marker: {
        type: "run_event_desync",
        run_id: "run-other",
        expected_seq: 2,
        reason: "typed_buffer_overflow",
      },
    });

    expect(provisionalMarker).toBe(provisional);
    expect(unrelatedMarker).toBe(started);
  });

  it("marks a typed sequence gap desynchronized and permits local completion", () => {
    const started = typedStarted();
    const desynchronized = runMonitorReducer(started, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("node.started", 3, { node_id: "search", attempt: 1 }),
    });
    const laterTyped = runMonitorReducer(desynchronized, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("node.started", 2, { node_id: "search", attempt: 1 }),
    });
    const completed = runMonitorReducer(desynchronized, {
      type: "complete",
      runId: "run-provisional",
      seq: 99,
      response,
      source: "rest_fallback",
      finishedAt: 1700,
    });

    expect(desynchronized.current).toEqual(
      expect.objectContaining({
        status: "running",
        seq: 1,
        syncStatus: "desynchronized",
        syncWarning: expect.stringContaining("事件不完整"),
      }),
    );
    expect(laterTyped).toBe(desynchronized);
    expect(completed.current).toEqual(
      expect.objectContaining({
        status: "completed",
        syncStatus: "desynchronized",
        topologySource: "typed-events",
      }),
    );
  });

  it("marks a typed sequence gap desynchronized and permits local cancellation", () => {
    const started = typedStarted();
    const desynchronized = runMonitorReducer(started, {
      type: "run-event",
      provisionalRunId: "run-provisional",
      event: typedEvent("node.started", 4, { node_id: "search", attempt: 1 }),
    });
    const cancelled = runMonitorReducer(desynchronized, {
      type: "cancel",
      runId: "run-provisional",
      seq: 99,
      finishedAt: 1800,
    });

    expect(cancelled.current).toEqual(
      expect.objectContaining({
        status: "cancelled",
        syncStatus: "desynchronized",
        syncWarning: expect.stringContaining("事件不完整"),
      }),
    );
  });

  it("keeps phase metadata and measured phase durations through completion", () => {
    const started = runMonitorReducer(initialRunMonitorState, {
      type: "start",
      id: "run-1",
      query: "问题",
      startedAt: 1000,
    });
    const generating = runMonitorReducer(started, {
      type: "phase",
      runId: "run-1",
      seq: 1,
      phase: "generating",
      at: 1400,
      metadata: { route: "hybrid", chunks: 3 },
    });
    const completed = runMonitorReducer(generating, {
      type: "complete",
      runId: "run-1",
      seq: 2,
      response,
      source: "rest_fallback",
      finishedAt: 1600,
    });

    expect(generating.current).toEqual(
      expect.objectContaining({
        id: "run-1",
        seq: 1,
        phase: "generating",
        phaseStartedAt: 1400,
        phaseDurations: { retrieving: 400 },
        route: "hybrid",
        chunks: 3,
      }),
    );
    expect(completed.current).toEqual(
      expect.objectContaining({
        status: "completed",
        source: "rest_fallback",
        phaseDurations: { retrieving: 400, generating: 200 },
      }),
    );
  });

  it("ignores stale run events and non-increasing sequence numbers", () => {
    const started = runMonitorReducer(initialRunMonitorState, {
      type: "start",
      id: "run-2",
      query: "问题",
      startedAt: 1000,
    });
    const wrongRun = runMonitorReducer(started, {
      type: "phase",
      runId: "run-old",
      seq: 1,
      phase: "generating",
      at: 1100,
    });
    const advanced = runMonitorReducer(started, {
      type: "phase",
      runId: "run-2",
      seq: 2,
      phase: "generating",
      at: 1200,
    });
    const staleSeq = runMonitorReducer(advanced, {
      type: "phase",
      runId: "run-2",
      seq: 1,
      phase: "verifying",
      at: 1300,
    });

    expect(wrongRun).toBe(started);
    expect(staleSeq).toBe(advanced);
  });

  it("preserves the second retrieval attempt after execution returns to generation", () => {
    const started = runMonitorReducer(initialRunMonitorState, {
      type: "start",
      id: "run-retry",
      query: "问题",
      startedAt: 1000,
      source: "demo",
    });
    const retrying = runMonitorReducer(started, {
      type: "phase",
      runId: "run-retry",
      seq: 1,
      phase: "retrieving_again",
      at: 1100,
    });
    const generating = runMonitorReducer(retrying, {
      type: "phase",
      runId: "run-retry",
      seq: 2,
      phase: "generating",
      at: 1200,
    });

    expect(generating.current).toEqual(
      expect.objectContaining({ source: "demo", retrievalAttempt: 2, phase: "generating" }),
    );
  });
  it("keeps terminal runs immutable when late events arrive", () => {
    const started = runMonitorReducer(initialRunMonitorState, {
      type: "start",
      id: "run-terminal",
      query: "问题",
      startedAt: 1000,
    });
    const completed = runMonitorReducer(started, {
      type: "complete",
      runId: "run-terminal",
      seq: 1,
      response,
      source: "live_stream",
      finishedAt: 1100,
    });
    const latePhase = runMonitorReducer(completed, {
      type: "phase",
      runId: "run-terminal",
      seq: 2,
      phase: "retrieving_again",
      at: 1200,
    });

    expect(latePhase).toBe(completed);
  });
  it("turns cancellation into a terminal visible state", () => {
    const started = runMonitorReducer(initialRunMonitorState, {
      type: "start",
      id: "run-3",
      query: "问题",
      startedAt: 1000,
    });
    const cancelled = runMonitorReducer(started, {
      type: "cancel",
      runId: "run-3",
      seq: 1,
      finishedAt: 1200,
    });

    expect(cancelled.current).toEqual(
      expect.objectContaining({ status: "cancelled", finishedAt: 1200 }),
    );
  });
});
