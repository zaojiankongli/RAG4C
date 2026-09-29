import { useMemo } from "react";

import {
  TaskOperationsCenter,
  type TaskEvent as ViewTaskEvent,
  type TaskMutationOutcome as ViewTaskMutationOutcome,
  type TaskOperation as ViewTaskOperation,
  type TaskOperationDetail as ViewTaskOperationDetail,
  type TaskOperationsController,
  type TaskOperationsTab,
  type TaskReconciliationItem,
  type TaskSavedView as ViewTaskSavedView,
} from "./components";
import { useEnterpriseTaskOperations } from "./hooks/useEnterpriseTaskOperations";
import { displayTaskStatus } from "./model/taskVocabulary";
import type {
  TaskActionOutcome,
  TaskDetail,
  TaskEvent,
  TaskProjection,
  TaskReconciliationRun,
  TaskRoute,
  TaskSavedView,
} from "./model/taskModel";

export interface TaskOperationsPageProps {
  tenantId: string;
  accountId?: string;
  actorToken: string;
  capabilityReady: boolean;
  tenantLabel: string;
  readOnly: boolean;
  mobile?: boolean;
  onTaskHandoff?: (route: TaskRoute) => void;
}

function safeError(error: Error | null): string | null {
  return error ? "Enterprise Task Operations authority is unavailable" : null;
}

function operationProjection(task: TaskProjection): ViewTaskOperation {
  return {
    id: task.id,
    tenant_id: task.tenant_id,
    task_label: task.task_label,
    task_type: task.task_type,
    source_label: task.source_label ?? task.source_id,
    source_kind: task.source_kind,
    queue_name: task.queue_name ?? "权威任务队列",
    status: task.status,
    attempt_number: task.attempt_number,
    max_attempts: task.max_attempts,
    progress: task.progress,
    created_at: task.created_at,
    started_at: task.started_at,
    finished_at: task.finished_at,
    next_retry_at: task.next_retry_at,
    owner_label: task.owner_label,
    duration_ms: task.duration_ms,
    error_code: task.error_code,
    safe_error: task.safe_error,
    retryable: task.retryable && task.source_current,
    cancellable: task.cancellable && task.source_current,
    revision: task.revision,
    mutation_generation: task.mutation_generation,
    action_required: task.action_required,
    source_current: task.source_current,
    safe_snapshot: { ...task.safe_snapshot },
  };
}

function eventProjection(event: TaskEvent): ViewTaskEvent {
  return {
    id: event.id,
    sequence: event.sequence,
    event_type: event.event_type,
    occurred_at: event.occurred_at,
    actor_id: event.actor_id,
    request_id: event.request_id,
    event_digest: event.event_digest,
    previous_event_digest: event.previous_event_digest,
    safe_snapshot: { ...event.safe_snapshot },
  };
}

function detailProjection(detail: TaskDetail): ViewTaskOperationDetail {
  return {
    ...operationProjection(detail),
    events: detail.events.map(eventProjection),
  };
}

function savedViewFilter(view: TaskSavedView): Exclude<TaskOperationsTab, "activity"> {
  const statuses = new Set(view.filters.statuses.map(displayTaskStatus));
  if (
    statuses.size > 0 &&
    [...statuses].every((status) => status === "queued" || status === "running")
  )
    return "running";
  if (
    statuses.size > 0 &&
    [...statuses].every(
      (status) => status === "failed" || status === "blocked" || status === "unavailable",
    )
  )
    return "failed";
  if (statuses.size === 1 && statuses.has("completed")) return "completed";
  return "all";
}

function savedViewProjection(view: TaskSavedView, activeId: string | null): ViewTaskSavedView {
  return {
    id: view.id,
    label: view.name,
    filter: savedViewFilter(view),
    pinned: activeId === view.id,
  };
}

function reconciliationProjection(run: TaskReconciliationRun): TaskReconciliationItem {
  const severity =
    run.invalid_count > 0
      ? "high"
      : run.stale_count > 0 || run.status === "failed"
        ? "medium"
        : "low";
  const status =
    run.status === "completed" && run.invalid_count === 0 && run.stale_count === 0
      ? "resolved"
      : "open";
  return {
    id: run.id,
    task_id: "verified-task-projection",
    severity,
    status,
    category: "projection_reconciliation",
    summary:
      run.safe_error ??
      `created ${run.created_count} · updated ${run.updated_count} · stale ${run.stale_count} · invalid ${run.invalid_count}`,
    detected_at: run.completed_at ?? run.started_at,
    action_required: status === "open",
  };
}

function mutationProjection(outcome: TaskActionOutcome | null): ViewTaskMutationOutcome {
  if (!outcome) return { state: "unavailable", message: "Task operation unavailable" };
  return {
    state: outcome.state,
    message: outcome.message,
  };
}

