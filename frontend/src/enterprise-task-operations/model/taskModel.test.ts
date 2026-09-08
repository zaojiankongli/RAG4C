import { describe, expect, it } from "vitest";

import {
  projectTaskAction,
  projectTaskDetail,
  projectTaskProjection,
  projectTaskReconciliationRun,
  projectTaskSavedView,
  projectTaskSummary,
  projectTaskRoute,
  type TaskModelScope,
} from "./taskModel";

const scope: TaskModelScope = { tenantId: "tenant-a", accountId: "account-a" };
const digest = "a".repeat(64);
const nextDigest = "b".repeat(64);
const timestamp = "2026-08-29T12:00:00.000000Z";

const routeBySource: Record<string, { code: string; params: Record<string, string> }> = {
  document_ingest: { code: "document_operations", params: { document_id: "document-a" } },
  index_operation: {
    code: "index_operations",
    params: { dataset_id: "dataset-a", operation_id: "index-a" },
  },
  source_sync: { code: "source_control", params: { source_id: "source-a" } },
  document_delete: { code: "document_deletion", params: { document_id: "document-a" } },
  audit_export: { code: "audit_compliance", params: { export_id: "export-a" } },
  release_quality_scan: { code: "release_quality", params: { scan_id: "scan-a" } },
  release_recertification: { code: "release_quality", params: { job_id: "recert-a" } },
};

function taskRaw(overrides: Record<string, unknown> = {}) {
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
    lease_owner: "worker-a",
    lease_until: timestamp,
    safe_error_code: "SOURCE_TIMEOUT",
    safe_error: "同步源暂时超时，可安全重试",
    target_route_code: "source_control",
    target_route_params_json: { source_id: "source-a" },
    source_current: true,
    projection_digest: digest,
    occurred_at: timestamp,
    started_at: timestamp,
    finished_at: null,
    updated_at: timestamp,
    task_label: "同步客户服务手册",
    task_type: "source_sync",
    source_label: "客户服务手册",
    queue_name: "source-sync",
    next_retry_at: null,
    owner_label: "平台自动化",
    duration_ms: 180000,
    retryable: true,
    cancellable: false,
    mutation_generation: 2,
    safe_snapshot_json: { source_revision: 3, phase: "sync" },
    ...overrides,
  };
}

function actionRaw(overrides: Record<string, unknown> = {}) {
  return {
    id: "action-a",
    tenant_id: "tenant-a",
    task_id: "task-a",
    action_type: "retry",
    status: "requested",
    expected_source_revision: 3,
    expected_source_digest: digest,
    idempotency_digest: digest,
    actor_id: "account-a",
    request_id: "request-a",
    safe_reason: "重新执行已确认的同步任务",
    requested_at: timestamp,
    dispatched_at: null,
    applied_at: null,
    rejected_at: null,
    expires_at: timestamp,
    result_code: null,
    ...overrides,
  };
}

function eventRaw(overrides: Record<string, unknown> = {}) {
  return {
    id: "event-a",
    tenant_id: "tenant-a",
    task_id: "task-a",
    sequence: 1,
    event_type: "materialized",
    previous_event_digest: null,
    event_digest: digest,
    actor_id: "system:reconciler",
    request_id: "request-a",
    safe_snapshot_json: { normalized_status: "failed", source_revision: 3 },
    occurred_at: timestamp,
    ...overrides,
  };
}

function viewRaw(overrides: Record<string, unknown> = {}) {
  return {
    id: "view-a",
    tenant_id: "tenant-a",
    account_id: "account-a",
    name: "失败同步任务",
    status: "active",
    filters_json: {
      source_kinds: ["source_sync"],
      categories: ["source"],
      statuses: ["failed"],
      action_required: true,
      dataset_id: "dataset-a",
      workspace_id: "workspace-a",
      occurred_from: "2026-08-01T00:00:00.000000Z",
      occurred_to: timestamp,
    },
    revision: 2,
    created_at: timestamp,
    updated_at: timestamp,
    archived_at: null,
    created_by: "account-a",
    updated_by: "account-a",
    ...overrides,
  };
}

