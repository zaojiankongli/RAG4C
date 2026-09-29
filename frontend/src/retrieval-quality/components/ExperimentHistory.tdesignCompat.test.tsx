// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Experiment } from "../model/contracts";

vi.mock("../../ui/rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: (component: string) =>
      ["button", "card", "input", "select", "tag"].includes(component),
  },
}));

import ExperimentHistory from "./ExperimentHistory";

afterEach(cleanup);

const item: Experiment = {
  sequence: 10,
  id: "exp-tdesign",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  query: "报销流程",
  query_hash: "a".repeat(64),
  strategy_snapshot: { variant_name: "策略 A", dataset_serving_generation: 12, route_target: "auto" },
  result_snapshot: { results: [] },
  evidence_lineage: { citations: [] },
  latency_ms: 22,
  status: "completed",
  created_by: "judge-a",
  created_at: "2026-08-25T00:00:00Z",
  run_id: "run-1",
};

describe("ExperimentHistory TDesign facade compatibility", () => {
  it("keeps text fields accessible and emits actual strings through the TDesign branch", async () => {
    const user = userEvent.setup();
    const onFilters = vi.fn();
    render(
      <ExperimentHistory
        items={[item]}
        status="ready"
        error={null}
        paging={false}
        hasPrevious={false}
        hasNext={false}
        onFilters={onFilters}
        onRefresh={vi.fn()}
        onNext={vi.fn()}
        onPrevious={vi.fn()}
        onSelect={vi.fn()}
      />,
    );

    await user.type(screen.getByRole("textbox", { name: "运行 ID" }), "run-tdesign");
    await user.type(screen.getByRole("textbox", { name: "精确查询" }), "年假制度");
    await user.click(screen.getByRole("button", { name: "应用筛选" }));

    expect(onFilters).toHaveBeenCalledWith({
      runId: "run-tdesign",
      query: "年假制度",
      status: undefined,
    });
  });

  it("keeps the TDesign detail opener focusable", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <ExperimentHistory
        items={[item]}
        status="ready"
        error={null}
        paging={false}
        hasPrevious={false}
        hasNext={false}
        onFilters={vi.fn()}
        onRefresh={vi.fn()}
        onNext={vi.fn()}
        onPrevious={vi.fn()}
        onSelect={onSelect}
      />,
    );

    await user.click(screen.getByRole("button", { name: "查看实验 exp-tdesign" }));
    expect(onSelect.mock.calls[0]?.[1]?.current).toBeInstanceOf(HTMLButtonElement);
    expect(onSelect.mock.calls[0]?.[1]?.current?.disabled).toBe(false);
  });
});
