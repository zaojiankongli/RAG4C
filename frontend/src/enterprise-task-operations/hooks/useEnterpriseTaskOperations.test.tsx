// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  TaskActionOutcome,
  TaskApi,
  TaskDetail,
  TaskPage,
  TaskProjection,
  TaskReconciliationRun,
  TaskSavedView,
  TaskSummary,
} from "../api/taskApi";
import { useEnterpriseTaskOperations } from "./useEnterpriseTaskOperations";
import type { TaskApiScope } from "../api/taskApi";

const scope: TaskApiScope = { tenantId: "tenant-a", actorToken: "token-a", accountId: "account-a" };
const scopeB: TaskApiScope = {
  tenantId: "tenant-b",
  actorToken: "token-b",
  accountId: "account-b",
};
const summary: TaskSummary = {
  tenant_id: "tenant-a",
  state: "ready",
  queued_count: 1,
  running_count: 2,
  succeeded_count: 3,
  completed_count: 3,
  failed_count: 1,
  cancelled_count: 0,
  blocked_count: 0,
  stale_count: 0,
  action_required_count: 1,
  reconciliation_count: 0,
  retryable_count: 1,
  as_of: "2026-08-29T12:00:00.000000Z",
  reason_code: null,
};
const task = { id: "task-a", tenant_id: "tenant-a" } as TaskProjection;
const page: TaskPage<TaskProjection> = {
  items: [task],
  next_cursor: "next",
  invalid_item_count: 0,
};
const detail = { ...task, events: [] } as TaskDetail;
const view = { id: "view-a", tenant_id: "tenant-a" } as TaskSavedView;
const run = { id: "run-a", tenant_id: "tenant-a" } as TaskReconciliationRun;
const outcome: TaskActionOutcome = {
  state: "applied",
  operation: "retry",
  resource_id: "task-a",
  action_id: "action-a",
  revision: 2,
  message: null,
  retryable: false,
};

function makeApi(overrides: Partial<TaskApi> = {}): TaskApi {
  return {
    fetchSummary: vi.fn().mockResolvedValue(summary),
    fetchTasks: vi.fn().mockResolvedValue(page),
    fetchTask: vi.fn().mockResolvedValue(detail),
    fetchTaskEvents: vi
      .fn()
      .mockResolvedValue({ items: [], next_cursor: null, invalid_item_count: 0 }),
    fetchViews: vi
      .fn()
      .mockResolvedValue({ items: [view], next_cursor: null, invalid_item_count: 0 }),
    fetchReconciliationRuns: vi
      .fn()
      .mockResolvedValue({ items: [run], next_cursor: null, invalid_item_count: 0 }),
    retryTask: vi.fn().mockResolvedValue(outcome),
    cancelTask: vi.fn().mockResolvedValue(outcome),
    acknowledgeTask: vi.fn().mockResolvedValue(outcome),
    createView: vi.fn().mockResolvedValue(view),
    updateView: vi.fn().mockResolvedValue(view),
    previewReconciliation: vi.fn().mockResolvedValue(outcome),
    reconcile: vi.fn().mockResolvedValue(outcome),
    ...overrides,
  };
}

