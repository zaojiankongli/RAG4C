// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import SavedViewsPanel from "./SavedViewsPanel";

const savedViews = [
  { id: "view-001", label: "失败任务 · 需要处理", filter: "failed", pinned: true },
  { id: "view-002", label: "运行中的同步", filter: "running", pinned: false },
] as const;

function makeController(overrides: Record<string, unknown> = {}) {
  return {
    status: "ready",
    items: savedViews,
    activeId: "view-001",
    error: null,
    onSelect: vi.fn(),
    ...overrides,
  };
}

afterEach(cleanup);

describe("SavedViewsPanel", () => {
  it("preserves active/pinned state and invokes the verified selection callback", async () => {
    const user = userEvent.setup();
    const controller = makeController();

    render(<SavedViewsPanel controller={controller as never} />);

    expect(screen.getByRole("heading", { name: "Saved Views" })).toBeTruthy();
    expect(screen.getByText("失败任务 · 需要处理")).toBeTruthy();
    expect(screen.getByText("PINNED")).toBeTruthy();
    expect(screen.getByText("失败任务 · 需要处理").closest(".is-active")).not.toBeNull();
    expect(
      screen.getByRole("button", { name: "打开视图 失败任务 · 需要处理" }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen.getByRole("button", { name: "打开视图 运行中的同步" }).getAttribute("aria-pressed"),
    ).toBe("false");

    await user.click(screen.getByRole("button", { name: "打开视图 失败任务 · 需要处理" }));
    expect(controller.onSelect).toHaveBeenCalledWith(savedViews[0]);
  });

  it("keeps read-only copy explicit without blocking view selection", async () => {
    const user = userEvent.setup();
    const controller = makeController();

    render(<SavedViewsPanel controller={controller as never} readOnly />);

    expect(screen.getByText("只读模式下不能保存或删除视图。")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "打开视图 运行中的同步" }));
    expect(controller.onSelect).toHaveBeenCalledWith(savedViews[1]);
  });

  it("keeps unavailable, partial, error, loading, and empty authority states explicit", () => {
    const { rerender } = render(
      <SavedViewsPanel controller={makeController({ status: "unavailable", items: [] }) as never} />,
    );
    expect(screen.getByRole("alert").textContent).toContain("暂不可用");
    expect(screen.getByText("未返回")).toBeTruthy();

    rerender(
      <SavedViewsPanel controller={makeController({ status: "partial", items: [] }) as never} />,
    );
    expect(screen.getByRole("alert").textContent).toContain("部分");
    expect(screen.getByText("未返回")).toBeTruthy();

    rerender(
      <SavedViewsPanel controller={makeController({ status: "error", items: [] }) as never} />,
    );
    expect(screen.getByRole("alert").textContent).toContain("读取失败");
    expect(screen.getByText("未返回")).toBeTruthy();

    rerender(
      <SavedViewsPanel controller={makeController({ status: "loading", items: [] }) as never} />,
    );
    expect(screen.getByText("未返回")).toBeTruthy();

    rerender(
      <SavedViewsPanel controller={makeController({ status: "empty", items: [] }) as never} />,
    );
    expect(screen.getByText("暂无保存视图")).toBeTruthy();
    expect(screen.getByText("把常用的运行筛选固定到这里。")).toBeTruthy();
  });
});
