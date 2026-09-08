export type AutomationTriggerCode =
  | "task_failed"
  | "task_source_stale"
  | "source_sync_failed"
  | "release_quality_alert_opened"
  | "release_recertification_blocked"
  | "approval_request_terminal";
export type AutomationConditionCode =
  | "always"
  | "status_is"
  | "action_required"
  | "severity_at_least"
  | "attempt_exhausted"
  | "source_is_stale";
export type AutomationActionCode =
  "notify_operator" | "request_approval" | "open_task_attention" | "pause_rule";
export type AutomationRuleStatus = "draft" | "active" | "paused" | "archived";
export type AutomationRunStatus =
  "started" | "not_matched" | "requested" | "completed" | "failed" | "blocked";
export type AutomationRequestStatus =
  "requested" | "dispatched" | "applied" | "rejected" | "expired";
export type AutomationSummaryState = "ready" | "partial" | "unavailable" | "error";
export type AutomationSeverity = "info" | "warning" | "error" | "critical";
export type AutomationSafeValue = string | number | boolean | null;
export interface AutomationScope {
  tenantId: string;
  accountId?: string;
}
export interface AutomationRoute {
  code: string;
  path: "/enterprise/tasks" | "/sources" | "/enterprise/knowledge-base" | "/enterprise/approvals";
  query: Readonly<Record<string, string>>;
  href: string;
}
export interface AutomationRule {
  id: string;
  tenant_id: string;
  name: string;
  status: AutomationRuleStatus;
  revision: number;
  current_revision_id: string | null;
  workspace_id: string | null;
  dataset_id: string | null;
  priority: number;
  created_at: string;
  created_by: string;
  updated_at: string;
  updated_by: string;
  archived_at: string | null;
}
export interface AutomationActionStep {
  step_index: number;
  action_code: AutomationActionCode;
  params: Readonly<Record<string, AutomationSafeValue>>;
}
export interface AutomationRuleDefinition {
  trigger_code: AutomationTriggerCode;
  condition_code: AutomationConditionCode;
  condition_params: Readonly<Record<string, AutomationSafeValue>>;
  action_plan: AutomationActionStep[];
}
export interface AutomationRuleRevision {
  id: string;
  tenant_id: string;
  rule_id: string;
  revision: number;
  trigger_code: AutomationTriggerCode;
  condition_code: AutomationConditionCode;
  condition_params: Readonly<Record<string, AutomationSafeValue>>;
  action_plan: AutomationActionStep[];
  definition_digest: string;
  created_at: string;
  created_by: string;
}
export interface AutomationRun {
  id: string;
  tenant_id: string;
  rule_id: string;
  rule_revision_id: string;
  trigger_event_id: string;
  trigger_event_digest: string;
  status: AutomationRunStatus;
  condition_matched: boolean;
  action_count: number;
  requested_count: number;
  rejected_count: number;
  started_at: string;
  completed_at: string | null;
  safe_error_code: string | null;
  safe_error: string | null;
}
export interface AutomationActionRequest {
  id: string;
  tenant_id: string;
  run_id: string;
  rule_id: string;
  step_index: number;
  action_code: AutomationActionCode;
  status: AutomationRequestStatus;
  target_kind: string | null;
  target_id: string | null;
  target_revision: number | null;
  target_digest: string | null;
  safe_params: Readonly<Record<string, AutomationSafeValue>>;
  safe_reason: string;
  approval_request_id: string | null;
  notification_id: string | null;
  task_id: string | null;
  requested_at: string;
  dispatched_at: string | null;
  applied_at: string | null;
  rejected_at: string | null;
  expires_at: string;
}
export type AutomationEventType =
  | "rule_created"
  | "revision_created"
  | "revision_activated"
  | "rule_paused"
  | "trigger_observed"
  | "condition_not_matched"
  | "run_started"
  | "action_requested"
  | "action_rejected"
  | "run_completed"
  | "run_failed";
