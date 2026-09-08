export type OperationsSeverity = "healthy" | "warning" | "critical" | "unavailable";
export type OperationsHorizonBand =
  "expired" | "24_hours" | "7_days" | "30_days" | "healthy" | "unavailable";
export type OperationsReleaseRole = "active" | "pinned";
export type OperationsGateState = "passed" | "waived" | "not_required" | "blocked" | "unavailable";
export type OperationsAlertStatus = "open" | "acknowledged" | "resolved" | "suppressed";
export type OperationsAlertType =
  | "certification_expiring"
  | "certification_expired"
  | "certification_stale"
  | "waiver_expiring"
  | "waiver_expired"
  | "quality_gate_blocked"
  | "quality_authority_unavailable";
export type RecertificationJobStatus =
  | "pending"
  | "claimed"
  | "awaiting_evidence"
  | "ready_to_certify"
  | "completed"
  | "failed"
  | "cancelled";
export type RecertificationTrigger =
  | "manual"
  | "certification_warning"
  | "certification_expired"
  | "stale_evidence"
  | "alert_escalation";

export interface OperationsScope {
  tenantId: string;
  datasetId: string;
}

export interface QualityAuthorityProjection {
  state: "ready" | "unavailable";
  unavailable_reason: string | null;
  tenant_id: string;
  dataset_id: string;
  release_id: string;
  channel_id: string;
  channel_name: string;
  release_role: OperationsReleaseRole;
  release_number: number | null;
  gate_state: OperationsGateState;
  gate_reason: string;
  severity: OperationsSeverity;
  horizon_band: OperationsHorizonBand;
  minutes_to_certification_expiry: number | null;
  minutes_to_waiver_expiry: number | null;
  certification_id: string | null;
  certification_valid_until: string | null;
  waiver_id: string | null;
  waiver_expires_at: string | null;
  active_alert_count: number;
  recertification_job_status: RecertificationJobStatus | null;
  last_observed_at: string | null;
  observation_digest: string | null;
}

export interface HorizonCounts {
  expired: number;
  "24_hours": number;
  "7_days": number;
  "30_days": number;
  healthy: number;
  unavailable: number;
}

export interface QualityOperationsSummary {
  state: "ready" | "unavailable";
  tenant_id: string;
  dataset_id: string;
  generated_at: string | null;
  last_completed_scan_at: string | null;
  horizon_counts: HorizonCounts;
  items: QualityAuthorityProjection[];
}

export interface QualityOperationsAlert {
  id: string;
  tenant_id: string;
  dataset_id: string;
  release_id: string;
  channel_id: string;
  release_role: OperationsReleaseRole;
  alert_type: OperationsAlertType;
  severity: "warning" | "critical";
  status: OperationsAlertStatus;
  revision: number;
  occurrence_count: number;
  opened_at: string;
  last_observed_at: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  acknowledged_comment: string | null;
  resolved_at: string | null;
  suppressed_until: string | null;
}

export interface RecertificationJob {
  id: string;
  tenant_id: string;
  dataset_id: string;
  release_id: string;
  channel_id: string;
  release_role: OperationsReleaseRole;
  trigger: RecertificationTrigger;
  status: RecertificationJobStatus;
  cycle_key: string;
  attempt_count: number;
  max_attempts: number;
  next_attempt_at: string | null;
  created_at: string;
  updated_at: string;
  safe_error_code: string | null;
  safe_error: string | null;
}

const DIGEST = /^[0-9a-f]{64}$/;
const SECRET =
  /(?:token\s*[:=]|password\s*[:=]|api[_-]?key\s*[:=]|apikey\s*[:=]|authorization\s*[:=]|credential\s*[:=]|idempotency[_-]?key\s*[:=]|ticket\s*[:=]|client[_-]?secret\s*[:=]|bearer\s+|opaque-secret|opaque-ticket|raw-ticket|secret:\/\/|sk_(?:live|test)|(?:mysql|mariadb|postgres(?:ql)?|redis(?:s)?):\/\/)/i;
