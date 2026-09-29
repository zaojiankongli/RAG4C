import { ApiError, request } from "../../api/client";
import {
  TASK_SOURCE_KIND_VALUES,
  TASK_STATUS_FACT_VALUES,
} from "../model/taskVocabulary";
import {
  projectTaskActionOutcome,
  projectTaskDetail,
  projectTaskEventPage,
  projectTaskPage,
  projectTaskReconciliationRunPage,
  projectTaskSavedViewFilters,
  projectTaskSavedViewPage,
  projectTaskSummary,
  type TaskActionOutcome,
  type TaskApiCategory,
  type TaskDetail,
  type TaskEvent,
  type TaskModelScope,
  type TaskPage,
  type TaskProjection,
  type TaskReconciliationRun,
  type TaskSavedView,
  type TaskSavedViewFilters,
  type TaskSourceKind,
  type TaskStatus,
  type TaskSummary,
} from "../model/taskModel";

export type {
  TaskAction,
  TaskActionOutcome,
  TaskActionStatus,
  TaskActionType,
  TaskApiCategory,
  TaskCategory,
  TaskDetail,
  TaskDisplayStatus,
  TaskEvent,
  TaskEventType,
  TaskMutationState,
  TaskOperatorAction,
  TaskOperation,
  TaskPage,
  TaskProjection,
  TaskReconciliationRun,
  TaskReconciliationStatus,
  TaskRoute,
  TaskSavedView,
  TaskSavedViewFilters,
  TaskSavedViewStatus,
  TaskSourceKind,
  TaskStatus,
  TaskSummary,
  TaskSummaryState,
} from "../model/taskModel";

export interface TaskApiScope {
  tenantId: string;
  actorToken: string;
  accountId?: string;
}
export interface TaskRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}
export interface TaskPageQuery {
  cursor?: string | null;
  limit?: number;
  status?: TaskStatus | "all";
  sourceKind?: TaskSourceKind;
  actionRequired?: boolean;
}
export interface TaskEventQuery {
  cursor?: string | null;
  limit?: number;
}
export interface TaskViewQuery {
  cursor?: string | null;
  limit?: number;
  status?: "active" | "archived" | "all";
}
export interface TaskReconciliationQuery {
  cursor?: string | null;
  limit?: number;
  status?: "started" | "completed" | "failed" | "all";
}
export interface TaskActionInput {
  expectedSourceRevision: number;
  expectedSourceDigest: string;
  reason: string;
}
export interface TaskSavedViewInput {
  name: string;
  filters: TaskSavedViewFilterInput;
  reason: string;
}
export type TaskSavedViewFilterInput = Omit<
  Partial<TaskSavedViewFilters>,
  "categories"
> & {
  categories?: TaskApiCategory[];
};
export interface UpdateTaskSavedViewInput {
  expectedRevision: number;
  name?: string;
  filters?: TaskSavedViewFilterInput;
  status?: "active" | "archived";
  reason: string;
}
export interface TaskReconciliationInput {
  sourceKinds?: TaskSourceKind[];
  reason: string;
}

export interface TaskApi {
  fetchSummary: typeof fetchTaskSummary;
  fetchTasks: typeof fetchTasks;
  fetchTask: typeof fetchTask;
  fetchTaskEvents: typeof fetchTaskEvents;
  fetchViews: typeof fetchTaskViews;
  fetchReconciliationRuns: typeof fetchReconciliationRuns;
  retryTask: typeof retryTask;
  cancelTask: typeof cancelTask;
  acknowledgeTask: typeof acknowledgeTask;
  createView: typeof createTaskSavedView;
  updateView: typeof updateTaskSavedView;
  previewReconciliation: typeof previewTaskReconciliation;
  reconcile: typeof reconcileTasks;
}

