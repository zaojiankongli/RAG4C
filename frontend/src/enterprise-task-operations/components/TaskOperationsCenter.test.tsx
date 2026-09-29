// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import TaskOperationsCenter from "./TaskOperationsCenter";
import type { TaskMutationController } from "./taskOperationsTypes";

const operation = {
  id: "task-001",
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

const runningOperation = {
  ...operation,
  id: "task-002",
  task_label: "同步客户服务手册",
  status: "running",
  attempt_number: 1,
  progress: 64,
  error_code: null,
  safe_error: null,
  retryable: false,
  cancellable: true,
} as const;

const summary = {
  state: "ready",
  tenant_id: "tenant-001",
  as_of: "2026-08-29T08:05:00Z",
  queued_count: 7,
  running_count: 2,
  failed_count: 1,
  completed_count: 128,
  stale_count: 3,
  reconciliation_count: 2,
  retryable_count: 1,
} as const;

const detail = {
  ...operation,
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
    {
      id: "event-002",
      sequence: 2,
      event_type: "attempt_failed",
      occurred_at: "2026-08-29T08:04:00Z",
      actor_id: "worker-001",
      request_id: "request-002",
      event_digest: "b".repeat(64),
      previous_event_digest: "a".repeat(64),
      safe_snapshot: { error_code: "INDEX_TIMEOUT" },
    },
  ],
} as const;

const savedViews = [
  { id: "view-001", label: "失败任务 · 需要处理", filter: "failed", pinned: true },
  { id: "view-002", label: "运行中的同步", filter: "running", pinned: false },
] as const;

const reconciliation = [
  {
    id: "reconcile-001",
    task_id: "task-001",
    severity: "high",
    status: "open",
    category: "attempt_gap",
    summary: "Worker 回报与任务状态存在版本差异",
    detected_at: "2026-08-29T08:04:30Z",
    action_required: true,
  },
] as const;

function makeController(overrides: Record<string, unknown> = {}) {
  return {
    active: true,
    summary: { status: "ready", value: summary, error: null },
    operations: {
      status: "ready",
      items: [operation, runningOperation],
      invalidItemCount: 0,
      nextCursor: null,
      error: null,
    },
    detail: { status: "ready", value: detail, error: null },
    activity: {
      status: "ready",
      items: detail.events,
      invalidItemCount: 0,
      nextCursor: null,
      error: null,
    },
    savedViews: {
      status: "ready",
      items: savedViews,
      activeId: "view-001",
      error: null,
      onSelect: vi.fn(),
      onSave: vi.fn(),
      onDelete: vi.fn(),
    },
    reconciliation: {
      status: "ready",
      items: reconciliation,
      invalidItemCount: 0,
      error: null,
      onResolve: vi.fn(),
    },
    mutation: {
      status: "idle",
      error: null,
      retry: vi.fn().mockResolvedValue({ state: "applied" }),
      cancel: vi.fn().mockResolvedValue({ state: "applied" }),
      reconcile: vi.fn().mockResolvedValue({ state: "applied" }),
    },
    ...overrides,
  };
}

afterEach(cleanup);

