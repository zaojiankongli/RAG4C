export type RecoveryPolicyStatus = "active" | "paused";
export type RecoveryEntryStatus =
  "recycled" | "restoring" | "restored" | "purge_requested" | "purged" | "failed";
export type RecoveryOriginalLifecycleState = "active" | "expired";
export type LegalHoldStatus = "active" | "released";
export type PurgeRequestStatus =
  "pending_approval" | "approved" | "cancelled" | "expired" | "executed";
export type RecoveryEventType =
  | "recycled"
  | "restored"
  | "hold_applied"
  | "hold_released"
  | "purge_requested"
  | "purge_approved"
  | "purge_cancelled";
export type RecoveryRouteCode = "enterprise_approval";
export type RecoverySummaryState = "ready" | "partial" | "unavailable" | "error";
export type RecoveryMutationState =
  | "applied"
  | "unchanged"
  | "approval_required"
  | "rejected"
  | "coalesced"
  | "replayed"
  | "conflict"
  | "blocked"
  | "unavailable";

export interface RecoveryModelScope {
  tenantId: string;
  accountId?: string;
}

export interface RecoveryRoute {
  code: RecoveryRouteCode;
  path: "/enterprise/approvals";
  query: Readonly<{ request: string }>;
  href: string;
}

export type RecoverySafeValue = string | number | boolean | null;
export type RecoverySafeSnapshot = Readonly<Record<string, RecoverySafeValue>>;

export interface ContentRetentionPolicy {
  id: string;
  tenant_id: string;
  status: RecoveryPolicyStatus;
  retention_days: number;
  auto_purge_enabled: boolean;
  purge_requires_approval: boolean;
  revision: number;
  created_at: string;
  created_by: string;
  updated_at: string;
  updated_by: string;
}

export interface RecoveryEntry {
  id: string;
  tenant_id: string;
  dataset_id: string;
  document_id: string;
  recycle_generation: number;
  active_recycle_key: string | null;
  status: RecoveryEntryStatus;
  revision: number;
  document_mutation_generation: number;
  original_lifecycle_state: RecoveryOriginalLifecycleState;
  original_retrieval_enabled: boolean;
  retention_days_snapshot: number;
  recycled_at: string;
  recycled_by: string;
  purge_eligible_at: string;
  restored_at: string | null;
  restored_by: string | null;
  purge_requested_at: string | null;
  purged_at: string | null;
  purged_by: string | null;
  safe_snapshot: RecoverySafeSnapshot;
  dataset_label?: string;
  document_label?: string;
  current_retrieval_enabled?: boolean;
  active_hold_count?: number | null;
  snapshot_digest: string;
  created_at: string;
  updated_at: string;
}

