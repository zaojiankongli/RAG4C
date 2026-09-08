export type NotificationCategory = "quality" | "approval";
export type NotificationSourceKind = "quality_alert" | "approval_pending_for_me";
export type NotificationSeverity = "info" | "warning" | "critical";
export type NotificationReceiptStatus = "unread" | "read" | "archived";
export type NotificationSubscriptionStatus = "active" | "archived";
export type NotificationPreference = "subscribed" | "muted";
export type NotificationEventType = "materialized" | "marked_read" | "marked_unread" | "archived";
export type NotificationRecipientReason =
  "tenant_owner" | "tenant_admin" | "dataset_owner" | "eligible_approver" | "explicit_subscription";
export type NotificationRouteCode = "knowledge_quality_operations" | "enterprise_approval";

export interface NotificationModelScope {
  tenantId: string;
  accountId?: string;
}

export interface NotificationRoute {
  code: NotificationRouteCode;
  path: "/enterprise/knowledge-base" | "/enterprise/approvals";
  query: Readonly<Record<string, string>>;
  href: string;
}

export type NotificationSafeFactValue = string | number | boolean | null;
export type NotificationSafeFacts = Readonly<Record<string, NotificationSafeFactValue>>;

export interface Notification {
  id: string;
  tenant_id: string;
  source_kind: NotificationSourceKind;
  source_id: string;
  source_revision: number;
  source_dataset_id: string | null;
  category: NotificationCategory;
  severity: NotificationSeverity;
  action_required: boolean;
  mandatory: boolean;
  notification_key: string;
  source_digest: string;
  title_code: string;
  summary_code: string;
  safe_facts: NotificationSafeFacts;
  target_route_code: NotificationRouteCode;
  target_route_params: Readonly<Record<string, string>>;
  route: NotificationRoute;
  occurred_at: string;
  created_at: string;
  created_by: string;
}

export interface NotificationRecipient {
  id: string;
  tenant_id: string;
  notification_id: string;
  account_id: string;
  recipient_reason: NotificationRecipientReason;
  mandatory: boolean;
  assignment_digest: string;
  assigned_at: string;
}

export interface NotificationReceipt {
  id: string;
  tenant_id: string;
  notification_id: string;
  account_id: string;
  status: NotificationReceiptStatus;
  revision: number;
  read_at: string | null;
  archived_at: string | null;
  updated_at: string;
}

export interface NotificationSubscription {
  id: string;
  tenant_id: string;
  account_id: string;
  category: NotificationCategory;
  status: NotificationSubscriptionStatus;
  preference: NotificationPreference;
  active_subscription_key: string | null;
  revision: number;
  minimum_severity: NotificationSeverity;
  muted_until: string | null;
  created_at: string;
  created_by: string;
  updated_at: string;
  updated_by: string;
  archived_at: string | null;
  archived_by: string | null;
}

export interface NotificationEvent {
  id: string;
  tenant_id: string;
  notification_id: string;
  account_id: string;
  sequence: number;
  event_type: NotificationEventType;
  previous_event_digest: string | null;
  event_digest: string;
  actor_id: string;
  request_id: string;
  safe_snapshot: NotificationSafeFacts;
  occurred_at: string;
}

export type NotificationUnreadState = "zero" | "count" | "unavailable";

export interface NotificationSummary {
  state: "ready" | "unavailable";
  tenant_id: string;
  account_id: string;
  unread_count: number | null;
  unread_state: NotificationUnreadState;
  as_of: string | null;
  reason_code: string | null;
}

export interface NotificationInboxItem {
  notification: Notification;
  recipient: NotificationRecipient;
  receipt: NotificationReceipt;
}

export interface NotificationDetail extends NotificationInboxItem {
  events: NotificationEvent[];
}

export interface NotificationMutationOutcome {
  state: "applied" | "unchanged" | "coalesced" | "unavailable";
  operation: string;
  resource_id: string | null;
  message: string | null;
  retryable: boolean;
}

const DIGEST = /^[0-9a-f]{64}$/;
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const SAFE_CODE = /^[a-z][a-z0-9_.-]{0,63}$/;
function hasNotificationControlCharacters(value: string): boolean {
  for (const character of value) {
    const code = character.charCodeAt(0);
    if (code === 0 || code === 10 || code === 13) return true;
  }
  return false;
}
const SECRET =
  /(?:password\s*[:=]|passwd\s*[:=]|secret\s*[:=]|credential\s*[:=]|authorization\s*[:=]|access[_ -]?token\s*[:=]|refresh[_ -]?token\s*[:=]|api[_ -]?key\s*[:=]|client[_ -]?secret\s*[:=]|idempotency[_ -]?key\s*[:=]|ticket\s*[:=]|token\s*[:=]|bearer\s+|secret:\/\/|sk_(?:live|test)|ghp_|xox[baprs]-)/i;
