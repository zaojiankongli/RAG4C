// @vitest-environment jsdom

import { StrictMode } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { BackendRunEvent } from "../types/rag";
import type { RunDetailDto, RunEventsDto, RunHealthDto, RunListDto } from "../types/runs";
import { useRunHistory, type RunsHistoryApi } from "./useRunHistory";

const runId = "run-strict";
const topology = {
  id: "rag.query",
  revision: "sha256:strict",
  executor: "sequential_stream" as const,
  nodes: [
    {
      id: "receive",
      label: "Receive",
      group: "input" as const,
      description: "Receive",
      optional: false,
      repeatable: false,
      available: true,
      attributes: {},
    },
  ],
  edges: [],
};
const summary = {
  schema_version: 1 as const,
  run_id: runId,
  status: "completed" as const,
  outcome: "answered" as const,
  started_at: "2026-08-24T00:00:00Z",
  updated_at: "2026-08-24T00:00:01Z",
  finished_at: "2026-08-24T00:00:01Z",
  elapsed_ms: 1000,
  boot_id: "boot-strict",
  worker_id: "worker-strict",
  topology_id: topology.id,
  topology_revision: topology.revision,
  executor: topology.executor,
  last_seq: 1,
  event_count: 1,
  earliest_available_seq: 1,
  current_node_ids: [],
  failed_node_ids: [],
  route: "hybrid",
  degraded_count: 0,
  retry_count: 0,
  attention: [],
  event_integrity: "complete" as const,
  persistence_status: "durable" as const,
  interruption_reason: null,
};
const started: BackendRunEvent = {
  schema_version: 1,
  run_id: runId,
  seq: 1,
  occurred_at: "2026-08-24T00:00:00Z",
  elapsed_ms: 0,
  topology_id: topology.id,
  topology_revision: topology.revision,
  type: "run.started",
  attributes: { executor: "sequential_stream" },
};
const health: RunHealthDto = {
  schema_version: 1,
  status: "ok",
  enabled: true,
  boot_id: "boot-strict",
  worker_id: "worker-strict",
  memory: {
    active_runs: 0,
    recent_runs: 1,
    event_count: 1,
    dropped_runs: 0,
    dropped_events: 0,
  },
  persistence: {
    enabled: true,
    state: "ready",
    database: "run-history.sqlite3",
    wal: true,
    writer_queue_depth: 0,
    last_commit_at: "2026-08-24T00:00:01Z",
    commit_lag_ms: 0,
    dropped_mutations: 0,
    quick_check: "ok",
  },
  heartbeat: { interval_s: 5, stale_after_s: 30, last_heartbeat_at: null },
  retention: { days: 30, max_runs: 100000, memory_terminal_ttl_s: 21600 },
  scope_stability: "installation",
};
const list: RunListDto = {
  schema_version: 1,
  items: [summary],
  next_cursor: null,
  as_of: "2026-08-24T00:00:02Z",
  source: "sqlite",
  history_state: "complete",
  retention: { days: 30, max_runs: 100000 },
};
const detail: RunDetailDto = {
  schema_version: 1,
  summary,
  topology,
  node_rollup: [],
  history: {
    event_integrity: "complete",
    earliest_available_seq: 1,
    last_seq: 1,
    persistence_status: "durable",
  },
};
const events: RunEventsDto = {
  schema_version: 1,
  run_id: runId,
  events: [started],
  after_seq: 1,
  latest_seq: 1,
  terminal: true,
  timed_out: false,
  history_state: "complete",
  earliest_available_seq: 1,
  persistence_status: "durable",
  retry_after_ms: 500,
};

function successfulApi(): RunsHistoryApi {
  return {
    fetchRunHealth: vi.fn(async () => health),
    fetchRuns: vi.fn(async () => list),
    fetchRunDetail: vi.fn(async () => detail),
    fetchRunEvents: vi.fn(async () => events),
  };
}

function Probe({ api }: { api: RunsHistoryApi }) {
  const state = useRunHistory({ api, liveRun: null, initial: { runId } });
  return (
    <output data-testid="strict-history">
      {state.items.length}:{state.detail?.summary.run_id ?? "none"}:{state.events.length}
    </output>
  );
}

afterEach(cleanup);

describe("useRunHistory React StrictMode lifecycle", () => {
  it("accepts successful list, detail, and events after the development remount", async () => {
    render(
      <StrictMode>
        <Probe api={successfulApi()} />
      </StrictMode>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("strict-history").textContent).toBe("1:run-strict:1");
    });
  });
});
