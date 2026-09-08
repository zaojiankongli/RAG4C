// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
afterEach(cleanup);
import type { RunNodeRollupDto } from "../types/runs";
import type { RunKnowledgeProjection } from "../run/serverRunProjection";
import RunKnowledgePanel from "./RunKnowledgePanel";

function rollup(overrides: Partial<RunNodeRollupDto>): RunNodeRollupDto {
  return {
    node_id: "verify", attempt: 1, status: "completed", started_elapsed_ms: 10,
    finished_elapsed_ms: 30, duration_ms: 20, degraded_reason: null, retry_reason: null,
    error_type: null, error_code: null, ...overrides,
  };
}

describe("RunKnowledgePanel", () => {
  it("does not infer missing knowledge counts as zero", () => {
    render(<RunKnowledgePanel knowledge={{ route: "hybrid", components: {} }} nodeRollup={[]} />);
    expect(screen.getAllByText("本次运行未记录该项").length).toBeGreaterThan(0);
    expect(screen.queryByText(/^0$/)).toBeNull();
  });

  it("renders route, effective route, safe component facts, verification layers, and attempts", () => {
    const knowledge: RunKnowledgeProjection = {
      route: "vector_graph_rag", effectiveRoute: "hybrid", candidateCount: 12, chunkCount: 8,
      sourceCount: 3, citationCount: 2,
      components: {
        hyde: { state: "enabled", attempt: 1, durationMs: 11 },
        subqueries: { state: "skipped", reason: "disabled" },
        stepback: { state: "degraded", reason: "generation_failed" },
        graph: { state: "enabled" }, rerank: { state: "degraded", reason: "rerank_failed" },
        sentenceWindow: { state: "enabled" },
      },
      verification: { missingEvidence: false, entailmentEvaluated: true, supported: true, attempt: 9, durationMs: 99 },
      verificationLayers: {
        l1: { status: "completed", attempt: 1, durationMs: 11, count: 2 },
        l2: { status: "degraded", attempt: 2, count: 1, reason: "stale_evidence" },
        l3: { status: "skipped", reason: "entailment_disabled" },
      },
      degradedReason: "rerank_failed", fallbackReason: "graph_retrieval_failed",
    };
    render(<RunKnowledgePanel knowledge={knowledge} nodeRollup={[
      rollup({ attempt: 1, status: "retry_failed", error_code: "verification_failed" }),
      rollup({ attempt: 2, status: "retry_completed", duration_ms: 31 }),
    ]} />);
    expect(screen.getByText("vector_graph_rag")).not.toBeNull();
    expect(screen.getByText("hybrid")).not.toBeNull();
    for (const label of ["HyDE", "SubQueries", "Stepback", "图检索", "重排", "父段落窗口"])
      expect(screen.getByText(label)).not.toBeNull();
    for (const layer of ["L1 引用检查", "L2 文本校验", "L3 蕴含核验"]) {
      const card = screen.getByRole("article", { name: layer });
      expect(within(card).getByText("状态")).not.toBeNull();
      expect(within(card).getByText("Attempt")).not.toBeNull();
      expect(within(card).getByText("耗时")).not.toBeNull();
      expect(within(card).getByText("聚合计数")).not.toBeNull();
      expect(within(card).getByText("原因")).not.toBeNull();
    }
    expect(within(screen.getByRole("article", { name: "L1 引用检查" })).getByText("11ms")).not.toBeNull();
    expect(within(screen.getByRole("article", { name: "L2 文本校验" })).getByText("stale_evidence")).not.toBeNull();
    expect(within(screen.getByRole("article", { name: "L3 蕴含核验" })).getAllByText("本次运行未记录该项")).toHaveLength(3);
    expect(screen.queryByText("99ms")).toBeNull();
    const table = screen.getByRole("table", { name: "核验 Attempt 对比" });
    expect(within(table).getByText("Attempt 1")).not.toBeNull();
    expect(within(table).getByText("Attempt 2")).not.toBeNull();
    expect(within(table).getByText("verification_failed")).not.toBeNull();
    expect(within(table).getByText("31ms")).not.toBeNull();
  });
});