const URL = /(?:https?|ftp|file|mailto|javascript|data):\S+|(?:^|\s)(?:www\.)\S+/i;
const SOURCE_KINDS = new Set<NotificationSourceKind>(["quality_alert", "approval_pending_for_me"]);
const CATEGORIES = new Set<NotificationCategory>(["quality", "approval"]);
const SEVERITIES = new Set<NotificationSeverity>(["info", "warning", "critical"]);
const RECEIPT_STATUSES = new Set<NotificationReceiptStatus>(["unread", "read", "archived"]);
const SUBSCRIPTION_STATUSES = new Set<NotificationSubscriptionStatus>(["active", "archived"]);
const PREFERENCES = new Set<NotificationPreference>(["subscribed", "muted"]);
const EVENT_TYPES = new Set<NotificationEventType>([
  "materialized",
  "marked_read",
  "marked_unread",
  "archived",
]);
const RECIPIENT_REASONS = new Set<NotificationRecipientReason>([
  "tenant_owner",
  "tenant_admin",
  "dataset_owner",
  "eligible_approver",
  "explicit_subscription",
]);
const ROUTES = new Set<NotificationRouteCode>([
  "knowledge_quality_operations",
  "enterprise_approval",
]);
const SAFE_FACT_KEYS = new Set([
  "alert_type",
  "release_id",
  "channel_id",
  "release_role",
  "revision",
  "severity",
  "source_status",
  "request_id",
  "approval_request_id",
  "action_type",
  "resource_type",
  "resource_id",
  "dataset_id",
  "category",
  "status",
  "target_type",
]);
const SAFE_SNAPSHOT_KEYS = new Set([
  "receipt_status",
  "receipt_revision",
  "source_revision",
  "source_digest",
  "notification_id",
  "event_type",
  "category",
  "severity",
  "action_required",
  "mandatory",
  "route_code",
  "status",
]);

function record(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(path + " must be an object");
  }
  return value as Record<string, unknown>;
}

function exactKeys(
  value: unknown,
  path: string,
  required: readonly string[],
  allowed: readonly string[] = required,
): Record<string, unknown> {
  const source = record(value, path);
  const allowedSet = new Set(allowed);
  for (const key of required) {
    if (!Object.prototype.hasOwnProperty.call(source, key)) {
      throw new Error(path + "." + key + " is required");
    }
  }
  for (const key of Object.keys(source)) {
    if (!allowedSet.has(key)) throw new Error(path + " contains unexpected field " + key);
  }
  return source;
}

function text(value: unknown, field: string, maximum: number): string {
  if (typeof value !== "string") throw new Error(field + " must be text");
  const normalized = value.trim();
  if (
    !normalized ||
    normalized.length > maximum ||
    hasNotificationControlCharacters(normalized) ||
    SECRET.test(normalized)
  ) {
    throw new Error(field + " is unsafe");
  }
  return normalized;
}

function identifier(value: unknown, field: string, maximum = 128): string {
  const normalized = text(value, field, maximum);
  if (!SAFE_ID.test(normalized)) throw new Error(field + " is invalid");
  return normalized;
}

function code(value: unknown, field: string): string {
  const normalized = text(value, field, 64);
  if (!SAFE_CODE.test(normalized)) throw new Error(field + " is invalid");
  return normalized;
}

function enumValue<T extends string>(value: unknown, field: string, values: Set<T>): T {
  if (typeof value !== "string" || !values.has(value as T)) throw new Error(field + " is invalid");
  return value as T;
}

function exactBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") throw new Error(field + " must be a boolean");
  return value;
}

function exactInteger(value: unknown, field: string, minimum = 1): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum) {
    throw new Error(field + " must be an exact integer");
  }
  return value;
}

function digest(value: unknown, field: string, nullable = false): string | null {
  if (nullable && (value === null || value === undefined)) return null;
  if (typeof value !== "string" || !DIGEST.test(value)) {
    throw new Error(field + " must be a lowercase SHA-256 digest");
  }
  return value;
}

