// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { TaskStateNotice } from "./taskOperationsUi";

afterEach(cleanup);

describe("TaskStateNotice", () => {
  it("keeps loading, unavailable, error, partial, and empty authority copy explicit", () => {
    const { rerender } = render(
      <TaskStateNotice status="loading" resourceLabel="任务记录" />,
    );
    expect(screen.getByText("正在读取任务记录…")).toBeTruthy();

    rerender(<TaskStateNotice status="unavailable" resourceLabel="任务记录" />);
    expect(screen.getByRole("alert").textContent).toContain("暂不可用");

    rerender(<TaskStateNotice status="error" resourceLabel="任务记录" />);
    expect(screen.getByRole("alert").textContent).toContain("读取失败");

    rerender(
      <TaskStateNotice status="partial" resourceLabel="任务记录" invalidItemCount={3} />,
    );
    expect(screen.getByRole("alert").textContent).toContain("3 条记录");

    rerender(
      <TaskStateNotice
        status="empty"
        resourceLabel="任务记录"
        emptyTitle="暂无任务记录"
        emptyDescription="暂无可验证任务。"
      />,
    );
    expect(screen.getByText("暂无任务记录")).toBeTruthy();
    expect(screen.getByText("暂无可验证任务。")).toBeTruthy();
  });
});
