// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from "vitest";

import { request } from "../../api/client";
import {
  acknowledgeTask,
  cancelTask,
  createTaskIdempotencyKey,
  createTaskSavedView,
  fetchReconciliationRuns,
  fetchTask,
  fetchTaskEvents,
  fetchTaskSummary,
  fetchTaskViews,
  fetchTasks,
  previewTaskReconciliation,
  reconcileTasks,
  retryTask,
  updateTaskSavedView,
  type TaskApiScope,
} from "./taskApi";

vi.mock("../../api/client", () => ({ request: vi.fn() }));
const mockedRequest = vi.mocked(request);
const scope: TaskApiScope = {
  tenantId: "tenant-a",
  actorToken: "actor-token",
  accountId: "account-a",
};
const digest = "a".repeat(64);
const timestamp = "2026-08-29T12:00:00.000000Z";

function taskRaw() {
  return {
    id: "task-a",
    tenant_id: "tenant-a",
    source_kind: "source_sync",
    source_id: "source-a",
    source_revision: 3,
    source_digest: digest,
    dataset_id: "dataset-a",
    workspace_id: "workspace-a",
    category: "source",
    normalized_status: "failed",
    action_required: true,
    progress_percent: 64,
    attempt_number: 2,
    max_attempts: 3,
    lease_owner: null,
    lease_until: null,
    safe_error_code: "SOURCE_TIMEOUT",
    safe_error: "同步源超时",
    target_route_code: "source_control",
    target_route_params_json: { source_id: "source-a" },
    source_current: true,
    projection_digest: digest,
    occurred_at: timestamp,
    started_at: timestamp,
    finished_at: null,
    updated_at: timestamp,
  };
}

function summaryRaw() {
  return {
    tenant_id: "tenant-a",
    state: "ready",
    queued_count: 0,
    running_count: 1,
    succeeded_count: 3,
    failed_count: 1,
    cancelled_count: 0,
    blocked_count: 0,
    stale_count: 0,
    action_required_count: 1,
    as_of: timestamp,
    reason_code: null,
  };
}

function page(items: unknown[] = [taskRaw()]) {
  return { items, next_cursor: "cursor-next", invalid_item_count: 0 };
}

function outcome() {
  return {
    state: "applied",
    operation: "retry",
    resource_id: "task-a",
    action_id: "action-a",
    revision: 4,
    message: "已提交",
    retryable: false,
  };
}