export interface LegalHold {
  id: string;
  tenant_id: string;
  dataset_id: string;
  document_id: string;
  recycle_entry_id: string;
  status: LegalHoldStatus;
  active_hold_key: string | null;
  revision: number;
  reason_code: string;
  safe_reason: string;
  held_at: string;
  held_by: string;
  released_at: string | null;
  released_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface PurgeRequest {
  id: string;
  tenant_id: string;
  dataset_id: string;
  document_id: string;
  recycle_entry_id: string;
  status: PurgeRequestStatus;
  revision: number;
  expected_entry_revision: number;
  request_digest: string;
  idempotency_key_digest: string;
  approval_request_id: string | null;
  route: RecoveryRoute | null;
  retention_snapshot: RecoverySafeSnapshot;
  legal_hold_count_snapshot: number;
  requested_at: string;
  requested_by: string;
  approved_at: string | null;
  cancelled_at: string | null;
  cancelled_by: string | null;
  expires_at: string;
  executed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RecoveryEvent {
  id: string;
  tenant_id: string;
  dataset_id: string;
  document_id: string;
  recycle_entry_id: string;
  sequence: number;
  event_type: RecoveryEventType;
  previous_event_digest: string | null;
  event_digest: string;
  actor_id: string;
  request_id: string;
  safe_snapshot: RecoverySafeSnapshot;
  occurred_at: string;
}

export interface RecoverySummary {
  tenant_id: string;
  state: RecoverySummaryState;
  recycled_count: number | null;
  expiring_count: number | null;
  held_count: number | null;
  pending_purge_count?: number | null;
  purge_pending_count?: number | null;
  as_of: string | null;
  reason_code: string | null;
}

export interface RecoveryPage<T> {
  items: T[];
  next_cursor: string | null;
  invalid_item_count: number;
}

export interface RecoveryEntryDetail {
  entry: RecoveryEntry;
  events: RecoveryEvent[];
}

export interface RecoveryMutationOutcome {
  state: RecoveryMutationState;
  operation: string;
  resource_id: string | null;
  approval_request_id: string | null;
  route: RecoveryRoute | null;
  revision: number | null;
  message: string | null;
  retryable: boolean;
}

const DIGEST = /^[0-9a-f]{64}$/;
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const SAFE_CODE = /^[a-z][a-z0-9_.-]{0,63}$/;
const SECRET =
  /(?:password|passwd|secret|credential|authorization|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|client[_ -]?secret|idempotency[_ -]?key|ticket|token)\s*[:=]|bearer\s+|(?:secret|token|credential):\/\/|sk_(?:live|test)|ghp_|xox[baprs]-/i;
const URL = /(?:https?|ftp|file|mailto|javascript|data):\S+|(?:^|\s)(?:www\.)\S+/i;
const UNSAFE_SNAPSHOT_KEY =
  /(?:body|content|metadata|query|note|prompt|token|credential|secret|password|authorization|ticket|url|uri|embedding|chunk|raw)/i;

const POLICY_STATUSES = new Set<RecoveryPolicyStatus>(["active", "paused"]);
const ENTRY_STATUSES = new Set<RecoveryEntryStatus>([
  "recycled",
  "restoring",
  "restored",
  "purge_requested",
  "purged",
  "failed",
]);
const ORIGINAL_STATES = new Set<RecoveryOriginalLifecycleState>(["active", "expired"]);
const HOLD_STATUSES = new Set<LegalHoldStatus>(["active", "released"]);
const PURGE_STATUSES = new Set<PurgeRequestStatus>([
  "pending_approval",
  "approved",
  "cancelled",
  "expired",
  "executed",
]);
const EVENT_TYPES = new Set<RecoveryEventType>([
  "recycled",
  "restored",
  "hold_applied",
  "hold_released",
  "purge_requested",
  "purge_approved",
  "purge_cancelled",
]);
const MUTATION_STATES = new Set<RecoveryMutationState>([
  "applied",
  "unchanged",
  "approval_required",
  "rejected",
  "coalesced",
  "replayed",
  "conflict",
  "blocked",
  "unavailable",
]);

function hasControlCharacters(value: string): boolean {
  for (const character of value) {
    const code = character.charCodeAt(0);
    if (code < 32 || code === 127) return true;
  }
  return false;
}

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

function text(value: unknown, field: string, maximum: number, unsafe = true): string {
  if (typeof value !== "string") throw new Error(field + " must be text");
  const normalized = value.trim();
  if (!normalized || normalized.length > maximum || hasControlCharacters(normalized)) {
    throw new Error(field + " is invalid");
  }
  if (unsafe && (SECRET.test(normalized) || URL.test(normalized))) {
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

function exactInteger(value: unknown, field: string, minimum = 1, maximum?: number): number {
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
    value < minimum ||
    (maximum !== undefined && value > maximum)
  ) {
    throw new Error(field + " must be an exact integer");
  }
  return value;
}

function nullableInteger(value: unknown, field: string, minimum = 1): number | null {
  return value === null || value === undefined ? null : exactInteger(value, field, minimum);
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
  return base.toISOString().slice(0, 19) + "." + (match[2] ?? "").padEnd(6, "0") + "Z";
}

function nullableTimestamp(value: unknown, field: string): string | null {
  return value === null || value === undefined ? null : normalizeTimestamp(value, field);
}

function nullableText(value: unknown, field: string, maximum: number): string | null {
  return value === null || value === undefined ? null : text(value, field, maximum);
}

function scopeTenant(scope: RecoveryModelScope): string {
  return identifier(scope.tenantId, "tenantId", 64);
}

function assertTenant(value: unknown, scope: RecoveryModelScope): string {
  const tenantId = identifier(value, "tenant_id", 64);
  if (tenantId !== scopeTenant(scope)) throw new Error("Recovery scope mismatch");
  return tenantId;
}

function safeSnapshot(value: unknown, field: string): RecoverySafeSnapshot {
  const source = record(value, field);
  const result: Record<string, RecoverySafeValue> = {};
  const keys = Object.keys(source);
  if (keys.length > 32) throw new Error(field + " has too many fields");
  for (const key of keys) {
    if (!/^[A-Za-z][A-Za-z0-9_.:-]{0,63}$/.test(key) || UNSAFE_SNAPSHOT_KEY.test(key)) {
      throw new Error(field + " contains unsafe field " + key);
    }
    const raw = source[key];
    if (typeof raw === "string") {
      result[key] = text(raw, field + "." + key, 512);
    } else if (raw === null || typeof raw === "boolean") {
      result[key] = raw;
    } else if (typeof raw === "number") {
      if (!Number.isFinite(raw)) throw new Error(field + "." + key + " must be finite");
      result[key] = raw;
    } else {
      throw new Error(field + "." + key + " must be a safe scalar");
    }
  }
  return result;
}

function nullableApprovalId(value: unknown, field: string): string | null {
  return value === null || value === undefined ? null : identifier(value, field);
}

function queryHref(path: string, query: Readonly<Record<string, string>>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) params.set(key, value);
  return path + "?" + params.toString();
}

export function projectRecoveryRoute(value: unknown): RecoveryRoute | null {
  const source = exactKeys(value, "recovery.route", ["approval_request_id"]);
  const approvalRequestId = nullableApprovalId(source.approval_request_id, "approval_request_id");
  if (approvalRequestId === null) return null;
  const path = "/enterprise/approvals" as const;
  const query = { request: approvalRequestId } as const;
  return { code: "enterprise_approval", path, query, href: queryHref(path, query) };
}

export function projectContentRetentionPolicy(
  value: unknown,
  scope: RecoveryModelScope,
): ContentRetentionPolicy {
  const source = exactKeys(value, "content_retention_policy", [
    "id",
    "tenant_id",
    "status",
    "retention_days",
    "auto_purge_enabled",
    "purge_requires_approval",
    "revision",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
  ]);
  return {
    id: identifier(source.id, "id"),
    tenant_id: assertTenant(source.tenant_id, scope),
    status: enumValue(source.status, "status", POLICY_STATUSES),
    retention_days: exactInteger(source.retention_days, "retention_days", 1, 3650),
    auto_purge_enabled: exactBoolean(source.auto_purge_enabled, "auto_purge_enabled"),
    purge_requires_approval: exactBoolean(
      source.purge_requires_approval,
      "purge_requires_approval",
    ),
    revision: exactInteger(source.revision, "revision"),
    created_at: normalizeTimestamp(source.created_at, "created_at"),
    created_by: identifier(source.created_by, "created_by"),
    updated_at: normalizeTimestamp(source.updated_at, "updated_at"),
    updated_by: identifier(source.updated_by, "updated_by"),
  };
}

export const projectRetentionPolicy = projectContentRetentionPolicy;

export function projectRecoveryEntry(value: unknown, scope: RecoveryModelScope): RecoveryEntry {
  const source = exactKeys(
    value,
    "recycle_entry",
    [
      "id",
      "tenant_id",
      "dataset_id",
      "document_id",
      "recycle_generation",
      "active_recycle_key",
      "status",
      "revision",
      "document_mutation_generation",
      "original_lifecycle_state",
      "original_retrieval_enabled",
      "retention_days_snapshot",
      "recycled_at",
      "recycled_by",
      "purge_eligible_at",
      "restored_at",
      "restored_by",
      "purge_requested_at",
      "purged_at",
      "purged_by",
      "safe_snapshot_json",
      "snapshot_digest",
      "created_at",
      "updated_at",
    ],
    [
      "id",
      "tenant_id",
      "dataset_id",
      "document_id",
      "recycle_generation",
      "active_recycle_key",
      "status",
      "revision",
      "document_mutation_generation",
      "original_lifecycle_state",
      "original_retrieval_enabled",
      "retention_days_snapshot",
      "recycled_at",
      "recycled_by",
      "purge_eligible_at",
      "restored_at",
      "restored_by",
      "purge_requested_at",
      "purged_at",
      "purged_by",
      "safe_snapshot_json",
      "snapshot_digest",
      "created_at",
      "updated_at",
      "dataset_label",
      "document_label",
      "current_retrieval_enabled",
      "active_hold_count",
    ],
  );
  const status = enumValue(source.status, "status", ENTRY_STATUSES);
  const datasetId = identifier(source.dataset_id, "dataset_id", 64);
  const documentId = identifier(source.document_id, "document_id", 128);
  const activeKey =
    source.active_recycle_key === null
      ? null
      : identifier(source.active_recycle_key, "active_recycle_key", 256);
  const shouldBeActive =
    status === "recycled" || status === "restoring" || status === "purge_requested";
  const canonicalKey = datasetId + ":" + documentId;
  if (shouldBeActive && activeKey !== canonicalKey) {
    throw new Error("active_recycle_key does not match recovery lifecycle");
  }
  if (!shouldBeActive && activeKey !== null) {
    throw new Error("inactive recovery entry must not have active_recycle_key");
  }
  const recycledAt = normalizeTimestamp(source.recycled_at, "recycled_at");
  const recycledBy = identifier(source.recycled_by, "recycled_by");
  const restoredAt = nullableTimestamp(source.restored_at, "restored_at");
  const restoredBy =
    source.restored_by === null || source.restored_by === undefined
      ? null
      : identifier(source.restored_by, "restored_by");
  const purgeRequestedAt = nullableTimestamp(source.purge_requested_at, "purge_requested_at");
  const purgedAt = nullableTimestamp(source.purged_at, "purged_at");
  const purgedBy =
    source.purged_by === null || source.purged_by === undefined
      ? null
      : identifier(source.purged_by, "purged_by");
  if (status === "restored" && (restoredAt === null || restoredBy === null)) {
    throw new Error("restored recovery entry requires restore evidence");
  }
  if (status === "purged" && (purgedAt === null || purgedBy === null)) {
    throw new Error("purged recovery entry requires purge evidence");
  }
  if (status === "purge_requested" && purgeRequestedAt === null) {
    throw new Error("purge_requested recovery entry requires request evidence");
  }
  if (status === "recycled" && (restoredAt !== null || restoredBy !== null || purgedAt !== null)) {
    throw new Error("recycled recovery entry has inconsistent terminal evidence");
  }
  const datasetLabel =
    source.dataset_label === undefined
      ? undefined
      : text(source.dataset_label, "dataset_label", 256);
  const documentLabel =
    source.document_label === undefined
      ? undefined
      : text(source.document_label, "document_label", 512);
  const currentRetrievalEnabled =
    source.current_retrieval_enabled === undefined
      ? undefined
      : exactBoolean(source.current_retrieval_enabled, "current_retrieval_enabled");
  const activeHoldCount =
    source.active_hold_count === undefined || source.active_hold_count === null
      ? source.active_hold_count === undefined
        ? undefined
        : null
      : exactInteger(source.active_hold_count, "active_hold_count", 0);
  const projectedEntry: RecoveryEntry = {
    id: identifier(source.id, "id"),
    tenant_id: assertTenant(source.tenant_id, scope),
    dataset_id: datasetId,
    document_id: documentId,
    recycle_generation: exactInteger(source.recycle_generation, "recycle_generation"),
    active_recycle_key: activeKey,
    status,
    revision: exactInteger(source.revision, "revision"),
    document_mutation_generation: exactInteger(
      source.document_mutation_generation,
      "document_mutation_generation",
      0,
    ),
    original_lifecycle_state: enumValue(
      source.original_lifecycle_state,
      "original_lifecycle_state",
      ORIGINAL_STATES,
    ),
    original_retrieval_enabled: exactBoolean(
      source.original_retrieval_enabled,
      "original_retrieval_enabled",
    ),
    retention_days_snapshot: exactInteger(
      source.retention_days_snapshot,
      "retention_days_snapshot",
      1,
      3650,
    ),
    recycled_at: recycledAt,
    recycled_by: recycledBy,
    purge_eligible_at: normalizeTimestamp(source.purge_eligible_at, "purge_eligible_at"),
    restored_at: restoredAt,
    restored_by: restoredBy,
    purge_requested_at: purgeRequestedAt,
    purged_at: purgedAt,
    purged_by: purgedBy,
    safe_snapshot: safeSnapshot(source.safe_snapshot_json, "safe_snapshot_json"),
    ...(datasetLabel === undefined ? {} : { dataset_label: datasetLabel }),
    ...(documentLabel === undefined ? {} : { document_label: documentLabel }),
    ...(currentRetrievalEnabled === undefined
      ? {}
      : { current_retrieval_enabled: currentRetrievalEnabled }),
    ...(activeHoldCount === undefined ? {} : { active_hold_count: activeHoldCount }),
    snapshot_digest: digest(source.snapshot_digest, "snapshot_digest") as string,
    created_at: normalizeTimestamp(source.created_at, "created_at"),
    updated_at: normalizeTimestamp(source.updated_at, "updated_at"),
  };
  return projectedEntry;
}

export const projectRecycleEntry = projectRecoveryEntry;

export function projectDocumentLegalHold(value: unknown, scope: RecoveryModelScope): LegalHold {
  const source = exactKeys(value, "legal_hold", [
    "id",
    "tenant_id",
    "dataset_id",
    "document_id",
    "recycle_entry_id",
    "status",
    "active_hold_key",
    "revision",
    "reason_code",
    "safe_reason",
    "held_at",
    "held_by",
    "released_at",
    "released_by",
    "created_at",
    "updated_at",
  ]);
  const status = enumValue(source.status, "status", HOLD_STATUSES);
  const recycleEntryId = identifier(source.recycle_entry_id, "recycle_entry_id");
  const reasonCode = code(source.reason_code, "reason_code");
  const activeHoldKey =
    source.active_hold_key === null
      ? null
      : identifier(source.active_hold_key, "active_hold_key", 256);
  const canonicalKey = recycleEntryId + ":" + reasonCode;
  if (status === "active" && activeHoldKey !== canonicalKey) {
    throw new Error("active_hold_key does not match legal hold identity");
  }
  if (status === "released" && activeHoldKey !== null) {
    throw new Error("released legal hold must not have active_hold_key");
  }
  const releasedAt = nullableTimestamp(source.released_at, "released_at");
  const releasedBy =
    source.released_by === null || source.released_by === undefined
      ? null
      : identifier(source.released_by, "released_by");
  if (status === "released" && (releasedAt === null || releasedBy === null)) {
    throw new Error("released legal hold requires release evidence");
  }
  return {
    id: identifier(source.id, "id"),
    tenant_id: assertTenant(source.tenant_id, scope),
    dataset_id: identifier(source.dataset_id, "dataset_id", 64),
    document_id: identifier(source.document_id, "document_id"),
    recycle_entry_id: recycleEntryId,
    status,
    active_hold_key: activeHoldKey,
    revision: exactInteger(source.revision, "revision"),
    reason_code: reasonCode,
    safe_reason: text(source.safe_reason, "safe_reason", 512),
    held_at: normalizeTimestamp(source.held_at, "held_at"),
    held_by: identifier(source.held_by, "held_by"),
    released_at: releasedAt,
    released_by: releasedBy,
    created_at: normalizeTimestamp(source.created_at, "created_at"),
    updated_at: normalizeTimestamp(source.updated_at, "updated_at"),
  };
}

export const projectLegalHold = projectDocumentLegalHold;

export function projectDocumentPurgeRequest(
  value: unknown,
  scope: RecoveryModelScope,
): PurgeRequest {
  const source = exactKeys(value, "purge_request", [
    "id",
    "tenant_id",
    "dataset_id",
    "document_id",
    "recycle_entry_id",
    "status",
    "revision",
    "expected_entry_revision",
    "request_digest",
    "idempotency_key_digest",
    "approval_request_id",
    "route",
    "retention_snapshot_json",
    "legal_hold_count_snapshot",
    "requested_at",
    "requested_by",
    "approved_at",
    "cancelled_at",
    "cancelled_by",
    "expires_at",
    "executed_at",
    "created_at",
    "updated_at",
  ]);
  const status = enumValue(source.status, "status", PURGE_STATUSES);
  const approvalRequestId = nullableApprovalId(source.approval_request_id, "approval_request_id");
  if ((status === "pending_approval" || status === "approved") && approvalRequestId === null) {
    throw new Error("active purge request requires an Approval handoff");
  }
  const datasetId = identifier(source.dataset_id, "dataset_id", 64);
  const documentId = identifier(source.document_id, "document_id");
  const entryId = identifier(source.recycle_entry_id, "recycle_entry_id");
  const retentionSnapshot = safeSnapshot(source.retention_snapshot_json, "retention_snapshot_json");
  const holdCount = exactInteger(source.legal_hold_count_snapshot, "legal_hold_count_snapshot", 0);
  if ((status === "pending_approval" || status === "approved") && holdCount !== 0) {
    throw new Error("purge request with an active legal hold is invalid");
  }
  const cancelledAt = nullableTimestamp(source.cancelled_at, "cancelled_at");
  const cancelledBy =
    source.cancelled_by === null || source.cancelled_by === undefined
      ? null
      : identifier(source.cancelled_by, "cancelled_by");
  if (status === "cancelled" && (cancelledAt === null || cancelledBy === null)) {
    throw new Error("cancelled purge request requires cancellation evidence");
  }
  return {
    id: identifier(source.id, "id"),
    tenant_id: assertTenant(source.tenant_id, scope),
    dataset_id: datasetId,
    document_id: documentId,
    recycle_entry_id: entryId,
    status,
    revision: exactInteger(source.revision, "revision"),
    expected_entry_revision: exactInteger(
      source.expected_entry_revision,
      "expected_entry_revision",
    ),
    request_digest: digest(source.request_digest, "request_digest") as string,
    idempotency_key_digest: digest(
      source.idempotency_key_digest,
      "idempotency_key_digest",
    ) as string,
    approval_request_id: approvalRequestId,
    route: projectMutationRoute(source.route, approvalRequestId),
    retention_snapshot: retentionSnapshot,
    legal_hold_count_snapshot: holdCount,
    requested_at: normalizeTimestamp(source.requested_at, "requested_at"),
    requested_by: identifier(source.requested_by, "requested_by"),
    approved_at: nullableTimestamp(source.approved_at, "approved_at"),
    cancelled_at: cancelledAt,
    cancelled_by: cancelledBy,
    expires_at: normalizeTimestamp(source.expires_at, "expires_at"),
    executed_at: nullableTimestamp(source.executed_at, "executed_at"),
    created_at: normalizeTimestamp(source.created_at, "created_at"),
    updated_at: normalizeTimestamp(source.updated_at, "updated_at"),
  };
}

export const projectPurgeRequest = projectDocumentPurgeRequest;

export function projectRecoveryEvent(value: unknown, scope: RecoveryModelScope): RecoveryEvent {
  const source = exactKeys(value, "recovery_event", [
    "id",
    "tenant_id",
    "dataset_id",
    "document_id",
    "recycle_entry_id",
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
    id: identifier(source.id, "id"),
    tenant_id: assertTenant(source.tenant_id, scope),
    dataset_id: identifier(source.dataset_id, "dataset_id", 64),
    document_id: identifier(source.document_id, "document_id"),
    recycle_entry_id: identifier(source.recycle_entry_id, "recycle_entry_id"),
    sequence: exactInteger(source.sequence, "sequence"),
    event_type: enumValue(source.event_type, "event_type", EVENT_TYPES),
    previous_event_digest: digest(source.previous_event_digest, "previous_event_digest", true),
    event_digest: digest(source.event_digest, "event_digest") as string,
    actor_id: identifier(source.actor_id, "actor_id"),
    request_id: identifier(source.request_id, "request_id"),
    safe_snapshot: safeSnapshot(source.safe_snapshot_json, "safe_snapshot_json"),
    occurred_at: normalizeTimestamp(source.occurred_at, "occurred_at"),
  };
}

export const projectRecoveryAuditEvent = projectRecoveryEvent;

export function projectRecoverySummary(value: unknown, scope: RecoveryModelScope): RecoverySummary {
  const source = exactKeys(
    value,
    "recovery_summary",
    ["tenant_id", "state", "recycled_count", "expiring_count", "held_count", "as_of"],
    [
      "tenant_id",
      "state",
      "recycled_count",
      "expiring_count",
      "held_count",
      "pending_purge_count",
      "purge_pending_count",
      "as_of",
      "reason_code",
    ],
  );
  const hasPending = Object.prototype.hasOwnProperty.call(source, "pending_purge_count");
  const hasPurgePending = Object.prototype.hasOwnProperty.call(source, "purge_pending_count");
  if (hasPending === hasPurgePending) {
    throw new Error("recovery_summary must contain exactly one purge pending count");
  }
  const state = enumValue(
    source.state,
    "state",
    new Set<RecoverySummaryState>(["ready", "partial", "unavailable", "error"]),
  );
  const reasonCode = nullableText(source.reason_code, "reason_code", 96);
  const pendingValue = hasPending ? source.pending_purge_count : source.purge_pending_count;
  const projectedPending =
    pendingValue === null ? null : exactInteger(pendingValue, "purge_pending_count", 0);
  const recycledCount =
    source.recycled_count === null
      ? null
      : exactInteger(source.recycled_count, "recycled_count", 0);
  const expiringCount =
    source.expiring_count === null
      ? null
      : exactInteger(source.expiring_count, "expiring_count", 0);
  const heldCount =
    source.held_count === null ? null : exactInteger(source.held_count, "held_count", 0);
  const asOf = source.as_of === null ? null : normalizeTimestamp(source.as_of, "as_of");
  if (
    state === "ready" &&
    (recycledCount === null ||
      expiringCount === null ||
      heldCount === null ||
      projectedPending === null ||
      asOf === null)
  ) {
    throw new Error("ready recovery summary must contain authoritative counts");
  }
  if (
    state === "unavailable" &&
    (recycledCount !== null ||
      expiringCount !== null ||
      heldCount !== null ||
      projectedPending !== null ||
      asOf !== null)
  ) {
    throw new Error("unavailable recovery summary must not claim counts");
  }
  return {
    tenant_id: assertTenant(source.tenant_id, scope),
    state,
    recycled_count: recycledCount,
    expiring_count: expiringCount,
    held_count: heldCount,
    as_of: asOf,
    reason_code: reasonCode,
    ...(hasPending
      ? { pending_purge_count: projectedPending }
      : { purge_pending_count: projectedPending }),
  };
}

export function projectRecoveryEntryDetail(
  value: unknown,
  scope: RecoveryModelScope,
): RecoveryEntryDetail {
  const source = exactKeys(value, "recovery_entry_detail", ["entry", "events"]);
  if (!Array.isArray(source.events))
    throw new Error("recovery_entry_detail.events must be an array");
  const entry = projectRecoveryEntry(source.entry, scope);
  const events: RecoveryEvent[] = [];
  for (const rawEvent of source.events) {
    const event = projectRecoveryEvent(rawEvent, scope);
    if (
      event.tenant_id !== entry.tenant_id ||
      event.dataset_id !== entry.dataset_id ||
      event.document_id !== entry.document_id ||
      event.recycle_entry_id !== entry.id
    ) {
      throw new Error("recovery event identity is inconsistent");
    }
    const previous = events[events.length - 1];
    if (!previous) {
      if (
        event.sequence !== 1 ||
        event.previous_event_digest !== null ||
        event.event_type !== "recycled"
      ) {
        throw new Error("recovery event chain must start with recycled sequence 1");
      }
    } else if (
      event.sequence !== previous.sequence + 1 ||
      event.previous_event_digest !== previous.event_digest
    ) {
      throw new Error("recovery event chain is inconsistent");
    }
    events.push(event);
  }
  return { entry, events };
}

export function sanitizeRecoveryMessage(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const normalized = value.trim();
  if (
    !normalized ||
    normalized.length > 512 ||
    hasControlCharacters(normalized) ||
    SECRET.test(normalized) ||
    URL.test(normalized)
  ) {
    return fallback;
  }
  return normalized;
}

function projectMutationRoute(
  value: unknown,
  approvalRequestId: string | null,
): RecoveryRoute | null {
  const canonical = projectRecoveryRoute({ approval_request_id: approvalRequestId });
  if (value === null) {
    if (canonical !== null) throw new Error("recovery.route is required for approval handoff");
    return null;
  }
  const source = exactKeys(value, "recovery.route", [
    "target_route_code",
    "target_route_params_json",
  ]);
  if (source.target_route_code !== "enterprise_approval" || canonical === null) {
    throw new Error("recovery.route is invalid");
  }
  const params = exactKeys(source.target_route_params_json, "recovery.route.params", [
    "approval_request_id",
  ]);
  if (nullableApprovalId(params.approval_request_id, "approval_request_id") !== approvalRequestId) {
    throw new Error("recovery.route approval_request_id does not match");
  }
  return canonical;
}


export function projectRecoveryMutationOutcome(value: unknown): RecoveryMutationOutcome {
  const source = exactKeys(value, "recovery_mutation", [
    "state",
    "operation",
    "resource_id",
    "approval_request_id",
    "route",
    "revision",
    "message",
    "retryable",
  ]);
  const approvalRequestId = nullableApprovalId(source.approval_request_id, "approval_request_id");
  return {
    state: enumValue(source.state, "state", MUTATION_STATES),
    operation: code(source.operation, "operation"),
    resource_id: source.resource_id === null ? null : identifier(source.resource_id, "resource_id"),
    approval_request_id: approvalRequestId,
    route: projectMutationRoute(source.route, approvalRequestId),
    revision: nullableInteger(source.revision, "revision"),
    message:
      source.message === null || source.message === undefined
        ? null
        : sanitizeRecoveryMessage(source.message, "Recovery operation unavailable"),
    retryable: exactBoolean(source.retryable, "retryable"),
  };
}
