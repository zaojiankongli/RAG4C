// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Experiment } from "../model/contracts";
import ExperimentHistory from "./ExperimentHistory";

afterEach(cleanup);
const item: Experiment = { sequence: 10, id: "exp-1", tenant_id: "tenant-a", dataset_id: "dataset-a", query: "报销流程", query_hash: "a".repeat(64), strategy_snapshot: { variant_name: "策略 A", dataset_serving_generation: 12, route_target: "auto" }, result_snapshot: { results: [] }, evidence_lineage: { citations: [] }, latency_ms: 22, status: "completed", created_by: "judge-a", created_at: "2026-08-25T00:00:00Z", run_id: "run-1" };

describe("ExperimentHistory", () => {
  it("renders facade controls and submits supported filters", async () => {
    const user = userEvent.setup(); const onFilters = vi.fn();
    render(<ExperimentHistory items={[item]} status="ready" error={null} paging={false} hasPrevious={false} hasNext onFilters={onFilters} onRefresh={vi.fn()} onNext={vi.fn()} onPrevious={vi.fn()} onSelect={vi.fn()} />);
    expect(screen.getByRole("table", { name: "检索实验历史，每页十条" })).toBeTruthy();
    expect(document.querySelector(".rag-card")).not.toBeNull();
    expect(document.querySelector(".rag-tag.is-success")).not.toBeNull();
    expect(screen.getByRole("button", { name: "应用筛选" }).className).toContain("is-primary");
    expect(screen.getByRole("button", { name: "查看实验 exp-1" }).className).toContain("is-text");
    await user.selectOptions(screen.getByLabelText("实验状态"), "failed");
    await user.type(screen.getByLabelText("运行 ID"), "run-2");
    await user.type(screen.getByLabelText("精确查询"), "年假制度");
    await user.click(screen.getByRole("button", { name: "应用筛选" }));
    expect(onFilters).toHaveBeenCalledWith({ status: "failed", runId: "run-2", query: "年假制度" });
    expect(screen.getByRole("button", { name: "查看实验 exp-1" })).toBeTruthy();
  });

  it("keeps loading, error, empty, and keyset pagination states explicit", async () => {
    const user = userEvent.setup();
    const onNext = vi.fn();
    const onPrevious = vi.fn();
    const { rerender } = render(
      <ExperimentHistory
        items={[]}
        status="loading"
        error={null}
        paging={false}
        hasPrevious={false}
        hasNext={false}
        onFilters={vi.fn()}
        onRefresh={vi.fn()}
        onNext={onNext}
        onPrevious={onPrevious}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("正在加载实验历史…")).toBeTruthy();

    rerender(
      <ExperimentHistory
        items={[]}
        status="ready"
        error={new Error("history unavailable")}
        paging={false}
        hasPrevious
        hasNext
        onFilters={vi.fn()}
        onRefresh={vi.fn()}
        onNext={onNext}
        onPrevious={onPrevious}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("history unavailable");
    expect(screen.getByText("当前筛选没有实验记录。")).toBeTruthy();
    expect((screen.getByRole("button", { name: "上一页" }) as HTMLButtonElement).disabled).toBe(false);
    expect((screen.getByRole("button", { name: "下一页" }) as HTMLButtonElement).disabled).toBe(false);
    await user.click(screen.getByRole("button", { name: "上一页" }));
    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(onPrevious).toHaveBeenCalledOnce();
    expect(onNext).toHaveBeenCalledOnce();
  });

  it("passes the selected experiment and focus opener to the detail callback", async () => {
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
    await user.click(screen.getByRole("button", { name: "查看实验 exp-1" }));
    expect(onSelect).toHaveBeenCalledOnce();
    expect(onSelect.mock.calls[0]?.[0]).toBe(item);
    expect(onSelect.mock.calls[0]?.[1]?.current).toBeInstanceOf(HTMLButtonElement);
  });
});

