import { describe, expect, it } from "vitest";
import type { BackendRunEvent } from "../types/rag";
import type { RunDetailDto, RunEventsDto } from "../types/runs";
import { projectRunTimeline, projectServerFlow, projectServerRun } from "./serverRunProjection";

const topology = {
  id: "rag.query",
  revision: "sha256:history",
  executor: "sequential_stream" as const,
  nodes: [
    {
      id: "a",
      label: "A",
      group: "retrieve" as const,
      description: "A",
      optional: false,
      repeatable: false,
      available: true,
      attributes: {},
    },
    {
      id: "b",
      label: "B",
      group: "generate" as const,
      description: "B",
      optional: false,
      repeatable: true,
      available: true,
      attributes: {},
    },
    {
      id: "c",
      label: "C",
      group: "verify" as const,
      description: "C",
      optional: false,
      repeatable: false,
      available: true,
      attributes: {},
    },
  ],
  edges: [
    { id: "a.b", source: "a", target: "b", kind: "dependency" as const },
    { id: "b.c", source: "b", target: "c", kind: "dependency" as const },
  ],
};
function evt(
  type: BackendRunEvent["type"],
  seq: number,
  elapsed_ms: number,
  overrides: Partial<BackendRunEvent> = {},
): BackendRunEvent {
  return {
    schema_version: 1,
    run_id: "run-1",
    seq,
    occurred_at: "2026-08-23T08:00:00.000Z",
    elapsed_ms,
    topology_id: topology.id,
    topology_revision: topology.revision,
    type,
    attributes: {},
    ...overrides,
  };
}
function detail(overrides: Partial<RunDetailDto> = {}): RunDetailDto {
  return {
    schema_version: 1,
    summary: {
      schema_version: 1,
      run_id: "run-1",
      status: "completed",
      outcome: "answered",
      started_at: "2026-08-23T08:00:00.000Z",
      updated_at: "2026-08-23T08:00:01.000Z",
      finished_at: "2026-08-23T08:00:01.000Z",
      elapsed_ms: 1000,
      boot_id: "boot",
      worker_id: "worker",
      topology_id: topology.id,
      topology_revision: topology.revision,
      executor: topology.executor,
      last_seq: 11,
      event_count: 11,
      earliest_available_seq: 1,
      current_node_ids: [],
      failed_node_ids: [],
      route: "hybrid",
      degraded_count: 0,
      retry_count: 1,
      attention: [],
      event_integrity: "complete",
      persistence_status: "durable",
      interruption_reason: null,
    },
    topology,
    node_rollup: [
      {
        node_id: "b",
        attempt: 2,
        status: "completed",
        started_elapsed_ms: 610,
        finished_elapsed_ms: 700,
        duration_ms: 90,
        degraded_reason: null,
        retry_reason: "transient",
        error_type: null,
        error_code: null,
      },
    ],
    history: {
      event_integrity: "complete",
      earliest_available_seq: 1,
      last_seq: 11,
      persistence_status: "durable",
    },
    ...overrides,
  };
}
function events(items: BackendRunEvent[]): RunEventsDto {
  return {
    schema_version: 1,
    run_id: "run-1",
    events: items,
    after_seq: items[items.length - 1]?.seq ?? 0,
    latest_seq: items[items.length - 1]?.seq ?? 0,
    terminal: true,
    timed_out: false,
    history_state: "complete",
    earliest_available_seq: 1,
    persistence_status: "durable",
    retry_after_ms: 500,
  };
}

