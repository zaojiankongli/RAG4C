// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import TaskOperationsHeader from "./TaskOperationsHeader";

afterEach(cleanup);

describe("TaskOperationsHeader", () => {
  it("preserves the refresh callback, settings label, authority date, and writable tag", () => {
    const onRefresh = vi.fn();
    render(
      <TaskOperationsHeader
        tenantLabel="RAG4C 企业"
        asOf="2026-08-29T08:05:00Z"
        onRefresh={onRefresh}
      />,
    );

    expect(screen.getByRole("heading", { name: "任务运营中心" })).toBeTruthy();
    expect(screen.getByText("RAG4C 企业")).toBeTruthy();
    expect(screen.getByText(/权威时间/)).toBeTruthy();
    expect(screen.getByText("操作权威已连接")).toBeTruthy();
    expect(screen.getByRole("button", { name: "任务运营设置" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("preserves the read-only tag and omits refresh when no callback is supplied", () => {
    render(<TaskOperationsHeader readOnly />);

    expect(screen.getByText("只读模式")).toBeTruthy();
    expect(screen.getByText("只读")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "刷新状态" })).toBeNull();
  });
});