export default function TaskOperationsPage({
  tenantId,
  accountId,
  actorToken,
  capabilityReady,
  tenantLabel,
  readOnly,
  mobile = false,
  onTaskHandoff,
}: TaskOperationsPageProps) {
  const taskOperations = useEnterpriseTaskOperations(
    { tenantId, accountId, actorToken },
    { enabled: capabilityReady, readOnly },
  );

  const controller = useMemo<TaskOperationsController>(() => {
    const sourceTasks = taskOperations.operations.items;
    const tasks = sourceTasks.map(operationProjection);
    const sourceTaskById = new Map(sourceTasks.map((task) => [task.id, task]));
    const sourceViewById = new Map(taskOperations.savedViews.items.map((view) => [view.id, view]));
    const firstTaskId = sourceTasks[0]?.id;

    const findTask = (task: ViewTaskOperation): TaskProjection => {
      const sourceTask = sourceTaskById.get(task.id);
      if (!sourceTask) throw new Error("Task projection is no longer current");
      return sourceTask;
    };
    const refresh = async () => taskOperations.load.reload();

    return {
      active: taskOperations.active,
      summary: {
        status: taskOperations.summary.status,
        value: taskOperations.summary.value
          ? {
              state: taskOperations.summary.value.state,
              tenant_id: taskOperations.summary.value.tenant_id,
              as_of: taskOperations.summary.value.as_of,
              queued_count: taskOperations.summary.value.queued_count,
              running_count: taskOperations.summary.value.running_count,
              failed_count: taskOperations.summary.value.failed_count,
              completed_count: taskOperations.summary.value.completed_count,
              stale_count: taskOperations.summary.value.stale_count,
              reconciliation_count: taskOperations.summary.value.reconciliation_count,
              retryable_count: taskOperations.summary.value.retryable_count,
            }
          : null,
        error: safeError(taskOperations.summary.error),
        reload: refresh,
      },
      operations: {
        status: taskOperations.operations.status,
        items: tasks,
        invalidItemCount: taskOperations.operations.invalidItemCount,
        nextCursor: taskOperations.operations.nextCursor,
        error: safeError(taskOperations.operations.error),
        reload: refresh,
      },
      detail: {
        status: taskOperations.detail.status,
        value: taskOperations.detail.value ? detailProjection(taskOperations.detail.value) : null,
        error: safeError(taskOperations.detail.error),
        load: async (taskId?: string) => (taskId ? taskOperations.detail.load(taskId) : false),
      },
      activity: {
        status: taskOperations.activity.status,
        items: taskOperations.activity.items.map(eventProjection),
        invalidItemCount: taskOperations.activity.invalidItemCount,
        nextCursor: taskOperations.activity.nextCursor,
        error: safeError(taskOperations.activity.error),
        reload: async () => (firstTaskId ? taskOperations.activity.load(firstTaskId) : false),
      },
      savedViews: {
        status: taskOperations.savedViews.status,
        items: taskOperations.savedViews.items.map((view) =>
          savedViewProjection(view, taskOperations.savedViews.activeId),
        ),
        activeId: taskOperations.savedViews.activeId,
        error: safeError(taskOperations.savedViews.error),
        reload: taskOperations.savedViews.load,
        onSelect: (view) => {
          const sourceView = sourceViewById.get(view.id);
          if (sourceView) taskOperations.savedViews.select(sourceView);
        },
      },
      reconciliation: {
        status: taskOperations.reconciliation.status,
        items: taskOperations.reconciliation.items.map(reconciliationProjection),
        invalidItemCount: taskOperations.reconciliation.invalidItemCount,
        nextCursor: taskOperations.reconciliation.nextCursor,
        error: safeError(taskOperations.reconciliation.error),
        reload: taskOperations.reconciliation.load,
        onResolve: async (item) => {
          const sourceRun = taskOperations.reconciliation.items.find((run) => run.id === item.id);
          if (!sourceRun) return mutationProjection(null);
          return mutationProjection(
            await taskOperations.mutation.previewReconciliation({
              sourceKinds: [...sourceRun.source_kinds],
              reason: "Task Center explicit reconciliation preview",
            }),
          );
        },
      },
      mutation: {
        status: taskOperations.mutation.status,
        error: safeError(taskOperations.mutation.error),
        retry: async (task) =>
          mutationProjection(await taskOperations.mutation.retry(findTask(task))),
        cancel: async (task) =>
          mutationProjection(await taskOperations.mutation.cancel(findTask(task))),
        acknowledge: async (task) =>
          mutationProjection(await taskOperations.mutation.acknowledge(findTask(task))),
      },
    };
  }, [taskOperations]);

  const handleHandoff = (task: ViewTaskOperation) => {
    const sourceTask = taskOperations.operations.items.find(
      (candidate) => candidate.id === task.id,
    );
    if (sourceTask) onTaskHandoff?.(sourceTask.route);
  };

  return (
    <TaskOperationsCenter
      controller={controller}
      capabilityReady={capabilityReady}
      readOnly={readOnly}
      mobile={mobile}
      tenantLabel={tenantLabel}
      onHandoff={onTaskHandoff ? handleHandoff : undefined}
    />
  );
}
