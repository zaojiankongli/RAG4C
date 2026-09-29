// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RunItem, RunResponse } from "../model/contracts";

vi.mock("../../ui/rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: (component: string) =>
      ["alert", "button", "card", "collapse", "input-number", "tag"].includes(component),
  },
}));

import ComparisonResults from "./ComparisonResults";

afterEach(cleanup);

function item(overrides: Partial<RunItem> = {}): RunItem {
  return {
    sequence: 1,
    id: "exp-tdesign",
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    query: "Q",
    query_hash: "a".repeat(64),
    strategy_snapshot: {
      strategy_revision: 1,
      dataset_serving_generation: 12,
      route_target: "auto",
      top_k: 8,
    },
    result_snapshot: {
      route: "hybrid",
      reranked: true,
      degraded: false,
      failure_code: "retrieval_execution_failed",
      traces: [],
      results: [],
    },
    evidence_lineage: {
      evidence_revision: 1,
      citations: [
        {
          rank: 1,
          document_id: "doc-1",
          chunk_id: "chunk-1",
          document_revision: 4,
          content_revision: 8,
          content_hash: "b".repeat(64),
        },
      ],
    },
    latency_ms: 37,
    status: "failed",
    created_by: "judge-a",
    created_at: "2026-08-25T00:00:00Z",
    run_id: "run-tdesign",
    name: "失败策略",
    route: "hybrid",
    result_count: 1,
    reranked: true,
    degraded: true,
    ...overrides,
  };
}

describe("ComparisonResults TDesign facade compatibility", () => {
  it("keeps status Card, Tag, and Alert semantics in the TDesign renderer", () => {
    const response: RunResponse = {
      run_id: "run-tdesign",
      dataset_serving_generation: 12,
      items: [item()],
    };
    render(<ComparisonResults response={response} />);

    expect(document.querySelector(".t-card")).not.toBeNull();
    expect(document.querySelector(".t-tag--danger")).not.toBeNull();
    expect(document.querySelector(".t-tag--light")).not.toBeNull();
    expect(document.querySelector(".t-alert--error")).not.toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("检索失败");
    expect(screen.getByRole("table", { name: "证据排名对比" })).toBeTruthy();
  });

  it("keeps sanitized trace content behind the TDesign Collapse trigger", async () => {
    const user = userEvent.setup();
    const response: RunResponse = {
      run_id: "run-trace",
      dataset_serving_generation: 12,
      items: [
        item({
          id: "exp-trace",
          status: "completed",
          result_snapshot: {
            traces: ["search token=hidden-token"],
            results: [],
          },
        }),
      ],
    };
    render(<ComparisonResults response={response} />);

    expect(screen.queryByText(/token=hidden-token/)).toBeNull();
    const trigger = screen.getByRole("button", { name: /安全执行 Trace/ });
    await user.click(trigger);
    expect(screen.getByText(/token=\[已隐藏\]/)).toBeTruthy();
    expect(screen.queryByText(/token=hidden-token/)).toBeNull();
  });
});
