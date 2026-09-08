import { describe, expect, it } from "vitest";
import type { BackendRunEvent, BackendRunTopology, RecentQuery } from "../types/rag";
import {
  FLOW_TOPOLOGY_SOURCE_META,
  projectCompletedLiveRun,
  projectLivePhase,
  projectRecentQuery,
  projectTerminalLiveRun,
  projectTypedLocalTerminal,
  projectTypedRunEvent,
  projectTypedRunStarted,
} from "./runProjection";

const typedTopology: BackendRunTopology = {
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
    { id: "search.finalize", source: "search", target: "finalize", kind: "dependency" },
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
    topology_id: typedTopology.id,
    topology_revision: typedTopology.revision,
    type,
    attributes: {},
    ...overrides,
  };
}

function typedStarted() {
  const projection = projectTypedRunStarted(
    "问题",
    typedEvent("run.started", 1, { attributes: { topology: typedTopology } }),
    "live_stream",
  );
  if (!projection) throw new Error("typed projection fixture must be valid");
  return projection;
}

function recent(overrides: Partial<RecentQuery> = {}): RecentQuery {
  return {
    query: "公司的报销流程是什么？",
    route: "hybrid",
    abstained: false,
    duration_ms: 1186,
    citations: 4,
    traces: [],
    ts: "2026-08-16 19:49:21",
    ...overrides,
  };
}

describe("projectRecentQuery", () => {
  it("keeps the executed trace order and merges repeated legacy steps", () => {
    const projection = projectRecentQuery(
      recent({
        traces: [
          "[gate] 判定为简单查询",
          "[router] 路由决策: target=hybrid",
          "[retrieval] 混合检索命中 6 条",
          "[retrieval] 重排后保留 top-3",
          "[generation] 生成答案",
          "[verify/L1] 引用存在性检查通过",
          "[verify/L2] 文本哈希校验通过",
          "[done] 返回 QueryResult",
        ],
      }),
    );

    expect(projection.nodes.map((node) => node.id)).toEqual([
      "run-input",
      "gate",
      "route",
      "search",
      "generate",
      "verify",
      "run-output",
    ]);
    expect(projection.nodes.find((node) => node.id === "search")?.details).toHaveLength(2);
    expect(projection.topologySource).toBe("trace-inferred");
    expect(projection.edges.every((edge) => edge.kind === "inferred")).toBe(true);
    expect(projection.edges.every((edge) => edge.status === "completed")).toBe(true);
  });

  it("renders unknown timed plugin stages as generic extension nodes", () => {
    const projection = projectRecentQuery(
      recent({
        traces: ["search:120ms", "legal_guard_plugin:33ms", "generate:240ms"],
      }),
    );

    expect(projection.nodes).toContainEqual(
      expect.objectContaining({
        id: "legal_guard_plugin",
        label: "legal guard plugin",
        group: "extension",
        durationMs: 33,
        status: "completed",
      }),
    );
  });

  it("marks the output as degraded when the run abstains", () => {
    const projection = projectRecentQuery(
      recent({
        abstained: true,
        citations: 0,
        traces: [
          "[retrieval] 混合检索完成，最高分 0.31",
          "[abstention] 检索分数低于阈值，拒绝作答",
        ],
      }),
    );

    expect(projection.status).toBe("degraded");
    expect(projection.nodes[projection.nodes.length - 1]).toEqual(
      expect.objectContaining({
        id: "run-output",
        label: "资料不足，未生成回答",
        status: "degraded",
      }),
    );
  });
});

describe("projectLivePhase", () => {
  it("returns to retrieval with a second-attempt marker", () => {
    const projection = projectLivePhase({
      query: "问题",
      phase: "retrieving_again",
      elapsedMs: 6400,
    });

    expect(projection.status).toBe("running");
    expect(projection.nodes.find((node) => node.id === "search")).toEqual(
      expect.objectContaining({ status: "active", attempt: 2 }),
    );
    expect(projection.nodes.find((node) => node.id === "generate")?.status).toBe("completed");
    expect(projection.nodes.find((node) => node.id === "verify")?.status).toBe("pending");
  });

  it("keeps retry evidence after the run advances back to generation", () => {
    const projection = projectLivePhase({
      query: "问题",
      phase: "generating",
      elapsedMs: 900,
      phaseElapsedMs: 200,
      retrievalAttempt: 2,
    });

    expect(projection.nodes.find((node) => node.id === "search")?.attempt).toBe(2);
    expect(projection.edges).toContainEqual(
      expect.objectContaining({
        kind: "retry",
        source: "generate",
        target: "search",
        status: "completed",
      }),
    );
  });
  it("keeps the coarse live topology stable when the run completes", () => {
    const projection = projectCompletedLiveRun({
      query: "问题",
      elapsedMs: 700,
      startedAt: "2026-08-22T12:00:00.000Z",
      route: "hybrid",
      chunks: 3,
      phaseDurations: { retrieving: 400, generating: 200, verifying: 100 },
      response: {
        result: {
          query: "问题",
          answer: "回答",
          citations: [],
          verdict: {},
          abstained: false,
          route: "hybrid",
          traces: ["[gate] 简单查询", "[retrieval] 命中 3 条", "[generation] 生成答案"],
        },
        using_mock: false,
        duration_ms: 700,
      },
      source: "live_stream",
    });

    expect(projection.nodes.map((node) => node.id)).toEqual([
      "run-input",
      "search",
      "generate",
      "verify",
      "run-output",
    ]);
    expect(projection.topologySource).toBe("coarse-phase");
    expect(projection.nodes.find((node) => node.id === "search")).toEqual(
      expect.objectContaining({
        durationMs: 400,
        details: expect.arrayContaining(["命中 3 个候选片段", "检索路线：hybrid"]),
      }),
    );
  });
  it("settles active and pending nodes when a run is cancelled", () => {
    const projection = projectTerminalLiveRun({
      query: "问题",
      phase: "generating",
      elapsedMs: 2100,
      status: "cancelled",
    });

    expect(projection.status).toBe("cancelled");
    expect(projection.nodes.find((node) => node.id === "generate")?.status).toBe("cancelled");
    expect(projection.nodes.find((node) => node.id === "verify")?.status).toBe("skipped");
    expect(projection.nodes[projection.nodes.length - 1]).toEqual(
      expect.objectContaining({ label: "运行已取消", status: "cancelled" }),
    );
  });
});

