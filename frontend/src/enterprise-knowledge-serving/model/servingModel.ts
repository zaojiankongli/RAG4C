export const SERVING_STAGE_CODES = ["source", "parse", "chunk", "index", "serve"] as const;
export type ServingStageCode = (typeof SERVING_STAGE_CODES)[number];
export type ServingStageState = "ready" | "lagging" | "blocked" | "missing" | "unavailable";
export const SERVING_STAGE_STATES = [
  "ready",
  "lagging",
  "blocked",
  "missing",
  "unavailable",
] as const;
export type ServingOverallState = "ready" | "degraded" | "blocked" | "unavailable";
export const SERVING_OVERALL_STATES = ["ready", "degraded", "blocked", "unavailable"] as const;
export type ServingProfileStatus = "draft" | "active" | "paused" | "archived";
export type ServingEvidenceKind =
  | "source"
  | "source_sync_run"
  | "document"
  | "ingest_attempt"
  | "chunk_head"
  | "index_operation"
  | "release"
  | "certification"
  | "task";
export const SERVING_EVIDENCE_KINDS: readonly ServingEvidenceKind[] = [
  "source",
  "source_sync_run",
  "document",
  "ingest_attempt",
  "chunk_head",
  "index_operation",
  "release",
  "certification",
  "task",
];
export type ServingEventType =
  | "profile_created"
  | "policy_revision_created"
  | "policy_activated"
  | "snapshot_recorded"
  | "stage_degraded"
  | "stage_blocked"
  | "service_recovered";
export const SERVING_EVENT_TYPES: readonly ServingEventType[] = [
  "profile_created",
  "policy_revision_created",
  "policy_activated",
  "snapshot_recorded",
  "stage_degraded",
  "stage_blocked",
  "service_recovered",
];
export const SERVING_ROUTE_CODES = [
  "knowledge_sources",
  "knowledge_documents",
  "knowledge_indexing",
  "knowledge_base_releases",
  "release_quality",
  "enterprise_tasks",
] as const;
export type ServingRouteCode = (typeof SERVING_ROUTE_CODES)[number];
export interface ServingEvidenceRoute {
  code: ServingRouteCode;
  path: string;
  parameter: string;
  href: string;
}
interface ServingEvidenceRouteSpec {
  code: ServingRouteCode;
  path: string;
  parameter: string;
}
export const SERVING_EVIDENCE_ROUTE_CATALOG = {
  source: { code: "knowledge_sources", path: "/enterprise/knowledge-base", parameter: "source" },
  source_sync_run: {
    code: "knowledge_sources",
    path: "/enterprise/knowledge-base",
    parameter: "sync",
  },
  document: {
    code: "knowledge_documents",
    path: "/enterprise/knowledge-base",
    parameter: "document",
  },
  ingest_attempt: {
    code: "knowledge_documents",
    path: "/enterprise/knowledge-base",
    parameter: "document",
  },
  chunk_head: {
    code: "knowledge_documents",
    path: "/enterprise/knowledge-base",
    parameter: "document",
  },
  index_operation: {
    code: "knowledge_indexing",
    path: "/enterprise/tasks",
    parameter: "operation",
  },
  release: {
    code: "knowledge_base_releases",
    path: "/enterprise/knowledge-base",
    parameter: "release",
  },
  certification: {
    code: "release_quality",
    path: "/enterprise/knowledge-base",
    parameter: "certification",
  },
  task: { code: "enterprise_tasks", path: "/enterprise/tasks", parameter: "task" },
} as const satisfies Readonly<Record<ServingEvidenceKind, ServingEvidenceRouteSpec>>;

export interface ServingScope {
  tenantId: string;
  accountId?: string;
  datasetId: string;
}
export interface ServingProfile {
  id: string;
  tenant_id: string;
  workspace_id: string | null;
  dataset_id: string;
  name: string;
  normalized_name: string;
  status: ServingProfileStatus;
  active_profile_key: string | null;
  revision: number;
  current_policy_revision_id: string | null;
  current_snapshot_id: string | null;
  created_at: string;
  created_by: string;
  updated_at: string;
  updated_by: string;
  archived_at: string | null;
  archived_by: string | null;
}
export interface ServingPolicyRevision {
  id: string;
  tenant_id: string;
  profile_id: string;
  revision: number;
  max_source_staleness_seconds: number;
  max_parse_lag_seconds: number;
  max_index_lag_seconds: number;
  max_failed_document_count: number;
  max_pending_index_count: number;
  require_current_release: boolean;
  require_passing_certification: boolean;
  policy_digest: string;
  created_at: string;
  created_by: string;
}
export interface ServingSnapshot {
  id: string;
  tenant_id: string;
  profile_id: string;
  policy_revision_id: string;
  observation_key: string;
  state: ServingOverallState;
  source_count: number;
  ready_source_count: number;
  stale_source_count: number;
  active_document_count: number;
  failed_document_count: number;
  pending_index_count: number;
  expected_serving_generation: number;
  observed_serving_generation: number;
  current_release_id: string | null;
  current_certification_id: string | null;
  stage_count: number;
  ready_stage_count: number;
  blocked_stage_count: number;
  snapshot_digest: string;
  as_of: string;
  created_at: string;
  created_by: string;
}
export interface ServingStageFact {
  id: string;
  tenant_id: string;
  profile_id: string;
  snapshot_id: string;
  stage_code: ServingStageCode;
  sequence: number;
  state: ServingStageState;
  item_count: number;
  ready_count: number;
  warning_count: number;
  pending_count: number;
  error_count: number;
  lag_seconds: number;
  expected_revision: number | null;
  observed_revision: number | null;
  expected_digest: string | null;
  observed_digest: string | null;
  safe_error_code: string | null;
  safe_error: string | null;
  stage_digest: string;
  observed_at: string;
}
export interface ServingEvidenceLink {
  id: string;
  tenant_id: string;
  profile_id: string;
  snapshot_id: string;
  stage_fact_id: string;
  evidence_kind: ServingEvidenceKind;
  resource_id: string;
  resource_revision: number | null;
  resource_digest: string | null;
  route_code: ServingRouteCode;
  safe_label: string;
  evidence_digest: string;
  created_at: string;
}
export type ServingSafeValue =
  | string
  | number
  | boolean
  | null
  | ReadonlyArray<ServingSafeValue>
  | Readonly<{ [key: string]: ServingSafeValue }>;
