// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { RunItem, RunResponse } from "../model/contracts";
import ComparisonResults from "./ComparisonResults";

function item(overrides: Partial<RunItem> = {}): RunItem { return { sequence: 1, id: "exp-1", tenant_id: "tenant-a", dataset_id: "dataset-a", query: "Q", query_hash: "a".repeat(64), strategy_snapshot: { strategy_revision: 1, dataset_serving_generation: 12, route_target: "auto", top_k: 8 }, result_snapshot: { route: "hybrid", reranked: true, degraded: false, traces: ["search token=secret"], results: [{ rank: 1, document_id: "doc-1", chunk_id: "chunk-1", document_revision: 4, content_revision: 8, content_hash: "b".repeat(64), score: .8, branch: "hybrid", content: "safe evidence" }] }, evidence_lineage: { evidence_revision: 1, citations: [{ rank: 1, document_id: "doc-1", chunk_id: "chunk-1", document_revision: 4, content_revision: 8, content_hash: "b".repeat(64) }] }, latency_ms: 37, status: "completed", created_by: "judge-a", created_at: "2026-08-25T00:00:00Z", run_id: "run-1", name: "策略 A", route: "hybrid", result_count: 1, reranked: true, degraded: false, ...overrides }; }

afterEach(cleanup);

describe("ComparisonResults", () => {
  it("renders the immutable generation rail and neutral aligned evidence", () => {
    const response: RunResponse = { run_id: "run-1", dataset_serving_generation: 12, items: [item(), item({ id: "exp-2", sequence: 2, name: "策略 B" })] };
    render(<ComparisonResults response={response} />);
    expect(screen.getByText("数据集服务代次 G12")).toBeTruthy();
    expect(screen.getByRole("table", { name: "证据排名对比" })).toBeTruthy();
    expect(screen.getAllByText("bbbbbbbbbbbb…")).toHaveLength(2);
    expect(document.body.textContent).not.toContain("获胜");
    expect(document.body.textContent?.toLowerCase()).not.toContain("winner");
    expect(document.body.textContent).not.toContain("token=secret");
  });

  it("shows failed and no-hit variants truthfully", () => {
    const response: RunResponse = { run_id: "run-2", dataset_serving_generation: 13, items: [item({ status: "failed", result_count: 0, result_snapshot: { failure_code: "retrieval_execution_failed", results: [] }, name: "失败策略" }), item({ id: "exp-3", result_count: 0, result_snapshot: { results: [] }, name: "空结果策略" })] };
    render(<ComparisonResults response={response} />);
    expect(screen.getAllByText("检索失败").length).toBeGreaterThan(0);
    expect(screen.getAllByText("没有召回结果").length).toBeGreaterThan(0);
  });
});


