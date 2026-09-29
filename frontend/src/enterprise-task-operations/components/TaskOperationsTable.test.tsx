// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import TaskOperationsTable from "./TaskOperationsTable";

const operation = {
  id: "task-table-001",
  tenant_id: "tenant-001",
  task_label: "重建产品知识库索引",
  task_type: "index_rebuild",
  source_label: "产品知识库",
  source_kind: "dataset",
  queue_name: "priority-indexing",
  status: "failed",
  attempt_number: 2,
  max_attempts: 3,
  progress: 48,
  created_at: "2026-08-29T08:00:00Z",
  started_at: "2026-08-29T08:01:00Z",
  finished_at: "2026-08-29T08:04:00Z",
  next_retry_at: "2026-08-29T08:10:00Z",
  owner_label: "平台自动化",
  duration_ms: 180000,
  error_code: "INDEX_TIMEOUT",
  safe_error: "索引服务超时，任务可安全重试",
  retryable: true,
  cancellable: false,
  revision: 4,
  mutation_generation: 2,
  safe_snapshot: { shard_count: 12, source_revision: 18 },
} as const;

afterEach(cleanup);

describe("TaskOperationsTable", () => {
  it("renders the mobile task card and keeps verified retry/detail actions", async () => {
    const user = userEvent.setup();
    const onOpenDetail = vi.fn();
    const onRetry = vi.fn();

    render(
      <TaskOperationsTable
        operations={[operation]}
        mobile
        onOpenDetail={onOpenDetail}
        onRetry={onRetry}
      />,
    );

    expect(screen.getByTestId("task-operations-mobile-cards")).toBeTruthy();
    expect(screen.getByText("重建产品知识库索引")).toBeTruthy();
    expect(screen.getAllByText("失败")).toHaveLength(2);
    expect(screen.getByText("索引服务超时，任务可安全重试")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "查看 重建产品知识库索引 详情" }));
    await user.click(screen.getByRole("button", { name: "重试 重建产品知识库索引" }));

    expect(onOpenDetail).toHaveBeenCalledWith(operation);
    expect(onRetry).toHaveBeenCalledWith(operation);
  });

  it("keeps retry and cancel controls fail-closed in read-only mode", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const onCancel = vi.fn();
    const cancellable = { ...operation, cancellable: true } as const;

    render(
      <TaskOperationsTable
        operations={[cancellable]}
        mobile
        readOnly
        onRetry={onRetry}
        onCancel={onCancel}
      />,
    );

    const retry = screen.getByRole("button", { name: "重试 重建产品知识库索引" });
    const cancel = screen.getByRole("button", { name: "取消 重建产品知识库索引" });
    expect(retry.getAttribute("aria-disabled")).toBe("true");
    expect(cancel.getAttribute("aria-disabled")).toBe("true");

    await user.click(retry);
    await user.click(cancel);

    expect(onRetry).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("renders one desktop table surface with facts, actions, and empty/loading contracts", async () => {
    const user = userEvent.setup();
    const onOpenDetail = vi.fn();

    const { rerender } = render(
      <TaskOperationsTable operations={[operation]} onOpenDetail={onOpenDetail} />,
    );

    expect(screen.getByTestId("task-operations-desktop-table")).toBeTruthy();
    const scroll = screen.getByLabelText("任务表格，可横向滚动");
    expect(scroll.classList.contains("rag-table-scroll")).toBe(true);
    expect(scroll.getAttribute("tabindex")).toBe("0");
    const table = within(scroll).getByRole("table");
    expect(table.classList.contains("is-small")).toBe(true);
    expect(table.classList.contains("is-align-top")).toBe(true);
    expect((table as HTMLTableElement).style.minWidth).toBe("900px");
    expect(screen.getByRole("columnheader", { name: "任务与来源" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "状态" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "队列 / 尝试" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: /priority-indexing/ })).toBeTruthy();
    expect(screen.getByRole("cell", { name: /2\s*\/\s*3/ })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "查看 重建产品知识库索引 详情" }));
    expect(onOpenDetail).toHaveBeenCalledWith(operation);

    rerender(<TaskOperationsTable operations={[]} loading />);
    expect(screen.getByText("正在读取任务状态…")).toBeTruthy();

    rerender(<TaskOperationsTable operations={[operation]} loading />);
    expect(screen.getByText("加载中…")).toBeTruthy();
    expect(screen.getByLabelText("任务表格，可横向滚动")).toBeTruthy();

    rerender(<TaskOperationsTable operations={[]} />);
    expect(screen.getByText("暂无任务记录")).toBeTruthy();
  });

  it("keeps focus on the real overflow container instead of the outer shell", () => {
    render(<TaskOperationsTable operations={[operation]} />);

    const shell = screen.getByTestId("task-operations-desktop-table");
    const scroll = screen.getByLabelText("任务表格，可横向滚动");

    expect(shell.getAttribute("tabindex")).toBeNull();
    scroll.focus();
    expect(document.activeElement).toBe(scroll);
  });

  it("moves the actual horizontal scroll owner with ArrowLeft and ArrowRight", () => {
    render(<TaskOperationsTable operations={[operation]} />);

    const scroll = screen.getByLabelText("任务表格，可横向滚动");
    scroll.scrollLeft = 100;
    fireEvent.keyDown(scroll, { key: "ArrowRight" });
    expect(scroll.scrollLeft).toBe(196);
    fireEvent.keyDown(scroll, { key: "ArrowLeft" });
    expect(scroll.scrollLeft).toBe(100);
  });
});