describe("useEnterpriseTaskOperations", () => {
  it("is inactive-safe and does not read or mutate until enabled", async () => {
    const api = makeApi();
    const { result, rerender } = renderHook(
      ({ enabled }) => useEnterpriseTaskOperations(scope, { enabled, api }),
      { initialProps: { enabled: false } },
    );
    expect(result.current.active).toBe(false);
    expect(result.current.load.status).toBe("idle");
    expect(api.fetchSummary).not.toHaveBeenCalled();
    await act(async () => expect(result.current.mutation.retry(task)).resolves.toBeNull());
    expect(api.retryTask).not.toHaveBeenCalled();
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    expect(api.fetchSummary).toHaveBeenCalledTimes(1);
  });

  it("loads summary and tasks in parallel with one AbortSignal while lazy resources stay idle", async () => {
    let resolveSummary!: (value: TaskSummary) => void;
    let resolveTasks!: (value: TaskPage<TaskProjection>) => void;
    const summaryPromise = new Promise<TaskSummary>((resolve) => (resolveSummary = resolve));
    const tasksPromise = new Promise<TaskPage<TaskProjection>>(
      (resolve) => (resolveTasks = resolve),
    );
    const api = makeApi({
      fetchSummary: vi.fn().mockReturnValue(summaryPromise),
      fetchTasks: vi.fn().mockReturnValue(tasksPromise),
    });
    const { result } = renderHook(() => useEnterpriseTaskOperations(scope, { enabled: true, api }));
    await waitFor(() => expect(api.fetchTasks).toHaveBeenCalledTimes(1));
    const summaryOptions = vi.mocked(api.fetchSummary).mock.calls[0]?.[1];
    const taskOptions = vi.mocked(api.fetchTasks).mock.calls[0]?.[2];
    expect(summaryOptions?.signal).toBeInstanceOf(AbortSignal);
    expect(taskOptions?.signal).toBe(summaryOptions?.signal);
    expect(result.current.detail.status).toBe("idle");
    expect(result.current.savedViews.status).toBe("idle");
    expect(result.current.reconciliation.status).toBe("idle");
    await act(async () => {
      resolveSummary(summary);
      resolveTasks(page);
    });
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
  });

  it("lazy-loads detail, events, saved views, and reconciliation with context fences", async () => {
    const api = makeApi();
    const { result, rerender } = renderHook(
      ({ currentScope }) => useEnterpriseTaskOperations(currentScope, { enabled: true, api }),
      { initialProps: { currentScope: scope } },
    );
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    expect(api.fetchTask).not.toHaveBeenCalled();
    await act(async () => {
      await result.current.detail.load("task-a");
      await result.current.activity.load("task-a");
      await result.current.savedViews.load();
      await result.current.reconciliation.load();
    });
    expect(api.fetchTask).toHaveBeenCalledWith(
      scope,
      "task-a",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(api.fetchTaskEvents).toHaveBeenCalled();
    expect(api.fetchViews).toHaveBeenCalledWith(
      scope,
      { status: "active" },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(api.fetchReconciliationRuns).toHaveBeenCalled();
    rerender({ currentScope: scopeB });
    await waitFor(() =>
      expect(
        vi.mocked(api.fetchSummary).mock.calls[
          vi.mocked(api.fetchSummary).mock.calls.length - 1
        ]?.[0],
      ).toEqual(scopeB),
    );
  });

  it("drops stale responses after a context change", async () => {
    let resolveOld!: (value: TaskSummary) => void;
    const api = makeApi({
      fetchSummary: vi.fn((requestScope) =>
        requestScope.tenantId === "tenant-a"
          ? new Promise<TaskSummary>((resolve) => (resolveOld = resolve))
          : Promise.resolve({ ...summary, tenant_id: "tenant-b" }),
      ),
    });
    const { result, rerender } = renderHook(
      ({ currentScope }) => useEnterpriseTaskOperations(currentScope, { enabled: true, api }),
      { initialProps: { currentScope: scope } },
    );
    await waitFor(() => expect(api.fetchSummary).toHaveBeenCalledTimes(1));
    rerender({ currentScope: scopeB });
    await waitFor(() => expect(result.current.summary.value?.tenant_id).toBe("tenant-b"));
    await act(async () => resolveOld(summary));
    expect(result.current.summary.value?.tenant_id).toBe("tenant-b");
  });

  it("serializes retry/cancel/acknowledge mutations, preserves keys for retry, and blocks read-only", async () => {
    let releaseFirst!: () => void;
    const gate = new Promise<void>((resolve) => (releaseFirst = resolve));
    const retryTask = vi
      .fn()
      .mockImplementationOnce(async () => {
        await gate;
        return outcome;
      })
      .mockRejectedValueOnce(new Error("temporary unavailable"))
      .mockResolvedValueOnce(outcome);
    const api = makeApi({ retryTask });
    const { result } = renderHook(() => useEnterpriseTaskOperations(scope, { enabled: true, api }));
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    let first!: Promise<TaskActionOutcome | null>;
    let second!: Promise<TaskActionOutcome | null>;
    await act(async () => {
      first = result.current.mutation.retry(task, { idempotencyKey: "first-key" });
      second = result.current.mutation.retry(task, { idempotencyKey: "second-key" });
    });
    expect(retryTask).toHaveBeenCalledTimes(1);
    await act(async () => releaseFirst());
    await expect(first).resolves.toMatchObject({ state: "applied" });
    await expect(second).rejects.toThrow(/unavailable/i);
    await waitFor(() => expect(result.current.mutation.status).toBe("error"));
    await act(async () => {
      await result.current.mutation.retryLast();
    });
    expect(retryTask).toHaveBeenCalledTimes(3);
    expect(retryTask.mock.calls[1]?.[3]).toEqual(
      expect.objectContaining({ idempotencyKey: "second-key" }),
    );
    const readOnlyApi = makeApi();
    const readOnly = renderHook(() =>
      useEnterpriseTaskOperations(scope, { enabled: true, readOnly: true, api: readOnlyApi }),
    );
    await waitFor(() => expect(readOnly.result.current.load.status).toBe("ready"));
    await act(async () =>
      expect(readOnly.result.current.mutation.cancel(task)).resolves.toBeNull(),
    );
    expect(readOnlyApi.cancelTask).not.toHaveBeenCalled();
    readOnly.unmount();
  });

  it("reports partial authority when summary or task list fails without fabricating counts", async () => {
    const api = makeApi({
      fetchSummary: vi.fn().mockRejectedValue(new Error("summary unavailable")),
    });
    const { result } = renderHook(() => useEnterpriseTaskOperations(scope, { enabled: true, api }));
    await waitFor(() => expect(result.current.load.status).toBe("partial"));
    expect(result.current.summary.status).toBe("error");
    expect(result.current.summary.value).toBeNull();
    expect(result.current.operations.items).toHaveLength(1);
  });
});
