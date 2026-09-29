import {
  displayTaskCategory,
  displayTaskStatus,
  isTaskSourceKind,
  normalizeTaskStatus,
  savedViewTaskCategory,
} from "./taskVocabulary";
import type {
  TaskCategory,
  TaskDisplayStatus,
  TaskSourceKind,
  TaskStatus,
} from "./taskVocabulary";

export type {
  TaskApiCategory,
  TaskCategory,
  TaskDisplayStatus,
  TaskFactCategory,
  TaskSourceKind,
  TaskStatus,
} from "./taskVocabulary";

export type TaskActionType = "retry" | "cancel" | "acknowledge";
export type TaskActionStatus = "requested" | "dispatched" | "applied" | "rejected" | "expired";
export type TaskEventType =
  | "materialized"
  | "status_changed"
  | "source_stale"
  | "action_requested"
  | "action_applied"
  | "action_rejected"
  | "attention_acknowledged";
export type TaskSavedViewStatus = "active" | "archived";
export type TaskReconciliationStatus = "started" | "completed" | "failed";
export type TaskSummaryState = "ready" | "partial" | "unavailable" | "error";
export type TaskMutationState =
  "applied" | "replayed" | "conflict" | "blocked" | "rejected" | "unavailable";
export type TaskRouteCode =
  | "document_operations"
  | "index_operations"
  | "source_control"
  | "document_deletion"
  | "audit_compliance"
  | "release_quality";

export interface TaskModelScope {
  tenantId: string;
  accountId?: string;
}
export interface TaskRoute {
  code: TaskRouteCode;
  path: "/documents" | "/sources" | "/enterprise" | "/enterprise/knowledge-base";
  query: Readonly<Record<string, string>>;
  href: string;
}
export type TaskSafeValue = string | number | boolean | null;
export type TaskSafeSnapshot = Readonly<Record<string, TaskSafeValue>>;

export interface TaskProjection {
  id: string;
  tenant_id: string;
  source_kind: TaskSourceKind;
  source_id: string;
  source_revision: number;
  source_digest: string;
  dataset_id: string | null;
  workspace_id: string | null;
  category: TaskCategory;
  normalized_status: TaskStatus;
  status: TaskDisplayStatus;
  action_required: boolean;
  progress_percent: number | null;
  progress: number | null;
  attempt_number: number;
  max_attempts: number;
  lease_owner: string | null;
  lease_until: string | null;
  safe_error_code: string | null;
  safe_error: string | null;
  target_route_code: TaskRouteCode;
  target_route_params: Readonly<Record<string, string>>;
  route: TaskRoute;
  source_current: boolean;
  projection_digest: string;
  occurred_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
  task_label: string;
  task_type: string;
  source_label: string | null;
  queue_name: string | null;
  created_at: string;
  next_retry_at: string | null;
  owner_label: string | null;
  duration_ms: number | null;
  error_code: string | null;
  retryable: boolean;
  cancellable: boolean;
  revision: number;
  mutation_generation: number;
  safe_snapshot: TaskSafeSnapshot;
}
export type Task = TaskProjection;
export type TaskOperation = TaskProjection;

export interface TaskOperatorAction {
  id: string;
  tenant_id: string;
  task_id: string;
  action_type: TaskActionType;
  status: TaskActionStatus;
  expected_source_revision: number;
  expected_source_digest: string;
  idempotency_digest: string;
  actor_id: string;
  request_id: string;
  safe_reason: string;
  requested_at: string;
  dispatched_at: string | null;
  applied_at: string | null;
  rejected_at: string | null;
  expires_at: string;
  result_code: string | null;
}
export type TaskAction = TaskOperatorAction;

export interface TaskEvent {
  id: string;
  tenant_id: string;
  task_id: string;
  sequence: number;
  event_type: TaskEventType;
  previous_event_digest: string | null;
  event_digest: string;
  actor_id: string;
  request_id: string;
  safe_snapshot: TaskSafeSnapshot;
  occurred_at: string;
}
export interface TaskDetail extends TaskProjection {
  events: TaskEvent[];
}
export type TaskProjectionDetail = TaskDetail;
export interface TaskPage<T> {
  items: T[];
  next_cursor: string | null;
  invalid_item_count: number;
}