const SOURCE_KINDS = new Set<TaskSourceKind>(TASK_SOURCE_KIND_VALUES);
const STATUS = new Set<TaskStatus>(TASK_STATUS_FACT_VALUES);
function hasControlCharacters(value: string): boolean {
  for (const character of value) {
    const code = character.charCodeAt(0);
    if (code < 32 || code === 127) return true;
  }
  return false;
}
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const URL = /(?:https?|ftp|file|mailto|javascript|data):\S+|(?:^|\s)(?:www\.)\S+/i;
const SECRET =
  /(?:password|passwd|secret|credential|authorization|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|client[_ -]?secret|idempotency[_ -]?key|ticket|token)\s*[:=]|bearer\s+|(?:sk_(?:live|test)|ghp_|xox[baprs]-)/i;

function object(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error(`${field} is unavailable`);
  return value as Record<string, unknown>;
}
function text(value: unknown, field: string, max = 512): string {
  if (typeof value !== "string") throw new Error(`${field} is invalid`);
  const normalized = value.trim();
  if (!normalized || normalized.length > max || hasControlCharacters(normalized))
    throw new Error(`${field} is invalid`);
  return normalized;
}
function identifier(value: unknown, field: string, max = 128): string {
  const normalized = text(value, field, max);
  if (!IDENTIFIER.test(normalized) || normalized.includes(".."))
    throw new Error(`${field} is invalid`);
  return normalized;
}
function digest(value: unknown, field: string): string {
  const normalized = text(value, field, 64);
  if (!DIGEST.test(normalized)) throw new Error(`${field} is invalid`);
  return normalized;
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
function exactBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${field} must be a boolean`);
  return value;
}
function reason(value: unknown): string {
  const normalized = text(value, "reason", 512);
  if (URL.test(normalized) || SECRET.test(normalized)) throw new Error("reason is unsafe");
  return normalized;
}
function normalizedScope(scope: TaskApiScope): TaskApiScope {
  return {
    tenantId: identifier(scope.tenantId, "tenantId", 64),
    actorToken: text(scope.actorToken, "actorToken", 2048),
    accountId: scope.accountId === undefined ? undefined : identifier(scope.accountId, "accountId"),
  };
}
function modelScope(scope: TaskApiScope): TaskModelScope {
  const normalized = normalizedScope(scope);
  return { tenantId: normalized.tenantId, accountId: normalized.accountId };
}
function headers(scope: TaskApiScope, idempotencyKey?: string): Record<string, string> {
  const normalized = normalizedScope(scope);
  const result: Record<string, string> = {
    "Content-Type": "application/json",
    "X-RAG4C-Tenant": normalized.tenantId,
    Authorization: `Bearer ${normalized.actorToken}`,
  };
  if (normalized.accountId) result["X-RAG4C-Account"] = normalized.accountId;
  if (idempotencyKey !== undefined)
    result["Idempotency-Key"] = text(idempotencyKey, "idempotencyKey", 128);
  return result;
}
function requiredMutationKey(options?: TaskRequestOptions): string {
  if (!options?.idempotencyKey) throw new Error("idempotencyKey is required");
  return text(options.idempotencyKey, "idempotencyKey", 128);
}
function safeId(value: unknown, field = "id"): string {
  return identifier(value, field);
}
function pageLimit(value: unknown, field = "limit"): number {
  return exactInteger(value, field, 1, 200);
}
function cursor(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  return text(value, "cursor", 2048);
}
function sourceKind(value: unknown): TaskSourceKind {
  const normalized = text(value, "sourceKind", 48) as TaskSourceKind;
  if (!SOURCE_KINDS.has(normalized)) throw new Error("sourceKind is not allowed");
  return normalized;
}
function queryStatus(value: unknown): TaskStatus | "all" {
  const normalized = text(value, "status", 32) as TaskStatus | "all";
  if (normalized !== "all" && !STATUS.has(normalized)) throw new Error("status is not allowed");
  return normalized;
}
function safeRequest<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  return request<T>(path, init).catch((error: unknown) => {
    if (error instanceof ApiError && error.kind === "aborted") throw error;
    throw new Error(fallback);
  });
}
function queryString(query: TaskPageQuery = {}): string {
  const input = inputKeys(query, "taskQuery", [
    "cursor",
    "limit",
    "status",
    "sourceKind",
    "actionRequired",
  ]);
  const params = new URLSearchParams();
  if (input.actionRequired !== undefined)
    params.set("action_required", String(exactBoolean(input.actionRequired, "actionRequired")));
  const nextCursor = cursor(input.cursor);
  if (nextCursor) params.set("cursor", nextCursor);
  params.set("limit", String(pageLimit(input.limit ?? 50)));
  if (input.sourceKind !== undefined)
    params.set("source_kind", sourceKind(input.sourceKind));
  if (input.status !== undefined && input.status !== "all")
    params.set("status", queryStatus(input.status));
  const queryText = params.toString();
  return queryText ? `?${queryText}` : "";
}
function childQuery(query: TaskEventQuery = {}): string {
  const params = new URLSearchParams();
  const nextCursor = cursor(query.cursor);
  if (nextCursor) params.set("cursor", nextCursor);
  params.set("limit", String(pageLimit(query.limit ?? 50)));
  return `?${params.toString()}`;
}
function viewQuery(query: TaskViewQuery = {}): string {
  const params = new URLSearchParams();
  const nextCursor = cursor(query.cursor);
  if (nextCursor) params.set("cursor", nextCursor);
  params.set("limit", String(pageLimit(query.limit ?? 50)));
  if (query.status !== undefined && query.status !== "all") params.set("status", query.status);
  return `?${params.toString()}`;
}
function reconciliationQuery(query: TaskReconciliationQuery = {}): string {
  const params = new URLSearchParams();
  const nextCursor = cursor(query.cursor);
  if (nextCursor) params.set("cursor", nextCursor);
  params.set("limit", String(pageLimit(query.limit ?? 50)));
  if (query.status !== undefined && query.status !== "all") params.set("status", query.status);
  return `?${params.toString()}`;
}
function pathId(value: unknown, field = "id"): string {
  return encodeURIComponent(safeId(value, field));
}
function inputKeys(
  value: unknown,
  field: string,
  allowedKeys: readonly string[],
): Record<string, unknown> {
  const source = object(value, field);
  const allowedSet = new Set(allowedKeys);
  for (const key of Object.keys(source))
    if (!allowedSet.has(key)) throw new Error(`${field} contains unexpected field: ${key}`);
  return source;
}
function normalizedSourceKinds(value: unknown): TaskSourceKind[] | undefined {
  if (value === undefined) return undefined;
  if (!Array.isArray(value) || value.length > 7) throw new Error("sourceKinds is invalid");
  const result = value.map((item) => sourceKind(item));
  if (new Set(result).size !== result.length) throw new Error("sourceKinds must be unique");
  return result;
}
function normalizeFilters(value: unknown): TaskSavedViewFilters {
  const source = inputKeys(value, "filters", [
    "source_kinds",
    "categories",
    "statuses",
    "action_required",
    "dataset_id",
    "workspace_id",
    "occurred_from",
    "occurred_to",
  ]);
  return projectTaskSavedViewFilters({
    source_kinds: source.source_kinds ?? [],
    categories: source.categories ?? [],
    statuses: source.statuses ?? [],
    action_required: source.action_required ?? null,
    dataset_id: source.dataset_id ?? null,
    workspace_id: source.workspace_id ?? null,
    occurred_from: source.occurred_from ?? null,
    occurred_to: source.occurred_to ?? null,
  });
}
function actionBody(input: unknown): {
  expected_source_revision: number;
  expected_source_digest: string;
  reason: string;
} {
  const source = inputKeys(input, "action", [
    "expectedSourceRevision",
    "expectedSourceDigest",
    "reason",
  ]);
  return {
    expected_source_revision: exactInteger(
      source.expectedSourceRevision,
      "expectedSourceRevision",
      1,
    ),
    expected_source_digest: digest(source.expectedSourceDigest, "expectedSourceDigest"),
    reason: reason(source.reason),
  };
}
function mutationRequest(
  scope: TaskApiScope,
  path: string,
  method: "POST" | "PATCH",
  body: Record<string, unknown>,
  options: TaskRequestOptions | undefined,
  fallback: string,
): Promise<TaskActionOutcome> {
  const key = requiredMutationKey(options);
  return safeRequest<unknown>(
    path,
    { method, headers: headers(scope, key), body: JSON.stringify(body), signal: options?.signal },
    fallback,
  ).then(projectTaskActionOutcome);
}

export function fetchTaskSummary(
  scope: TaskApiScope,
  options: TaskRequestOptions = {},
): Promise<TaskSummary> {
  const normalized = normalizedScope(scope);
  return safeRequest<unknown>(
    "/api/enterprise/tasks/summary",
    { method: "GET", headers: headers(normalized), signal: options.signal },
    "Task summary is unavailable",
  ).then((raw) => projectTaskSummary(raw, modelScope(normalized)));
}
export function fetchTasks(
  scope: TaskApiScope,
  query: TaskPageQuery = {},
  options: TaskRequestOptions = {},
): Promise<TaskPage<TaskProjection>> {
  const normalized = normalizedScope(scope);
  const path = `/api/enterprise/tasks${queryString(query)}`;
  return safeRequest<unknown>(
    path,
    { method: "GET", headers: headers(normalized), signal: options.signal },
    "Task list is unavailable",
  ).then((raw) => projectTaskPage(raw, modelScope(normalized)));
}
export function fetchTask(
  scope: TaskApiScope,
  taskId: string,
  options: TaskRequestOptions = {},
): Promise<TaskDetail> {
  const normalized = normalizedScope(scope);
  const path = `/api/enterprise/tasks/${pathId(taskId, "taskId")}`;
  return safeRequest<unknown>(
    path,
    { method: "GET", headers: headers(normalized), signal: options.signal },
    "Task detail is unavailable",
  ).then((raw) => projectTaskDetail(raw, modelScope(normalized)));
}
export function fetchTaskEvents(
  scope: TaskApiScope,
  taskId: string,
  query: TaskEventQuery = {},
  options: TaskRequestOptions = {},
): Promise<TaskPage<TaskEvent>> {
  const normalized = normalizedScope(scope);
  const path = `/api/enterprise/tasks/${pathId(taskId, "taskId")}/events${childQuery(query)}`;
  return safeRequest<unknown>(
    path,
    { method: "GET", headers: headers(normalized), signal: options.signal },
    "Task events are unavailable",
  ).then((raw) => projectTaskEventPage(raw, modelScope(normalized)));
}
export function fetchTaskViews(
  scope: TaskApiScope,
  query: TaskViewQuery = {},
  options: TaskRequestOptions = {},
): Promise<TaskPage<TaskSavedView>> {
  const normalized = normalizedScope(scope);
  const path = `/api/enterprise/task-views${viewQuery(query)}`;
  return safeRequest<unknown>(
    path,
    { method: "GET", headers: headers(normalized), signal: options.signal },
    "Task views are unavailable",
  ).then((raw) => projectTaskSavedViewPage(raw, modelScope(normalized)));
}
export function fetchReconciliationRuns(
  scope: TaskApiScope,
  query: TaskReconciliationQuery = {},
  options: TaskRequestOptions = {},
): Promise<TaskPage<TaskReconciliationRun>> {
  const normalized = normalizedScope(scope);
  const path = `/api/enterprise/tasks/reconciliation-runs${reconciliationQuery(query)}`;
  return safeRequest<unknown>(
    path,
    { method: "GET", headers: headers(normalized), signal: options.signal },
    "Task reconciliation history is unavailable",
  ).then((raw) => projectTaskReconciliationRunPage(raw, modelScope(normalized)));
}
export function retryTask(
  scope: TaskApiScope,
  taskId: string,
  input: TaskActionInput,
  options?: TaskRequestOptions,
): Promise<TaskActionOutcome> {
  return mutationRequest(
    scope,
    `/api/enterprise/tasks/${pathId(taskId, "taskId")}/retry`,
    "POST",
    actionBody(input),
    options,
    "Task retry is unavailable",
  );
}
export function cancelTask(
  scope: TaskApiScope,
  taskId: string,
  input: TaskActionInput,
  options?: TaskRequestOptions,
): Promise<TaskActionOutcome> {
  return mutationRequest(
    scope,
    `/api/enterprise/tasks/${pathId(taskId, "taskId")}/cancel`,
    "POST",
    actionBody(input),
    options,
    "Task cancellation is unavailable",
  );
}
export function acknowledgeTask(
  scope: TaskApiScope,
  taskId: string,
  input: TaskActionInput,
  options?: TaskRequestOptions,
): Promise<TaskActionOutcome> {
  return mutationRequest(
    scope,
    `/api/enterprise/tasks/${pathId(taskId, "taskId")}/acknowledge`,
    "POST",
    actionBody(input),
    options,
    "Task acknowledgement is unavailable",
  );
}
export function createTaskSavedView(
  scope: TaskApiScope,
  input: TaskSavedViewInput,
  options?: TaskRequestOptions,
): Promise<TaskActionOutcome> {
  const source = inputKeys(input, "savedView", ["name", "filters", "reason"]);
  return mutationRequest(
    scope,
    "/api/enterprise/task-views",
    "POST",
    {
      name: text(source.name, "name", 96),
      filters: normalizeFilters(source.filters),
      reason: reason(source.reason),
    },
    options,
    "Task view creation is unavailable",
  );
}
export function updateTaskSavedView(
  scope: TaskApiScope,
  viewId: string,
  input: UpdateTaskSavedViewInput,
  options?: TaskRequestOptions,
): Promise<TaskActionOutcome> {
  const source = inputKeys(input, "savedView", [
    "expectedRevision",
    "name",
    "filters",
    "status",
    "reason",
  ]);
  const body: Record<string, unknown> = {
    expected_revision: exactInteger(source.expectedRevision, "expectedRevision"),
    reason: reason(source.reason),
  };
  if (source.name !== undefined) body.name = text(source.name, "name", 96);
  if (source.filters !== undefined) body.filters = normalizeFilters(source.filters);
  if (source.status !== undefined) {
    if (source.status !== "active" && source.status !== "archived")
      throw new Error("status is invalid");
    body.status = source.status;
  }
  return mutationRequest(
    scope,
    `/api/enterprise/task-views/${pathId(viewId, "viewId")}`,
    "PATCH",
    body,
    options,
    "Task view update is unavailable",
  );
}
export function previewTaskReconciliation(
  scope: TaskApiScope,
  input: TaskReconciliationInput,
  options?: TaskRequestOptions,
): Promise<TaskActionOutcome> {
  const source = inputKeys(input, "reconciliation", ["sourceKinds", "reason"]);
  const body: Record<string, unknown> = { reason: reason(source.reason), dry_run: true };
  const sourceKinds = normalizedSourceKinds(source.sourceKinds);
  if (sourceKinds !== undefined) body.source_kinds = sourceKinds;
  return mutationRequest(
    scope,
    "/api/enterprise/tasks/reconcile/preview",
    "POST",
    body,
    options,
    "Task reconciliation preview is unavailable",
  );
}
export function reconcileTasks(
  scope: TaskApiScope,
  input: TaskReconciliationInput,
  options?: TaskRequestOptions,
): Promise<TaskActionOutcome> {
  const source = inputKeys(input, "reconciliation", ["sourceKinds", "reason"]);
  const body: Record<string, unknown> = { reason: reason(source.reason), dry_run: false };
  const sourceKinds = normalizedSourceKinds(source.sourceKinds);
  if (sourceKinds !== undefined) body.source_kinds = sourceKinds;
  return mutationRequest(
    scope,
    "/api/enterprise/tasks/reconcile",
    "POST",
    body,
    options,
    "Task reconciliation is unavailable",
  );
}

export function createTaskIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) return `rag4c-task-${cryptoApi.randomUUID()}`;
  if (cryptoApi?.getRandomValues) {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    return `rag4c-task-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
  }
  return `rag4c-task-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export const fetchTaskDetail = fetchTask;
export const listTasks = fetchTasks;
export const listTaskEvents = fetchTaskEvents;
export const listTaskViews = fetchTaskViews;
export const listReconciliationRuns = fetchReconciliationRuns;
export const retry = retryTask;
export const cancel = cancelTask;
export const acknowledge = acknowledgeTask;