function normalizeTimestamp(value: unknown, field: string): string {
  if (typeof value !== "string") throw new Error(field + " must be an ISO timestamp");
  const input = value.trim();
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/.exec(
    input,
  );
  if (!match || Number.isNaN(Date.parse(input)))
    throw new Error(field + " must be an ISO timestamp");
  const parsed = new Date(input);
  const base = new Date(parsed.getTime() - parsed.getUTCMilliseconds());
  const fraction = (match[2] ?? "").padEnd(6, "0");
  return base.toISOString().slice(0, 19) + "." + fraction + "Z";
}

function nullableTimestamp(value: unknown, field: string): string | null {
  if (value === null || value === undefined) return null;
  return normalizeTimestamp(value, field);
}

function nullableText(value: unknown, field: string, maximum: number): string | null {
  if (value === null || value === undefined) return null;
  return text(value, field, maximum);
}

function scopeTenant(scope: NotificationModelScope): string {
  return text(scope.tenantId, "tenantId", 64);
}

function assertTenant(value: unknown, scope: NotificationModelScope, field = "tenant_id"): string {
  const tenantId = text(value, field, 64);
  if (tenantId !== scopeTenant(scope)) throw new Error("Notification scope mismatch");
  return tenantId;
}

function assertAccount(
  value: unknown,
  scope: NotificationModelScope,
  field = "account_id",
): string {
  const accountId = identifier(value, field, 64);
  if (scope.accountId !== undefined && accountId !== text(scope.accountId, "accountId", 64)) {
    throw new Error("Notification account scope mismatch");
  }
  return accountId;
}

function projectSafeFacts(
  value: unknown,
  field: string,
  allowedKeys: Set<string>,
): NotificationSafeFacts {
  const source = record(value, field);
  const projected: Record<string, NotificationSafeFactValue> = {};
  for (const [key, raw] of Object.entries(source)) {
    if (!allowedKeys.has(key)) throw new Error(field + " contains unsafe field " + key);
    if (typeof raw === "string") {
      projected[key] = text(raw, field + "." + key, 256);
    } else if (raw === null || typeof raw === "boolean") {
      projected[key] = raw;
    } else if (typeof raw === "number") {
      projected[key] = exactInteger(raw, field + "." + key, 0);
    } else {
      throw new Error(field + "." + key + " must be a safe scalar");
    }
  }
  return projected;
}

function routeParams(value: unknown, field: string): Record<string, unknown> {
  return record(value, field);
}

function queryHref(path: string, query: Readonly<Record<string, string>>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) params.set(key, value);
  return path + "?" + params.toString();
}

export function projectNotificationRoute(
  value: unknown,
  _scope: NotificationModelScope,
): NotificationRoute {
  const source = exactKeys(value, "notification.route", [
    "target_route_code",
    "target_route_params_json",
    "source_kind",
    "source_dataset_id",
  ]);
  const routeCode = enumValue(source.target_route_code, "target_route_code", ROUTES);
  const params = routeParams(source.target_route_params_json, "target_route_params_json");
  const sourceKind = enumValue(source.source_kind, "source_kind", SOURCE_KINDS);
  const sourceDataset =
    source.source_dataset_id === null
      ? null
      : identifier(source.source_dataset_id, "source_dataset_id", 64);

  if (routeCode === "knowledge_quality_operations") {
    if (sourceKind !== "quality_alert" || sourceDataset === null) {
      throw new Error("quality route source scope is invalid");
    }
    const allowed = ["dataset_id", "section", "alert_id"] as const;
    for (const key of Object.keys(params)) {
      if (!(allowed as readonly string[]).includes(key)) {
        throw new Error("quality route contains unsafe field " + key);
      }
    }
    const datasetId = identifier(params.dataset_id, "target_route_params_json.dataset_id", 64);
    const section = text(params.section, "target_route_params_json.section", 32);
    if (datasetId !== sourceDataset || section !== "releases") {
      throw new Error("quality route scope is invalid");
    }
    const query: Record<string, string> = { dataset: datasetId, section };
    if (params.alert_id !== undefined)
      query.alert = identifier(params.alert_id, "target_route_params_json.alert_id");
    const path = "/enterprise/knowledge-base" as const;
    return { code: routeCode, path, query, href: queryHref(path, query) };
  }

  if (sourceKind !== "approval_pending_for_me" || sourceDataset !== null) {
    throw new Error("approval route source scope is invalid");
  }
  if (
    Object.keys(params).length !== 1 ||
    !Object.prototype.hasOwnProperty.call(params, "request_id")
  ) {
    throw new Error("approval route parameters are invalid");
  }
  const requestId = identifier(params.request_id, "target_route_params_json.request_id");
  const query = { request: requestId };
  const path = "/enterprise/approvals" as const;
  return { code: routeCode, path, query, href: queryHref(path, query) };
}