export interface TaskSavedViewFilters {
  source_kinds: TaskSourceKind[];
  categories: TaskCategory[];
  statuses: TaskStatus[];
  action_required: boolean | null;
  dataset_id: string | null;
  workspace_id: string | null;
  occurred_from: string | null;
  occurred_to: string | null;
}
export interface TaskSavedView {
  id: string;
  tenant_id: string;
  account_id: string;
  name: string;
  status: TaskSavedViewStatus;
  filters: TaskSavedViewFilters;
  revision: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  created_by: string;
  updated_by: string;
}
export interface TaskReconciliationRun {
  id: string;
  tenant_id: string;
  source_kinds: TaskSourceKind[];
  source_inventory_digest: string;
  status: TaskReconciliationStatus;
  created_count: number;
  updated_count: number;
  stale_count: number;
  invalid_count: number;
  started_at: string;
  completed_at: string | null;
  safe_error_code: string | null;
  safe_error: string | null;
}
export interface TaskSummary {
  tenant_id: string;
  state: TaskSummaryState;
  queued_count: number | null;
  running_count: number | null;
  succeeded_count: number | null;
  completed_count: number | null;
  failed_count: number | null;
  cancelled_count: number | null;
  blocked_count: number | null;
  stale_count: number | null;
  action_required_count: number | null;
  reconciliation_count: number | null;
  retryable_count: number | null;
  as_of: string | null;
  reason_code: string | null;
}
export interface TaskActionOutcome {
  state: TaskMutationState;
  operation: string;
  resource_id: string | null;
  action_id: string | null;
  revision: number | null;
  message: string | null;
  retryable: boolean;
}

const ROUTES: Record<TaskRouteCode, { path: TaskRoute["path"]; allowed: readonly string[] }> = {
  document_operations: { path: "/documents", allowed: ["document_id"] },
  index_operations: { path: "/enterprise/knowledge-base", allowed: ["dataset_id", "operation_id"] },
  source_control: { path: "/sources", allowed: ["source_id"] },
  document_deletion: { path: "/documents", allowed: ["document_id"] },
  audit_compliance: { path: "/enterprise", allowed: ["export_id"] },
  release_quality: { path: "/enterprise", allowed: ["scan_id", "job_id"] },
};
const DIGEST = /^[0-9a-f]{64}$/;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/;
const CODE = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;
const SAFE_KEY = /^[A-Za-z][A-Za-z0-9_.-]{0,63}$/;
function hasControlCharacters(value: string): boolean {
  for (const character of value) {
    const code = character.charCodeAt(0);
    if (code < 32 || code === 127) return true;
  }
  return false;
}
const URL = /(?:https?|ftp|file|mailto|javascript|data):\S+|(?:^|\s)(?:www\.)\S+/i;
const SECRET =
  /(?:password|passwd|secret|credential|authorization|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|client[_ -]?secret|idempotency[_ -]?key|ticket|token)\s*[:=]|bearer\s+|(?:sk_(?:live|test)|ghp_|xox[baprs]-)/i;
const UNSAFE_KEY =
  /(?:body|content|metadata|query|note|comment|ticket|token|credential|secret|password|url|href|embedding|payload|result)/i;