describe("projectServerRun", () => {
  it("replays events through the typed reducer and preserves rollup", () => {
    const value = projectServerRun(
      detail(),
      events([
        evt("run.started", 1, 0, { attributes: { topology } }),
        evt("node.started", 2, 10, { node_id: "b", attempt: 1 }),
        evt("node.completed", 3, 110, { node_id: "b", attempt: 1, duration_ms: 100 }),
        evt("run.completed", 4, 120, { attributes: { outcome: "answered" } }),
      ]),
    );
    expect(value.flow.topologySource).toBe("server-history");
    expect(value.flow.nodes.find((node) => node.id === "b")?.status).toBe("completed");
    expect(value.nodeRollup).toEqual(detail().node_rollup);
  });
  it("rejects topology revision mismatch", () => {
    expect(() =>
      projectServerRun(
        detail(),
        events([
          evt("run.started", 1, 0, {
            topology_revision: "sha256:other",
            attributes: { topology: { ...topology, revision: "sha256:other" } },
          }),
        ]),
      ),
    ).toThrow(/topology revision/i);
  });
  it("extracts retries transitive waves and idle gaps over 250ms", () => {
    const value = projectServerRun(
      detail(),
      events([
        evt("run.started", 1, 0, { attributes: { topology } }),
        evt("node.started", 2, 0, { node_id: "a", attempt: 1 }),
        evt("node.started", 3, 80, { node_id: "b", attempt: 1 }),
        evt("node.completed", 4, 100, { node_id: "a", attempt: 1, duration_ms: 100 }),
        evt("node.started", 5, 170, { node_id: "c", attempt: 1 }),
        evt("node.completed", 6, 180, { node_id: "b", attempt: 1, duration_ms: 100 }),
        evt("node.completed", 7, 240, { node_id: "c", attempt: 1, duration_ms: 70 }),
        evt("retry.started", 8, 600, {
          node_id: "b",
          attempt: 1,
          attributes: { target_attempt: 2, reason: "transient" },
        }),
        evt("node.started", 9, 610, { node_id: "b", attempt: 2 }),
        evt("node.completed", 10, 700, { node_id: "b", attempt: 2, duration_ms: 90 }),
        evt("run.completed", 11, 710, { attributes: { outcome: "answered" } }),
      ]),
    );
    expect(
      value.intervals.map(({ nodeId, attempt, startMs, endMs }) => ({
        nodeId,
        attempt,
        startMs,
        endMs,
      })),
    ).toEqual([
      { nodeId: "a", attempt: 1, startMs: 0, endMs: 100 },
      { nodeId: "b", attempt: 1, startMs: 80, endMs: 180 },
      { nodeId: "c", attempt: 1, startMs: 170, endMs: 240 },
      { nodeId: "b", attempt: 2, startMs: 610, endMs: 700 },
    ]);
    expect(value.waves).toEqual([
      expect.objectContaining({
        id: 1,
        startMs: 0,
        endMs: 240,
        intervalCount: 3,
        maxConcurrency: 2,
      }),
      expect.objectContaining({
        id: 2,
        startMs: 610,
        endMs: 700,
        intervalCount: 1,
        maxConcurrency: 1,
      }),
    ]);
    expect(value.idleGaps).toEqual([{ startMs: 240, endMs: 610, durationMs: 370 }]);
    expect(value.intervals[value.intervals.length - 1]).toEqual(
      expect.objectContaining({ attempt: 2, retry: true }),
    );
  });
  it("keeps open intervals partial and knowledge absences undefined", () => {
    const value = projectServerRun(
      detail(),
      events([
        evt("run.started", 1, 0, { attributes: { topology } }),
        evt("route.selected", 2, 5, { node_id: "a", attempt: 1, attributes: { route: "hybrid" } }),
        evt("node.started", 3, 10, { node_id: "a", attempt: 1 }),
      ]),
    );
    expect(value.intervals[0]).toEqual(
      expect.objectContaining({ endMs: undefined, partial: true }),
    );
    expect(value.knowledge.route).toBe("hybrid");
    expect(value.knowledge.candidateCount).toBeUndefined();
    expect(value.knowledge.chunkCount).toBeUndefined();
    expect(value.knowledge.citationCount).toBeUndefined();
  });
  it.each([
    ["run id", { run_id: "other" }],
    ["topology id", { topology_id: "other" }],
    ["topology revision", { topology_revision: "other" }],
  ])("preflights every replay event %s before exposing derived or raw data", (_name, mismatch) => {
    const page = events([
      evt("run.started", 1, 0, { attributes: { topology } }),
      evt("node.started", 2, 10, { node_id: "a", attempt: 1 }),
      evt("node.completed", 3, 20, {
        node_id: "a",
        attempt: 1,
        attributes: { candidate_count: 99 },
        ...mismatch,
      }),
    ]);
    expect(() => projectServerRun(detail(), page)).toThrow(/mismatch/i);
  });

  it("derives optional knowledge component states from node facts and rollup", () => {
    const nodeIds = [
      "plugin.hyde.expand",
      "plugin.subqueries.expand",
      "plugin.stepback.expand",
      "graph.retrieve",
      "rerank",
      "sentence_window",
    ];
    const detailed = detail({
      topology: {
        ...topology,
        nodes: nodeIds.map((id) => ({
          id,
          label: id,
          group: "extension" as const,
          description: id,
          optional: true,
          repeatable: false,
          available: true,
          attributes: {},
        })),
        edges: [],
      },
      summary: {
        ...detail().summary,
        topology_id: topology.id,
        topology_revision: topology.revision,
      },
      node_rollup: [
        {
          node_id: "rerank",
          attempt: 1,
          status: "failed",
          started_elapsed_ms: 1,
          finished_elapsed_ms: 2,
          duration_ms: 1,
          degraded_reason: "rerank_failed",
          retry_reason: null,
          error_type: "RerankError",
          error_code: "rerank_failed",
        },
      ],
    });
    const page = events([
      evt("run.started", 1, 0, { attributes: { topology: detailed.topology } }),
      evt("node.completed", 2, 10, { node_id: "plugin.hyde.expand", attempt: 1 }),
      evt("node.skipped", 3, 20, {
        node_id: "plugin.subqueries.expand",
        attempt: 1,
        attributes: { reason: "disabled" },
      }),
      evt("node.failed", 4, 30, {
        node_id: "plugin.stepback.expand",
        attempt: 1,
        attributes: { reason: "generation_failed" },
        error: { type: "GenerationError", recoverable: true },
      }),
      evt("degraded", 5, 31, {
        node_id: "plugin.stepback.expand",
        attempt: 1,
        attributes: { reason: "generation_failed" },
      }),
      evt("node.completed", 6, 40, { node_id: "graph.retrieve", attempt: 1 }),
      evt("node.failed", 7, 50, {
        node_id: "rerank",
        attempt: 1,
        attributes: { reason: "rerank_failed" },
        error: { type: "RerankError", recoverable: true },
      }),
      evt("degraded", 8, 51, {
        node_id: "rerank",
        attempt: 1,
        attributes: { reason: "rerank_failed" },
      }),
    ]);
    const knowledge = projectServerRun(detailed, page).knowledge;
    expect(knowledge.components).toEqual(
      expect.objectContaining({
        hyde: expect.objectContaining({ state: "enabled" }),
        subqueries: expect.objectContaining({ state: "skipped", reason: "disabled" }),
        stepback: expect.objectContaining({ state: "degraded", reason: "generation_failed" }),
        graph: expect.objectContaining({ state: "enabled" }),
        rerank: expect.objectContaining({ state: "degraded", reason: "rerank_failed" }),
      }),
    );
    expect(knowledge.components.sentenceWindow).toBeUndefined();
    expect(knowledge).not.toHaveProperty("subqueries", expect.any(String));
  });

  it("keeps touching intervals in one wave without counting them as concurrent", () => {
    const value = projectServerRun(
      detail(),
      events([
        evt("run.started", 1, 0, { attributes: { topology } }),
        evt("node.started", 2, 0, { node_id: "a", attempt: 1 }),
        evt("node.completed", 3, 100, { node_id: "a", attempt: 1, duration_ms: 100 }),
        evt("node.started", 4, 100, { node_id: "b", attempt: 1 }),
        evt("node.completed", 5, 200, { node_id: "b", attempt: 1, duration_ms: 100 }),
      ]),
    );
    expect(value.waves).toEqual([expect.objectContaining({ intervalCount: 2, maxConcurrency: 1 })]);
  });
  it("ignores ordinary reasons until a provable route fallback fact arrives", () => {
    const value = projectServerRun(
      detail(),
      events([
        evt("run.started", 1, 0, { attributes: { topology } }),
        evt("node.skipped", 2, 10, {
          node_id: "a",
          attempt: 1,
          attributes: { reason: "disabled" },
        }),
        evt("retry.failed", 3, 20, {
          node_id: "b",
          attempt: 1,
          attributes: { reason: "transient" },
          error: { type: "RetryError", recoverable: true },
        }),
        evt("node.failed", 4, 30, {
          node_id: "c",
          attempt: 1,
          attributes: { reason: "verification_failed" },
          error: { type: "VerifyError", recoverable: false },
        }),
        evt("route.selected", 5, 40, {
          node_id: "a",
          attempt: 1,
          attributes: {
            route: "hybrid",
            effective_route: "hybrid",
            fallback_route: "vector_graph_rag",
            reason: "graph_retrieval_failed",
          },
        }),
      ]),
    );
    expect(value.knowledge.fallbackReason).toBe("graph_retrieval_failed");
    expect(value.knowledge.fallbackReason).not.toBe("disabled");
    expect(value.knowledge.fallbackReason).not.toBe("transient");
    expect(value.knowledge.fallbackReason).not.toBe("verification_failed");
  });
});