export interface ServingEvent {
  id: string;
  tenant_id: string;
  profile_id: string;
  snapshot_id: string | null;
  stream_key: string;
  sequence: number;
  event_type: ServingEventType;
  previous_event_digest: string | null;
  event_digest: string;
  actor_id: string;
  request_id: string;
  safe_snapshot: Readonly<Record<string, ServingSafeValue>>;
  occurred_at: string;
}
export interface ServingSummary {
  tenant_id: string;
  dataset_id: string;
  profile_id: string | null;
  profile_name: string | null;
  profile_status: ServingProfileStatus | null;
  state: ServingOverallState;
  as_of: string | null;
  snapshot_id: string | null;
  snapshot_digest: string | null;
  policy_revision: number | null;
  serving_generation: number | null;
  source_count: number | null;
  ready_source_count: number | null;
  stale_source_count: number | null;
  active_document_count: number | null;
  failed_document_count: number | null;
  pending_index_count: number | null;
  stage_count: number;
  ready_stage_count: number;
  blocked_stage_count: number;
  current_release_id: string | null;
  current_certification_id: string | null;
  stage_facts: ServingStageFact[];
  reason_code: string | null;
}
export interface ServingProfileEnvelope {
  profile: ServingProfile;
  current_policy: ServingPolicyRevision | null;
  current_snapshot: ServingSnapshot | null;
}
export interface ServingSnapshotDetail {
  snapshot: ServingSnapshot;
  stage_facts: ServingStageFact[];
  evidence_links: ServingEvidenceLink[];
  events: ServingEvent[];
}
export interface ServingPage<T> {
  items: T[];
  count: number | null;
  next_cursor: string | null;
  invalid_item_count: number;
}
export type ServingEventPage = ServingPage<ServingEvent>;
export interface ServingBlocker {
  code: string;
  stage_code: ServingStageCode | null;
  safe_message: string;
}
export interface ServingPreview {
  preview: true;
  state: ServingOverallState;
  policy_revision: number;
  stage_facts: ServingStageFact[];
  blockers: ServingBlocker[];
}
export type ServingMutationState =
  "applied" | "replayed" | "conflict" | "blocked" | "rejected" | "unavailable";
export interface ServingMutationOutcome {
  state: ServingMutationState;
  operation: string;
  resource_id: string | null;
  revision: number | null;
  message: string | null;
  retryable: boolean;
}
export interface ServingHandoff {
  evidence_kind: ServingEvidenceKind;
  route_code: ServingRouteCode;
  dataset_id: string;
  resource_id: string | null;
}

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$/;
const CODE = /^[a-z][a-z0-9_.-]{0,127}$/;
const DIGEST = /^[0-9a-f]{64}$/;
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f]/;
const URL = /(?:[a-z][a-z0-9+.-]{1,31}:\/\/|(?:^|\s)www\.)\S+/i;
const SECRET =
  /(?:password|secret|credential|authorization|bearer|token|ticket|api[_ -]?key|access[_ -]?key)\s*[:=]?\s*\S+/i;
