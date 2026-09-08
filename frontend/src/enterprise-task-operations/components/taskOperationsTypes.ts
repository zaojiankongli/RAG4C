export type TaskOperationStatus =
  "queued" | "running" | "failed" | "completed" | "cancelled" | "blocked" | "unavailable";

export type TaskOperationsTab = "all" | "running" | "failed" | "completed" | "activity";

export type TaskResourceStatus =
  "idle" | "loading" | "ready" | "partial" | "empty" | "unavailable" | "error";

export type TaskMutationStatus = "idle" | "saving" | "success" | "error";

export type SafeTaskValue = string | number | boolean | null;

export interface TaskOperation {
  id: string;
  tenant_id: string;
  task_label: string;
  task_type: string;
  source_label: string;
  source_kind: string;
  queue_name: string;
  status: TaskOperationStatus;
  attempt_number: number;
  max_attempts: number;
  progress: number | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  next_retry_at: string | null;
  owner_label: string | null;
  duration_ms: number | null;
  error_code: string | null;
  safe_error: string | null;
  retryable: boolean;
  cancellable: boolean;
  revision: number;
  mutation_generation: number;
  action_required?: boolean;
  source_current?: boolean;
  safe_snapshot?: Record<string, SafeTaskValue>;
}

export type TaskEventType =
  | "enqueued"
  | "attempt_started"
  | "attempt_failed"
  | "retry_scheduled"
  | "completed"
  | "cancelled"
  | "reconciled"
  | "materialized"
  | "status_changed"
  | "source_stale"
  | "action_requested"
  | "action_applied"
  | "action_rejected"
  | "attention_acknowledged";

export interface TaskEvent {
  id: string;
  sequence: number;
  event_type: TaskEventType;
  occurred_at: string;
  actor_id: string;
  request_id: string;
  event_digest: string;
  previous_event_digest: string | null;
  safe_snapshot: Record<string, SafeTaskValue>;
}

export interface TaskOperationDetail extends TaskOperation {
  events: TaskEvent[];
}

export interface TaskOperationsSummary {
  state: "ready" | "partial" | "unavailable" | "error";
  tenant_id: string;
  as_of: string | null;
  queued_count: number | null;
  running_count: number | null;
  failed_count: number | null;
  completed_count: number | null;
  stale_count: number | null;
  reconciliation_count: number | null;
  retryable_count: number | null;
}

export interface TaskSavedView {
  id: string;
  label: string;
  filter: Exclude<TaskOperationsTab, "activity">;
  pinned: boolean;
}

export interface TaskReconciliationItem {
  id: string;
  task_id: string;
  severity: "low" | "medium" | "high" | "critical";
  status: "open" | "resolved" | "ignored";
  category: string;
  summary: string;
  detected_at: string | null;
  action_required: boolean;
}

export interface TaskResource<T> {
  status: TaskResourceStatus;
  value: T | null;
  error: string | null;
  load?: (id?: string) => void | Promise<unknown>;
  reload?: () => void | Promise<unknown>;
}

export interface TaskCollection<T> {
  status: TaskResourceStatus;
  items: T[];
  invalidItemCount: number;
  nextCursor: string | null;
  error: string | null;
  reload?: () => void | Promise<unknown>;
}

export interface TaskSavedViewsController {
  status: TaskResourceStatus;
  items: TaskSavedView[];
  activeId?: string | null;
  error: string | null;
  reload?: () => void | Promise<unknown>;
  onSelect?: (view: TaskSavedView) => void;
  onSave?: (view: TaskSavedView) => void | Promise<unknown>;
  onDelete?: (view: TaskSavedView) => void | Promise<unknown>;
}

export interface TaskReconciliationController extends TaskCollection<TaskReconciliationItem> {
  onResolve?: (item: TaskReconciliationItem) => void | Promise<unknown>;
}

export interface TaskMutationOutcome {
  state: "applied" | "replayed" | "conflict" | "blocked" | "rejected" | "unavailable";
  message?: string | null;
}

export interface TaskMutationController {
  status: TaskMutationStatus;
  error: string | null;
  retry?: (task: TaskOperation) => void | Promise<TaskMutationOutcome | unknown>;
  cancel?: (task: TaskOperation) => void | Promise<TaskMutationOutcome | unknown>;
  acknowledge?: (task: TaskOperation) => void | Promise<TaskMutationOutcome | unknown>;
  reconcile?: (item: TaskReconciliationItem) => void | Promise<TaskMutationOutcome | unknown>;
}

export interface TaskOperationsController {
  active: boolean;
  summary: TaskResource<TaskOperationsSummary>;
  operations: TaskCollection<TaskOperation>;
  detail: TaskResource<TaskOperationDetail>;
  activity: TaskCollection<TaskEvent>;
  savedViews: TaskSavedViewsController;
  reconciliation: TaskReconciliationController;
  mutation: TaskMutationController;
}

export type TaskOperationAction = (task: TaskOperation) => void;
