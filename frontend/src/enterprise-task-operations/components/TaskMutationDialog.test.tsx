// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaskCancelDialog, TaskRetryDialog } from "./TaskMutationDialog";

const task = {
  id: "task-mutation-001",
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
  cancellable: true,
  revision: 4,
  mutation_generation: 2,
  safe_snapshot: { shard_count: 12, source_revision: 18 },
} as const;

afterEach(cleanup);

describe("TaskMutationDialog", () => {
  it("requires explicit confirmation before submitting retry", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();

    render(
      <TaskRetryDialog
        visible
        task={task as never}
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "重试任务" });
    const submit = screen.getByRole("button", { name: "确认重试" });
    expect(submit.hasAttribute("disabled")).toBe(true);

    await user.click(screen.getByRole("checkbox", { name: "我确认重新执行此任务" }));
    expect(submit.hasAttribute("disabled")).toBe(false);
    await user.click(submit);
    expect(onSubmit).toHaveBeenCalledWith(task);
    expect(dialog.getAttribute("aria-modal")).toBe("true");
  });

  it("keeps cancel confirmation and read-only/saving fences explicit", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const { rerender } = render(
      <TaskCancelDialog
        visible
        task={task as never}
        readOnly
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByText("只读模式下不允许执行此操作")).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "我确认取消此任务" }).hasAttribute("disabled")).toBe(
      true,
    );
    expect(screen.getByRole("button", { name: "确认取消" }).hasAttribute("disabled")).toBe(true);

    rerender(
      <TaskCancelDialog
        visible
        task={task as never}
        saving
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    expect(screen.getByRole("checkbox", { name: "我确认取消此任务" }).hasAttribute("disabled")).toBe(
      true,
    );
    expect(screen.getByRole("button", { name: "确认取消" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "取消" }).hasAttribute("disabled")).toBe(true);
    expect(screen.queryByRole("button", { name: "关闭对话框" })).toBeNull();
    await user.keyboard("{Escape}");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("resets confirmation when the dialog is hidden or task changes", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const { rerender } = render(
      <TaskRetryDialog
        visible
        task={task as never}
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    );

    await user.click(screen.getByRole("checkbox", { name: "我确认重新执行此任务" }));
    rerender(
      <TaskRetryDialog
        visible
        task={{ ...task, id: "task-mutation-002" } as never}
        onClose={vi.fn()}
        onSubmit={onSubmit}
      />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "确认重试" }).hasAttribute("disabled")).toBe(true),
    );
  });
});