const SQL_LIKE =
  /(?:\bselect\s+(?:distinct\s+)?[\w*"\x60'([]|\binsert\s+(?:into\s+)?[\w"\x60'(]|\bupdate\s+[\w"\x60.]+\s+set\b|\bdelete\s+from\b|\bdrop\s+(?:table|database|schema|index|view|trigger|procedure|function)\b|\balter\s+(?:table|database|schema|index|view|trigger|procedure|function)\b|\bcreate\s+(?:table|database|schema|index|view|trigger|procedure|function)\b|\bgrant\s+\w+\s+on\b|\brevoke\s+\w+\s+on\b|\bexec(?:ute)?\s+\S+|\bunion\s+(?:all\s+)?select\b)/i;
const BEARER = /\bbearer\b/i;
const JWT =
  /(?:^|[^A-Za-z0-9_-])[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}(?![A-Za-z0-9_-])/;
const MAX_SAFE_DEPTH = 12;
const MAX_SAFE_ITEMS = 64;
const MAX_SAFE_UTF8_BYTES = 16_384;
const UNSAFE_KEY_PARTS = [
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
] as const;

function object(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value))
    throw new Error(field + " must be an object");
  return value as Record<string, unknown>;
}
function required(source: Record<string, unknown>, key: string, field = key): unknown {
  if (!Object.prototype.hasOwnProperty.call(source, key)) throw new Error(field + " is required");
  return source[key];
}
function exact(value: unknown, field: string, allowed: readonly string[]): Record<string, unknown> {
  const source = object(value, field);
  const set = new Set(allowed);
  for (const key of Object.keys(source))
    if (!set.has(key)) throw new Error(field + " contains unexpected field: " + key);
  return source;
}
function text(value: unknown, field: string, max = 512): string {
  if (typeof value !== "string") throw new Error(field + " is invalid");
  const result = value.trim();
  if (
    !result ||
    result.length > max ||
    CONTROL.test(result) ||
    URL.test(result) ||
    SECRET.test(result) ||
    SQL_LIKE.test(result) ||
    BEARER.test(result) ||
    JWT.test(result)
  )
    throw new Error(field + " is unsafe");
  return result;
}
function id(value: unknown, field: string): string {
  const result = text(value, field, 128);
  if (!IDENTIFIER.test(result) || result.includes("..")) throw new Error(field + " is invalid");
  return result;
}
function resourceIdentifier(value: unknown, field: string): string {
  if (typeof value !== "string") throw new Error(field + " is invalid resource identifier");
  const result = value.trim();
  if (
    !result ||
    result.length > 128 ||
    CONTROL.test(result) ||
    /\s/.test(result) ||
    URL.test(result) ||
    SECRET.test(result)
  )
    throw new Error(field + " is invalid resource identifier");
  return result;
}
function code(value: unknown, field: string): string {
  const result = text(value, field, 128);
  if (!CODE.test(result)) throw new Error(field + " is invalid code");
  return result;
}
function digest(value: unknown, field: string): string {
  const result = text(value, field, 64);
  if (!DIGEST.test(result)) throw new Error(field + " is invalid digest");
  return result;
}
function integer(value: unknown, field: string, min = 0, max = Number.MAX_SAFE_INTEGER): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min || value > max)
    throw new Error(field + " must be an exact integer");
  return value;
}
function bool(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") throw new Error(field + " must be boolean");
  return value;
}
function time(value: unknown, field: string): string {
  const result = text(value, field, 64);
  if (Number.isNaN(Date.parse(result))) throw new Error(field + " is invalid time");
  return result;
}
function nullableText(value: unknown, field: string, max = 512): string | null {
  return value === null ? null : text(value, field, max);
}
function nullableInteger(value: unknown, field: string, min = 0): number | null {
  return value === null ? null : integer(value, field, min);
}
function tenant(value: unknown, scope: ServingScope): string {
  const result = id(value, "tenant_id");
  if (result !== id(scope.tenantId, "scope.tenantId")) throw new Error("tenant scope mismatch");
  return result;
}
function dataset(value: unknown, scope: ServingScope): string {
  const result = id(value, "dataset_id");
  if (result !== id(scope.datasetId, "scope.datasetId")) throw new Error("dataset scope mismatch");
  return result;
}
function enumValue<T extends string>(value: unknown, field: string, values: readonly T[]): T {
  const result = text(value, field, 128);
  if (!values.includes(result as T)) throw new Error(field + " is invalid");
  return result as T;
}
function normalizedKey(key: string): string {
  return key
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/[^a-z0-9]+/gi, "_")
    .toLowerCase()
    .replace(/^_+|_+$/g, "");
}
function unsafeKey(key: string): boolean {
  const compact = normalizedKey(key).replaceAll("_", "");
  return UNSAFE_KEY_PARTS.some((part) => compact.includes(part));
}
function safeMappingText(value: unknown, field: string): string {
  if (typeof value !== "string") throw new Error(field + " is invalid");
  const result = value.trim();
  if (
    !result ||
    CONTROL.test(result) ||
    URL.test(result) ||
    SECRET.test(result) ||
    SQL_LIKE.test(result) ||
    BEARER.test(result) ||
    JWT.test(result)
  )
    throw new Error(field + " is unsafe");
  if (new TextEncoder().encode(result).length > MAX_SAFE_UTF8_BYTES)
    throw new Error(field + " exceeds safe byte limit");
  return result;
}
function safeUtf8Bytes(value: ServingSafeValue): number {
  return new TextEncoder().encode(JSON.stringify(value)).length;
}
function safeValue(
  value: unknown,
  field: string,
  depth = 0,
  items = { value: 0 },
): ServingSafeValue {
  if (depth > MAX_SAFE_DEPTH) throw new Error(field + " is too deeply nested");
  items.value += 1;
  if (items.value > MAX_SAFE_ITEMS) throw new Error(field + " contains too many items");
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new Error(field + " must be a safe integer");
    return value;
  }
  if (typeof value === "string") return safeMappingText(value, field);
  if (Array.isArray(value)) {
    const result = value.map((item, index) =>
      safeValue(item, field + "[" + index + "]", depth + 1, items),
    );
    if (safeUtf8Bytes(result) > MAX_SAFE_UTF8_BYTES)
      throw new Error(field + " exceeds safe byte limit");
    return result;
  }
  const source = object(value, field);
  const result: Record<string, ServingSafeValue> = {};
  for (const key of Object.keys(source).sort()) {
    const item = source[key];
    if (unsafeKey(key)) throw new Error(field + "." + key + " is unsafe");
    result[key] = safeValue(item, field + "." + key, depth + 1, items);
  }
  if (safeUtf8Bytes(result) > MAX_SAFE_UTF8_BYTES)
    throw new Error(field + " exceeds safe byte limit");
  return result;
}
function safeMapping(value: unknown, field: string): Readonly<Record<string, ServingSafeValue>> {
  const result = safeValue(value, field);
  if (typeof result !== "object" || result === null || Array.isArray(result))
    throw new Error(field + " must be a mapping");
  return result as Readonly<Record<string, ServingSafeValue>>;
}