describe("tab-scoped server projections", () => {
  it("projects flow and timeline through independent entry points", () => {
    const page = events([
      evt("run.started", 1, 0, { attributes: { topology } }),
      evt("node.started", 2, 10, { node_id: "a", attempt: 1 }),
      evt("node.completed", 3, 110, { node_id: "a", attempt: 1, duration_ms: 100 }),
      evt("run.completed", 4, 120, { attributes: { outcome: "answered" } }),
    ]);
    expect(projectServerFlow(detail(), page).topologySource).toBe("server-history");
    expect(projectRunTimeline(page.events, 1000).intervals[0]?.nodeId).toBe("a");
  });
});
it("applies a degraded event to the matching interval", () => {
  const value = projectRunTimeline([
    evt("node.started", 1, 0, { node_id: "a", attempt: 1 }),
    evt("node.failed", 2, 100, { node_id: "a", attempt: 1, duration_ms: 100 }),
    evt("degraded", 3, 101, { node_id: "a", attempt: 1, attributes: { reason: "fallback" } }),
  ], 200);
  expect(value.intervals[0]?.status).toBe("degraded");
});


describe("Task 13 safe verification knowledge", () => {
  it("projects L1/L2/L3 independently from corresponding safe node events and rollups", () => {
    const detailed = detail();
    detailed.node_rollup = [
      { node_id: "verify.l1", attempt: 1, status: "completed", started_elapsed_ms: 10, finished_elapsed_ms: 15, duration_ms: 5, degraded_reason: null, retry_reason: null, error_type: null, error_code: null },
      { node_id: "verify_l2", attempt: 2, status: "degraded", started_elapsed_ms: 16, finished_elapsed_ms: 25, duration_ms: 9, degraded_reason: "stale_evidence", retry_reason: null, error_type: null, error_code: null },
      { node_id: "verify.l3", attempt: 2, status: "skipped", started_elapsed_ms: null, finished_elapsed_ms: null, duration_ms: null, degraded_reason: null, retry_reason: "entailment_disabled", error_type: null, error_code: null },
    ];
    const value = projectServerRun(detailed, events([
      evt("run.started", 1, 0, { attributes: { topology } }),
      evt("node.completed", 2, 15, { node_id: "verify.l1", attempt: 1, duration_ms: 5, attributes: { citation_count: 3 } }),
      evt("degraded", 3, 25, { node_id: "verify_l2", attempt: 2, duration_ms: 9, attributes: { valid_count: 2, reason: "stale_evidence" } }),
      evt("node.skipped", 4, 26, { node_id: "verify.l3", attempt: 2, attributes: { successful_count: 1, reason: "entailment_disabled" } }),
    ]));
    expect(value.knowledge.verificationLayers).toEqual({
      l1: { status: "completed", attempt: 1, durationMs: 5, count: 3 },
      l2: { status: "degraded", attempt: 2, durationMs: 9, count: 2, reason: "stale_evidence" },
      l3: { status: "skipped", attempt: 2, count: 1, reason: "entailment_disabled" },
    });
  });

  it("does not use the overall verify event as L1/L2/L3 status attempt or duration", () => {
    const value = projectServerRun(detail(), events([
      evt("run.started", 1, 0, { attributes: { topology } }),
      evt("node.completed", 2, 31, { node_id: "verify", attempt: 9, duration_ms: 31, attributes: { citation_count: 2, missing_evidence: false, entailment_evaluated: true, supported: true } }),
    ]));
    expect(value.knowledge.verificationLayers).toEqual({});
    expect(value.knowledge.citationCount).toBe(2);
  });
});