export interface AutomationEvent {
  id: string;
  tenant_id: string;
  rule_id: string;
  run_id: string | null;
  stream_key: string;
  sequence: number;
  event_type: AutomationEventType;
  previous_event_digest: string | null;
  event_digest: string;
  actor_id: string;
  request_id: string;
  safe_snapshot: Readonly<Record<string, AutomationSafeValue>>;
  occurred_at: string;
}
export interface AutomationPage<T> {
  items: T[];
  next_cursor: string | null;
  invalid_item_count: number;
}
export interface AutomationSummary {
  tenant_id: string;
  state: AutomationSummaryState;
  active_rule_count: number | null;
  paused_rule_count: number | null;
  failed_run_count: number | null;
  pending_request_count: number | null;
  as_of: string | null;
  reason_code: string | null;
}

const TRIGGERS = new Set<AutomationTriggerCode>([
  "task_failed",
  "task_source_stale",
  "source_sync_failed",
  "release_quality_alert_opened",
  "release_recertification_blocked",
  "approval_request_terminal",
]);
const CONDITIONS = new Set<AutomationConditionCode>([
  "always",
  "status_is",
  "action_required",
  "severity_at_least",
  "attempt_exhausted",
  "source_is_stale",
]);
const ACTIONS = new Set<AutomationActionCode>([
  "notify_operator",
  "request_approval",
  "open_task_attention",
  "pause_rule",
]);
const RULE_STATUSES = new Set<AutomationRuleStatus>(["draft", "active", "paused", "archived"]);
const RUN_STATUSES = new Set<AutomationRunStatus>([
  "started",
  "not_matched",
  "requested",
  "completed",
  "failed",
  "blocked",
]);
const REQUEST_STATUSES = new Set<AutomationRequestStatus>([
  "requested",
  "dispatched",
  "applied",
  "rejected",
  "expired",
]);
const EVENT_TYPES = new Set<AutomationEventType>([
  "rule_created",
  "revision_created",
  "revision_activated",
  "rule_paused",
  "trigger_observed",
  "condition_not_matched",
  "run_started",
  "action_requested",
  "action_rejected",
  "run_completed",
  "run_failed",
]);
const SEVERITIES = new Set<AutomationSeverity>(["info", "warning", "error", "critical"]);
const STATUSES = new Set([
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
  "blocked",
  "unavailable",
  "approved",
  "rejected",
  "expired",
  "completed",
]);
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$/;
const CODE = /^[a-z][a-z0-9_.-]{0,127}$/;
const DIGEST = /^[0-9a-f]{64}$/;
function hasControlCharacters(value: string): boolean {
  for (const character of value) {
    const code = character.charCodeAt(0);
    if (code < 32 || code === 127) return true;
  }
  return false;
}
const URL = /(?:https?|ftp|file|mailto|javascript|data):\S+|(?:^|\s)(?:www\.)\S+/i;
const SECRET =
  /(?:password|secret|credential|authorization|bearer|token|ticket|api[_ -]?key)\s*[:=]?\s*\S+/i;
const SQL_LIKE =
  /(?:\bselect\s+(?:distinct\s+)?[\w*"`'([]|\binsert\s+(?:into\s+)?[\w"`'(]|\bupdate\s+[\w"`.]+\s+set\b|\bdelete\s+from\b|\bdrop\s+(?:table|database|schema|index|view|trigger|procedure|function)\b|\balter\s+(?:table|database|schema|index|view|trigger|procedure|function)\b|\bcreate\s+(?:table|database|schema|index|view|trigger|procedure|function)\b|\bgrant\s+\w+\s+on\b|\brevoke\s+\w+\s+on\b|\bexec(?:ute)?\s+\S+|\bunion\s+(?:all\s+)?select\b|\b(?:select|insert|update|delete|drop|alter|create|grant|revoke|exec(?:ute)?|union)\s*$)/i;
const BEARER = /\bbearer\b/i;
const JWT =
  /(?:^|[^A-Za-z0-9_-])[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}(?![A-Za-z0-9_-])/;