describe("Stage 24 TaskOperationsCenter", () => {
  it("expands the attention board and SOURCE-to-OUTCOME rail on demand", async () => {
    const user = userEvent.setup();
    render(
      <TaskOperationsCenter controller={makeController() as never} tenantLabel="RAG4C 企业" />,
    );

    expect(screen.getByRole("heading", { name: "任务运营中心" })).toBeTruthy();
    expect(screen.getByText(/RAG4C 企业/)).toBeTruthy();
    await user.click(screen.getByText("运行摘要与生命周期", { exact: true }));
    expect(screen.getByRole("region", { name: "任务运营关注面板" })).toBeTruthy();
    expect(screen.getByText("失败待处理")).toBeTruthy();
    expect(screen.getByText("状态全程留痕", { exact: true })).toBeTruthy();
    expect(screen.queryByText("No hidden transition", { exact: true })).toBeNull();

    const rail = screen.getByRole("list", { name: "任务执行生命周期" });
    expect(
      within(rail)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      expect.stringContaining("SOURCE"),
      expect.stringContaining("QUEUE"),
      expect.stringContaining("ATTEMPT"),
      expect.stringContaining("OUTCOME"),
    ]);
  });

  it("keeps the default surface focused on tasks and real exceptions", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <TaskOperationsCenter controller={makeController() as never} readOnly />,
    );
    expect(screen.getByRole("heading", { name: "任务列表" })).toBeTruthy();
    expect(screen.getByTestId("task-operations-desktop-table")).toBeTruthy();
    expect(screen.getByText("只读模式")).toBeTruthy();
    expect(screen.getByRole("region", { name: "需要关注的异常" })).toBeTruthy();
    expect(screen.getByText("高风险对账")).toBeTruthy();
    expect(screen.getByText("陈旧状态 3")).toBeTruthy();
    // jsdom exposes closed-details descendants to role queries; assert native disclosure state.
    for (const label of ["任务执行链路", "Saved Views", "Reconciliation"]) {
      expect(screen.getByRole("heading", { name: label }).closest("details")?.open).toBe(false);
    }
    expect(screen.getByRole("region", { name: "任务运营关注面板" }).closest("details")?.open).toBe(
      false,
    );
    const disclosures = Array.from(container.querySelectorAll("details"));
    expect(disclosures).toHaveLength(3);
    expect(disclosures.every((item) => !item.open)).toBe(true);

    await user.click(screen.getByRole("button", { name: "查看异常任务（当前列表 1 项）" }));
    expect(screen.getByRole("tab", { name: "异常" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("button", { name: "查看 重建产品知识库索引 详情" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "查看 同步客户服务手册 详情" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "查看对账诊断" }));
    expect(disclosures[2].open).toBe(true);
    expect(document.activeElement).toBe(disclosures[2].querySelector("summary"));
    expect(screen.getByRole("heading", { name: "Reconciliation" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "处理 reconcile-001" }).hasAttribute("disabled"),
    ).toBe(true);
    await user.click(screen.getByText("对账诊断", { exact: true }));
    expect(disclosures[2].open).toBe(false);
    expect(screen.getByText("高风险对账")).toBeTruthy();
  });

  it("does not show an exception banner for healthy tasks or make auxiliary errors disappear", () => {
    const healthy = makeController({
      summary: {
        status: "ready",
        value: { ...summary, failed_count: 0, stale_count: 0, reconciliation_count: 0 },
        error: null,
      },
      operations: {
        status: "ready",
        items: [runningOperation],
        invalidItemCount: 0,
        nextCursor: null,
        error: null,
      },
      reconciliation: { status: "empty", items: [], invalidItemCount: 0, error: null },
    });
    const { rerender, container } = render(<TaskOperationsCenter controller={healthy as never} />);
    expect(screen.queryByRole("region", { name: "需要关注的异常" })).toBeNull();
    rerender(
      <TaskOperationsCenter
        controller={
          makeController({
            ...healthy,
            savedViews: { ...healthy.savedViews, status: "error", error: "safe error" },
            reconciliation: { ...healthy.reconciliation, status: "partial", invalidItemCount: 1 },
            activity: { ...healthy.activity, status: "unavailable" },
            mutation: { ...healthy.mutation, error: "重试失败" },
          }) as never
        }
      />,
    );
    const notices = screen
      .getAllByRole("alert")
      .map((alert) => alert.textContent)
      .join(" ");
    expect(notices).toContain("保存视图读取失败");
    expect(notices).toContain("对账诊断部分记录无法读取");
    expect(notices).toContain("任务活动暂不可用");
    expect(notices).toContain("重试失败");
    expect(Array.from(container.querySelectorAll("details")).every((item) => !item.open)).toBe(
      true,
    );
  });

  it("exposes the five operational tabs and switches the activity surface", async () => {
    const user = userEvent.setup();
    render(<TaskOperationsCenter controller={makeController() as never} />);

    for (const label of ["全部", "运行中", "异常", "已完成", "活动记录"]) {
      expect(screen.getByRole("tab", { name: label })).toBeTruthy();
    }

    await user.click(screen.getByRole("tab", { name: "活动记录" }));
    expect(screen.getByRole("heading", { name: "任务活动链" })).toBeTruthy();
    expect(screen.getByText(/attempt_failed/)).toBeTruthy();
  });

  it("renders exactly one task surface for desktop and mobile controllers", () => {
    const { rerender } = render(
      <TaskOperationsCenter controller={makeController() as never} mobile={false} />,
    );
    expect(screen.getByTestId("task-operations-desktop-table")).toBeTruthy();
    expect(screen.queryByTestId("task-operations-mobile-cards")).toBeNull();

    rerender(<TaskOperationsCenter controller={makeController() as never} mobile />);
    expect(screen.getByTestId("task-operations-mobile-cards")).toBeTruthy();
    expect(screen.queryByTestId("task-operations-desktop-table")).toBeNull();
  });

  it("opens the event drawer and returns focus after Escape", async () => {
    const user = userEvent.setup();
    render(<TaskOperationsCenter controller={makeController() as never} />);

    const detailButton = screen.getByRole("button", { name: "查看 重建产品知识库索引 详情" });
    await user.click(detailButton);
    expect(screen.getByRole("dialog", { name: "任务详情与事件链" })).toBeTruthy();
    expect(screen.getByText("Immutable task event chain")).toBeTruthy();
    expect(screen.getByText(/attempt_failed/)).toBeTruthy();

    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "任务详情与事件链" })).toBeNull(),
    );
    await waitFor(() => expect(document.activeElement).toBe(detailButton));
  });

  it("requires explicit confirmation before retry and cancel mutations", async () => {
    const user = userEvent.setup();
    const controller = makeController();
    render(<TaskOperationsCenter controller={controller as never} />);

    await user.click(screen.getByRole("button", { name: "重试 重建产品知识库索引" }));
    const retryDialog = screen.getByRole("dialog", { name: "重试任务" });
    const retrySubmit = within(retryDialog).getByRole("button", { name: "确认重试" });
    expect(retrySubmit.hasAttribute("disabled")).toBe(true);
    await user.click(within(retryDialog).getByRole("checkbox", { name: "我确认重新执行此任务" }));
    await user.click(within(retryDialog).getByRole("button", { name: "确认重试" }));
    expect(controller.mutation.retry).toHaveBeenCalledWith(operation);

    await user.click(screen.getByRole("button", { name: "取消 同步客户服务手册" }));
    const cancelDialog = screen.getByRole("dialog", { name: "取消任务" });
    await user.click(within(cancelDialog).getByRole("checkbox", { name: "我确认取消此任务" }));
    await user.click(within(cancelDialog).getByRole("button", { name: "确认取消" }));
    expect(controller.mutation.cancel).toHaveBeenCalledWith(runningOperation);
  });

  it("preserves Saved Views and Reconciliation loading while panels are collapsed", async () => {
    const loadViews = vi.fn().mockResolvedValue(true);
    const loadReconciliation = vi.fn().mockResolvedValue(true);
    const controller = makeController({
      savedViews: {
        status: "idle",
        items: [],
        activeId: null,
        error: null,
        reload: loadViews,
      },
      reconciliation: {
        status: "idle",
        items: [],
        invalidItemCount: 0,
        nextCursor: null,
        error: null,
        reload: loadReconciliation,
      },
    });

    render(<TaskOperationsCenter controller={controller as never} />);
    await waitFor(() => expect(loadViews).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(loadReconciliation).toHaveBeenCalledTimes(1));
  });

  it("renders Saved Views and Reconciliation as controlled operational panels", async () => {
    const user = userEvent.setup();
    const controller = makeController();
    render(<TaskOperationsCenter controller={controller as never} />);

    await user.click(screen.getByText("保存视图管理", { exact: true }));
    await user.click(screen.getByText("对账诊断", { exact: true }));
    expect(screen.getByRole("heading", { name: "Saved Views" })).toBeTruthy();
    expect(screen.getByText("失败任务 · 需要处理")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Reconciliation" })).toBeTruthy();
    expect(
      within(screen.getByRole("region", { name: "Reconciliation" })).getByText(
        "Worker 回报与任务状态存在版本差异",
      ),
    ).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "打开视图 失败任务 · 需要处理" }));
    expect(controller.savedViews.onSelect).toHaveBeenCalledWith(savedViews[0]);
    await user.click(screen.getByRole("button", { name: "处理 reconcile-001" }));
    expect(controller.reconciliation.onResolve).toHaveBeenCalledWith(reconciliation[0]);
  });

  it("does not invent zeroes for unavailable, partial, or empty authority", () => {
    const { rerender } = render(
      <TaskOperationsCenter
        controller={
          makeController({
            summary: { status: "unavailable", value: null, error: "unavailable" },
            operations: {
              status: "unavailable",
              items: [],
              invalidItemCount: 0,
              nextCursor: null,
              error: null,
            },
          }) as never
        }
      />,
    );
    expect(
      screen.getAllByRole("alert").some((alert) => alert.textContent?.includes("暂不可用")),
    ).toBe(true);
    expect(screen.queryByText("待处理 0")).toBeNull();

    rerender(
      <TaskOperationsCenter
        controller={
          makeController({
            operations: {
              status: "partial",
              items: [],
              invalidItemCount: 2,
              nextCursor: null,
              error: null,
            },
          }) as never
        }
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("部分任务记录无法读取");

    rerender(
      <TaskOperationsCenter
        controller={
          makeController({
            operations: {
              status: "ready",
              items: [],
              invalidItemCount: 0,
              nextCursor: null,
              error: null,
            },
          }) as never
        }
      />,
    );
    expect(screen.getByText("暂无任务记录")).toBeTruthy();
  });

  it("keeps blocked and unavailable source states explicit and exposes only verified handoff/acknowledge actions", async () => {
    const user = userEvent.setup();
    const onHandoff = vi.fn();
    const blockedOperation = {
      ...operation,
      id: "task-blocked",
      task_label: "等待来源修复",
      status: "blocked",
      action_required: true,
      source_current: false,
      retryable: false,
      cancellable: false,
    } as const;
    const unavailableOperation = {
      ...operation,
      id: "task-unavailable",
      task_label: "来源暂不可用",
      status: "unavailable",
      action_required: false,
      source_current: false,
      retryable: false,
      cancellable: false,
    } as const;
    const controller = makeController({
      operations: {
        status: "ready",
        items: [blockedOperation, unavailableOperation],
        invalidItemCount: 0,
        nextCursor: null,
        error: null,
      },
      detail: { status: "idle", value: null, error: null, load: vi.fn() },
      mutation: {
        status: "idle",
        error: null,
        retry: vi.fn(),
        cancel: vi.fn(),
        acknowledge: vi.fn().mockResolvedValue({ state: "applied" }),
      },
    });

    render(<TaskOperationsCenter controller={controller as never} onHandoff={onHandoff} />);

    expect(screen.getByText("已阻塞")).toBeTruthy();
    expect(screen.getByText("来源不可用")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "查看 等待来源修复 详情" }));
    await user.click(screen.getByRole("button", { name: "查看任务来源" }));
    expect(onHandoff).toHaveBeenCalledWith(expect.objectContaining({ id: blockedOperation.id }));
    await user.click(screen.getByRole("button", { name: "确认已知悉" }));
    expect((controller.mutation as TaskMutationController).acknowledge).toHaveBeenCalledWith(
      expect.objectContaining({ id: blockedOperation.id }),
    );
  });

  it("keeps the detail surface stable when acknowledge authority rejects", async () => {
    const user = userEvent.setup();
    const blockedOperation = {
      ...operation,
      id: "task-ack-rejected",
      task_label: "等待人工确认",
      status: "blocked",
      action_required: true,
      retryable: false,
      cancellable: false,
    } as const;
    const acknowledge = vi.fn().mockRejectedValue(new Error("safe acknowledgement rejected"));
    const controller = makeController({
      operations: {
        status: "ready",
        items: [blockedOperation],
        invalidItemCount: 0,
        nextCursor: null,
        error: null,
      },
      detail: { status: "idle", value: null, error: null, load: vi.fn() },
      mutation: {
        status: "error",
        error: "确认失败",
        retry: vi.fn(),
        cancel: vi.fn(),
        acknowledge,
      },
    });

    render(<TaskOperationsCenter controller={controller as never} />);
    await user.click(screen.getByRole("button", { name: "查看 等待人工确认 详情" }));
    await user.click(screen.getByRole("button", { name: "确认已知悉" }));
    await waitFor(() => expect(acknowledge).toHaveBeenCalled());
    expect(screen.getByRole("dialog", { name: "任务详情与事件链" })).toBeTruthy();
    expect(screen.getByRole("alert").textContent).toContain("确认失败");
  });

  it("keeps destructive controls disabled in read-only mode and hides unsafe detail facts", async () => {
    const user = userEvent.setup();
    const unsafeDetail = { ...detail, safe_snapshot: { token: "do-not-render" } };
    const controller = makeController({
      detail: { status: "ready", value: unsafeDetail, error: null },
    });
    render(<TaskOperationsCenter controller={controller as never} readOnly />);

    const retry = screen.getByRole("button", { name: "重试 重建产品知识库索引" });
    expect(retry.getAttribute("aria-disabled")).toBe("true");
    await user.click(retry);
    expect(screen.queryByRole("dialog", { name: "重试任务" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "查看 重建产品知识库索引 详情" }));
    expect(screen.queryByText("do-not-render")).toBeNull();
  });
});
