import { afterEach, describe, expect, it, vi } from "vitest";
import type { BackendRunEvent } from "../types/rag";
import { RunOpsApiError, fetchRunDetail, fetchRunEvents, fetchRunHealth, fetchRuns } from "./runs";

const NOW = "2026-08-23T08:00:00.000Z";
const topology = {
  id: "rag.query",
  revision: "sha256:test",
  executor: "sequential_stream",
  nodes: [
    {
      id: "generation",
      label: "Generation",
      group: "generate",
      description: "Generate",
      optional: false,
      repeatable: true,
      available: true,
      attributes: {},
    },
  ],
  edges: [],
};
function summary(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    run_id: "run-1",
    status: "completed",
    outcome: "answered",
    started_at: NOW,
    updated_at: NOW,
    finished_at: NOW,
    elapsed_ms: 120,
    boot_id: "boot",
    worker_id: "worker",
    topology_id: topology.id,
    topology_revision: topology.revision,
    executor: topology.executor,
    last_seq: 3,
    event_count: 3,
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
function list(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: 1,
    items: [summary(overrides)],
    next_cursor: null,
    as_of: NOW,
    source: "memory+sqlite",
    history_state: "complete",
    retention: { days: 30, max_runs: 100000 },
  };
}
function event(overrides: Partial<BackendRunEvent> = {}): BackendRunEvent {
  return {
    schema_version: 1,
    run_id: "run-1",
    seq: 1,
    occurred_at: NOW,
    elapsed_ms: 0,
    topology_id: topology.id,
    topology_revision: topology.revision,
    type: "run.started",
    attributes: { topology },
    ...overrides,
  };
}
function response(body: unknown, status = 200, headers?: HeadersInit) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}
function stub(body: unknown, status = 200, headers?: HeadersInit) {
  vi.stubGlobal("localStorage", { getItem: vi.fn(() => null) });
  const fn = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    response(body, status, headers),
  );
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("Runs API", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("encodes deterministic filters without tenant", async () => {
    const fetchMock = stub({ ...list(), items: [] });
    await fetchRuns({
      view: "errors",
      status: ["interrupted", "failed", "failed"],
      slowMs: 45000,
      startedAfter: "2026-08-22T00:00:00Z",
      startedBefore: "2026-08-23T00:00:00Z",
      fingerprint: "fp/value",
      limit: 50,
      cursor: "a+b/=",
    });
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://localhost:8000/api/runs?view=errors&status=failed&status=interrupted&slow_ms=45000&started_after=2026-08-22T00%3A00%3A00Z&started_before=2026-08-23T00%3A00%3A00Z&fingerprint=fp%2Fvalue&limit=50&cursor=a%2Bb%2F%3D",
    );
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain("tenant");
  });
  it("forwards AbortSignal and event polling parameters", async () => {
    const fetchMock = stub({
      schema_version: 1,
      run_id: "run/1",
      events: [],
      after_seq: 8,
      latest_seq: 8,
      terminal: false,
      timed_out: true,
      history_state: "complete",
      earliest_available_seq: 1,
      persistence_status: "durable",
      retry_after_ms: 500,
    });
    await fetchRunEvents("run/1", {
      afterSeq: 8,
      limit: 300,
      waitMs: 25000,
      signal: new AbortController().signal,
    });
    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "http://localhost:8000/api/runs/run%2F1/events?after_seq=8&limit=300&wait_ms=25000",
    );
    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBeInstanceOf(AbortSignal);
  });
  it.each([
    [401, "ops_unauthorized"],
    [403, "ops_forbidden"],
    [404, "run_not_found"],
    [409, "run_event_gap"],
    [429, "run_poll_capacity"],
    [503, "run_ops_unavailable"],
  ] as const)("structures HTTP %i errors", async (status, code) => {
    stub(
      { detail: { code, message: "safe", earliest_available_seq: 18, latest_seq: 42 } },
      status,
      { "Retry-After": "1" },
    );
    const caught = await fetchRunDetail("run-1").catch((error: unknown) => error);
    expect(caught).toBeInstanceOf(RunOpsApiError);
    expect(caught).toEqual(
      expect.objectContaining({
        status,
        code,
        message: "safe",
        retryAfterSeconds: 1,
        earliestAvailableSeq: 18,
        latestSeq: 42,
      }),
    );
  });
  it("accepts scope_stability in health", async () => {
    stub({
      schema_version: 1,
      status: "ok",
      enabled: true,
      boot_id: "boot",
      worker_id: "worker",
      memory: {
        active_runs: 1,
        recent_runs: 2,
        event_count: 3,
        dropped_runs: 0,
        dropped_events: 0,
      },
      persistence: {
        enabled: true,
        state: "ready",
        database: "run-history.sqlite3",
        wal: true,
        writer_queue_depth: 0,
        last_commit_at: NOW,
        commit_lag_ms: 1,
        dropped_mutations: 0,
        quick_check: "ok",
      },
      heartbeat: { interval_s: 5, stale_after_s: 30, last_heartbeat_at: NOW },
      retention: { days: 30, max_runs: 100000, memory_terminal_ttl_s: 21600 },
      scope_stability: "installation",
    });
    await expect(fetchRunHealth()).resolves.toEqual(
      expect.objectContaining({ scope_stability: "installation" }),
    );
  });
  it.each([
    ["schema", { ...list(), schema_version: 2 }],
    ["status", list({ status: "unknown" })],
    ["attention", list({ attention: ["urgent"] })],
    ["required", { ...list(), as_of: undefined }],
    ["nan", list({ elapsed_ms: Number.NaN })],
    ["negative", list({ retry_count: -1 })],
  ])("rejects malformed list %s", async (_name, payload) => {
    stub(payload);
    await expect(fetchRuns({})).rejects.toThrow(/Invalid Runs API response/);
  });
  it.each([
    ["type", event({ type: "node.exploded" as BackendRunEvent["type"] })],
    ["schema", event({ schema_version: 2 as 1 })],
    ["seq", event({ seq: 0 })],
    ["number", event({ elapsed_ms: Infinity })],
    ["node", event({ type: "node.started", node_id: undefined, attempt: 1 })],
    ["date", event({ occurred_at: "not-a-date" })],
    ["naive date", event({ occurred_at: "2026-08-23T12:00:00" })],
  ])("rejects malformed event %s", async (_name, malformed) => {
    stub({
      schema_version: 1,
      run_id: "run-1",
      events: [malformed],
      after_seq: 1,
      latest_seq: 1,
      terminal: false,
      timed_out: false,
      history_state: "complete",
      earliest_available_seq: 1,
      persistence_status: "durable",
      retry_after_ms: 500,
    });
    await expect(fetchRunEvents("run-1", { afterSeq: 0 })).rejects.toThrow(
      /Invalid Runs API response/,
    );
  });
  it("validates detail topology rollup and history", async () => {
    stub({
      schema_version: 1,
      summary: summary(),
      topology,
      node_rollup: [
        {
          node_id: "generation",
          attempt: 1,
          status: "completed",
          started_elapsed_ms: 10,
          finished_elapsed_ms: 110,
          duration_ms: 100,
          degraded_reason: null,
          retry_reason: null,
          error_type: null,
          error_code: null,
        },
      ],
      history: {
        event_integrity: "complete",
        earliest_available_seq: 1,
        last_seq: 3,
        persistence_status: "durable",
      },
    });
    await expect(fetchRunDetail("run-1")).resolves.toEqual(
      expect.objectContaining({ summary: expect.objectContaining({ run_id: "run-1" }) }),
    );
  });
  it.each([
    ["unknown top-level", { private_fact: 1 }],
    ["nested prompt", { mode: { prompt: "secret" } }],
    ["nested camel token", { mode: { accessToken: "secret" } }],
    ["nested authorization", { mode: [{ Authorization: "secret" }] }],
    ["nested query", { topology: { metadata: { user_query: "secret" } } }],
    ["nested text", { mode: { outputText: "secret" } }],
  ])("rejects event attributes with %s", async (_name, attributes) => {
    stub({
      schema_version: 1,
      run_id: "run-1",
      events: [event({ attributes })],
      after_seq: 1,
      latest_seq: 1,
      terminal: false,
      timed_out: false,
      history_state: "complete",
      earliest_available_seq: 1,
      persistence_status: "durable",
      retry_after_ms: 500,
    });
    await expect(fetchRunEvents("run-1", { afterSeq: 0 })).rejects.toThrow(
      /Invalid Runs API response/,
    );
  });

  it.each([
    ["response run", { run_id: "other" }],
    ["event run", { events: [event({ run_id: "other" })] }],
    [
      "non-contiguous seq",
      {
        events: [
          event({ seq: 2 }),
          event({ seq: 4, type: "run.completed", attributes: { outcome: "answered" } }),
        ],
        after_seq: 4,
        latest_seq: 4,
      },
    ],
    [
      "duplicate seq",
      { events: [event({ seq: 2 }), event({ seq: 2 })], after_seq: 2, latest_seq: 2 },
    ],
    ["seq not after request", { events: [event({ seq: 1 })], after_seq: 1 }],
    ["seq above latest", { events: [event({ seq: 2 })], after_seq: 2, latest_seq: 1 }],
    ["wrong response after_seq", { events: [event({ seq: 2 })], after_seq: 3, latest_seq: 3 }],
    [
      "mixed topology",
      {
        events: [
          event({ seq: 2 }),
          event({
            seq: 3,
            type: "run.completed",
            topology_revision: "sha256:other",
            attributes: { outcome: "answered" },
          }),
        ],
        after_seq: 3,
        latest_seq: 3,
      },
    ],
  ])("rejects invalid event ledger %s", async (_name, overrides) => {
    stub({
      schema_version: 1,
      run_id: "run-1",
      events: [event({ seq: 2 })],
      after_seq: 2,
      latest_seq: 2,
      terminal: false,
      timed_out: false,
      history_state: "complete",
      earliest_available_seq: 1,
      persistence_status: "durable",
      retry_after_ms: 500,
      ...overrides,
    });
    await expect(fetchRunEvents("run-1", { afterSeq: 1 })).rejects.toThrow(
      /Invalid Runs API response/,
    );
  });

  it("accepts an empty event page only when after_seq echoes the requested cursor", async () => {
    stub({
      schema_version: 1,
      run_id: "run-1",
      events: [],
      after_seq: 8,
      latest_seq: 8,
      terminal: false,
      timed_out: true,
      history_state: "complete",
      earliest_available_seq: 1,
      persistence_status: "durable",
      retry_after_ms: 500,
    });
    await expect(fetchRunEvents("run-1", { afterSeq: 8 })).resolves.toEqual(
      expect.objectContaining({ after_seq: 8, events: [] }),
    );
  });

  it.each([
    ["health status", "health", { status: "unknown" }],
    ["health required", "health", { worker_id: undefined }],
    ["topology executor", "detail", { topology: { ...topology, executor: "plugin" } }],
    [
      "topology group",
      "detail",
      { topology: { ...topology, nodes: [{ ...topology.nodes[0], group: "secret" }] } },
    ],
    [
      "topology edge",
      "detail",
      {
        topology: {
          ...topology,
          edges: [{ id: "bad", source: "missing", target: "generation", kind: "dependency" }],
        },
      },
    ],
    ["detail run", "detail", { summary: summary({ run_id: "other" }) }],
    ["detail topology id", "detail", { summary: summary({ topology_id: "other" }) }],
    ["detail topology revision", "detail", { summary: summary({ topology_revision: "other" }) }],
  ])("rejects DTO guard matrix %s", async (_name, endpoint, overrides) => {
    const health = {
      schema_version: 1,
      status: "ok",
      enabled: true,
      boot_id: "boot",
      worker_id: "worker",
      memory: {
        active_runs: 1,
        recent_runs: 2,
        event_count: 3,
        dropped_runs: 0,
        dropped_events: 0,
      },
      persistence: {
        enabled: true,
        state: "ready",
        database: "db",
        wal: true,
        writer_queue_depth: 0,
        last_commit_at: NOW,
        commit_lag_ms: 1,
        dropped_mutations: 0,
        quick_check: "ok",
      },
      heartbeat: { interval_s: 5, stale_after_s: 30, last_heartbeat_at: NOW },
      retention: { days: 30, max_runs: 100, memory_terminal_ttl_s: 10 },
      scope_stability: "installation",
    };
    const detailPayload = {
      schema_version: 1,
      summary: summary(),
      topology,
      node_rollup: [],
      history: {
        event_integrity: "complete",
        earliest_available_seq: 1,
        last_seq: 3,
        persistence_status: "durable",
      },
    };
    stub(endpoint === "health" ? { ...health, ...overrides } : { ...detailPayload, ...overrides });
    await expect(
      endpoint === "health" ? fetchRunHealth() : fetchRunDetail("run-1"),
    ).rejects.toThrow(/Invalid Runs API response/);
  });
  it("rejects APIKey deterministically even when locale folding maps uppercase I to dotless i", async () => {
    const original = String.prototype.toLocaleLowerCase;
    const localeSpy = vi.spyOn(String.prototype, "toLocaleLowerCase").mockImplementation(function (
      this: string,
    ) {
      return String(this) === "I" ? "ı" : String.prototype.toLowerCase.call(this);
    });
    try {
      stub({
        schema_version: 1,
        run_id: "run-1",
        events: [event({ attributes: { mode: { APIKey: "secret" } } })],
        after_seq: 1,
        latest_seq: 1,
        terminal: false,
        timed_out: false,
        history_state: "complete",
        earliest_available_seq: 1,
        persistence_status: "durable",
        retry_after_ms: 500,
      });
      await expect(fetchRunEvents("run-1", { afterSeq: 0 })).rejects.toThrow(
        /Invalid Runs API response/,
      );
    } finally {
      localeSpy.mockRestore();
      expect(String.prototype.toLocaleLowerCase).toBe(original);
    }
  });
});