describe("Stage 24 task API", () => {
  beforeEach(() => vi.clearAllMocks());

  it("sends scoped read requests, stable query parameters, and shared AbortSignal", async () => {
    mockedRequest
      .mockResolvedValueOnce(summaryRaw())
      .mockResolvedValueOnce(page())
      .mockResolvedValueOnce({ task: taskRaw(), events: [] })
      .mockResolvedValueOnce(page([]))
      .mockResolvedValueOnce(page([]))
      .mockResolvedValueOnce(page([]));
    const controller = new AbortController();
    await fetchTaskSummary(scope, { signal: controller.signal });
    await fetchTasks(
      scope,
      {
        status: "failed",
        sourceKind: "source_sync",
        cursor: "cursor-a",
        limit: 20,
        actionRequired: true,
      },
      { signal: controller.signal },
    );
    await fetchTask(scope, "task/a", { signal: controller.signal });
    await fetchTaskEvents(scope, "task-a", { limit: 10 }, { signal: controller.signal });
    await fetchTaskViews(scope, { status: "active" }, { signal: controller.signal });
    await fetchReconciliationRuns(
      scope,
      { status: "completed", limit: 10 },
      { signal: controller.signal },
    );

    expect(mockedRequest.mock.calls.map(([path]) => path)).toEqual([
      "/api/enterprise/tasks/summary",
      "/api/enterprise/tasks?action_required=true&cursor=cursor-a&limit=20&source_kind=source_sync&status=failed",
      "/api/enterprise/tasks/task%2Fa",
      "/api/enterprise/tasks/task-a/events?limit=10",
      "/api/enterprise/task-views?limit=50&status=active",
      "/api/enterprise/tasks/reconciliation-runs?limit=10&status=completed",
    ]);
    expect(mockedRequest.mock.calls.every(([, init]) => init?.signal === controller.signal)).toBe(
      true,
    );
    expect(mockedRequest.mock.calls[0]?.[1]?.headers).toEqual({
      "Content-Type": "application/json",
      "X-RAG4C-Tenant": "tenant-a",
      Authorization: "Bearer actor-token",
      "X-RAG4C-Account": "account-a",
    });
  });

  it("projects all mutation routes and always sends Idempotency-Key", async () => {
    mockedRequest.mockResolvedValue(outcome());
    const options = { idempotencyKey: "stage24-key" };
    await retryTask(
      scope,
      "task-a",
      { expectedSourceRevision: 3, expectedSourceDigest: digest, reason: "retry controlled task" },
      options,
    );
    await cancelTask(
      scope,
      "task-a",
      { expectedSourceRevision: 3, expectedSourceDigest: digest, reason: "cancel queued task" },
      options,
    );
    await acknowledgeTask(
      scope,
      "task-a",
      { expectedSourceRevision: 3, expectedSourceDigest: digest, reason: "acknowledge attention" },
      options,
    );
    await createTaskSavedView(
      scope,
      {
        name: "失败任务",
        filters: { categories: ["documents", "sources"], statuses: ["failed"] },
        reason: "save view",
      },
      options,
    );
    await updateTaskSavedView(
      scope,
      "view-a",
      {
        expectedRevision: 2,
        name: "失败任务 2",
        filters: { action_required: true },
        reason: "update view",
      },
      options,
    );
    await previewTaskReconciliation(
      scope,
      { sourceKinds: ["source_sync"], reason: "preview only" },
      options,
    );
    await reconcileTasks(
      scope,
      { sourceKinds: ["source_sync"], reason: "reconcile explicitly" },
      options,
    );

    expect(mockedRequest.mock.calls.map(([path]) => path)).toEqual([
      "/api/enterprise/tasks/task-a/retry",
      "/api/enterprise/tasks/task-a/cancel",
      "/api/enterprise/tasks/task-a/acknowledge",
      "/api/enterprise/task-views",
      "/api/enterprise/task-views/view-a",
      "/api/enterprise/tasks/reconcile/preview",
      "/api/enterprise/tasks/reconcile",
    ]);
    for (const [, init] of mockedRequest.mock.calls)
      expect(init?.headers).toEqual(expect.objectContaining({ "Idempotency-Key": "stage24-key" }));
    expect(mockedRequest.mock.calls[0]?.[1]?.body).toBe(
      JSON.stringify({
        expected_source_revision: 3,
        expected_source_digest: digest,
        reason: "retry controlled task",
      }),
    );
    expect(mockedRequest.mock.calls[3]?.[1]?.body).toBe(
      JSON.stringify({
        name: "失败任务",
        filters: {
          source_kinds: [],
          categories: ["content", "source"],
          statuses: ["failed"],
          action_required: null,
          dataset_id: null,
          workspace_id: null,
          occurred_from: null,
          occurred_to: null,
        },
        reason: "save view",
      }),
    );
  });

  it("rejects missing idempotency keys, malformed inputs, unsupported source kinds, and unsafe reasons before network calls", () => {
    expect(() =>
      retryTask(scope, "task-a", {
        expectedSourceRevision: 3,
        expectedSourceDigest: digest,
        reason: "retry",
      }),
    ).toThrow(/idempotency/i);
    expect(() =>
      retryTask(
        scope,
        "task-a",
        {
          expectedSourceRevision: true as unknown as number,
          expectedSourceDigest: digest,
          reason: "retry",
        },
        { idempotencyKey: "key" },
      ),
    ).toThrow(/revision|integer/i);
    expect(() =>
      retryTask(
        scope,
        "task-a",
        { expectedSourceRevision: 0, expectedSourceDigest: digest, reason: "retry" },
        { idempotencyKey: "key" },
      ),
    ).toThrow(/revision|integer/i);
    expect(() => fetchTasks(scope, { sourceKind: "arbitrary" as never })).toThrow(/source/i);
    expect(() => fetchTasks(scope, { category: "source" } as never)).toThrow(/category/i);
    expect(() => fetchTasks(scope, { sourceKinds: ["source_sync"] } as never)).toThrow(/sourceKinds/i);
    expect(() => fetchTasks(scope, { datasetId: "dataset-a" } as never)).toThrow(/datasetId/i);
    expect(() => fetchTasks(scope, { workspaceId: "workspace-a" } as never)).toThrow(/workspaceId/i);
    expect(() =>
      createTaskSavedView(
        scope,
        { name: "view", filters: { query: "status=failed" } as never, reason: "save" },
        { idempotencyKey: "key" },
      ),
    ).toThrow(/query|filter/i);
    expect(() =>
      retryTask(
        scope,
        "task-a",
        { expectedSourceRevision: 3, expectedSourceDigest: digest, reason: "Bearer secret-token" },
        { idempotencyKey: "key" },
      ),
    ).toThrow(/unsafe|reason/i);
    expect(mockedRequest).not.toHaveBeenCalled();
  });

  it("does not send the display-only completed status as a fact-status query", () => {
    expect(() => fetchTasks(scope, { status: "completed" as never })).toThrow(/status/i);
    expect(mockedRequest).not.toHaveBeenCalled();
  });

  it("rejects unknown response fields and bounds list/mutation inputs", async () => {
    mockedRequest.mockResolvedValueOnce({ ...summaryRaw(), unexpected: true });
    await expect(fetchTaskSummary(scope)).rejects.toThrow(/unexpected|summary|field/i);
    expect(() => fetchTasks(scope, { limit: 201 })).toThrow(/limit/i);
    expect(() => fetchTaskEvents(scope, "task-a", { cursor: "bad\nvalue" })).toThrow(/cursor/i);
    expect(() =>
      createTaskSavedView(scope, { name: "", filters: {}, reason: "x" }, { idempotencyKey: "key" }),
    ).toThrow(/name/i);
    expect(() =>
      retryTask(
        scope,
        "task-a",
        { expectedSourceRevision: 3, expectedSourceDigest: digest, reason: "x" },
        { idempotencyKey: "k".repeat(129) },
      ),
    ).toThrow(/idempotency/i);
  });

  it("creates a nonempty task idempotency key", () => {
    expect(createTaskIdempotencyKey()).toMatch(/^rag4c-task-/);
  });
});