const ROLES = new Set<OperationsReleaseRole>(["active", "pinned"]);
const SEVERITIES = new Set<OperationsSeverity>(["healthy", "warning", "critical", "unavailable"]);
const HORIZONS = new Set<OperationsHorizonBand>([
  "expired",
  "24_hours",
  "7_days",
  "30_days",
  "healthy",
  "unavailable",
]);
const GATES = new Set<OperationsGateState>([
  "passed",
  "waived",
  "not_required",
  "blocked",
  "unavailable",
]);
const ALERT_TYPES = new Set<OperationsAlertType>([
  "certification_expiring",
  "certification_expired",
  "certification_stale",
  "waiver_expiring",
  "waiver_expired",
  "quality_gate_blocked",
  "quality_authority_unavailable",
]);
const ALERT_STATUSES = new Set<OperationsAlertStatus>([
  "open",
  "acknowledged",
  "resolved",
  "suppressed",
]);
const JOB_STATUSES = new Set<RecertificationJobStatus>([
  "pending",
  "claimed",
  "awaiting_evidence",
  "ready_to_certify",
  "completed",
  "failed",
  "cancelled",
]);
const JOB_TRIGGERS = new Set<RecertificationTrigger>([
  "manual",
  "certification_warning",
  "certification_expired",
  "stale_evidence",
  "alert_escalation",
]);

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown, field: string, maximum = 512): string {
  if (typeof value !== "string") throw new Error(`${field} must be a string`);
  const normalized = value.trim();
  if (!normalized || normalized.length > maximum || /[\r\n\0]/.test(normalized)) {
    throw new Error(`${field} is invalid`);
  }
  if (SECRET.test(normalized)) throw new Error(`${field} contains protected information`);
  return normalized;
}

function nullableText(value: unknown, field: string, maximum = 512): string | null {
  if (value === null || value === undefined) return null;
  return text(value, field, maximum);
}

function exactInteger(value: unknown, field: string, minimum?: number): number {
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new Error(`${field} must be an exact integer`);
  }
  if (minimum !== undefined && value < minimum) throw new Error(`${field} is invalid`);
  return value;
}

function nullableInteger(value: unknown, field: string): number | null {
  if (value === null || value === undefined) return null;
  return exactInteger(value, field);
}

function timestamp(value: unknown, field: string, required = true): string | null {
  if ((value === null || value === undefined) && !required) return null;
  const normalized = text(value, field, 64);
  const parsed = Date.parse(normalized);
  if (!Number.isFinite(parsed)) throw new Error(`${field} must be an ISO timestamp`);
  return new Date(parsed).toISOString().replace(".000Z", ".000Z");
}

function digest(value: unknown, field: string, required = true): string | null {
  if ((value === null || value === undefined) && !required) return null;
  const normalized = text(value, field, 64).toLowerCase();
  if (!DIGEST.test(normalized)) throw new Error(`${field} must be a sha256 digest`);
  return normalized;
}

function enumValue<T extends string>(value: unknown, field: string, values: Set<T>): T {
  const normalized = text(value, field, 64) as T;
  if (!values.has(normalized)) throw new Error(`${field} is invalid`);
  return normalized;
}

function unavailableAuthority(
  value: Record<string, unknown> | null,
  scope: OperationsScope,
  reason: string,
): QualityAuthorityProjection {
  const safe = (candidate: unknown, fallback: string): string => {
    try {
      return text(candidate, "authority identity", 128);
    } catch {
      return fallback;
    }
  };
  return {
    state: "unavailable",
    unavailable_reason: reason,
    tenant_id: scope.tenantId,
    dataset_id: scope.datasetId,
    release_id: safe(value?.release_id, "unavailable-release"),
    channel_id: safe(value?.channel_id, "unavailable-channel"),
    channel_name: safe(value?.channel_name, "不可用 Channel"),
    release_role: value?.release_role === "pinned" ? "pinned" : "active",
    release_number: null,
    gate_state: "unavailable",
    gate_reason: "quality_authority_unavailable",
    severity: "unavailable",
    horizon_band: "unavailable",
    minutes_to_certification_expiry: null,
    minutes_to_waiver_expiry: null,
    certification_id: null,
    certification_valid_until: null,
    waiver_id: null,
    waiver_expires_at: null,
    active_alert_count: 0,
    recertification_job_status: null,
    last_observed_at: null,
    observation_digest: null,
  };
}

export function sanitizeOperationsMessage(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const normalized = value.trim();
  if (!normalized || normalized.length > 512 || /[\r\n\0]/.test(normalized)) return fallback;
  return SECRET.test(normalized) ? "受保护信息已隐藏" : normalized;
}

