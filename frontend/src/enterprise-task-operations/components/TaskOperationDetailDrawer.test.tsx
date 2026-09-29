// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import TaskOperationDetailDrawer from "./TaskOperationDetailDrawer";
import type { TaskOperationDetail } from "./taskOperationsTypes";

const task = {
  id: "task-detail-001",
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
  action_required: true,
  revision: 4,
  mutation_generation: 2,
  safe_snapshot: { shard_count: 12, source_revision: 18, token: "do-not-render" },
} as const;

const detail: TaskOperationDetail = {
  ...task,
  events: [
    {
      id: "event-001",
      sequence: 1,
      event_type: "enqueued",
      occurred_at: "2026-08-29T08:00:00Z",
      actor_id: "account-001",
      request_id: "request-001",
      event_digest: "a".repeat(64),
      previous_event_digest: null,
      safe_snapshot: { queue_name: "priority-indexing" },
    },
  ],
};

afterEach(cleanup);

describe("TaskOperationDetailDrawer", () => {
  it("keeps loading, unavailable, error, and empty authority states explicit", () => {
    const { rerender } = render(
      <TaskOperationDetailDrawer
        visible
        state={{ status: "loading", value: null, error: null }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("正在读取任务事件链…")).toBeTruthy();

    rerender(
      <TaskOperationDetailDrawer
        visible
        state={{ status: "unavailable", value: null, error: null }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("暂不可用");

    rerender(
      <TaskOperationDetailDrawer
        visible
        state={{ status: "error", value: null, error: "read failed" }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("读取失败");

    rerender(
      <TaskOperationDetailDrawer
        visible
        state={{ status: "ready", value: { ...detail, events: [] }, error: null }}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByText("暂无事件")).toBeTruthy();
  });

  it("preserves safe facts, events, and verified actions", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onRetry = vi.fn();
    const onCancel = vi.fn();
    const onAcknowledge = vi.fn();
    const onHandoff = vi.fn();

    render(
      <TaskOperationDetailDrawer
        visible
        state={{ status: "ready", value: detail, error: null }}
        onClose={onClose}
        onRetry={onRetry}
        onCancel={onCancel}
        onAcknowledge={onAcknowledge}
        onHandoff={onHandoff}
      />,
    );

    expect(screen.getByRole("dialog", { name: "任务详情与事件链" })).toBeTruthy();
    expect(screen.getByText("重建产品知识库索引")).toBeTruthy();
    expect(screen.getByText("Body-free snapshot")).toBeTruthy();
    expect(screen.getByText("digest " + "a".repeat(64))).toBeTruthy();
    expect(screen.queryByText("do-not-render")).toBeNull();

    await user.click(screen.getByRole("button", { name: "查看任务来源" }));
    await user.click(screen.getByRole("button", { name: "确认已知悉" }));
    await user.click(screen.getByRole("button", { name: "重试" }));
    await user.click(screen.getByRole("button", { name: "取消任务" }));
    await user.click(screen.getByRole("button", { name: "关闭任务详情" }));

    expect(onHandoff).toHaveBeenCalledWith(expect.objectContaining({ id: task.id }));
    expect(onAcknowledge).toHaveBeenCalledWith(expect.objectContaining({ id: task.id }));
    expect(onRetry).toHaveBeenCalledWith(expect.objectContaining({ id: task.id }));
    expect(onCancel).toHaveBeenCalledWith(expect.objectContaining({ id: task.id }));
    expect(onClose).toHaveBeenCalled();
  });

  it("keeps destructive detail actions disabled in read-only mode and returns focus", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);
    trigger.focus();

    render(
      <TaskOperationDetailDrawer
        visible
        state={{ status: "ready", value: detail, error: null }}
        readOnly
        onClose={onClose}
        onRetry={vi.fn()}
        onCancel={vi.fn()}
        onAcknowledge={vi.fn()}
        returnFocusRef={{ current: trigger }}
      />,
    );

    expect(screen.getByRole("button", { name: "重试" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "取消任务" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "确认已知悉" }).hasAttribute("disabled")).toBe(true);

    await user.click(screen.getByRole("button", { name: "关闭任务详情" }));
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(onClose).toHaveBeenCalled();
    trigger.remove();
  });
});