describe("Stage 24 task projectors", () => {
  it("supports all seven source kinds and projects a safe internal route", () => {
    for (const [sourceKind, route] of Object.entries(routeBySource)) {
      const raw = taskRaw({
        source_kind: sourceKind,
        target_route_code: route.code,
        target_route_params_json: route.params,
        category:
          sourceKind === "source_sync"
            ? "source"
            : sourceKind === "index_operation"
              ? "indexing"
              : sourceKind === "audit_export"
                ? "compliance"
                : sourceKind.startsWith("release_")
                  ? "quality"
                  : "content",
      });
      const projected = projectTaskProjection(raw, scope);
      expect(projected.source_kind).toBe(sourceKind);
      expect(projected.route.code).toBe(route.code);
      expect(projected.route.href.startsWith("/")).toBe(true);
      expect(projected.route.href).not.toContain("http");
    }
  });

  it("normalizes the authoritative status without inventing zeroes", () => {
    const projected = projectTaskProjection(
      taskRaw({ normalized_status: "succeeded", progress_percent: 100 }),
      scope,
    );
    expect(projected.normalized_status).toBe("succeeded");
    expect(projected.status).toBe("completed");
    expect(projected.progress_percent).toBe(100);
    expect(projected.progress).toBe(100);
  });

  it("rejects cross-tenant, unknown, malformed, or unsafe projection fields", () => {
    expect(() => projectTaskProjection(taskRaw({ tenant_id: "tenant-b" }), scope)).toThrow(
      /tenant/i,
    );
    expect(() => projectTaskProjection(taskRaw({ unexpected: true }), scope)).toThrow(
      /unexpected|field/i,
    );
    expect(() => projectTaskProjection(taskRaw({ source_kind: "arbitrary_task" }), scope)).toThrow(
      /source/i,
    );
    expect(() => projectTaskProjection(taskRaw({ normalized_status: "maybe" }), scope)).toThrow(
      /status/i,
    );
    expect(() =>
      projectTaskProjection(taskRaw({ projection_digest: "not-a-digest" }), scope),
    ).toThrow(/digest/i);
    expect(() =>
      projectTaskProjection(taskRaw({ safe_snapshot_json: { raw_body: "do not persist" } }), scope),
    ).toThrow(/unsafe|snapshot|body/i);
    expect(() =>
      projectTaskProjection(
        taskRaw({ target_route_params_json: { href: "https://evil.example" } }),
        scope,
      ),
    ).toThrow(/route|url|unsafe/i);
  });

  it("projects action, event chain, detail and rejects a broken event chain", () => {
    const action = projectTaskAction(actionRaw(), scope);
    expect(action).toMatchObject({ action_type: "retry", status: "requested", task_id: "task-a" });
    expect(() =>
      projectTaskAction(actionRaw({ safe_reason: "Bearer secret-token" }), scope),
    ).toThrow(/unsafe|reason/i);

    const detail = projectTaskDetail(
      {
        task: taskRaw(),
        events: [
          eventRaw(),
          eventRaw({
            id: "event-b",
            sequence: 2,
            previous_event_digest: digest,
            event_digest: nextDigest,
            event_type: "status_changed",
          }),
        ],
      },
      scope,
    );
    expect(detail.events).toHaveLength(2);
    expect(detail.events[1]?.previous_event_digest).toBe(digest);
    expect(() =>
      projectTaskDetail(
        { task: taskRaw(), events: [eventRaw({ sequence: 2, previous_event_digest: nextDigest })] },
        scope,
      ),
    ).toThrow(/chain|sequence|materialized/i);
  });

  it("projects bounded Saved View filters and rejects free-form query or URLs", () => {
    const view = projectTaskSavedView(viewRaw(), scope);
    expect(view.filters).toEqual({
      source_kinds: ["source_sync"],
      categories: ["source"],
      statuses: ["failed"],
      action_required: true,
      dataset_id: "dataset-a",
      workspace_id: "workspace-a",
      occurred_from: "2026-08-01T00:00:00.000000Z",
      occurred_to: timestamp,
    });
    expect(() =>
      projectTaskSavedView(viewRaw({ filters_json: { query: "status=failed" } }), scope),
    ).toThrow(/query|filter|field/i);
    expect(() =>
      projectTaskSavedView(
        viewRaw({
          filters_json: {
            source_kinds: [],
            categories: [],
            statuses: [],
            action_required: null,
            dataset_id: null,
            workspace_id: null,
            occurred_from: timestamp,
            occurred_to: "2026-07-01T00:00:00.000000Z",
          },
        }),
        scope,
      ),
    ).toThrow(/time|range|order/i);
  });

  it("projects reconciliation run lifecycle and exact summary states", () => {
    const run = projectTaskReconciliationRun(
      {
        id: "reconcile-a",
        tenant_id: "tenant-a",
        source_kinds: Object.keys(routeBySource),
        source_inventory_digest: digest,
        status: "completed",
        created_count: 2,
        updated_count: 4,
        stale_count: 1,
        invalid_count: 0,
        started_at: timestamp,
        completed_at: timestamp,
        safe_error_code: null,
        safe_error: null,
      },
      scope,
    );
    expect(run.status).toBe("completed");
    expect(run.source_kinds).toHaveLength(7);

    const summary = projectTaskSummary(
      {
        tenant_id: "tenant-a",
        state: "ready",
        queued_count: 0,
        running_count: 2,
        succeeded_count: 10,
        failed_count: 1,
        cancelled_count: 0,
        blocked_count: 0,
        stale_count: 0,
        action_required_count: 1,
        as_of: timestamp,
        reason_code: null,
      },
      scope,
    );
    expect(summary.queued_count).toBe(0);
    expect(summary.succeeded_count).toBe(10);
    expect(summary.state).toBe("ready");
    expect(
      projectTaskSummary(
        {
          tenant_id: "tenant-a",
          state: "unavailable",
          queued_count: null,
          running_count: null,
          succeeded_count: null,
          failed_count: null,
          cancelled_count: null,
          blocked_count: null,
          stale_count: null,
          action_required_count: null,
          as_of: null,
          reason_code: "task_authority_unavailable",
        },
        scope,
      ).queued_count,
    ).toBeNull();
    expect(() =>
      projectTaskSummary({ tenant_id: "tenant-a", state: "ready", queued_count: null }, scope),
    ).toThrow(/summary|count/i);
  });

  it("builds only allow-listed internal routes", () => {
    expect(
      projectTaskRoute(
        {
          source_kind: "source_sync",
          source_id: "source-a",
          dataset_id: null,
          target_route_code: "source_control",
          target_route_params_json: { source_id: "source-a" },
        },
        scope,
      ),
    ).toEqual({
      code: "source_control",
      path: "/sources",
      query: { source: "source-a" },
      href: "/sources?source=source-a",
    });
    expect(() =>
      projectTaskRoute(
        {
          source_kind: "source_sync",
          source_id: "source-a",
          dataset_id: null,
          target_route_code: "source_control",
          target_route_params_json: { url: "javascript:alert(1)" },
        },
        scope,
      ),
    ).toThrow(/route|url|field/i);
  });
});
