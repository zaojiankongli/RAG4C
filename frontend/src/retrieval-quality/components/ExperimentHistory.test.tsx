// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Experiment } from "../model/contracts";
import ExperimentHistory from "./ExperimentHistory";

afterEach(cleanup);
const item: Experiment = { sequence: 10, id: "exp-1", tenant_id: "tenant-a", dataset_id: "dataset-a", query: "报销流程", query_hash: "a".repeat(64), strategy_snapshot: { variant_name: "策略 A", dataset_serving_generation: 12, route_target: "auto" }, result_snapshot: { results: [] }, evidence_lineage: { citations: [] }, latency_ms: 22, status: "completed", created_by: "judge-a", created_at: "2026-08-25T00:00:00Z", run_id: "run-1" };

describe("ExperimentHistory", () => {
  it("renders supported filters and a semantic ten-row keyset table", async () => {
    const user = userEvent.setup(); const onFilters = vi.fn();
    render(<ExperimentHistory items={[item]} status="ready" error={null} paging={false} hasPrevious={false} hasNext onFilters={onFilters} onRefresh={vi.fn()} onNext={vi.fn()} onPrevious={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.getByRole("table", { name: "检索实验历史，每页十条" })).toBeTruthy();
    await user.click(screen.getByLabelText("实验状态")); await user.click(screen.getByText("失败"));
    await user.type(screen.getAllByLabelText("运行 ID").find((node) => node.tagName === "INPUT") as HTMLInputElement, "run-2");
    await user.type(screen.getAllByLabelText("精确查询").find((node) => node.tagName === "INPUT") as HTMLInputElement, "年假制度");
    await user.click(screen.getByRole("button", { name: "应用筛选" }));
    expect(onFilters).toHaveBeenCalledWith({ status: "failed", runId: "run-2", query: "年假制度" });
    expect(screen.getByRole("button", { name: "查看实验 exp-1" })).toBeTruthy();
  });
});