describe("projectTypedRunEvent", () => {
  it("marks the typed output node degraded and records a normalized abstention reason", () => {
    const gated = projectTypedRunEvent(
      typedStarted(),
      typedEvent("node.completed", 2, {
        node_id: "search",
        attributes: { abstained: true, reason: "no_retrieval_results" },
      }),
    );
    if (!gated) throw new Error("typed abstention gate fixture must be valid");

    const projection = projectTypedRunEvent(
      gated,
      typedEvent("run.completed", 3, { attributes: { outcome: "abstained" } }),
    );

    expect(projection).toEqual(expect.objectContaining({ status: "degraded" }));
    expect(projection?.nodes.find((node) => node.id === "finalize")).toEqual(
      expect.objectContaining({
        status: "degraded",
        details: expect.arrayContaining(["reason: no_retrieval_results"]),
      }),
    );
  });

  it("projects safe backend failure fields onto the failed node without raw messages", () => {
    const active = projectTypedRunEvent(
      typedStarted(),
      typedEvent("node.started", 2, { node_id: "search", attempt: 1 }),
    );
    if (!active) throw new Error("typed node start fixture must be valid");

    const nodeFailed = projectTypedRunEvent(
      active,
      typedEvent("node.failed", 3, {
        node_id: "search",
        attributes: { reason: "retrieval_failed" },
        error: { type: "RetrieverError", code: "retrieval_failed", recoverable: false },
      }),
    );
    if (!nodeFailed) throw new Error("typed node failure fixture must be valid");

    const projection = projectTypedRunEvent(
      nodeFailed,
      typedEvent("run.failed", 4, {
        attributes: {
          reason: "stream_error",
          message: "private prompt contents",
        },
        error: { type: "RuntimeError", code: "stream_error", recoverable: false },
      }),
    );
    const failedNode = projection?.nodes.find((node) => node.id === "search");

    expect(projection).toEqual(expect.objectContaining({ status: "failed" }));
    expect(failedNode).toEqual(
      expect.objectContaining({
        status: "failed",
        details: expect.arrayContaining([
          "reason: stream_error",
          "error category: RuntimeError",
          "error code: stream_error",
          "recoverable: no",
        ]),
      }),
    );
    expect(failedNode?.details.join(" ")).not.toContain("retryable");
    expect(failedNode?.details.join(" ")).not.toContain("private prompt contents");
  });

  it("marks the output node degraded when typed state is settled locally after abstention", () => {
    const projection = projectTypedLocalTerminal(typedStarted(), "degraded", 450, "live_stream");

    expect(projection).toEqual(
      expect.objectContaining({ status: "degraded", topologySource: "typed-events" }),
    );
    expect(projection.nodes.find((node) => node.id === "finalize")).toEqual(
      expect.objectContaining({ status: "degraded" }),
    );
  });

  it("does not project raw or unbounded reason strings from event attributes", () => {
    const rejectedReasons = ["private prompt contents must not render", "a".repeat(65)];

    for (const reason of rejectedReasons) {
      const projection = projectTypedRunEvent(
        typedStarted(),
        typedEvent("node.completed", 2, {
          node_id: "search",
          attributes: { reason },
        }),
      );

      expect(
        projection?.nodes.find((node) => node.id === "search")?.details.join(" "),
      ).not.toContain(reason);
    }
  });
});

describe("FLOW_TOPOLOGY_SOURCE_META", () => {
  it("distinguishes backend typed topology from coarse phases and inferred traces", () => {
    expect(FLOW_TOPOLOGY_SOURCE_META["typed-events"]).toEqual(
      expect.objectContaining({ label: "后端拓扑" }),
    );
    expect(FLOW_TOPOLOGY_SOURCE_META["coarse-phase"].label).toBe("阶段视图");
    expect(FLOW_TOPOLOGY_SOURCE_META["trace-inferred"].label).toBe("追踪推断");
    expect(FLOW_TOPOLOGY_SOURCE_META["typed-events"].tooltip).toContain("后端");
    expect(new Set(Object.values(FLOW_TOPOLOGY_SOURCE_META).map((item) => item.label)).size).toBe(
      4,
    );
    expect(FLOW_TOPOLOGY_SOURCE_META["server-history"]).toEqual(
      expect.objectContaining({ label: "后端权威历史", color: "geekblue" }),
    );
  });
});