const MAX_SAFE_DEPTH = 12;
const MAX_SAFE_ITEMS = 64;
const MAX_SAFE_UTF8_BYTES = 16_384;
const SAFE_CODE_KEYS = new Set([
  "trigger_code",
  "condition_code",
  "action_code",
  "event_type",
  "error_code",
  "reason_code",
  "status_code",
]);
const UNSAFE_KEY_PARTS = new Set([
  "sql",
  "query",
  "url",
  "uri",
  "href",
  "webhook",
  "script",
  "expression",
  "eval",
  "exec",
  "prompt",
  "body",
  "content",
  "raw",
  "payload",
  "token",
  "credential",
  "ticket",
  "password",
  "secret",
  "authorization",
  "bearer",
  "cookie",
  "header",
  "answer",
  "apikey",
  "accesskey",
  "code",
]);

function object(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error(`${field} must be an object`);
  return value as Record<string, unknown>;
}
function required(source: Record<string, unknown>, key: string, field = key): unknown {
  if (!Object.prototype.hasOwnProperty.call(source, key)) throw new Error(`${field} is required`);
  return source[key];
}
function exact(value: unknown, field: string, allowed: readonly string[]): Record<string, unknown> {
  const source = object(value, field);
  const set = new Set(allowed);
  for (const key of Object.keys(source))
    if (!set.has(key)) throw new Error(`${field} contains unexpected field: ${key}`);
  return source;
}
function text(value: unknown, field: string, max = 512): string {
  if (typeof value !== "string") throw new Error(`${field} is invalid`);
  const result = value.trim();
  if (
    !result ||
    result.length > max ||
    hasControlCharacters(result) ||
    URL.test(result) ||
    SECRET.test(result) ||
    SQL_LIKE.test(result) ||
    BEARER.test(result) ||
    JWT.test(result)
  )
    throw new Error(`${field} is unsafe`);
  return result;
}
function id(value: unknown, field: string): string {
  const result = text(value, field, 128);
  if (!IDENTIFIER.test(result) || result.includes("..")) throw new Error(`${field} is invalid`);
  return result;
}
function code(value: unknown, field: string): string {
  const result = text(value, field, 128);
  if (!CODE.test(result)) throw new Error(`${field} is invalid code`);
  return result;
}
function digest(value: unknown, field: string): string {
  const result = text(value, field, 64);
  if (!DIGEST.test(result)) throw new Error(`${field} is invalid digest`);
  return result;
}
function integer(value: unknown, field: string, min = 0, max = Number.MAX_SAFE_INTEGER): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < min || value > max)
    throw new Error(`${field} must be an exact integer`);
  return value;
}
function bool(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") throw new Error(`${field} must be boolean`);
  return value;
}
function time(value: unknown, field: string): string {
  const result = text(value, field, 64);
  if (Number.isNaN(Date.parse(result))) throw new Error(`${field} is invalid time`);
  return result;
}
function tenant(value: unknown, scope: AutomationScope): string {
  const result = id(value, "tenant_id");
  if (result !== id(scope.tenantId, "scope.tenantId")) throw new Error("tenant scope mismatch");
  return result;
}
function normalizedKey(key: string): string {
  const separated = key
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2");
  return separated
    .replace(/[^a-z0-9]+/gi, "_")
    .toLowerCase()
    .replace(/^_+|_+$/g, "");
}
function unsafeKey(key: string): boolean {
  const normalized = normalizedKey(key);
  if (SAFE_CODE_KEYS.has(normalized)) return false;
  const compact = normalized.replaceAll("_", "");
  if (compact === "code" || compact.startsWith("raw")) return true;
  for (const part of UNSAFE_KEY_PARTS) if (compact.includes(part)) return true;
  return false;
}
function utf8ByteLength(value: unknown, field: string): number {
  let serialized: string | undefined;
  try {
    serialized = JSON.stringify(value);
  } catch {
    throw new Error(`${field} contains an unsupported value`);
  }
  if (serialized === undefined) throw new Error(`${field} contains an unsupported value`);
  return new TextEncoder().encode(serialized).length;
}
function validateSafeNode(
  value: unknown,
  field: string,
  depth: number,
  itemCount: { value: number },
): void {
  if (depth > MAX_SAFE_DEPTH) throw new Error(`${field} is too deeply nested`);
  if (value === null || typeof value === "boolean") return;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error(`${field} is unsupported`);
    return;
  }
  if (typeof value === "string") {
    text(value, field, 512);
    return;
  }
  if (Array.isArray(value)) {
    if (value.length > MAX_SAFE_ITEMS) throw new Error(`${field} contains too many items`);
    itemCount.value += value.length;
    if (itemCount.value > MAX_SAFE_ITEMS) throw new Error(`${field} contains too many items`);
    value.forEach((item, index) =>
      validateSafeNode(item, `${field}[${index}]`, depth + 1, itemCount),
    );
    return;
  }
  if (typeof value === "object") {
    const source = value as Record<string, unknown>;
    if (Object.keys(source).length > MAX_SAFE_ITEMS)
      throw new Error(`${field} contains too many fields`);
    itemCount.value += Object.keys(source).length;
    if (itemCount.value > MAX_SAFE_ITEMS) throw new Error(`${field} contains too many fields`);
    for (const [key, item] of Object.entries(source)) {
      if (!key || unsafeKey(key)) throw new Error(`${field} contains a forbidden field`);
      validateSafeNode(item, `${field}.${key}`, depth + 1, itemCount);
    }
    return;
  }
  throw new Error(`${field} contains an unsupported value`);
}
function safeParams(value: unknown, field: string): Record<string, AutomationSafeValue> {
  const source = object(value, field);
  if (Object.keys(source).length > 32) throw new Error(`${field} too large`);
  validateSafeNode(source, field, 0, { value: 0 });
  if (utf8ByteLength(source, field) > MAX_SAFE_UTF8_BYTES)
    throw new Error(`${field} exceeds the UTF-8 byte limit`);
  const out: Record<string, AutomationSafeValue> = {};
  for (const [key, raw] of Object.entries(source)) {
    if (!IDENTIFIER.test(key) || unsafeKey(key)) throw new Error(`${field}.${key} is unsafe`);
    if (raw === null || typeof raw === "boolean") out[key] = raw;
    else if (typeof raw === "number" && Number.isFinite(raw)) out[key] = raw;
    else if (typeof raw === "string") out[key] = text(raw, `${field}.${key}`, 512);
    else throw new Error(`${field}.${key} is unsupported`);
  }
  return out;
}
function exactParams(
  value: unknown,
  field: string,
  keys: readonly string[],
): Record<string, AutomationSafeValue> {
  const source = safeParams(value, field);
  const expected = new Set(keys);
  for (const key of Object.keys(source))
    if (!expected.has(key)) throw new Error(`${field} contains unexpected field: ${key}`);
  for (const key of keys)
    if (!Object.prototype.hasOwnProperty.call(source, key))
      throw new Error(`${field}.${key} is required`);
  return source;
}
function enumValue<T extends string>(value: unknown, field: string, values: Set<T>): T {
  const result = text(value, field, 64) as T;
  if (!values.has(result)) throw new Error(`${field} is not allowed`);
  return result;
}
function conditionParams(
  conditionCode: AutomationConditionCode,
  value: unknown,
): Record<string, AutomationSafeValue> {
  const source =
    conditionCode === "always"
      ? exactParams(value, "condition_params", [])
      : conditionCode === "status_is"
        ? exactParams(value, "condition_params", ["status"])
        : conditionCode === "action_required" || conditionCode === "source_is_stale"
          ? exactParams(value, "condition_params", ["value"])
          : conditionCode === "severity_at_least"
            ? exactParams(value, "condition_params", ["severity"])
            : exactParams(value, "condition_params", ["minimum_attempts"]);
  if (conditionCode === "always") return {};
  if (conditionCode === "status_is")
    return { status: enumValue(source.status, "condition_params.status", STATUSES) };
  if (conditionCode === "action_required" || conditionCode === "source_is_stale")
    return { value: bool(source.value, "condition_params.value") };
  if (conditionCode === "severity_at_least")
    return { severity: enumValue(source.severity, "condition_params.severity", SEVERITIES) };
  return {
    minimum_attempts: integer(source.minimum_attempts, "condition_params.minimum_attempts", 1, 100),
  };
}
function actionParams(
  actionCode: AutomationActionCode,
  value: unknown,
): Record<string, AutomationSafeValue> {
  const source =
    actionCode === "notify_operator"
      ? exactParams(value, "action.params", ["category", "severity", "title"])
      : actionCode === "request_approval"
        ? exactParams(value, "action.params", ["action_type", "resource_type", "reason_code"])
        : actionCode === "open_task_attention"
          ? exactParams(value, "action.params", ["task_id", "reason_code"])
          : exactParams(value, "action.params", ["reason_code"]);
  if (actionCode === "notify_operator")
    return {
      category: code(source.category, "action.params.category"),
      severity: enumValue(source.severity, "action.params.severity", SEVERITIES),
      title: text(source.title, "action.params.title", 160),
    };
  if (actionCode === "request_approval")
    return {
      action_type: code(source.action_type, "action.params.action_type"),
      resource_type: code(source.resource_type, "action.params.resource_type"),
      reason_code: code(source.reason_code, "action.params.reason_code"),
    };
  if (actionCode === "open_task_attention")
    return {
      task_id: id(source.task_id, "action.params.task_id"),
      reason_code: code(source.reason_code, "action.params.reason_code"),
    };
  return { reason_code: code(source.reason_code, "action.params.reason_code") };
}

