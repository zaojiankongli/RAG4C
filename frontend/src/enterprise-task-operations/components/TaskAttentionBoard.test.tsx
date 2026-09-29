// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import TaskAttentionBoard from "./TaskAttentionBoard";

afterEach(cleanup);

describe("TaskAttentionBoard", () => {
  it("preserves tenant badge, metric labels, counts, and hints", () => {
    render(
      <TaskAttentionBoard
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

    expect(screen.getByRole("region", { name: "任务运营关注面板" })).toBeTruthy();
    expect(screen.getByText("租户范围内")).toBeTruthy();
    expect(screen.getByText("失败待处理")).toBeTruthy();
    expect(screen.getByText("正在运行")).toBeTruthy();
    expect(screen.getByText("队列等待")).toBeTruthy();
    expect(screen.getByText("已完成")).toBeTruthy();
    expect(screen.getByText("128")).toBeTruthy();
    expect(screen.getByText("权威结果已落库")).toBeTruthy();
  });

  it("does not invent metric zeroes when summary is unavailable", () => {
    render(<TaskAttentionBoard summary={null} />);

    expect(screen.getAllByText("未返回")).toHaveLength(6);
  });
});
