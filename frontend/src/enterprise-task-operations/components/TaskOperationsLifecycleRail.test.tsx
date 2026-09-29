// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import TaskOperationsLifecycleRail from "./TaskOperationsLifecycleRail";

afterEach(cleanup);

describe("TaskOperationsLifecycleRail", () => {
  it("preserves the four-stage trace and audit note", () => {
    render(
      <TaskOperationsLifecycleRail
        summary={{
          state: "ready",
          tenant_id: "tenant-a",
          as_of: "2026-08-29T08:05:00Z",
          queued_count: 7,
          running_count: 2,
          failed_count: 1,
          completed_count: 128,
          stale_count: 3,
          reconciliation_count: 2,
          retryable_count: 1,
        }}
      />,
    );

    const rail = screen.getByRole("list", { name: "任务执行生命周期" });
    expect(within(rail).getAllByRole("listitem")).toHaveLength(4);
    expect(within(rail).getByText("7 等待")).toBeTruthy();
    expect(within(rail).getByText("2 活跃")).toBeTruthy();
    expect(within(rail).getByText("128 完成 · 1 失败")).toBeTruthy();
    expect(screen.getByText("状态全程留痕")).toBeTruthy();
  });

  it("keeps pending semantics explicit when authority is unavailable", () => {
    render(<TaskOperationsLifecycleRail summary={null} />);

    const rail = screen.getByRole("list", { name: "任务执行生命周期" });
    expect(within(rail).getByText("未返回 等待")).toBeTruthy();
    expect(within(rail).getByText("未返回 活跃")).toBeTruthy();
    expect(within(rail).getByText("未返回 完成 · 未返回 失败")).toBeTruthy();
    expect(rail.querySelector(".task-operations__rail-stage--pending")).toBeTruthy();
  });

  it("does not advertise completed stages for partial or unavailable authority", () => {
    const { rerender } = render(
      <TaskOperationsLifecycleRail
        summary={{
          state: "partial",
          tenant_id: "tenant-a",
          as_of: null,
          queued_count: null,
          running_count: null,
          failed_count: null,
          completed_count: null,
          stale_count: null,
          reconciliation_count: null,
          retryable_count: null,
        }}
      />,
    );

    const partialRail = screen.getByRole("list", { name: "任务执行生命周期" });
    expect(partialRail.querySelectorAll(".task-operations__rail-stage--warning")).toHaveLength(3);
    expect(partialRail.querySelector(".task-operations__rail-stage--complete")).toBeTruthy();

    rerender(
      <TaskOperationsLifecycleRail
        summary={{
          state: "unavailable",
          tenant_id: "tenant-a",
          as_of: null,
          queued_count: null,
          running_count: null,
          failed_count: null,
          completed_count: null,
          stale_count: null,
          reconciliation_count: null,
          retryable_count: null,
        }}
      />,
    );
    const unavailableRail = screen.getByRole("list", { name: "任务执行生命周期" });
    expect(unavailableRail.querySelectorAll(".task-operations__rail-stage--warning")).toHaveLength(
      3,
    );
  });
});