export function projectNotification(value: unknown, scope: NotificationModelScope): Notification {
  const source = exactKeys(value, "notification", [
    "id",
    "tenant_id",
    "source_kind",
    "source_id",
    "source_revision",
    "source_dataset_id",
    "category",
    "severity",
    "action_required",
    "mandatory",
    "notification_key",
    "source_digest",
    "title_code",
    "summary_code",
    "safe_facts_json",
    "target_route_code",
    "target_route_params_json",
    "occurred_at",
    "created_at",
    "created_by",
  ]);
  const tenantId = assertTenant(source.tenant_id, scope);
  const sourceKind = enumValue(source.source_kind, "source_kind", SOURCE_KINDS);
  const sourceDataset =
    source.source_dataset_id === null
      ? null
      : identifier(source.source_dataset_id, "source_dataset_id", 64);
  const category = enumValue(source.category, "category", CATEGORIES);
  if (
    (sourceKind === "quality_alert" && (sourceDataset === null || category !== "quality")) ||
    (sourceKind === "approval_pending_for_me" &&
      (sourceDataset !== null || category !== "approval"))
  ) {
    throw new Error("notification source scope is invalid");
  }
  const route = projectNotificationRoute(
    {
      target_route_code: source.target_route_code,
      target_route_params_json: source.target_route_params_json,
      source_kind: source.source_kind,
      source_dataset_id: source.source_dataset_id,
    },
    scope,
  );
  const safeFacts = projectSafeFacts(source.safe_facts_json, "safe_facts_json", SAFE_FACT_KEYS);
  return {
    id: identifier(source.id, "id"),
    tenant_id: tenantId,
    source_kind: sourceKind,
    source_id: identifier(source.source_id, "source_id"),
    source_revision: exactInteger(source.source_revision, "source_revision"),
    source_dataset_id: sourceDataset,
    category,
    severity: enumValue(source.severity, "severity", SEVERITIES),
    action_required: exactBoolean(source.action_required, "action_required"),
    mandatory: exactBoolean(source.mandatory, "mandatory"),
    notification_key: digest(source.notification_key, "notification_key")!,
    source_digest: digest(source.source_digest, "source_digest")!,
    title_code: code(source.title_code, "title_code"),
    summary_code: code(source.summary_code, "summary_code"),
    safe_facts: safeFacts,
    target_route_code: route.code,
    target_route_params: route.query,
    route,
    occurred_at: normalizeTimestamp(source.occurred_at, "occurred_at"),
    created_at: normalizeTimestamp(source.created_at, "created_at"),
    created_by: identifier(source.created_by, "created_by", 64),
  };
}

export function projectNotificationRecipient(
  value: unknown,
  scope: NotificationModelScope,
): NotificationRecipient {
  const source = exactKeys(value, "recipient", [
    "id",
    "tenant_id",
    "notification_id",
    "account_id",
    "recipient_reason",
    "mandatory",
    "assignment_digest",
    "assigned_at",
  ]);
  return {
    id: identifier(source.id, "id"),
    tenant_id: assertTenant(source.tenant_id, scope),
    notification_id: identifier(source.notification_id, "notification_id"),
    account_id: assertAccount(source.account_id, scope),
    recipient_reason: enumValue(source.recipient_reason, "recipient_reason", RECIPIENT_REASONS),
    mandatory: exactBoolean(source.mandatory, "mandatory"),
    assignment_digest: digest(source.assignment_digest, "assignment_digest")!,
    assigned_at: normalizeTimestamp(source.assigned_at, "assigned_at"),
  };
}