describe("Route B rich retry-degraded visual fixture", () => {
  it("projects overlap, retry, route fallback, optional components, and L1-L3 facts", () => {
    const richTopology = {
      ...topology,
      nodes: [
        ["retrieve", "retrieve"],
        ["graph.retrieve", "retrieve"],
        ["rerank", "retrieve"],
        ["sentence_window", "retrieve"],
        ["verify.l1", "verify"],
        ["verify.l2", "verify"],
        ["verify.l3", "verify"],
      ].map(([id, group]) => ({
        id,
        label: id,
        group: group as "retrieve" | "verify",
        description: id,
        optional: id !== "retrieve",
        repeatable: id === "retrieve",
        available: true,
        attributes: {},
      })),
      edges: [],
    };
    const detailed = detail({
      topology: richTopology,
      summary: {
        ...detail().summary,
        last_seq: 22,
        event_count: 22,
        elapsed_ms: 10_000,
        degraded_count: 1,
        retry_count: 1,
      },
      node_rollup: [],
      history: { ...detail().history, last_seq: 22 },
    });
    const value = projectServerRun(
      detailed,
      events([
        evt("run.started", 1, 0, { attributes: { topology: richTopology } }),
        evt("node.started", 2, 500, { node_id: "retrieve", attempt: 1 }),
        evt("node.started", 3, 800, { node_id: "graph.retrieve", attempt: 1 }),
        evt("node.failed", 4, 2500, {
          node_id: "retrieve",
          attempt: 1,
          duration_ms: 2000,
          attributes: { reason: "retrieval_timeout" },
          error: { type: "SyntheticFailure", code: "retrieval_timeout", recoverable: true },
        }),
        evt("retry.started", 5, 2600, {
          node_id: "retrieve",
          attempt: 2,
          attributes: { reason: "retrieval_timeout", target_attempt: 2 },
        }),
        evt("node.failed", 6, 3000, {
          node_id: "graph.retrieve",
          attempt: 1,
          duration_ms: 2200,
          attributes: { reason: "graph_retrieval_failed" },
          error: { type: "SyntheticFailure", code: "graph_retrieval_failed", recoverable: true },
        }),
        evt("degraded", 7, 3010, {
          node_id: "graph.retrieve",
          attempt: 1,
          attributes: {
            reason: "graph_retrieval_failed",
            effective_route: "hybrid",
            fallback_route: "vector",
          },
        }),
        evt("node.started", 8, 3500, { node_id: "retrieve", attempt: 2 }),
        evt("node.completed", 9, 5200, {
          node_id: "retrieve",
          attempt: 2,
          duration_ms: 1700,
          attributes: { candidate_count: 12, chunk_count: 8, source_count: 4 },
        }),
        evt("retry.completed", 10, 5250, {
          node_id: "retrieve",
          attempt: 2,
          duration_ms: 2650,
          attributes: { reason: "retrieval_recovered" },
        }),
        evt("node.started", 11, 5300, { node_id: "rerank", attempt: 1 }),
        evt("node.completed", 12, 6300, {
          node_id: "rerank",
          attempt: 1,
          duration_ms: 1000,
          attributes: { input_count: 8, output_count: 5 },
        }),
        evt("node.started", 13, 6400, { node_id: "sentence_window", attempt: 1 }),
        evt("node.completed", 14, 7200, {
          node_id: "sentence_window",
          attempt: 1,
          duration_ms: 800,
          attributes: { input_count: 5, output_count: 5 },
        }),
        evt("node.started", 15, 7300, { node_id: "verify.l1", attempt: 1 }),
        evt("node.completed", 16, 8100, {
          node_id: "verify.l1",
          attempt: 1,
          duration_ms: 800,
          attributes: { citation_count: 5, valid_count: 5, reason: "citations_present" },
        }),
        evt("node.started", 17, 8150, { node_id: "verify.l2", attempt: 1 }),
        evt("node.completed", 18, 8950, {
          node_id: "verify.l2",
          attempt: 1,
          duration_ms: 800,
          attributes: { valid_count: 4, reason: "hash_match" },
        }),
        evt("node.started", 19, 9000, { node_id: "verify.l3", attempt: 1 }),
        evt("node.completed", 20, 9800, {
          node_id: "verify.l3",
          attempt: 1,
          duration_ms: 800,
          attributes: { successful_count: 3, supported: true, reason: "entailed" },
        }),
        evt("route.selected", 21, 9850, {
          attributes: {
            route: "vector_graph_rag",
            effective_route: "hybrid",
            fallback_route: "vector",
            reason: "graph_retrieval_failed",
          },
        }),
        evt("run.completed", 22, 10_000, { attributes: { outcome: "answered" } }),
      ]),
    );

    expect(value.waves[0]).toEqual(
      expect.objectContaining({
        startMs: 500,
        endMs: 3000,
        wallTimeMs: 2500,
        intervalCount: 2,
        maxConcurrency: 2,
      }),
    );
    expect(value.idleGaps).toContainEqual({ startMs: 3000, endMs: 3500, durationMs: 500 });
    const readableIntervals = value.intervals.filter((interval) =>
      ["retrieve", "graph.retrieve", "rerank", "sentence_window", "verify.l1", "verify.l2", "verify.l3"].includes(interval.nodeId),
    );
    expect(readableIntervals.every((interval) => (interval.durationMs ?? 0) >= 800 && (interval.durationMs ?? 0) <= 2500)).toBe(true);
    expect(value.intervals).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ nodeId: "retrieve", attempt: 1, status: "failed" }),
        expect.objectContaining({ nodeId: "retrieve", attempt: 2, status: "completed", retry: true }),
        expect.objectContaining({ nodeId: "graph.retrieve", attempt: 1, status: "degraded" }),
      ]),
    );
    expect(value.knowledge).toEqual(
      expect.objectContaining({
        effectiveRoute: "hybrid",
        fallbackReason: "graph_retrieval_failed",
        components: expect.objectContaining({
          graph: expect.objectContaining({ state: "degraded" }),
          rerank: expect.objectContaining({ state: "enabled", durationMs: 1000 }),
          sentenceWindow: expect.objectContaining({ state: "enabled", durationMs: 800 }),
        }),
        verificationLayers: {
          l1: { status: "completed", attempt: 1, durationMs: 800, count: 5, reason: "citations_present" },
          l2: { status: "completed", attempt: 1, durationMs: 800, count: 4, reason: "hash_match" },
          l3: { status: "completed", attempt: 1, durationMs: 800, count: 3, reason: "entailed" },
        },
      }),
    );
  });
});