export function safeServingDisplayText(value: unknown): string | null {
  try {
    return text(value, "display");
  } catch {
    return null;
  }
}

export function projectServingProfile(value: unknown, scope: ServingScope): ServingProfile {
  const s = exact(value, "serving_profile", [
    "id",
    "tenant_id",
    "workspace_id",
    "dataset_id",
    "name",
    "normalized_name",
    "status",
    "active_profile_key",
    "revision",
    "current_policy_revision_id",
    "current_snapshot_id",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
    "archived_at",
    "archived_by",
  ]);
  return {
    id: id(required(s, "id"), "id"),
    tenant_id: tenant(required(s, "tenant_id"), scope),
    workspace_id:
      required(s, "workspace_id") === null ? null : id(required(s, "workspace_id"), "workspace_id"),
    dataset_id: dataset(required(s, "dataset_id"), scope),
    name: text(required(s, "name"), "name", 128),
    normalized_name: text(required(s, "normalized_name"), "normalized_name", 128),
    status: enumValue(required(s, "status"), "status", ["draft", "active", "paused", "archived"]),
    active_profile_key:
      required(s, "active_profile_key") === null
        ? null
        : id(required(s, "active_profile_key"), "active_profile_key"),
    revision: integer(required(s, "revision"), "revision", 1),
    current_policy_revision_id:
      required(s, "current_policy_revision_id") === null
        ? null
        : id(required(s, "current_policy_revision_id"), "current_policy_revision_id"),
    current_snapshot_id:
      required(s, "current_snapshot_id") === null
        ? null
        : id(required(s, "current_snapshot_id"), "current_snapshot_id"),
    created_at: time(required(s, "created_at"), "created_at"),
    created_by: id(required(s, "created_by"), "created_by"),
    updated_at: time(required(s, "updated_at"), "updated_at"),
    updated_by: id(required(s, "updated_by"), "updated_by"),
    archived_at:
      required(s, "archived_at") === null ? null : time(required(s, "archived_at"), "archived_at"),
    archived_by:
      required(s, "archived_by") === null ? null : id(required(s, "archived_by"), "archived_by"),
  };
}
export function projectServingPolicyRevision(
  value: unknown,
  scope: ServingScope,
): ServingPolicyRevision {
  const s = exact(value, "serving_policy_revision", [
    "id",
    "tenant_id",
    "profile_id",
    "revision",
    "max_source_staleness_seconds",
    "max_parse_lag_seconds",
    "max_index_lag_seconds",
    "max_failed_document_count",
    "max_pending_index_count",
    "require_current_release",
    "require_passing_certification",
    "policy_digest",
    "created_at",
    "created_by",
  ]);
  return {
    id: id(required(s, "id"), "id"),
    tenant_id: tenant(required(s, "tenant_id"), scope),
    profile_id: id(required(s, "profile_id"), "profile_id"),
    revision: integer(required(s, "revision"), "revision", 1),
    max_source_staleness_seconds: integer(
      required(s, "max_source_staleness_seconds"),
      "max_source_staleness_seconds",
    ),
    max_parse_lag_seconds: integer(required(s, "max_parse_lag_seconds"), "max_parse_lag_seconds"),
    max_index_lag_seconds: integer(required(s, "max_index_lag_seconds"), "max_index_lag_seconds"),
    max_failed_document_count: integer(
      required(s, "max_failed_document_count"),
      "max_failed_document_count",
    ),
    max_pending_index_count: integer(
      required(s, "max_pending_index_count"),
      "max_pending_index_count",
    ),
    require_current_release: bool(
      required(s, "require_current_release"),
      "require_current_release",
    ),
    require_passing_certification: bool(
      required(s, "require_passing_certification"),
      "require_passing_certification",
    ),
    policy_digest: digest(required(s, "policy_digest"), "policy_digest"),
    created_at: time(required(s, "created_at"), "created_at"),
    created_by: id(required(s, "created_by"), "created_by"),
  };
}
export function projectServingSnapshot(value: unknown, scope: ServingScope): ServingSnapshot {
  const s = exact(value, "serving_snapshot", [
    "id",
    "tenant_id",
    "profile_id",
    "policy_revision_id",
    "observation_key",
    "state",
    "source_count",
    "ready_source_count",
    "stale_source_count",
    "active_document_count",
    "failed_document_count",
    "pending_index_count",
    "expected_serving_generation",
    "observed_serving_generation",
    "current_release_id",
    "current_certification_id",
    "stage_count",
    "ready_stage_count",
    "blocked_stage_count",
    "snapshot_digest",
    "as_of",
    "created_at",
    "created_by",
  ]);
  return {
    id: id(required(s, "id"), "id"),
    tenant_id: tenant(required(s, "tenant_id"), scope),
    profile_id: id(required(s, "profile_id"), "profile_id"),
    policy_revision_id: id(required(s, "policy_revision_id"), "policy_revision_id"),
    observation_key: id(required(s, "observation_key"), "observation_key"),
    state: enumValue(required(s, "state"), "state", SERVING_OVERALL_STATES),
    source_count: integer(required(s, "source_count"), "source_count"),
    ready_source_count: integer(required(s, "ready_source_count"), "ready_source_count"),
    stale_source_count: integer(required(s, "stale_source_count"), "stale_source_count"),
    active_document_count: integer(required(s, "active_document_count"), "active_document_count"),
    failed_document_count: integer(required(s, "failed_document_count"), "failed_document_count"),
    pending_index_count: integer(required(s, "pending_index_count"), "pending_index_count"),
    expected_serving_generation: integer(
      required(s, "expected_serving_generation"),
      "expected_serving_generation",
    ),
    observed_serving_generation: integer(
      required(s, "observed_serving_generation"),
      "observed_serving_generation",
    ),
    current_release_id:
      required(s, "current_release_id") === null
        ? null
        : id(required(s, "current_release_id"), "current_release_id"),
    current_certification_id:
      required(s, "current_certification_id") === null
        ? null
        : id(required(s, "current_certification_id"), "current_certification_id"),
    stage_count: integer(required(s, "stage_count"), "stage_count"),
    ready_stage_count: integer(required(s, "ready_stage_count"), "ready_stage_count"),
    blocked_stage_count: integer(required(s, "blocked_stage_count"), "blocked_stage_count"),
    snapshot_digest: digest(required(s, "snapshot_digest"), "snapshot_digest"),
    as_of: time(required(s, "as_of"), "as_of"),
    created_at: time(required(s, "created_at"), "created_at"),
    created_by: id(required(s, "created_by"), "created_by"),
  };
}
export function projectServingStageFact(value: unknown, scope: ServingScope): ServingStageFact {
  const s = exact(value, "serving_stage_fact", [
    "id",
    "tenant_id",
    "profile_id",
    "snapshot_id",
    "stage_code",
    "sequence",
    "state",
    "item_count",
    "ready_count",
    "warning_count",
    "pending_count",
    "error_count",
    "lag_seconds",
    "expected_revision",
    "observed_revision",
    "expected_digest",
    "observed_digest",
    "safe_error_code",
    "safe_error",
    "stage_digest",
    "observed_at",
  ]);
  const stageCode = enumValue(required(s, "stage_code"), "stage_code", SERVING_STAGE_CODES);
  const sequence = integer(required(s, "sequence"), "sequence", 1, SERVING_STAGE_CODES.length);
  if (SERVING_STAGE_CODES[sequence - 1] !== stageCode) throw new Error("stage sequence is invalid");
  const itemCount = integer(required(s, "item_count"), "item_count");
  const readyCount = integer(required(s, "ready_count"), "ready_count");
  const warningCount = integer(required(s, "warning_count"), "warning_count");
  const pendingCount = integer(required(s, "pending_count"), "pending_count");
  const errorCount = integer(required(s, "error_count"), "error_count");
  if ([readyCount, warningCount, pendingCount, errorCount].some((count) => count > itemCount))
    throw new Error("stage fact counters cannot exceed item_count");
  return {
    id: id(required(s, "id"), "id"),
    tenant_id: tenant(required(s, "tenant_id"), scope),
    profile_id: id(required(s, "profile_id"), "profile_id"),
    snapshot_id: id(required(s, "snapshot_id"), "snapshot_id"),
    stage_code: stageCode,
    sequence,
    state: enumValue(required(s, "state"), "state", SERVING_STAGE_STATES),
    item_count: itemCount,
    ready_count: readyCount,
    warning_count: warningCount,
    pending_count: pendingCount,
    error_count: errorCount,
    lag_seconds: integer(required(s, "lag_seconds"), "lag_seconds"),
    expected_revision: nullableInteger(required(s, "expected_revision"), "expected_revision", 1),
    observed_revision: nullableInteger(required(s, "observed_revision"), "observed_revision", 1),
    expected_digest:
      required(s, "expected_digest") === null
        ? null
        : digest(required(s, "expected_digest"), "expected_digest"),
    observed_digest:
      required(s, "observed_digest") === null
        ? null
        : digest(required(s, "observed_digest"), "observed_digest"),
    safe_error_code:
      required(s, "safe_error_code") === null
        ? null
        : code(required(s, "safe_error_code"), "safe_error_code"),
    safe_error: nullableText(required(s, "safe_error"), "safe_error", 512),
    stage_digest: digest(required(s, "stage_digest"), "stage_digest"),
    observed_at: time(required(s, "observed_at"), "observed_at"),
  };
}
export function projectServingEvidenceLink(
  value: unknown,
  scope: ServingScope,
): ServingEvidenceLink {
  const s = exact(value, "serving_evidence_link", [
    "id",
    "tenant_id",
    "profile_id",
    "snapshot_id",
    "stage_fact_id",
    "evidence_kind",
    "resource_id",
    "resource_revision",
    "resource_digest",
    "route_code",
    "safe_label",
    "evidence_digest",
    "created_at",
  ]);
  const evidenceKind = enumValue(
    required(s, "evidence_kind"),
    "evidence_kind",
    SERVING_EVIDENCE_KINDS,
  );
  const routeCode = enumValue(required(s, "route_code"), "route_code", SERVING_ROUTE_CODES);
  if (routeCode !== SERVING_EVIDENCE_ROUTE_CATALOG[evidenceKind].code)
    throw new Error("evidence route is not canonical for evidence kind");
  return {
    id: id(required(s, "id"), "id"),
    tenant_id: tenant(required(s, "tenant_id"), scope),
    profile_id: id(required(s, "profile_id"), "profile_id"),
    snapshot_id: id(required(s, "snapshot_id"), "snapshot_id"),
    stage_fact_id: id(required(s, "stage_fact_id"), "stage_fact_id"),
    evidence_kind: evidenceKind,
    resource_id: resourceIdentifier(required(s, "resource_id"), "resource_id"),
    resource_revision: nullableInteger(required(s, "resource_revision"), "resource_revision", 1),
    resource_digest:
      required(s, "resource_digest") === null
        ? null
        : digest(required(s, "resource_digest"), "resource_digest"),
    route_code: routeCode,
    safe_label: text(required(s, "safe_label"), "safe_label", 256),
    evidence_digest: digest(required(s, "evidence_digest"), "evidence_digest"),
    created_at: time(required(s, "created_at"), "created_at"),
  };
}
export function projectServingEvidenceRoute(
  value: unknown,
  scope: ServingScope,
): ServingEvidenceRoute {
  const link = projectServingEvidenceLink(value, scope);
  const route = SERVING_EVIDENCE_ROUTE_CATALOG[link.evidence_kind];
  return {
    code: route.code,
    path: route.path,
    parameter: route.parameter,
    href: `${route.path}?${route.parameter}=${encodeURIComponent(link.resource_id)}`,
  };
}
export function projectServingEvent(value: unknown, scope: ServingScope): ServingEvent {
  const s = exact(value, "serving_event", [
    "id",
    "tenant_id",
    "profile_id",
    "snapshot_id",
    "stream_key",
    "sequence",
    "event_type",
    "previous_event_digest",
    "event_digest",
    "actor_id",
    "request_id",
    "safe_snapshot",
    "occurred_at",
  ]);
  return {
    id: id(required(s, "id"), "id"),
    tenant_id: tenant(required(s, "tenant_id"), scope),
    profile_id: id(required(s, "profile_id"), "profile_id"),
    snapshot_id:
      required(s, "snapshot_id") === null ? null : id(required(s, "snapshot_id"), "snapshot_id"),
    stream_key: id(required(s, "stream_key"), "stream_key"),
    sequence: integer(required(s, "sequence"), "sequence", 1),
    event_type: enumValue(required(s, "event_type"), "event_type", SERVING_EVENT_TYPES),
    previous_event_digest:
      required(s, "previous_event_digest") === null
        ? null
        : digest(required(s, "previous_event_digest"), "previous_event_digest"),
    event_digest: digest(required(s, "event_digest"), "event_digest"),
    actor_id: id(required(s, "actor_id"), "actor_id"),
    request_id: id(required(s, "request_id"), "request_id"),
    safe_snapshot: safeMapping(required(s, "safe_snapshot"), "safe_snapshot"),
    occurred_at: time(required(s, "occurred_at"), "occurred_at"),
  };
}
export function deriveServingOverallState(
  stages: readonly ServingStageFact[],
): ServingOverallState {
  if (stages.length !== SERVING_STAGE_CODES.length)
    throw new Error("exactly five serving stages are required");
  const ordered = [...stages].sort((a, b) => a.sequence - b.sequence);
  ordered.forEach((stage, index) => {
    if (stage.stage_code !== SERVING_STAGE_CODES[index] || stage.sequence !== index + 1)
      throw new Error("serving stage order is invalid");
  });
  if (ordered.some((stage) => stage.state === "unavailable")) return "unavailable";
  if (ordered.some((stage) => stage.state === "blocked")) return "blocked";
  if (ordered.some((stage) => stage.state === "lagging" || stage.state === "missing"))
    return "degraded";
  return "ready";
}
export function projectServingSummary(value: unknown, scope: ServingScope): ServingSummary {
  const s = exact(value, "serving_summary", [
    "tenant_id",
    "dataset_id",
    "profile_id",
    "profile_name",
    "profile_status",
    "state",
    "as_of",
    "snapshot_id",
    "snapshot_digest",
    "policy_revision",
    "serving_generation",
    "source_count",
    "ready_source_count",
    "stale_source_count",
    "active_document_count",
    "failed_document_count",
    "pending_index_count",
    "stage_count",
    "ready_stage_count",
    "blocked_stage_count",
    "current_release_id",
    "current_certification_id",
    "stage_facts",
    "reason_code",
  ]);
  const rawStages = required(s, "stage_facts");
  if (!Array.isArray(rawStages)) throw new Error("stage_facts is invalid");
  const stageFacts = rawStages.map((item) => projectServingStageFact(item, scope));
  const state = enumValue(required(s, "state"), "state", SERVING_OVERALL_STATES);
  const profileId =
    required(s, "profile_id") === null ? null : id(required(s, "profile_id"), "profile_id");
  const reasonCode =
    required(s, "reason_code") === null ? null : code(required(s, "reason_code"), "reason_code");
  if (stageFacts.length === 0) {
    const emptySummaryHasCanonicalReason =
      (profileId === null && reasonCode === "profile_missing") ||
      (profileId !== null && reasonCode === "snapshot_missing");
    if (state !== "unavailable" || !emptySummaryHasCanonicalReason)
      throw new Error("empty serving summary has an invalid authority state");
  } else if (state !== deriveServingOverallState(stageFacts)) {
    throw new Error("derived serving state does not match authority state");
  }
  return {
    tenant_id: tenant(required(s, "tenant_id"), scope),
    dataset_id: dataset(required(s, "dataset_id"), scope),
    profile_id: profileId,
    profile_name:
      required(s, "profile_name") === null
        ? null
        : text(required(s, "profile_name"), "profile_name", 128),
    profile_status:
      required(s, "profile_status") === null
        ? null
        : enumValue(required(s, "profile_status"), "profile_status", [
            "draft",
            "active",
            "paused",
            "archived",
          ] as const),
    state,
    as_of: required(s, "as_of") === null ? null : time(required(s, "as_of"), "as_of"),
    snapshot_id:
      required(s, "snapshot_id") === null ? null : id(required(s, "snapshot_id"), "snapshot_id"),
    snapshot_digest:
      required(s, "snapshot_digest") === null
        ? null
        : digest(required(s, "snapshot_digest"), "snapshot_digest"),
    policy_revision:
      required(s, "policy_revision") === null
        ? null
        : integer(required(s, "policy_revision"), "policy_revision", 1),
    serving_generation:
      required(s, "serving_generation") === null
        ? null
        : integer(required(s, "serving_generation"), "serving_generation"),
    source_count:
      required(s, "source_count") === null
        ? null
        : integer(required(s, "source_count"), "source_count"),
    ready_source_count:
      required(s, "ready_source_count") === null
        ? null
        : integer(required(s, "ready_source_count"), "ready_source_count"),
    stale_source_count:
      required(s, "stale_source_count") === null
        ? null
        : integer(required(s, "stale_source_count"), "stale_source_count"),
    active_document_count:
      required(s, "active_document_count") === null
        ? null
        : integer(required(s, "active_document_count"), "active_document_count"),
    failed_document_count:
      required(s, "failed_document_count") === null
        ? null
        : integer(required(s, "failed_document_count"), "failed_document_count"),
    pending_index_count:
      required(s, "pending_index_count") === null
        ? null
        : integer(required(s, "pending_index_count"), "pending_index_count"),
    stage_count: integer(required(s, "stage_count"), "stage_count"),
    ready_stage_count: integer(required(s, "ready_stage_count"), "ready_stage_count"),
    blocked_stage_count: integer(required(s, "blocked_stage_count"), "blocked_stage_count"),
    current_release_id:
      required(s, "current_release_id") === null
        ? null
        : id(required(s, "current_release_id"), "current_release_id"),
    current_certification_id:
      required(s, "current_certification_id") === null
        ? null
        : id(required(s, "current_certification_id"), "current_certification_id"),
    stage_facts: stageFacts,
    reason_code: reasonCode,
  };
}
export function projectServingProfileEnvelope(
  value: unknown,
  scope: ServingScope,
): ServingProfileEnvelope {
  const s = exact(value, "serving_profile_envelope", [
    "profile",
    "current_policy",
    "current_snapshot",
  ]);
  return {
    profile: projectServingProfile(required(s, "profile"), scope),
    current_policy:
      required(s, "current_policy") === null
        ? null
        : projectServingPolicyRevision(required(s, "current_policy"), scope),
    current_snapshot:
      required(s, "current_snapshot") === null
        ? null
        : projectServingSnapshot(required(s, "current_snapshot"), scope),
  };
}
export function projectServingSnapshotDetail(
  value: unknown,
  scope: ServingScope,
): ServingSnapshotDetail {
  const s = exact(value, "serving_snapshot_detail", [
    "snapshot",
    "stage_facts",
    "evidence_links",
    "events",
  ]);
  const rawStages = required(s, "stage_facts");
  const rawEvidence = required(s, "evidence_links");
  const rawEvents = required(s, "events");
  if (!Array.isArray(rawStages) || !Array.isArray(rawEvidence) || !Array.isArray(rawEvents))
    throw new Error("serving snapshot detail collections are invalid");
  return {
    snapshot: projectServingSnapshot(required(s, "snapshot"), scope),
    stage_facts: rawStages.map((item) => projectServingStageFact(item, scope)),
    evidence_links: rawEvidence.map((item) => projectServingEvidenceLink(item, scope)),
    events: projectServingEventPage(
      { items: rawEvents, count: rawEvents.length, next_cursor: null, invalid_item_count: 0 },
      scope,
    ).items,
  };
}
export function projectServingPage<T>(
  value: unknown,
  scope: ServingScope,
  project: (item: unknown, scope: ServingScope) => T,
): ServingPage<T> {
  const s = exact(value, "serving_page", ["items", "count", "next_cursor", "invalid_item_count"]);
  if (!Array.isArray(s.items)) throw new Error("serving_page.items is invalid");
  return {
    items: s.items.map((item) => project(item, scope)),
    count: s.count === null ? null : integer(s.count, "count"),
    next_cursor: s.next_cursor === null ? null : text(s.next_cursor, "next_cursor", 2_048),
    invalid_item_count: integer(s.invalid_item_count, "invalid_item_count"),
  };
}
export function projectServingEventPage(value: unknown, scope: ServingScope): ServingEventPage {
  const page = projectServingPage(value, scope, projectServingEvent);
  const streams = new Map<string, ServingEvent[]>();
  for (const event of page.items)
    streams.set(event.stream_key, [...(streams.get(event.stream_key) ?? []), event]);
  for (const events of streams.values()) {
    const ordered = [...events].sort((a, b) => a.sequence - b.sequence);
    for (let index = 0; index < ordered.length; index += 1) {
      const current = ordered[index]!;
      if (current.sequence === 1 && current.previous_event_digest !== null)
        throw new Error("event chain root is invalid");
      if (current.sequence > 1 && current.previous_event_digest === null)
        throw new Error("event chain predecessor is missing");
      const previous = ordered[index - 1];
      if (
        previous &&
        current.sequence === previous.sequence + 1 &&
        current.previous_event_digest !== previous.event_digest
      )
        throw new Error("event chain predecessor is invalid");
    }
  }
  return page;
}
export function projectServingPreview(value: unknown, scope: ServingScope): ServingPreview {
  const s = exact(value, "serving_preview", [
    "preview",
    "state",
    "policy_revision",
    "stage_facts",
    "blockers",
  ]);
  if (s.preview !== true || !Array.isArray(s.stage_facts) || !Array.isArray(s.blockers))
    throw new Error("serving preview is invalid");
  const stageFacts = s.stage_facts.map((item) => projectServingStageFact(item, scope));
  const state = enumValue(s.state, "state", SERVING_OVERALL_STATES);
  if (state !== deriveServingOverallState(stageFacts))
    throw new Error("preview derived state does not match authority state");
  const blockers = s.blockers.map((item, index) => {
    const blocker = exact(item, "blockers[" + index + "]", ["code", "stage_code", "safe_message"]);
    return {
      code: code(required(blocker, "code"), "blockers[" + index + "].code"),
      stage_code:
        required(blocker, "stage_code") === null
          ? null
          : enumValue(
              required(blocker, "stage_code"),
              "blockers[" + index + "].stage_code",
              SERVING_STAGE_CODES,
            ),
      safe_message: text(
        required(blocker, "safe_message"),
        "blockers[" + index + "].safe_message",
        512,
      ),
    };
  });
  return {
    preview: true,
    state,
    policy_revision: integer(s.policy_revision, "policy_revision", 1),
    stage_facts: stageFacts,
    blockers,
  };
}
export function projectServingMutationOutcome(value: unknown): ServingMutationOutcome {
  const s = exact(value, "serving_mutation_outcome", [
    "state",
    "operation",
    "resource_id",
    "revision",
    "message",
    "retryable",
  ]);
  return {
    state: enumValue(s.state, "state", [
      "applied",
      "replayed",
      "conflict",
      "blocked",
      "rejected",
      "unavailable",
    ]),
    operation: code(s.operation, "operation"),
    resource_id: s.resource_id === null ? null : id(s.resource_id, "resource_id"),
    revision: s.revision === null ? null : integer(s.revision, "revision", 1),
    message: s.message === null ? null : safeServingDisplayText(s.message),
    retryable: bool(s.retryable, "retryable"),
  };
}
export function projectServingHandoff(value: unknown, scope: ServingScope): ServingHandoff {
  const s = exact(value, "serving_handoff", [
    "evidence_kind",
    "route_code",
    "dataset_id",
    "resource_id",
  ]);
  const evidenceKind = enumValue(
    required(s, "evidence_kind"),
    "evidence_kind",
    SERVING_EVIDENCE_KINDS,
  );
  const routeCode = enumValue(required(s, "route_code"), "route_code", SERVING_ROUTE_CODES);
  if (routeCode !== SERVING_EVIDENCE_ROUTE_CATALOG[evidenceKind].code)
    throw new Error("handoff route is not canonical for evidence kind");
  return {
    evidence_kind: evidenceKind,
    route_code: routeCode,
    dataset_id: dataset(required(s, "dataset_id"), scope),
    resource_id:
      required(s, "resource_id") === null
        ? null
        : resourceIdentifier(required(s, "resource_id"), "resource_id"),
  };
}