export function projectNotificationReceipt(
  value: unknown,
  scope: NotificationModelScope,
): NotificationReceipt {
  const source = exactKeys(value, "receipt", [
    "id",
    "tenant_id",
    "notification_id",
    "account_id",
    "status",
    "revision",
    "read_at",
    "archived_at",
    "updated_at",
  ]);
  const status = enumValue(source.status, "status", RECEIPT_STATUSES);
  const readAt = nullableTimestamp(source.read_at, "read_at");
  const archivedAt = nullableTimestamp(source.archived_at, "archived_at");
  if (
    (status === "unread" && (readAt !== null || archivedAt !== null)) ||
    (status === "read" && (readAt === null || archivedAt !== null)) ||
    (status === "archived" && archivedAt === null)
  ) {
    throw new Error("receipt lifecycle is invalid");
  }
  return {
    id: identifier(source.id, "id"),
    tenant_id: assertTenant(source.tenant_id, scope),
    notification_id: identifier(source.notification_id, "notification_id"),
    account_id: assertAccount(source.account_id, scope),
    status,
    revision: exactInteger(source.revision, "revision"),
    read_at: readAt,
    archived_at: archivedAt,
    updated_at: normalizeTimestamp(source.updated_at, "updated_at"),
  };
}

export function projectNotificationSubscription(
  value: unknown,
  scope: NotificationModelScope,
): NotificationSubscription {
  const source = exactKeys(value, "subscription", [
    "id",
    "tenant_id",
    "account_id",
    "category",
    "status",
    "preference",
    "active_subscription_key",
    "revision",
    "minimum_severity",
    "muted_until",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
    "archived_at",
    "archived_by",
  ]);
  const category = enumValue(source.category, "category", CATEGORIES);
  const status = enumValue(source.status, "status", SUBSCRIPTION_STATUSES);
  const preference = enumValue(source.preference, "preference", PREFERENCES);
  const accountId = assertAccount(source.account_id, scope);
  const activeKey = nullableText(source.active_subscription_key, "active_subscription_key", 96);
  const archivedAt = nullableTimestamp(source.archived_at, "archived_at");
  const archivedBy = nullableText(source.archived_by, "archived_by", 64);
  const mutedUntil = nullableTimestamp(source.muted_until, "muted_until");
  if (
    (status === "active" &&
      (activeKey !== accountId + ":" + category || archivedAt !== null || archivedBy !== null)) ||
    (status === "archived" && (activeKey !== null || archivedAt === null || archivedBy === null)) ||
    (preference === "subscribed" && mutedUntil !== null) ||
    (preference === "muted" && mutedUntil === null)
  ) {
    throw new Error("subscription lifecycle is invalid");
  }
  return {
    id: identifier(source.id, "id"),
    tenant_id: assertTenant(source.tenant_id, scope),
    account_id: accountId,
    category,
    status,
    preference,
    active_subscription_key: activeKey,
    revision: exactInteger(source.revision, "revision"),
    minimum_severity: enumValue(source.minimum_severity, "minimum_severity", SEVERITIES),
    muted_until: mutedUntil,
    created_at: normalizeTimestamp(source.created_at, "created_at"),
    created_by: identifier(source.created_by, "created_by", 64),
    updated_at: normalizeTimestamp(source.updated_at, "updated_at"),
    updated_by: identifier(source.updated_by, "updated_by", 64),
    archived_at: archivedAt,
    archived_by: archivedBy,
  };
}

export function projectNotificationEvent(
  value: unknown,
  scope: NotificationModelScope,
): NotificationEvent {
  const source = exactKeys(value, "event", [
    "id",
    "tenant_id",
    "notification_id",
    "account_id",
    "sequence",
    "event_type",
    "previous_event_digest",
    "event_digest",
    "actor_id",
    "request_id",
    "safe_snapshot_json",
    "occurred_at",
  ]);
  const sequence = exactInteger(source.sequence, "sequence");
  const previous = digest(source.previous_event_digest, "previous_event_digest", true);
  if ((sequence === 1 && previous !== null) || (sequence > 1 && previous === null)) {
    throw new Error("event hash chain is invalid");
  }
  return {
    id: identifier(source.id, "id"),
    tenant_id: assertTenant(source.tenant_id, scope),
    notification_id: identifier(source.notification_id, "notification_id"),
    account_id: assertAccount(source.account_id, scope),
    sequence,
    event_type: enumValue(source.event_type, "event_type", EVENT_TYPES),
    previous_event_digest: previous,
    event_digest: digest(source.event_digest, "event_digest")!,
    actor_id: identifier(source.actor_id, "actor_id", 64),
    request_id: identifier(source.request_id, "request_id", 128),
    safe_snapshot: projectSafeFacts(
      source.safe_snapshot_json,
      "safe_snapshot_json",
      SAFE_SNAPSHOT_KEYS,
    ),
    occurred_at: normalizeTimestamp(source.occurred_at, "occurred_at"),
  };
}

