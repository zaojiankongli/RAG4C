// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const taskHook = vi.hoisted(() => ({ useEnterpriseTaskOperations: vi.fn() }));
const center = vi.hoisted(() => ({ props: null as Record<string, unknown> | null }));

vi.mock("./hooks/useEnterpriseTaskOperations", () => taskHook);
vi.mock("./components", () => ({
  TaskOperationsCenter: (props: Record<string, any>) => {
    center.props = props;
    const firstTask = props.controller.operations.items[0];
    return (
      <section aria-label="任务中心适配器">
        <span>{firstTask?.task_label}</span>
        <span>{props.controller.summary.value?.failed_count}</span>
        <button onClick={() => props.controller.mutation.retry?.(firstTask)}>重试映射</button>
        <button onClick={() => props.onHandoff?.(firstTask)}>来源交接</button>
      </section>
    );
  },
}));

import TaskOperationsPage from "./TaskOperationsPage";

const projection = {
  id: "task-001",
  tenant_id: "tenant-a",
  source_kind: "source_sync",
  source_id: "source-a",
  source_revision: 7,
  source_digest: "a".repeat(64),
  dataset_id: "dataset-a",
  workspace_id: "workspace-a",
  category: "source",
  normalized_status: "failed",
  status: "failed",
  action_required: true,
  progress_percent: 48,
  progress: 48,
  attempt_number: 2,
  max_attempts: 3,
  lease_owner: null,
  lease_until: null,
  safe_error_code: "SYNC_TIMEOUT",
  safe_error: "来源同步超时，可安全重试",
  target_route_code: "source_control",
  target_route_params: { source_id: "source-a" },
  route: {
    code: "source_control",
    path: "/sources",
    query: { source: "source-a" },
    href: "/sources?source=source-a",
  },
  source_current: true,
  projection_digest: "b".repeat(64),
  occurred_at: "2026-08-29T08:04:00Z",
  started_at: "2026-08-29T08:01:00Z",
  finished_at: "2026-08-29T08:04:00Z",
  updated_at: "2026-08-29T08:04:30Z",
  task_label: "同步客户服务手册",
  task_type: "source_sync",
  source_label: "客户服务手册来源",
  queue_name: "source-sync",
  created_at: "2026-08-29T08:00:00Z",
  next_retry_at: null,
  owner_label: "平台自动化",
  duration_ms: 180000,
  error_code: "SYNC_TIMEOUT",
  retryable: true,
  cancellable: false,
  revision: 7,
  mutation_generation: 2,
  safe_snapshot: { source_revision: 7 },
} as const;

const retry = vi.fn().mockResolvedValue({ state: "applied", operation: "retry" });
const loadDetail = vi.fn().mockResolvedValue(true);
const loadEvents = vi.fn().mockResolvedValue(true);
const selectView = vi.fn();
const loadViews = vi.fn().mockResolvedValue(true);
const loadReconciliation = vi.fn().mockResolvedValue(true);

function hookValue() {
  return {
    active: true,
    readOnly: false,
    load: { status: "ready", error: null, reload: vi.fn().mockResolvedValue(true) },
    summary: {
      status: "ready",
      value: {
        tenant_id: "tenant-a",
        state: "ready",
        queued_count: 1,
        running_count: 2,
        succeeded_count: 8,
        completed_count: 8,
        failed_count: 1,
        cancelled_count: 0,
        blocked_count: 0,
        stale_count: 1,
        action_required_count: 1,
        reconciliation_count: 1,
        retryable_count: 1,
        as_of: "2026-08-29T08:05:00Z",
        reason_code: null,
      },
      error: null,
    },
    operations: {
      status: "ready",
      items: [projection],
      nextCursor: null,
      invalidItemCount: 0,
      error: null,
      reload: vi.fn().mockResolvedValue(true),
    },
    tasks: undefined,
    detail: { status: "idle", value: null, error: null, load: loadDetail },
    activity: {
      status: "idle",
      items: [],
      nextCursor: null,
      invalidItemCount: 0,
      error: null,
      reload: vi.fn().mockResolvedValue(true),
      load: loadEvents,
    },
    events: undefined,
    savedViews: {
      status: "ready",
      items: [
        {
          id: "view-a",
          tenant_id: "tenant-a",
          account_id: "account-a",
          name: "失败任务",
          status: "active",
          filters: {
            source_kinds: [],
            categories: [],
            statuses: ["failed"],
            action_required: true,
            dataset_id: null,
            workspace_id: null,
            occurred_from: null,
            occurred_to: null,
          },
          revision: 1,
          created_at: "2026-08-29T08:00:00Z",
          updated_at: "2026-08-29T08:00:00Z",
          archived_at: null,
          created_by: "account-a",
          updated_by: "account-a",
        },
      ],
      nextCursor: null,
      invalidItemCount: 0,
      error: null,
      reload: loadViews,
      load: loadViews,
      activeId: "view-a",
      select: selectView,
    },
    reconciliation: {
      status: "ready",
      items: [
        {
          id: "run-a",
          tenant_id: "tenant-a",
          source_kinds: ["source_sync"],
          source_inventory_digest: "c".repeat(64),
          status: "completed",
          created_count: 0,
          updated_count: 1,
          stale_count: 1,
          invalid_count: 0,
          started_at: "2026-08-29T08:00:00Z",
          completed_at: "2026-08-29T08:01:00Z",
          safe_error_code: null,
          safe_error: null,
        },
      ],
      nextCursor: null,
      invalidItemCount: 0,
      error: null,
      reload: loadReconciliation,
      load: loadReconciliation,
    },
    mutation: {
      status: "idle",
      outcome: null,
      error: null,
      retry,
      cancel: vi.fn(),
      acknowledge: vi.fn(),
      retryLast: vi.fn(),
      retryLastMutation: vi.fn(),
      createView: vi.fn(),
      updateView: vi.fn(),
      previewReconciliation: vi.fn(),
      reconcile: vi.fn(),
    },
  };
}

beforeEach(() => {
  center.props = null;
  taskHook.useEnterpriseTaskOperations.mockReset();
  taskHook.useEnterpriseTaskOperations.mockReturnValue(hookValue());
  retry.mockClear();
  loadDetail.mockClear();
  loadEvents.mockClear();
});

afterEach(cleanup);

describe("TaskOperationsPage adapter", () => {
  it("maps the verified task projection into the TDesign controller and preserves action fences", async () => {
    render(
      <TaskOperationsPage
        tenantId="tenant-a"
        accountId="account-a"
        actorToken="actor-token"
        capabilityReady
        tenantLabel="星海企业"
        readOnly={false}
      />,
    );

    expect(taskHook.useEnterpriseTaskOperations).toHaveBeenCalledWith(
      { tenantId: "tenant-a", accountId: "account-a", actorToken: "actor-token" },
      { enabled: true, readOnly: false },
    );
    expect(screen.getByRole("region", { name: "任务中心适配器" })).toBeTruthy();
    expect(screen.getByText("同步客户服务手册")).toBeTruthy();
    expect(screen.getByText("1")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "重试映射" }));
    await waitFor(() => expect(retry).toHaveBeenCalledWith(projection));
  });

  it("hands off only the strict route projected by the model", () => {
    const onTaskHandoff = vi.fn();
    render(
      <TaskOperationsPage
        tenantId="tenant-a"
        accountId="account-a"
        actorToken="actor-token"
        capabilityReady
        tenantLabel="星海企业"
        readOnly={false}
        onTaskHandoff={onTaskHandoff}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "来源交接" }));
    expect(onTaskHandoff).toHaveBeenCalledWith(projection.route);
  });
});