export function projectQualityAuthority(
  value: unknown,
  scope: OperationsScope,
): QualityAuthorityProjection {
  const item = record(value);
  if (!item) return unavailableAuthority(null, scope, "authority payload is unavailable");
  try {
    const tenantId = text(item.tenant_id, "tenant_id", 64);
    const datasetId = text(item.dataset_id, "dataset_id", 64);
    if (tenantId !== scope.tenantId || datasetId !== scope.datasetId) {
      return unavailableAuthority(item, scope, "authority scope mismatch");
    }
    const severity = enumValue(item.severity, "severity", SEVERITIES);
    const horizon = enumValue(item.horizon_band, "horizon_band", HORIZONS);
    const gateState = enumValue(item.gate_state, "gate_state", GATES);
    if (severity === "unavailable" || horizon === "unavailable" || gateState === "unavailable") {
      return unavailableAuthority(item, scope, "quality authority declared unavailable");
    }
    const jobStatus =
      item.recertification_job_status === null || item.recertification_job_status === undefined
        ? null
        : enumValue(item.recertification_job_status, "recertification_job_status", JOB_STATUSES);
    return {
      state: "ready",
      unavailable_reason: null,
      tenant_id: tenantId,
      dataset_id: datasetId,
      release_id: text(item.release_id, "release_id", 64),
      channel_id: text(item.channel_id, "channel_id", 128),
      channel_name: text(item.channel_name, "channel_name", 128),
      release_role: enumValue(item.release_role, "release_role", ROLES),
      release_number: nullableInteger(item.release_number, "release_number"),
      gate_state: gateState,
      gate_reason: text(item.gate_reason, "gate_reason", 128),
      severity,
      horizon_band: horizon,
      minutes_to_certification_expiry: nullableInteger(
        item.minutes_to_certification_expiry,
        "minutes_to_certification_expiry",
      ),
      minutes_to_waiver_expiry: nullableInteger(
        item.minutes_to_waiver_expiry,
        "minutes_to_waiver_expiry",
      ),
      certification_id: nullableText(item.certification_id, "certification_id", 64),
      certification_valid_until: timestamp(
        item.certification_valid_until,
        "certification_valid_until",
        false,
      ),
      waiver_id: nullableText(item.waiver_id, "waiver_id", 64),
      waiver_expires_at: timestamp(item.waiver_expires_at, "waiver_expires_at", false),
      active_alert_count: exactInteger(item.active_alert_count, "active_alert_count", 0),
      recertification_job_status: jobStatus,
      last_observed_at: timestamp(item.last_observed_at, "last_observed_at", false),
      observation_digest: digest(item.observation_digest, "observation_digest", false),
    };
  } catch (error) {
    return unavailableAuthority(
      item,
      scope,
      error instanceof Error
        ? sanitizeOperationsMessage(error.message, "authority malformed")
        : "authority malformed",
    );
  }
}

function projectHorizonCounts(value: unknown): HorizonCounts {
  const counts = record(value);
  if (!counts) throw new Error("horizon_counts is unavailable");
  return {
    expired: exactInteger(counts.expired, "horizon_counts.expired", 0),
    "24_hours": exactInteger(counts["24_hours"], "horizon_counts.24_hours", 0),
    "7_days": exactInteger(counts["7_days"], "horizon_counts.7_days", 0),
    "30_days": exactInteger(counts["30_days"], "horizon_counts.30_days", 0),
    healthy: exactInteger(counts.healthy, "horizon_counts.healthy", 0),
    unavailable: exactInteger(counts.unavailable, "horizon_counts.unavailable", 0),
  };
}

