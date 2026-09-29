// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ReconciliationPanel from "./ReconciliationPanel";

const item = {
  id: "reconcile-001",
  task_id: "task-001",
  severity: "high",
  status: "open",
  category: "attempt_gap",
  summary: "Worker 回报与任务状态存在版本差异",
  detected_at: "2026-08-29T08:04:30Z",
  action_required: true,
} as const;

function makeController(overrides: Record<string, unknown> = {}) {
  return {
    status: "ready",
    items: [item],
    invalidItemCount: 0,
    nextCursor: null,
    error: null,
    onResolve: vi.fn(),
    ...overrides,
  };
}

afterEach(cleanup);

describe("ReconciliationPanel", () => {
  it("preserves the panel facts and verified resolve action", async () => {
    const user = userEvent.setup();
    const controller = makeController();

    render(<ReconciliationPanel controller={controller as never} />);

    expect(screen.getByRole("region", { name: "Reconciliation" })).toBeTruthy();
    expect(screen.getByText("high")).toBeTruthy();
    expect(screen.getByText("reconcile-001")).toBeTruthy();
    expect(screen.getByText("Worker 回报与任务状态存在版本差异")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "处理 reconcile-001" }));
    expect(controller.onResolve).toHaveBeenCalledWith(item);
  });

  it("keeps resolve mutation disabled in read-only mode", async () => {
    const user = userEvent.setup();
    const controller = makeController();

    render(<ReconciliationPanel controller={controller as never} readOnly />);

    const resolve = screen.getByLabelText("处理 reconcile-001");
    expect(resolve.hasAttribute("disabled") || resolve.getAttribute("aria-disabled") === "true").toBe(
      true,
    );
    await user.click(resolve);
    expect(controller.onResolve).not.toHaveBeenCalled();
  });

  it("keeps unavailable, partial, error, and empty authority states explicit", () => {
    const { rerender } = render(
      <ReconciliationPanel controller={makeController({ status: "unavailable", items: [] }) as never} />,
    );
    expect(screen.getByRole("alert").textContent).toContain("暂不可用");
    expect(screen.getByText("open 未返回")).toBeTruthy();

    rerender(
      <ReconciliationPanel
        controller={makeController({ status: "partial", items: [item] }) as never}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("部分");
    expect(screen.getByText("open 未返回")).toBeTruthy();

    rerender(
      <ReconciliationPanel controller={makeController({ status: "error", items: [] }) as never} />,
    );
    expect(screen.getByRole("alert").textContent).toContain("读取失败");
    expect(screen.queryByText("暂无对账事项")).toBeNull();

    rerender(
      <ReconciliationPanel controller={makeController({ status: "empty", items: [] }) as never} />,
    );
    expect(screen.getByText("暂无对账事项")).toBeTruthy();
    expect(screen.getByText("运行状态一致，暂不需要人工介入。")).toBeTruthy();
  });

  it("counts only verified open items", () => {
    render(
      <ReconciliationPanel
        controller={
          makeController({
            items: [
              item,
              { ...item, id: "reconcile-resolved", status: "resolved", action_required: false },
            ],
          }) as never
        }
      />,
    );

    expect(screen.getByText("1 open")).toBeTruthy();
    expect(screen.queryByText("2 open")).toBeNull();
  });
});