function object(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error(`${field} must be an object`);
  return value as Record<string, unknown>;
}
function exactKeys(
  value: unknown,
  field: string,
  allowed: readonly string[],
): Record<string, unknown> {
  const source = object(value, field);
  const allow = new Set(allowed);
  for (const key of Object.keys(source))
    if (!allow.has(key)) throw new Error(`${field} contains unexpected field: ${key}`);
  return source;
}
function has(source: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(source, key);
}
function requiredText(value: unknown, field: string, max = 512): string {
  if (typeof value !== "string") throw new Error(`${field} is invalid`);
  const normalized = value.trim();
  if (!normalized || normalized.length > max || hasControlCharacters(normalized))
    throw new Error(`${field} is invalid`);
  return normalized;
}
function identifier(value: unknown, field: string, max = 128): string {
  const normalized = requiredText(value, field, max);
  if (!IDENTIFIER.test(normalized) || normalized.includes(".."))
    throw new Error(`${field} is invalid`);
  return normalized;
}
function code(value: unknown, field: string): string {
  const normalized = requiredText(value, field, 64);
  if (!CODE.test(normalized)) throw new Error(`${field} is invalid`);
  return normalized;
}
function exactBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${field} must be a boolean`);
  return value;
}
function exactInteger(value: unknown, field: string, minimum = 0, maximum?: number): number {
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
    value < minimum ||
    (maximum !== undefined && value > maximum)
  )
    throw new Error(`${field} must be an exact integer`);
  return value;
}
function nullableInteger(value: unknown, field: string, minimum = 0): number | null {
  return value === null || value === undefined ? null : exactInteger(value, field, minimum);
}
function nullableText(value: unknown, field: string, max = 512): string | null {
  return value === null || value === undefined ? null : requiredText(value, field, max);
}
function timestamp(value: unknown, field: string): string {
  const text = requiredText(value, field, 64);
  const parsed = new Date(text);
  if (!Number.isFinite(parsed.getTime()) || URL.test(text)) throw new Error(`${field} is invalid`);
  return parsed.toISOString().replace(/\.000Z$/, ".000000Z");
}
function nullableTimestamp(value: unknown, field: string): string | null {
  return value === null || value === undefined ? null : timestamp(value, field);
}
function digest(value: unknown, field: string): string {
  const text = requiredText(value, field, 64);
  if (!DIGEST.test(text)) throw new Error(`${field} is invalid`);
  return text;
}
function allowed<T extends string>(value: unknown, field: string, set: Set<T>): T {
  const text = requiredText(value, field, 64) as T;
  if (!set.has(text)) throw new Error(`${field} is not allowed`);
  return text;
}
function modelScope(scope: TaskModelScope): TaskModelScope {
  return {
    tenantId: identifier(scope.tenantId, "tenantId", 64),
    accountId: scope.accountId === undefined ? undefined : identifier(scope.accountId, "accountId"),
  };
}
function assertTenant(value: unknown, scope: TaskModelScope): string {
  const tenant = identifier(value, "tenant_id", 64);
  if (tenant !== scope.tenantId) throw new Error("tenant scope mismatch");
  return tenant;
}
function sourceKind(value: unknown, field = "source_kind"): TaskSourceKind {
  const kind = requiredText(value, field, 48) as TaskSourceKind;
  if (!isTaskSourceKind(kind)) throw new Error(`${field} is not allowed`);
  return kind;
}
function category(value: unknown, source: TaskSourceKind): TaskCategory {
  const normalized = requiredText(value, "category", 32);
  return displayTaskCategory(normalized, source);
}
function normalizedStatus(value: unknown): TaskStatus {
  const normalized = requiredText(value, "normalized_status", 32);
  return normalizeTaskStatus(normalized);
}
function safeMessage(value: unknown, field: string): string | null {
  if (value === null || value === undefined) return null;
  const text = requiredText(value, field);
  if (URL.test(text) || SECRET.test(text)) throw new Error(`${field} is unsafe`);
  return text;
}
function safeSnapshot(value: unknown, field: string): TaskSafeSnapshot {
  const source = object(value, field);
  if (Object.keys(source).length > 32) throw new Error(`${field} contains too many fields`);
  const result: Record<string, TaskSafeValue> = {};
  for (const [key, raw] of Object.entries(source)) {
    if (!SAFE_KEY.test(key) || UNSAFE_KEY.test(key)) throw new Error(`${field}.${key} is unsafe`);
    if (raw === null || typeof raw === "boolean") result[key] = raw;
    else if (typeof raw === "number" && Number.isFinite(raw)) result[key] = raw;
    else if (typeof raw === "string") {
      const text = requiredText(raw, `${field}.${key}`);
      if (URL.test(text) || SECRET.test(text)) throw new Error(`${field}.${key} is unsafe`);
      result[key] = text;
    } else throw new Error(`${field}.${key} must be a safe scalar`);
  }
  return result;
}
function optionalText(source: Record<string, unknown>, key: string, max = 256): string | null {
  return has(source, key) ? nullableText(source[key], key, max) : null;
}
function optionalTime(source: Record<string, unknown>, key: string): string | null {
  return has(source, key) ? nullableTimestamp(source[key], key) : null;
}
function routeParams(
  value: unknown,
  sourceKindValue: TaskSourceKind,
  routeCode: TaskRouteCode,
): Readonly<Record<string, string>> {
  const config = ROUTES[routeCode];
  if (!config) throw new Error("target_route_code is not allowed");
  const source = object(value, "target_route_params_json");
  const allowedKeys = new Set(config.allowed);
  const result: Record<string, string> = {};
  for (const key of Object.keys(source))
    if (!allowedKeys.has(key))
      throw new Error(`target_route_params_json contains unexpected field: ${key}`);
  for (const key of config.allowed)
    if (has(source, key)) result[key] = identifier(source[key], `target_route_params_json.${key}`);
  const required: Record<TaskSourceKind, readonly string[]> = {
    document_ingest: ["document_id"],
    index_operation: ["dataset_id", "operation_id"],
    source_sync: ["source_id"],
    document_delete: ["document_id"],
    audit_export: ["export_id"],
    release_quality_scan: ["scan_id"],
    release_recertification: ["job_id"],
  };
  for (const key of required[sourceKindValue])
    if (!result[key]) throw new Error(`target route requires ${key}`);
  return result;
}
function routeFromParams(
  sourceKindValue: TaskSourceKind,
  routeCode: TaskRouteCode,
  params: Readonly<Record<string, string>>,
): TaskRoute {
  const config = ROUTES[routeCode];
  const expected: Record<TaskSourceKind, TaskRouteCode> = {
    document_ingest: "document_operations",
    index_operation: "index_operations",
    source_sync: "source_control",
    document_delete: "document_deletion",
    audit_export: "audit_compliance",
    release_quality_scan: "release_quality",
    release_recertification: "release_quality",
  };
  if (expected[sourceKindValue] !== routeCode)
    throw new Error("target route does not match source_kind");
  const query: Record<string, string> = {};
  if (routeCode === "document_operations" || routeCode === "document_deletion")
    query.document = params.document_id;
  else if (routeCode === "index_operations") {
    query.dataset = params.dataset_id;
    query.operation = params.operation_id;
  } else if (routeCode === "source_control") query.source = params.source_id;
  else if (routeCode === "audit_compliance") {
    query.section = "audit";
    query.export = params.export_id;
  } else {
    query.section = "quality";
    query.run = params.scan_id ?? params.job_id;
  }
  for (const value of Object.values(query))
    if (!value || URL.test(value) || hasControlCharacters(value))
      throw new Error("target route value is unsafe");
  const queryString = new URLSearchParams(query).toString();
  return {
    code: routeCode,
    path: config.path,
    query,
    href: queryString ? `${config.path}?${queryString}` : config.path,
  };
}

export function projectTaskRoute(value: unknown, scope: TaskModelScope): TaskRoute {
  const source = exactKeys(value, "task_route", [
    "source_kind",
    "source_id",
    "dataset_id",
    "target_route_code",
    "target_route_params_json",
    "tenant_id",
  ]);
  const normalizedScope = modelScope(scope);
  if (has(source, "tenant_id")) assertTenant(source.tenant_id, normalizedScope);
  const kind = sourceKind(source.source_kind);
  const routeCode = requiredText(
    source.target_route_code,
    "target_route_code",
    64,
  ) as TaskRouteCode;
  if (!Object.prototype.hasOwnProperty.call(ROUTES, routeCode))
    throw new Error("target_route_code is not allowed");
  return routeFromParams(
    kind,
    routeCode,
    routeParams(source.target_route_params_json, kind, routeCode),
  );
}

const TASK_KEYS = [
  "id",
  "tenant_id",
  "source_kind",
  "source_id",
  "source_revision",
  "source_digest",
  "dataset_id",
  "workspace_id",
  "category",
  "normalized_status",
  "status",
  "action_required",
  "progress_percent",
  "progress",
  "attempt_number",
  "max_attempts",
  "lease_owner",
  "lease_until",
  "safe_error_code",
  "safe_error",
  "target_route_code",
  "target_route_params_json",
  "source_current",
  "projection_digest",
  "occurred_at",
  "started_at",
  "finished_at",
  "updated_at",
  "task_label",
  "task_type",
  "source_label",
  "queue_name",
  "created_at",
  "next_retry_at",
  "owner_label",
  "duration_ms",
  "error_code",
  "retryable",
  "cancellable",
  "mutation_generation",
  "safe_snapshot_json",
] as const;

export function projectTaskProjection(value: unknown, scope: TaskModelScope): TaskProjection {
  const source = exactKeys(value, "task_projection", TASK_KEYS);
  const normalizedScope = modelScope(scope);
  const tenantId = assertTenant(source.tenant_id, normalizedScope);
  const id = identifier(source.id, "id");
  const kind = sourceKind(source.source_kind);
  const sourceId = identifier(source.source_id, "source_id");
  const sourceRevision = exactInteger(source.source_revision, "source_revision");
  const sourceDigest = digest(source.source_digest, "source_digest");
  const datasetId =
    source.dataset_id === null || source.dataset_id === undefined
      ? null
      : identifier(source.dataset_id, "dataset_id");
  const workspaceId =
    source.workspace_id === null || source.workspace_id === undefined
      ? null
      : identifier(source.workspace_id, "workspace_id");
  const normalizedCategory = category(source.category, kind);
  const status = normalizedStatus(
    has(source, "normalized_status") ? source.normalized_status : source.status,
  );
  const progressValue = has(source, "progress_percent") ? source.progress_percent : source.progress;
  const progress =
    progressValue === null || progressValue === undefined
      ? null
      : exactInteger(progressValue, "progress_percent", 0, 100);
  const attemptNumber = exactInteger(source.attempt_number, "attempt_number");
  const maxAttempts = exactInteger(source.max_attempts, "max_attempts");
  if (maxAttempts > 0 && attemptNumber > maxAttempts)
    throw new Error("attempt_number exceeds max_attempts");
  const occurredAt = timestamp(source.occurred_at, "occurred_at");
  const startedAt = nullableTimestamp(source.started_at, "started_at");
  const finishedAt = nullableTimestamp(source.finished_at, "finished_at");
  const updatedAt = timestamp(source.updated_at, "updated_at");
  const routeCode = requiredText(
    source.target_route_code,
    "target_route_code",
    64,
  ) as TaskRouteCode;
  if (!Object.prototype.hasOwnProperty.call(ROUTES, routeCode))
    throw new Error("target_route_code is not allowed");
  const params = routeParams(source.target_route_params_json, kind, routeCode);
  const route = routeFromParams(kind, routeCode, params);
  const safeErrorCode =
    source.safe_error_code === null || source.safe_error_code === undefined
      ? null
      : code(source.safe_error_code, "safe_error_code");
  const safeError = safeMessage(source.safe_error, "safe_error");
  const snapshot = has(source, "safe_snapshot_json")
    ? safeSnapshot(source.safe_snapshot_json, "safe_snapshot_json")
    : {};
  const retryable = has(source, "retryable")
    ? exactBoolean(source.retryable, "retryable")
    : (status === "failed" || status === "blocked") &&
      (maxAttempts === 0 || attemptNumber < maxAttempts);
  const cancellable = has(source, "cancellable")
    ? exactBoolean(source.cancellable, "cancellable")
    : status === "queued" || status === "running";
  const mutationGeneration = has(source, "mutation_generation")
    ? exactInteger(source.mutation_generation, "mutation_generation")
    : sourceRevision;
  return {
    id,
    tenant_id: tenantId,
    source_kind: kind,
    source_id: sourceId,
    source_revision: sourceRevision,
    source_digest: sourceDigest,
    dataset_id: datasetId,
    workspace_id: workspaceId,
    category: normalizedCategory,
    normalized_status: status,
    status: displayTaskStatus(status),
    action_required: exactBoolean(source.action_required, "action_required"),
    progress_percent: progress,
    progress,
    attempt_number: attemptNumber,
    max_attempts: maxAttempts,
    lease_owner: optionalText(source, "lease_owner"),
    lease_until: optionalTime(source, "lease_until"),
    safe_error_code: safeErrorCode,
    safe_error: safeError,
    target_route_code: routeCode,
    target_route_params: params,
    route,
    source_current: exactBoolean(source.source_current, "source_current"),
    projection_digest: digest(source.projection_digest, "projection_digest"),
    occurred_at: occurredAt,
    started_at: startedAt,
    finished_at: finishedAt,
    updated_at: updatedAt,
    task_label: has(source, "task_label")
      ? requiredText(source.task_label, "task_label", 160)
      : sourceId,
    task_type: has(source, "task_type") ? code(source.task_type, "task_type") : kind,
    source_label: optionalText(source, "source_label", 160),
    queue_name: optionalText(source, "queue_name", 128),
    created_at: has(source, "created_at") ? timestamp(source.created_at, "created_at") : occurredAt,
    next_retry_at: optionalTime(source, "next_retry_at"),
    owner_label: optionalText(source, "owner_label", 160),
    duration_ms: has(source, "duration_ms")
      ? nullableInteger(source.duration_ms, "duration_ms")
      : null,
    error_code: safeErrorCode,
    retryable,
    cancellable,
    revision: sourceRevision,
    mutation_generation: mutationGeneration,
    safe_snapshot: snapshot,
  };
}
export const projectTask = projectTaskProjection;
const ACTION_KEYS = [
  "id",
  "tenant_id",
  "task_id",
  "action_type",
  "status",
  "expected_source_revision",
  "expected_source_digest",
  "idempotency_digest",
  "actor_id",
  "request_id",
  "safe_reason",
  "requested_at",
  "dispatched_at",
  "applied_at",
  "rejected_at",
  "expires_at",
  "result_code",
] as const;
const ACTION_TYPES = new Set<TaskActionType>(["retry", "cancel", "acknowledge"]);
const ACTION_STATUSES = new Set<TaskActionStatus>([
  "requested",
  "dispatched",
  "applied",
  "rejected",
  "expired",
]);
const EVENT_KEYS = [
  "id",
  "tenant_id",
  "task_id",
  "sequence",
  "event_type",
  "previous_event_digest",
  "event_digest",
  "actor_id",
  "request_id",
  "safe_snapshot_json",
  "occurred_at",
] as const;
const EVENT_TYPES = new Set<TaskEventType>([
  "materialized",
  "status_changed",
  "source_stale",
  "action_requested",
  "action_applied",
  "action_rejected",
  "attention_acknowledged",
]);

function safeReason(value: unknown): string {
  const reason = requiredText(value, "safe_reason");
  if (URL.test(reason) || SECRET.test(reason)) throw new Error("safe_reason is unsafe");
  return reason;
}
export function projectTaskAction(value: unknown, scope: TaskModelScope): TaskOperatorAction {
  const source = exactKeys(value, "task_action", ACTION_KEYS);
  const normalizedScope = modelScope(scope);
  return {
    id: identifier(source.id, "id"),
    tenant_id: assertTenant(source.tenant_id, normalizedScope),
    task_id: identifier(source.task_id, "task_id"),
    action_type: allowed(source.action_type, "action_type", ACTION_TYPES),
    status: allowed(source.status, "status", ACTION_STATUSES),
    expected_source_revision: exactInteger(
      source.expected_source_revision,
      "expected_source_revision",
    ),
    expected_source_digest: digest(source.expected_source_digest, "expected_source_digest"),
    idempotency_digest: digest(source.idempotency_digest, "idempotency_digest"),
    actor_id: identifier(source.actor_id, "actor_id"),
    request_id: identifier(source.request_id, "request_id"),
    safe_reason: safeReason(source.safe_reason),
    requested_at: timestamp(source.requested_at, "requested_at"),
    dispatched_at: nullableTimestamp(source.dispatched_at, "dispatched_at"),
    applied_at: nullableTimestamp(source.applied_at, "applied_at"),
    rejected_at: nullableTimestamp(source.rejected_at, "rejected_at"),
    expires_at: timestamp(source.expires_at, "expires_at"),
    result_code:
      source.result_code === null || source.result_code === undefined
        ? null
        : code(source.result_code, "result_code"),
  };
}
export const projectTaskOperatorAction = projectTaskAction;

export function projectTaskEvent(value: unknown, scope: TaskModelScope): TaskEvent {
  const source = exactKeys(value, "task_event", EVENT_KEYS);
  const normalizedScope = modelScope(scope);
  return {
    id: identifier(source.id, "id"),
    tenant_id: assertTenant(source.tenant_id, normalizedScope),
    task_id: identifier(source.task_id, "task_id"),
    sequence: exactInteger(source.sequence, "sequence", 1),
    event_type: allowed(source.event_type, "event_type", EVENT_TYPES),
    previous_event_digest:
      source.previous_event_digest === null
        ? null
        : digest(source.previous_event_digest, "previous_event_digest"),
    event_digest: digest(source.event_digest, "event_digest"),
    actor_id: identifier(source.actor_id, "actor_id"),
    request_id: identifier(source.request_id, "request_id"),
    safe_snapshot: safeSnapshot(source.safe_snapshot_json, "safe_snapshot_json"),
    occurred_at: timestamp(source.occurred_at, "occurred_at"),
  };
}
function projectEventChain(value: unknown, scope: TaskModelScope, taskId: string): TaskEvent[] {
  if (!Array.isArray(value)) throw new Error("task events must be an array");
  const result: TaskEvent[] = [];
  for (const raw of value) {
    const event = projectTaskEvent(raw, scope);
    if (event.task_id !== taskId) throw new Error("task event identity is inconsistent");
    const previous = result[result.length - 1];
    if (!previous) {
      if (
        event.sequence !== 1 ||
        event.previous_event_digest !== null ||
        event.event_type !== "materialized"
      )
        throw new Error("task event chain must start with materialized sequence 1");
    } else if (
      event.sequence !== previous.sequence + 1 ||
      event.previous_event_digest !== previous.event_digest
    )
      throw new Error("task event chain is inconsistent");
    result.push(event);
  }
  return result;
}
export function projectTaskDetail(value: unknown, scope: TaskModelScope): TaskDetail {
  const source = exactKeys(value, "task_detail", ["task", "events"]);
  const task = projectTaskProjection(source.task, scope);
  return { ...task, events: projectEventChain(source.events, scope, task.id) };
}
export const projectTaskProjectionDetail = projectTaskDetail;

export function projectTaskPage(value: unknown, scope: TaskModelScope): TaskPage<TaskProjection> {
  const source = exactKeys(value, "task_page", ["items", "next_cursor", "invalid_item_count"]);
  if (!Array.isArray(source.items)) throw new Error("task_page.items must be an array");
  return {
    items: source.items.map((item) => projectTaskProjection(item, scope)),
    next_cursor:
      source.next_cursor === null ? null : identifier(source.next_cursor, "next_cursor", 2048),
    invalid_item_count: exactInteger(source.invalid_item_count, "invalid_item_count"),
  };
}
export function projectTaskEventPage(value: unknown, scope: TaskModelScope): TaskPage<TaskEvent> {
  const source = exactKeys(value, "task_event_page", [
    "items",
    "next_cursor",
    "invalid_item_count",
  ]);
  if (!Array.isArray(source.items)) throw new Error("task_event_page.items must be an array");
  return {
    items: source.items.map((item) => projectTaskEvent(item, scope)),
    next_cursor:
      source.next_cursor === null ? null : identifier(source.next_cursor, "next_cursor", 2048),
    invalid_item_count: exactInteger(source.invalid_item_count, "invalid_item_count"),
  };
}

function categoryFilter(value: unknown, field: string): TaskCategory {
  const normalized = requiredText(value, field, 32);
  return savedViewTaskCategory(normalized, field);
}
function statusFilter(value: unknown, field: string): TaskStatus {
  const normalized = requiredText(value, field, 32);
  return normalizeTaskStatus(normalized, field);
}
function stringArray<T extends string>(
  value: unknown,
  field: string,
  projector: (item: unknown, field: string) => T,
): T[] {
  if (!Array.isArray(value) || value.length > 7)
    throw new Error(`${field} must be a bounded array`);
  const result = value.map((item) => projector(item, field));
  if (new Set(result).size !== result.length)
    throw new Error(`${field} must contain unique values`);
  return result;
}
export function projectTaskSavedViewFilters(value: unknown): TaskSavedViewFilters {
  const source = exactKeys(value, "task_saved_view_filters", [
    "source_kinds",
    "categories",
    "statuses",
    "action_required",
    "dataset_id",
    "workspace_id",
    "occurred_from",
    "occurred_to",
  ]);
  const sourceKinds = stringArray(source.source_kinds, "source_kinds", sourceKind);
  const categories = stringArray(source.categories, "categories", categoryFilter);
  const statuses = stringArray(source.statuses, "statuses", statusFilter);
  const actionRequired =
    source.action_required === null || source.action_required === undefined
      ? null
      : exactBoolean(source.action_required, "action_required");
  const datasetId =
    source.dataset_id === null || source.dataset_id === undefined
      ? null
      : identifier(source.dataset_id, "dataset_id");
  const workspaceId =
    source.workspace_id === null || source.workspace_id === undefined
      ? null
      : identifier(source.workspace_id, "workspace_id");
  const occurredFrom =
    source.occurred_from === null || source.occurred_from === undefined
      ? null
      : timestamp(source.occurred_from, "occurred_from");
  const occurredTo =
    source.occurred_to === null || source.occurred_to === undefined
      ? null
      : timestamp(source.occurred_to, "occurred_to");
  if ((occurredFrom === null) !== (occurredTo === null))
    throw new Error("time range must be bounded");
  if (
    occurredFrom &&
    occurredTo &&
    new Date(occurredFrom).getTime() > new Date(occurredTo).getTime()
  )
    throw new Error("time range is reversed");
  return {
    source_kinds: sourceKinds,
    categories,
    statuses,
    action_required: actionRequired,
    dataset_id: datasetId,
    workspace_id: workspaceId,
    occurred_from: occurredFrom,
    occurred_to: occurredTo,
  };
}
const VIEW_KEYS = [
  "id",
  "tenant_id",
  "account_id",
  "name",
  "status",
  "filters_json",
  "revision",
  "created_at",
  "updated_at",
  "archived_at",
  "created_by",
  "updated_by",
] as const;
export function projectTaskSavedView(value: unknown, scope: TaskModelScope): TaskSavedView {
  const source = exactKeys(value, "task_saved_view", VIEW_KEYS);
  const normalizedScope = modelScope(scope);
  if (!normalizedScope.accountId) throw new Error("account scope is required for saved views");
  const accountId = identifier(source.account_id, "account_id");
  if (accountId !== normalizedScope.accountId) throw new Error("account scope mismatch");
  return {
    id: identifier(source.id, "id"),
    tenant_id: assertTenant(source.tenant_id, normalizedScope),
    account_id: accountId,
    name: requiredText(source.name, "name", 96),
    status: allowed(source.status, "status", new Set<TaskSavedViewStatus>(["active", "archived"])),
    filters: projectTaskSavedViewFilters(source.filters_json),
    revision: exactInteger(source.revision, "revision"),
    created_at: timestamp(source.created_at, "created_at"),
    updated_at: timestamp(source.updated_at, "updated_at"),
    archived_at: nullableTimestamp(source.archived_at, "archived_at"),
    created_by: identifier(source.created_by, "created_by"),
    updated_by: identifier(source.updated_by, "updated_by"),
  };
}
export function projectTaskSavedViewPage(
  value: unknown,
  scope: TaskModelScope,
): TaskPage<TaskSavedView> {
  const source = exactKeys(value, "task_saved_view_page", [
    "items",
    "next_cursor",
    "invalid_item_count",
  ]);
  if (!Array.isArray(source.items)) throw new Error("task_saved_view_page.items must be an array");
  return {
    items: source.items.map((item) => projectTaskSavedView(item, scope)),
    next_cursor:
      source.next_cursor === null ? null : identifier(source.next_cursor, "next_cursor", 2048),
    invalid_item_count: exactInteger(source.invalid_item_count, "invalid_item_count"),
  };
}
const RECONCILIATION_KEYS = [
  "id",
  "tenant_id",
  "source_kinds",
  "source_inventory_digest",
  "status",
  "created_count",
  "updated_count",
  "stale_count",
  "invalid_count",
  "started_at",
  "completed_at",
  "safe_error_code",
  "safe_error",
] as const;
export function projectTaskReconciliationRun(
  value: unknown,
  scope: TaskModelScope,
): TaskReconciliationRun {
  const source = exactKeys(value, "task_reconciliation_run", RECONCILIATION_KEYS);
  const normalizedScope = modelScope(scope);
  const status = allowed(
    source.status,
    "status",
    new Set<TaskReconciliationStatus>(["started", "completed", "failed"]),
  );
  const completedAt = nullableTimestamp(source.completed_at, "completed_at");
  if (status === "completed" && completedAt === null)
    throw new Error("completed reconciliation requires completed_at");
  if (status === "started" && completedAt !== null)
    throw new Error("started reconciliation cannot be completed");
  return {
    id: identifier(source.id, "id"),
    tenant_id: assertTenant(source.tenant_id, normalizedScope),
    source_kinds: stringArray(source.source_kinds, "source_kinds", sourceKind),
    source_inventory_digest: digest(source.source_inventory_digest, "source_inventory_digest"),
    status,
    created_count: exactInteger(source.created_count, "created_count"),
    updated_count: exactInteger(source.updated_count, "updated_count"),
    stale_count: exactInteger(source.stale_count, "stale_count"),
    invalid_count: exactInteger(source.invalid_count, "invalid_count"),
    started_at: timestamp(source.started_at, "started_at"),
    completed_at: completedAt,
    safe_error_code:
      source.safe_error_code === null || source.safe_error_code === undefined
        ? null
        : code(source.safe_error_code, "safe_error_code"),
    safe_error: safeMessage(source.safe_error, "safe_error"),
  };
}
export function projectTaskReconciliationRunPage(
  value: unknown,
  scope: TaskModelScope,
): TaskPage<TaskReconciliationRun> {
  const source = exactKeys(value, "task_reconciliation_page", [
    "items",
    "next_cursor",
    "invalid_item_count",
  ]);
  if (!Array.isArray(source.items))
    throw new Error("task_reconciliation_page.items must be an array");
  return {
    items: source.items.map((item) => projectTaskReconciliationRun(item, scope)),
    next_cursor:
      source.next_cursor === null ? null : identifier(source.next_cursor, "next_cursor", 2048),
    invalid_item_count: exactInteger(source.invalid_item_count, "invalid_item_count"),
  };
}

const SUMMARY_KEYS = [
  "tenant_id",
  "state",
  "queued_count",
  "running_count",
  "succeeded_count",
  "completed_count",
  "failed_count",
  "cancelled_count",
  "blocked_count",
  "stale_count",
  "action_required_count",
  "reconciliation_count",
  "retryable_count",
  "as_of",
  "reason_code",
] as const;
export function projectTaskSummary(value: unknown, scope: TaskModelScope): TaskSummary {
  const source = exactKeys(value, "task_summary", SUMMARY_KEYS);
  const normalizedScope = modelScope(scope);
  const state = allowed(
    source.state,
    "state",
    new Set<TaskSummaryState>(["ready", "partial", "unavailable", "error"]),
  );
  const hasSucceeded = has(source, "succeeded_count");
  const hasCompleted = has(source, "completed_count");
  if (hasSucceeded === hasCompleted)
    throw new Error("task_summary must contain exactly one succeeded_count/completed_count");
  const succeeded = nullableInteger(
    hasSucceeded ? source.succeeded_count : source.completed_count,
    hasSucceeded ? "succeeded_count" : "completed_count",
  );
  const queued = nullableInteger(source.queued_count, "queued_count");
  const running = nullableInteger(source.running_count, "running_count");
  const failed = nullableInteger(source.failed_count, "failed_count");
  const cancelled = nullableInteger(source.cancelled_count, "cancelled_count");
  const blocked = nullableInteger(source.blocked_count, "blocked_count");
  const stale = nullableInteger(source.stale_count, "stale_count");
  const actionRequired = nullableInteger(source.action_required_count, "action_required_count");
  const reconciliation = nullableInteger(source.reconciliation_count, "reconciliation_count");
  const retryable = nullableInteger(source.retryable_count, "retryable_count");
  const asOf = nullableTimestamp(source.as_of, "as_of");
  if (
    state === "ready" &&
    [queued, running, succeeded, failed, cancelled, blocked, stale, actionRequired, asOf].some(
      (item) => item === null,
    )
  )
    throw new Error("ready task summary must contain authoritative counts");
  if (
    state === "unavailable" &&
    [queued, running, succeeded, failed, cancelled, blocked, stale, actionRequired, asOf].some(
      (item) => item !== null,
    )
  )
    throw new Error("unavailable task summary must not claim counts");
  return {
    tenant_id: assertTenant(source.tenant_id, normalizedScope),
    state,
    queued_count: queued,
    running_count: running,
    succeeded_count: succeeded,
    completed_count: succeeded,
    failed_count: failed,
    cancelled_count: cancelled,
    blocked_count: blocked,
    stale_count: stale,
    action_required_count: actionRequired,
    reconciliation_count: reconciliation,
    retryable_count: retryable,
    as_of: asOf,
    reason_code:
      source.reason_code === null || source.reason_code === undefined
        ? null
        : code(source.reason_code, "reason_code"),
  };
}

const OUTCOME_KEYS = [
  "state",
  "operation",
  "resource_id",
  "action_id",
  "revision",
  "message",
  "retryable",
] as const;
export function projectTaskActionOutcome(value: unknown): TaskActionOutcome {
  const source = exactKeys(value, "task_action_outcome", OUTCOME_KEYS);
  return {
    state: allowed(
      source.state,
      "state",
      new Set<TaskMutationState>([
        "applied",
        "replayed",
        "conflict",
        "blocked",
        "rejected",
        "unavailable",
      ]),
    ),
    operation: code(source.operation, "operation"),
    resource_id: source.resource_id === null ? null : identifier(source.resource_id, "resource_id"),
    action_id: source.action_id === null ? null : identifier(source.action_id, "action_id"),
    revision: nullableInteger(source.revision, "revision"),
    message: safeMessage(source.message, "message"),
    retryable: exactBoolean(source.retryable, "retryable"),
  };
}
export const projectTaskMutationOutcome = projectTaskActionOutcome;
export function sanitizeTaskMessage(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const text = value.trim();
  return !text || text.length > 512 || hasControlCharacters(text) || URL.test(text) || SECRET.test(text)
    ? fallback
    : text;
}