export function projectQualityOperationsSummary(
  value: unknown,
  scope: OperationsScope,
): QualityOperationsSummary {
  const source = record(value);
  const items = Array.isArray(source?.authorities)
    ? source.authorities.map((item) => projectQualityAuthority(item, scope))
    : [];
  try {
    if (!source) throw new Error("summary payload is unavailable");
    const tenantId = text(source.tenant_id, "tenant_id", 64);
    const datasetId = text(source.dataset_id, "dataset_id", 64);
    if (tenantId !== scope.tenantId || datasetId !== scope.datasetId) {
      throw new Error("summary scope mismatch");
    }
    const counts = projectHorizonCounts(source.horizon_counts);
    const unavailable = items.some((item) => item.state === "unavailable");
    return {
      state: unavailable ? "unavailable" : "ready",
      tenant_id: tenantId,
      dataset_id: datasetId,
      generated_at: timestamp(source.generated_at, "generated_at", false),
      last_completed_scan_at: timestamp(
        source.last_completed_scan_at,
        "last_completed_scan_at",
        false,
      ),
      horizon_counts: counts,
      items,
    };
  } catch {
    return {
      state: "unavailable",
      tenant_id: scope.tenantId,
      dataset_id: scope.datasetId,
      generated_at: null,
      last_completed_scan_at: null,
      horizon_counts: {
        expired: 0,
        "24_hours": 0,
        "7_days": 0,
        "30_days": 0,
        healthy: 0,
        unavailable: Math.max(1, items.length),
      },
      items: items.length ? items : [unavailableAuthority(null, scope, "summary unavailable")],
    };
  }
}

export function projectQualityAlert(
  value: unknown,
  scope: OperationsScope,
): QualityOperationsAlert {
  const item = record(value);
  if (!item) throw new Error("alert payload is unavailable");
  const tenantId = text(item.tenant_id, "tenant_id", 64);
  const datasetId = text(item.dataset_id, "dataset_id", 64);
  if (tenantId !== scope.tenantId || datasetId !== scope.datasetId) {
    throw new Error("alert scope mismatch");
  }
  const severity = enumValue(item.severity, "severity", new Set(["warning", "critical"]));
  return {
    id: text(item.id, "id", 64),
    tenant_id: tenantId,
    dataset_id: datasetId,
    release_id: text(item.release_id, "release_id", 64),
    channel_id: text(item.channel_id, "channel_id", 128),
    release_role: enumValue(item.release_role, "release_role", ROLES),
    alert_type: enumValue(item.alert_type, "alert_type", ALERT_TYPES),
    severity,
    status: enumValue(item.status, "status", ALERT_STATUSES),
    revision: exactInteger(item.revision, "revision", 1),
    occurrence_count: exactInteger(item.occurrence_count, "occurrence_count", 1),
    opened_at: timestamp(item.opened_at, "opened_at")!,
    last_observed_at: timestamp(item.last_observed_at, "last_observed_at")!,
    acknowledged_at: timestamp(item.acknowledged_at, "acknowledged_at", false),
    acknowledged_by: nullableText(item.acknowledged_by, "acknowledged_by", 64),
    acknowledged_comment:
      item.acknowledged_comment === null || item.acknowledged_comment === undefined
        ? null
        : sanitizeOperationsMessage(item.acknowledged_comment, "受保护信息已隐藏"),
    resolved_at: timestamp(item.resolved_at, "resolved_at", false),
    suppressed_until: timestamp(item.suppressed_until, "suppressed_until", false),
  };
}

export function projectRecertificationJob(
  value: unknown,
  scope: OperationsScope,
): RecertificationJob {
  const item = record(value);
  if (!item) throw new Error("job payload is unavailable");
  const tenantId = text(item.tenant_id, "tenant_id", 64);
  const datasetId = text(item.dataset_id, "dataset_id", 64);
  if (tenantId !== scope.tenantId || datasetId !== scope.datasetId) {
    throw new Error("job scope mismatch");
  }
  return {
    id: text(item.id, "id", 64),
    tenant_id: tenantId,
    dataset_id: datasetId,
    release_id: text(item.release_id, "release_id", 64),
    channel_id: text(item.channel_id, "channel_id", 128),
    release_role: enumValue(item.release_role, "release_role", ROLES),
    trigger: enumValue(item.trigger, "trigger", JOB_TRIGGERS),
    status: enumValue(item.status, "status", JOB_STATUSES),
    cycle_key: digest(item.cycle_key, "cycle_key")!,
    attempt_count: exactInteger(item.attempt_count, "attempt_count", 0),
    max_attempts: exactInteger(item.max_attempts, "max_attempts", 1),
    next_attempt_at: timestamp(item.next_attempt_at, "next_attempt_at", false),
    created_at: timestamp(item.created_at, "created_at")!,
    updated_at: timestamp(item.updated_at, "updated_at")!,
    safe_error_code: nullableText(item.safe_error_code, "safe_error_code", 64),
    safe_error:
      item.safe_error === null || item.safe_error === undefined
        ? null
        : sanitizeOperationsMessage(item.safe_error, "Unavailable"),
  };
}