export function projectAutomationRule(value: unknown, scope: AutomationScope): AutomationRule {
  const s = exact(value, "automation_rule", [
    "id",
    "tenant_id",
    "name",
    "status",
    "revision",
    "current_revision_id",
    "workspace_id",
    "dataset_id",
    "priority",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
    "archived_at",
  ]);
  return {
    id: id(required(s, "id"), "id"),
    tenant_id: tenant(required(s, "tenant_id"), scope),
    name: text(required(s, "name"), "name", 128),
    status: enumValue(required(s, "status"), "status", RULE_STATUSES),
    revision: integer(required(s, "revision"), "revision", 1),
    current_revision_id:
      required(s, "current_revision_id") === null
        ? null
        : id(required(s, "current_revision_id"), "current_revision_id"),
    workspace_id:
      required(s, "workspace_id") === null ? null : id(required(s, "workspace_id"), "workspace_id"),
    dataset_id:
      required(s, "dataset_id") === null ? null : id(required(s, "dataset_id"), "dataset_id"),
    priority: integer(required(s, "priority"), "priority", 0, 1000),
    created_at: time(required(s, "created_at"), "created_at"),
    created_by: id(required(s, "created_by"), "created_by"),
    updated_at: time(required(s, "updated_at"), "updated_at"),
    updated_by: id(required(s, "updated_by"), "updated_by"),
    archived_at:
      required(s, "archived_at") === null ? null : time(required(s, "archived_at"), "archived_at"),
  };
}
export function projectAutomationRuleRevision(
  value: unknown,
  scope: AutomationScope,
): AutomationRuleRevision {
  const s = exact(value, "automation_rule_revision", [
    "id",
    "tenant_id",
    "rule_id",
    "revision",
    "trigger_code",
    "condition_code",
    "condition_params_json",
    "action_plan_json",
    "definition_digest",
    "created_at",
    "created_by",
  ]);
  const rawPlan = required(s, "action_plan_json");
  if (!Array.isArray(rawPlan) || rawPlan.length < 1 || rawPlan.length > 4)
    throw new Error("action_plan_json is invalid");
  const conditionCode = enumValue(required(s, "condition_code"), "condition_code", CONDITIONS);
  return {
    id: id(required(s, "id"), "id"),
    tenant_id: tenant(required(s, "tenant_id"), scope),
    rule_id: id(required(s, "rule_id"), "rule_id"),
    revision: integer(required(s, "revision"), "revision", 1),
    trigger_code: enumValue(required(s, "trigger_code"), "trigger_code", TRIGGERS),
    condition_code: conditionCode,
    condition_params: conditionParams(conditionCode, required(s, "condition_params_json")),
    action_plan: rawPlan.map((item, index) => {
      const a = exact(item, `action_plan_json[${index}]`, ["step_index", "action_code", "params"]);
      const stepIndex = integer(required(a, "step_index"), "step_index", 0, 3);
      if (stepIndex !== index) throw new Error("action plan step order is invalid");
      const actionCode = enumValue(required(a, "action_code"), "action_code", ACTIONS);
      return {
        step_index: stepIndex,
        action_code: actionCode,
        params: actionParams(actionCode, required(a, "params")),
      };
    }),
    definition_digest: digest(required(s, "definition_digest"), "definition_digest"),
    created_at: time(required(s, "created_at"), "created_at"),
    created_by: id(required(s, "created_by"), "created_by"),
  };
}
export function projectAutomationRun(value: unknown, scope: AutomationScope): AutomationRun {
  const s = exact(value, "automation_run", [
    "id",
    "tenant_id",
    "rule_id",
    "rule_revision_id",
    "trigger_event_id",
    "trigger_event_digest",
    "status",
    "condition_matched",
    "action_count",
    "requested_count",
    "rejected_count",
    "started_at",
    "completed_at",
    "safe_error_code",
    "safe_error",
  ]);
  return {
    id: id(required(s, "id"), "id"),
    tenant_id: tenant(required(s, "tenant_id"), scope),
    rule_id: id(required(s, "rule_id"), "rule_id"),
    rule_revision_id: id(required(s, "rule_revision_id"), "rule_revision_id"),
    trigger_event_id: id(required(s, "trigger_event_id"), "trigger_event_id"),
    trigger_event_digest: digest(required(s, "trigger_event_digest"), "trigger_event_digest"),
    status: enumValue(required(s, "status"), "status", RUN_STATUSES),
    condition_matched: bool(required(s, "condition_matched"), "condition_matched"),
    action_count: integer(required(s, "action_count"), "action_count"),
    requested_count: integer(required(s, "requested_count"), "requested_count"),
    rejected_count: integer(required(s, "rejected_count"), "rejected_count"),
    started_at: time(required(s, "started_at"), "started_at"),
    completed_at:
      required(s, "completed_at") === null
        ? null
        : time(required(s, "completed_at"), "completed_at"),
    safe_error_code:
      required(s, "safe_error_code") === null
        ? null
        : code(required(s, "safe_error_code"), "safe_error_code"),
    safe_error:
      required(s, "safe_error") === null ? null : text(required(s, "safe_error"), "safe_error"),
  };
}
export function projectAutomationActionRequest(
  value: unknown,
  scope: AutomationScope,
): AutomationActionRequest {
  const s = exact(value, "automation_action_request", [
    "id",
    "tenant_id",
    "run_id",
    "rule_id",
    "step_index",
    "action_code",
    "status",
    "target_kind",
    "target_id",
    "target_revision",
    "target_digest",
    "safe_params_json",
    "safe_reason",
    "approval_request_id",
    "notification_id",
    "task_id",
    "requested_at",
    "dispatched_at",
    "applied_at",
    "rejected_at",
    "expires_at",
  ]);
  const targetRevision =
    required(s, "target_revision") === null
      ? null
      : integer(required(s, "target_revision"), "target_revision", 1);
  const targetDigest =
    required(s, "target_digest") === null
      ? null
      : digest(required(s, "target_digest"), "target_digest");
  if ((targetRevision === null) !== (targetDigest === null))
    throw new Error("target fence invalid");
  const actionCode = enumValue(required(s, "action_code"), "action_code", ACTIONS);
  return {
    id: id(required(s, "id"), "id"),
    tenant_id: tenant(required(s, "tenant_id"), scope),
    run_id: id(required(s, "run_id"), "run_id"),
    rule_id: id(required(s, "rule_id"), "rule_id"),
    step_index: integer(required(s, "step_index"), "step_index", 0, 3),
    action_code: actionCode,
    status: enumValue(required(s, "status"), "status", REQUEST_STATUSES),
    target_kind:
      required(s, "target_kind") === null ? null : code(required(s, "target_kind"), "target_kind"),
    target_id: required(s, "target_id") === null ? null : id(required(s, "target_id"), "target_id"),
    target_revision: targetRevision,
    target_digest: targetDigest,
    safe_params: actionParams(actionCode, required(s, "safe_params_json")),
    safe_reason: text(required(s, "safe_reason"), "safe_reason"),
    approval_request_id:
      required(s, "approval_request_id") === null
        ? null
        : id(required(s, "approval_request_id"), "approval_request_id"),
    notification_id:
      required(s, "notification_id") === null
        ? null
        : id(required(s, "notification_id"), "notification_id"),
    task_id: required(s, "task_id") === null ? null : id(required(s, "task_id"), "task_id"),
    requested_at: time(required(s, "requested_at"), "requested_at"),
    dispatched_at:
      required(s, "dispatched_at") === null
        ? null
        : time(required(s, "dispatched_at"), "dispatched_at"),
    applied_at:
      required(s, "applied_at") === null ? null : time(required(s, "applied_at"), "applied_at"),
    rejected_at:
      required(s, "rejected_at") === null ? null : time(required(s, "rejected_at"), "rejected_at"),
    expires_at: time(required(s, "expires_at"), "expires_at"),
  };
}
function projectEvent(value: unknown, scope: AutomationScope): AutomationEvent {
  const s = exact(value, "automation_event", [
    "id",
    "tenant_id",
    "rule_id",
    "run_id",
    "stream_key",
    "sequence",
    "event_type",
    "previous_event_digest",
    "event_digest",
    "actor_id",
    "request_id",
    "safe_snapshot_json",
    "occurred_at",
  ]);
  return {
    id: id(required(s, "id"), "id"),
    tenant_id: tenant(required(s, "tenant_id"), scope),
    rule_id: id(required(s, "rule_id"), "rule_id"),
    run_id: required(s, "run_id") === null ? null : id(required(s, "run_id"), "run_id"),
    stream_key: id(required(s, "stream_key"), "stream_key"),
    sequence: integer(required(s, "sequence"), "sequence", 1),
    event_type: enumValue(required(s, "event_type"), "event_type", EVENT_TYPES),
    previous_event_digest:
      required(s, "previous_event_digest") === null
        ? null
        : digest(required(s, "previous_event_digest"), "previous_event_digest"),
    event_digest: digest(required(s, "event_digest"), "event_digest"),
    actor_id: id(required(s, "actor_id"), "actor_id"),
    request_id: id(required(s, "request_id"), "request_id"),
    safe_snapshot: safeParams(required(s, "safe_snapshot_json"), "safe_snapshot_json"),
    occurred_at: time(required(s, "occurred_at"), "occurred_at"),
  };
}
function validateEventStream(events: AutomationEvent[], streamKey: string): void {
  const ordered = [...events].sort(
    (left, right) => left.sequence - right.sequence || left.id.localeCompare(right.id),
  );
  for (let index = 0; index < ordered.length; index++) {
    const current = ordered[index];
    if (!current) continue;
    if (index > 0 && current.sequence === ordered[index - 1]?.sequence)
      throw new Error(`event chain duplicate sequence in ${streamKey}`);
    if (index === 0) {
      if (current.sequence === 1) {
        if (
          !["rule_created", "run_started"].includes(current.event_type) ||
          current.previous_event_digest !== null
        )
          throw new Error("first event chain invalid");
      } else if (current.previous_event_digest === null) {
        throw new Error("mid-page event requires previous digest");
      }
      continue;
    }
    const previous = ordered[index - 1];
    if (
      !previous ||
      current.sequence !== previous.sequence + 1 ||
      current.previous_event_digest !== previous.event_digest
    )
      throw new Error(`event hash chain invalid in ${streamKey}`);
  }
}
export function projectAutomationEventPage(
  value: unknown,
  scope: AutomationScope,
): AutomationPage<AutomationEvent> {
  const s = exact(value, "automation_event_page", ["items", "next_cursor", "invalid_item_count"]);
  const rawItems = required(s, "items");
  if (!Array.isArray(rawItems)) throw new Error("items invalid");
  const items = rawItems.map((item) => projectEvent(item, scope));
  const streams = new Map<string, AutomationEvent[]>();
  for (const item of items)
    streams.set(item.stream_key, [...(streams.get(item.stream_key) ?? []), item]);
  for (const [streamKey, stream] of streams) validateEventStream(stream, streamKey);
  return {
    items,
    next_cursor:
      required(s, "next_cursor") === null
        ? null
        : text(required(s, "next_cursor"), "next_cursor", 2048),
    invalid_item_count: integer(required(s, "invalid_item_count"), "invalid_item_count"),
  };
}
export function projectAutomationSummary(
  value: unknown,
  scope: AutomationScope,
): AutomationSummary {
  const s = exact(value, "automation_summary", [
    "tenant_id",
    "state",
    "active_rule_count",
    "paused_rule_count",
    "failed_run_count",
    "pending_request_count",
    "as_of",
    "reason_code",
  ]);
  const state = enumValue(
    required(s, "state"),
    "state",
    new Set<AutomationSummaryState>(["ready", "partial", "unavailable", "error"]),
  );
  const count = (key: string) => {
    const value = required(s, key);
    return value === null ? null : integer(value, key);
  };
  const result: AutomationSummary = {
    tenant_id: tenant(required(s, "tenant_id"), scope),
    state,
    active_rule_count: count("active_rule_count"),
    paused_rule_count: count("paused_rule_count"),
    failed_run_count: count("failed_run_count"),
    pending_request_count: count("pending_request_count"),
    as_of: required(s, "as_of") === null ? null : time(required(s, "as_of"), "as_of"),
    reason_code:
      required(s, "reason_code") === null ? null : code(required(s, "reason_code"), "reason_code"),
  };
  const counts = [
    result.active_rule_count,
    result.paused_rule_count,
    result.failed_run_count,
    result.pending_request_count,
  ];
  if (state === "ready" && [...counts, result.as_of].some((item) => item === null))
    throw new Error("ready summary count unavailable");
  if (state === "unavailable" && [...counts, result.as_of].some((item) => item !== null))
    throw new Error("unavailable summary must not claim counts");
  return result;
}
export function projectAutomationTriggerRoute(value: unknown): AutomationRoute {
  const s = exact(value, "automation_trigger_route", ["trigger_code", "safe_facts_json"]);
  const trigger = enumValue(required(s, "trigger_code"), "trigger_code", TRIGGERS);
  const facts = safeParams(required(s, "safe_facts_json"), "safe_facts");
  let codeName: string;
  let path: AutomationRoute["path"];
  const query: Record<string, string> = {};
  if (trigger === "task_failed" || trigger === "task_source_stale") {
    codeName = "enterprise_tasks";
    path = "/enterprise/tasks";
    if (typeof facts.task_id === "string") query.task = id(facts.task_id, "safe_facts.task_id");
  } else if (trigger === "source_sync_failed") {
    codeName = "knowledge_sources";
    path = "/sources";
    if (typeof facts.source_id === "string")
      query.source = id(facts.source_id, "safe_facts.source_id");
  } else if (trigger === "approval_request_terminal") {
    codeName = "enterprise_approvals";
    path = "/enterprise/approvals";
    if (typeof facts.approval_request_id === "string")
      query.request = id(facts.approval_request_id, "safe_facts.approval_request_id");
  } else {
    codeName = "release_quality";
    path = "/enterprise/knowledge-base";
    query.section = "quality";
  }
  const qs = new URLSearchParams(query).toString();
  return { code: codeName, path, query, href: qs ? `${path}?${qs}` : path };
}