export function projectNotificationSummary(
  value: unknown,
  scope: NotificationModelScope,
): NotificationSummary {
  const source = exactKeys(
    value,
    "summary",
    ["tenant_id", "account_id", "state", "unread_count", "as_of"],
    ["tenant_id", "account_id", "state", "unread_count", "as_of", "reason_code"],
  );
  const state = source.state;
  if (state !== "ready" && state !== "unavailable") throw new Error("summary state is invalid");
  const accountId = assertAccount(source.account_id, scope);
  const tenantId = assertTenant(source.tenant_id, scope);
  const reasonCode = nullableText(source.reason_code, "reason_code", 96);
  if (state === "ready") {
    const count = exactInteger(source.unread_count, "unread_count", 0);
    const asOf = normalizeTimestamp(source.as_of, "as_of");
    return {
      state,
      tenant_id: tenantId,
      account_id: accountId,
      unread_count: count,
      unread_state: count === 0 ? "zero" : "count",
      as_of: asOf,
      reason_code: reasonCode,
    };
  }
  if (source.unread_count !== null || source.as_of !== null) {
    throw new Error("unavailable summary must not claim unread authority");
  }
  return {
    state,
    tenant_id: tenantId,
    account_id: accountId,
    unread_count: null,
    unread_state: "unavailable",
    as_of: null,
    reason_code: reasonCode,
  };
}

export function projectNotificationInboxItem(
  value: unknown,
  scope: NotificationModelScope,
): NotificationInboxItem {
  const source = exactKeys(value, "notification_item", ["notification", "recipient", "receipt"]);
  const notification = projectNotification(source.notification, scope);
  const recipient = projectNotificationRecipient(source.recipient, scope);
  const receipt = projectNotificationReceipt(source.receipt, {
    tenantId: scopeTenant(scope),
    accountId: scope.accountId ?? recipient.account_id,
  });
  if (
    recipient.tenant_id !== notification.tenant_id ||
    recipient.notification_id !== notification.id ||
    receipt.tenant_id !== notification.tenant_id ||
    receipt.notification_id !== notification.id ||
    receipt.account_id !== recipient.account_id
  ) {
    throw new Error("notification recipient/receipt identity is inconsistent");
  }
  return { notification, recipient, receipt };
}

export function projectNotificationDetail(
  value: unknown,
  scope: NotificationModelScope,
): NotificationDetail {
  const source = exactKeys(value, "notification_detail", [
    "notification",
    "recipient",
    "receipt",
    "events",
  ]);
  if (!Array.isArray(source.events)) throw new Error("notification_detail.events must be an array");
  const item = projectNotificationInboxItem(
    { notification: source.notification, recipient: source.recipient, receipt: source.receipt },
    scope,
  );
  const events: NotificationEvent[] = [];
  for (const event of source.events) {
    const projected = projectNotificationEvent(event, {
      tenantId: item.notification.tenant_id,
      accountId: item.recipient.account_id,
    });
    const previous = events[events.length - 1];
    if (
      previous &&
      (projected.sequence !== previous.sequence + 1 ||
        projected.previous_event_digest !== previous.event_digest)
    ) {
      throw new Error("notification event timeline is inconsistent");
    }
    if (
      projected.notification_id !== item.notification.id ||
      projected.account_id !== item.recipient.account_id
    ) {
      throw new Error("notification event identity is inconsistent");
    }
    events.push(projected);
  }
  return { ...item, events };
}

export function sanitizeNotificationMessage(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const normalized = value.trim();
  if (
    !normalized ||
    normalized.length > 512 ||
    hasNotificationControlCharacters(normalized) ||
    SECRET.test(normalized) ||
    URL.test(normalized)
  ) {
    return fallback;
  }
  return normalized;
}

export function projectNotificationMutationOutcome(value: unknown): NotificationMutationOutcome {
  const source = exactKeys(value, "notification_mutation", [
    "state",
    "operation",
    "resource_id",
    "message",
    "retryable",
  ]);
  const state = source.state;
  if (
    state !== "applied" &&
    state !== "unchanged" &&
    state !== "coalesced" &&
    state !== "unavailable"
  ) {
    throw new Error("notification mutation state is invalid");
  }
  return {
    state,
    operation: code(source.operation, "operation"),
    resource_id: source.resource_id === null ? null : identifier(source.resource_id, "resource_id"),
    message:
      source.message === null || source.message === undefined
        ? null
        : sanitizeNotificationMessage(source.message, "Notification operation unavailable"),
    retryable: exactBoolean(source.retryable, "retryable"),
  };
}
