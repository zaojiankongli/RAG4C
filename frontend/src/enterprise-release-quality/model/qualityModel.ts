export type QualityPolicyScope = "global" | "risk_tier" | "channel";
export type QualityPolicyStatus = "active" | "disabled";
export type QualityCertificationStatus = "passed" | "failed";
export type QualityGateState = "not_required" | "passed" | "blocked" | "waived" | "unavailable";
export type QualityMutationState = "applied" | "unchanged" | "approval_required" | "unavailable";

export interface QualityPolicy {
  id: string;
  tenant_id: string;
  name: string;
  scope_type: QualityPolicyScope;
  scope_value: string;
  channel_id: string | null;
  status: QualityPolicyStatus;
  revision: number;
  min_experiment_count: number;
  min_judged_result_count: number;
  min_judgment_coverage_bps: number;
  min_exact_agreement_bps: number;
  min_mean_score_milli: number;
  max_conflicting_results: number;
  require_all_experiments_completed: boolean;
  require_no_degraded_results: boolean;
  max_certification_age_minutes: number;
  policy_digest: string;
  created_at: string | null;
  created_by: string | null;
  updated_at: string | null;
  updated_by: string | null;
}

export interface QualityBaselineItem {
  id: string;
  ordinal: number;
  experiment_id: string;
  experiment_sequence: number;
  query_hash: string;
  experiment_serving_generation: number;
  strategy_digest: string;
  result_digest: string;
  evidence_digest: string;
  judgment_digest: string;
  created_at: string | null;
}

export interface QualityBaseline {
  id: string;
  tenant_id: string;
  dataset_id: string;
  name: string;
  baseline_revision: number;
  parent_baseline_id: string | null;
  experiment_count: number;
  query_count: number;
  baseline_digest: string;
  created_at: string | null;
  created_by: string | null;
  reason: string | null;
  request_id: string | null;
  items: QualityBaselineItem[];
}

export interface QualityCertification {
  id: string;
  tenant_id: string;
  dataset_id: string;
  release_id: string;
  baseline_id: string;
  policy_id: string;
  policy_revision: number;
  release_manifest_digest: string;
  release_mutation_generation: number;
  release_serving_generation: number;
  status: QualityCertificationStatus;
  experiment_count: number;
  completed_experiment_count: number;
  degraded_experiment_count: number;
  query_count: number;
  judged_result_count: number;
  total_result_count: number;
  judgment_count: number;
  judgment_coverage_bps: number | null;
  multi_judged_results: number;
  unanimous_results: number;
  conflicting_results: number;
  exact_agreement_bps: number | null;
  mean_score_milli: number | null;
  failed_rule_count: number;
  evidence_digest: string;
  certification_digest: string;
  valid_until: string | null;
  created_at: string | null;
  created_by: string | null;
  reason: string | null;
}

export interface QualityWaiver {
  id: string;
  tenant_id: string;
  dataset_id: string;
  release_id: string;
  channel_id: string;
  policy_id: string;
  policy_revision: number;
  release_manifest_digest: string;
  approval_request_id: string;
  approval_execution_id: string;
  reason: string | null;
  valid_from: string | null;
  expires_at: string | null;
  waiver_digest: string;
  created_at: string | null;
  created_by: string | null;
  request_id: string | null;
}

export interface QualityGate {
  tenant_id: string | null;
  dataset_id: string | null;
  release_id: string | null;
  channel_id: string | null;
  release_manifest_digest: string | null;
  channel_revision: number | null;
  risk_tier: "low" | "medium" | "high" | null;
  is_default_serving: boolean | null;
  state: QualityGateState;
  reason: string;
  policy: QualityPolicy | null;
  certification: QualityCertification | null;
  waiver: QualityWaiver | null;
}

export interface QualityPage<T> {
  items: T[];
  next_cursor: string | null;
  invalid_item_count: number;
}

export type QualityPolicyPage = QualityPage<QualityPolicy>;
export type QualityBaselinePage = QualityPage<QualityBaseline>;
export type QualityCertificationPage = QualityPage<QualityCertification>;

export interface QualityMutationOutcome {
  state: QualityMutationState;
  operation: string;
  resource_id: string | null;
  approval_request_id?: string | null;
  message: string | null;
  retryable: boolean;
}

function object(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

const SECRET =
  /(?:token\s*[:=]|password\s*[:=]|api[_-]?key\s*[:=]|apikey\s*[:=]|authorization\s*[:=]|credential\s*[:=]|idempotency[_-]?key\s*[:=]|ticket\s*[:=]|client[_-]?secret\s*[:=]|bearer\s+|opaque-ticket|raw-ticket|secret:\/\/|sk_(?:live|test)|(?:mysql|postgres(?:ql)?|redis(?:s)?):\/\/)/i;
const DIGEST = /^[0-9a-f]{64}$/;

function safeText(value: unknown, maximum = 512): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim();
  if (!normalized || normalized.length > maximum || /[\r\n\0]/.test(normalized)) return null;
  return SECRET.test(normalized) ? null : normalized;
}

function nullableText(value: unknown, maximum = 512): string | null {
  return value === null || value === undefined ? null : safeText(value, maximum);
}

function exactInteger(value: unknown, minimum = 0, maximum?: number): number | null {
  return typeof value === "number" &&
    Number.isInteger(value) &&
    value >= minimum &&
    (maximum === undefined || value <= maximum)
    ? value
    : null;
}

function nullableInteger(value: unknown, minimum = 0, maximum?: number): number | null {
  return value === null || value === undefined ? null : exactInteger(value, minimum, maximum);
}

function exactBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function digest(value: unknown): string | null {
  const normalized = safeText(value, 64)?.toLowerCase() ?? null;
  return normalized && DIGEST.test(normalized) ? normalized : null;
}

function dateText(value: unknown): string | null {
  return nullableText(value, 64);
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T | null {
  return typeof value === "string" && allowed.includes(value as T) ? (value as T) : null;
}

export function projectQualityPolicy(value: unknown): QualityPolicy | null {
  const row = object(value);
  if (!row) return null;
  const scope = oneOf(row.scope_type, ["global", "risk_tier", "channel"] as const);
  const status = oneOf(row.status, ["active", "disabled"] as const);
  const projected = {
    id: safeText(row.id, 64),
    tenant_id: safeText(row.tenant_id, 64),
    name: safeText(row.name, 128),
    scope_type: scope,
    scope_value: safeText(row.scope_value, 128),
    channel_id: nullableText(row.channel_id, 128),
    status,
    revision: exactInteger(row.revision, 1),
    min_experiment_count: exactInteger(row.min_experiment_count, 1),
    min_judged_result_count: exactInteger(row.min_judged_result_count, 0),
    min_judgment_coverage_bps: exactInteger(row.min_judgment_coverage_bps, 0, 10_000),
    min_exact_agreement_bps: exactInteger(row.min_exact_agreement_bps, 0, 10_000),
    min_mean_score_milli: exactInteger(row.min_mean_score_milli, 0, 3_000),
    max_conflicting_results: exactInteger(row.max_conflicting_results, 0),
    require_all_experiments_completed: exactBoolean(row.require_all_experiments_completed),
    require_no_degraded_results: exactBoolean(row.require_no_degraded_results),
    max_certification_age_minutes: exactInteger(row.max_certification_age_minutes, 1),
    policy_digest: digest(row.policy_digest),
    created_at: dateText(row.created_at),
    created_by: nullableText(row.created_by, 64),
    updated_at: dateText(row.updated_at),
    updated_by: nullableText(row.updated_by, 64),
  };
  if (
    Object.values(projected).some((item) => item === null) &&
    ![
      projected.channel_id,
      projected.created_at,
      projected.created_by,
      projected.updated_at,
      projected.updated_by,
    ].includes(null)
  ) {
    return null;
  }
  if (
    !projected.id ||
    !projected.tenant_id ||
    !projected.name ||
    !projected.scope_type ||
    !projected.scope_value ||
    !projected.status ||
    projected.revision === null ||
    projected.min_experiment_count === null ||
    projected.min_judged_result_count === null ||
    projected.min_judgment_coverage_bps === null ||
    projected.min_exact_agreement_bps === null ||
    projected.min_mean_score_milli === null ||
    projected.max_conflicting_results === null ||
    projected.require_all_experiments_completed === null ||
    projected.require_no_degraded_results === null ||
    projected.max_certification_age_minutes === null ||
    !projected.policy_digest
  )
    return null;
  if (
    projected.scope_type === "global" &&
    (projected.scope_value !== "*" || projected.channel_id !== null)
  )
    return null;
  if (
    projected.scope_type === "risk_tier" &&
    (!["low", "medium", "high"].includes(projected.scope_value) || projected.channel_id !== null)
  )
    return null;
  if (projected.scope_type === "channel" && projected.channel_id !== projected.scope_value)
    return null;
  return projected as QualityPolicy;
}

function projectBaselineItem(value: unknown): QualityBaselineItem | null {
  const row = object(value);
  if (!row) return null;
  const item: QualityBaselineItem = {
    id: safeText(row.id, 64) ?? "",
    ordinal: exactInteger(row.ordinal, 1) ?? 0,
    experiment_id: safeText(row.experiment_id, 64) ?? "",
    experiment_sequence: exactInteger(row.experiment_sequence, 1) ?? 0,
    query_hash: digest(row.query_hash) ?? "",
    experiment_serving_generation: exactInteger(row.experiment_serving_generation, 0) ?? -1,
    strategy_digest: digest(row.strategy_digest) ?? "",
    result_digest: digest(row.result_digest) ?? "",
    evidence_digest: digest(row.evidence_digest) ?? "",
    judgment_digest: digest(row.judgment_digest) ?? "",
    created_at: dateText(row.created_at),
  };
  return item.id &&
    item.ordinal > 0 &&
    item.experiment_id &&
    item.experiment_sequence > 0 &&
    item.query_hash &&
    item.experiment_serving_generation >= 0 &&
    item.strategy_digest &&
    item.result_digest &&
    item.evidence_digest &&
    item.judgment_digest
    ? item
    : null;
}

export function projectQualityBaseline(value: unknown): QualityBaseline | null {
  const row = object(value);
  if (!row) return null;
  const rawItems = Array.isArray(row.items) ? row.items : [];
  const items = rawItems
    .map(projectBaselineItem)
    .filter((item): item is QualityBaselineItem => item !== null);
  if (items.length !== rawItems.length) return null;
  const baseline: QualityBaseline = {
    id: safeText(row.id, 64) ?? "",
    tenant_id: safeText(row.tenant_id, 64) ?? "",
    dataset_id: safeText(row.dataset_id, 64) ?? "",
    name: safeText(row.name, 128) ?? "",
    baseline_revision: exactInteger(row.baseline_revision, 1) ?? 0,
    parent_baseline_id: nullableText(row.parent_baseline_id, 64),
    experiment_count: exactInteger(row.experiment_count, 1) ?? 0,
    query_count: exactInteger(row.query_count, 1) ?? 0,
    baseline_digest: digest(row.baseline_digest) ?? "",
    created_at: dateText(row.created_at),
    created_by: nullableText(row.created_by, 64),
    reason: nullableText(row.reason, 512),
    request_id: nullableText(row.request_id, 128),
    items,
  };
  return baseline.id &&
    baseline.tenant_id &&
    baseline.dataset_id &&
    baseline.name &&
    baseline.baseline_revision > 0 &&
    baseline.experiment_count > 0 &&
    baseline.query_count > 0 &&
    baseline.baseline_digest
    ? baseline
    : null;
}

export function projectQualityCertification(value: unknown): QualityCertification | null {
  const row = object(value);
  if (!row) return null;
  const status = oneOf(row.status, ["passed", "failed"] as const);
  const certification: QualityCertification = {
    id: safeText(row.id, 64) ?? "",
    tenant_id: safeText(row.tenant_id, 64) ?? "",
    dataset_id: safeText(row.dataset_id, 64) ?? "",
    release_id: safeText(row.release_id, 64) ?? "",
    baseline_id: safeText(row.baseline_id, 64) ?? "",
    policy_id: safeText(row.policy_id, 64) ?? "",
    policy_revision: exactInteger(row.policy_revision, 1) ?? 0,
    release_manifest_digest: digest(row.release_manifest_digest) ?? "",
    release_mutation_generation: exactInteger(row.release_mutation_generation, 0) ?? -1,
    release_serving_generation: exactInteger(row.release_serving_generation, 0) ?? -1,
    status: status ?? "failed",
    experiment_count: exactInteger(row.experiment_count, 1) ?? 0,
    completed_experiment_count: exactInteger(row.completed_experiment_count, 0) ?? -1,
    degraded_experiment_count: exactInteger(row.degraded_experiment_count, 0) ?? -1,
    query_count: exactInteger(row.query_count, 1) ?? 0,
    judged_result_count: exactInteger(row.judged_result_count, 0) ?? -1,
    total_result_count: exactInteger(row.total_result_count, 0) ?? -1,
    judgment_count: exactInteger(row.judgment_count, 0) ?? -1,
    judgment_coverage_bps: nullableInteger(row.judgment_coverage_bps, 0, 10_000),
    multi_judged_results: exactInteger(row.multi_judged_results, 0) ?? -1,
    unanimous_results: exactInteger(row.unanimous_results, 0) ?? -1,
    conflicting_results: exactInteger(row.conflicting_results, 0) ?? -1,
    exact_agreement_bps: nullableInteger(row.exact_agreement_bps, 0, 10_000),
    mean_score_milli: nullableInteger(row.mean_score_milli, 0, 3_000),
    failed_rule_count: exactInteger(row.failed_rule_count, 0) ?? -1,
    evidence_digest: digest(row.evidence_digest) ?? "",
    certification_digest: digest(row.certification_digest) ?? "",
    valid_until: dateText(row.valid_until),
    created_at: dateText(row.created_at),
    created_by: nullableText(row.created_by, 64),
    reason: nullableText(row.reason, 512),
  };
  return certification.id &&
    certification.tenant_id &&
    certification.dataset_id &&
    certification.release_id &&
    certification.baseline_id &&
    certification.policy_id &&
    certification.policy_revision > 0 &&
    certification.release_manifest_digest &&
    certification.release_mutation_generation >= 0 &&
    certification.release_serving_generation >= 0 &&
    status &&
    certification.experiment_count > 0 &&
    certification.query_count > 0 &&
    certification.completed_experiment_count >= 0 &&
    certification.degraded_experiment_count >= 0 &&
    certification.judged_result_count >= 0 &&
    certification.total_result_count >= 0 &&
    certification.judgment_count >= 0 &&
    certification.multi_judged_results >= 0 &&
    certification.unanimous_results >= 0 &&
    certification.conflicting_results >= 0 &&
    certification.failed_rule_count >= 0 &&
    certification.evidence_digest &&
    certification.certification_digest
    ? certification
    : null;
}

function projectWaiver(value: unknown): QualityWaiver | null {
  const row = object(value);
  if (!row) return null;
  const waiver: QualityWaiver = {
    id: safeText(row.id, 64) ?? "",
    tenant_id: safeText(row.tenant_id, 64) ?? "",
    dataset_id: safeText(row.dataset_id, 64) ?? "",
    release_id: safeText(row.release_id, 64) ?? "",
    channel_id: safeText(row.channel_id, 128) ?? "",
    policy_id: safeText(row.policy_id, 64) ?? "",
    policy_revision: exactInteger(row.policy_revision, 1) ?? 0,
    release_manifest_digest: digest(row.release_manifest_digest) ?? "",
    approval_request_id: safeText(row.approval_request_id, 64) ?? "",
    approval_execution_id: safeText(row.approval_execution_id, 128) ?? "",
    reason: nullableText(row.reason, 512),
    valid_from: dateText(row.valid_from),
    expires_at: dateText(row.expires_at),
    waiver_digest: digest(row.waiver_digest) ?? "",
    created_at: dateText(row.created_at),
    created_by: nullableText(row.created_by, 64),
    request_id: nullableText(row.request_id, 128),
  };
  return waiver.id &&
    waiver.tenant_id &&
    waiver.dataset_id &&
    waiver.release_id &&
    waiver.channel_id &&
    waiver.policy_id &&
    waiver.policy_revision > 0 &&
    waiver.release_manifest_digest &&
    waiver.approval_request_id &&
    waiver.approval_execution_id &&
    waiver.waiver_digest
    ? waiver
    : null;
}

function unavailableGate(reason = "invalid_quality_authority"): QualityGate {
  return {
    tenant_id: null,
    dataset_id: null,
    release_id: null,
    channel_id: null,
    release_manifest_digest: null,
    channel_revision: null,
    risk_tier: null,
    is_default_serving: null,
    state: "unavailable",
    reason,
    policy: null,
    certification: null,
    waiver: null,
  };
}

export function projectQualityGate(value: unknown): QualityGate {
  const row = object(value);
  if (!row) return unavailableGate();
  const state = oneOf(row.state, [
    "not_required",
    "passed",
    "blocked",
    "waived",
    "unavailable",
  ] as const);
  const risk = oneOf(row.risk_tier, ["low", "medium", "high"] as const);
  const policy =
    row.policy === null || row.policy === undefined ? null : projectQualityPolicy(row.policy);
  const certification =
    row.certification === null || row.certification === undefined
      ? null
      : projectQualityCertification(row.certification);
  const waiver = row.waiver === null || row.waiver === undefined ? null : projectWaiver(row.waiver);
  const gate: QualityGate = {
    tenant_id: safeText(row.tenant_id, 64),
    dataset_id: safeText(row.dataset_id, 64),
    release_id: safeText(row.release_id, 64),
    channel_id: safeText(row.channel_id, 128),
    release_manifest_digest: digest(row.release_manifest_digest),
    channel_revision: exactInteger(row.channel_revision, 1),
    risk_tier: risk,
    is_default_serving: exactBoolean(row.is_default_serving),
    state: state ?? "unavailable",
    reason: safeText(row.reason, 128) ?? "invalid_quality_authority",
    policy,
    certification,
    waiver,
  };
  if (
    !gate.tenant_id ||
    !gate.dataset_id ||
    !gate.release_id ||
    !gate.channel_id ||
    !gate.release_manifest_digest ||
    gate.channel_revision === null ||
    !gate.risk_tier ||
    gate.is_default_serving === null ||
    !state
  )
    return unavailableGate();
  if (row.policy !== null && row.policy !== undefined && policy === null) return unavailableGate();
  if (row.certification !== null && row.certification !== undefined && certification === null)
    return unavailableGate();
  if (row.waiver !== null && row.waiver !== undefined && waiver === null) return unavailableGate();
  if (state === "passed" && (!policy || !certification)) return unavailableGate();
  if (state === "waived" && (!policy || !waiver)) return unavailableGate();
  if (policy && policy.tenant_id !== gate.tenant_id) return unavailableGate();
  if (
    policy?.scope_type === "channel" &&
    (policy.channel_id !== gate.channel_id || policy.scope_value !== gate.channel_id)
  ) {
    return unavailableGate();
  }
  if (
    certification &&
    (certification.tenant_id !== gate.tenant_id ||
      certification.dataset_id !== gate.dataset_id ||
      certification.release_id !== gate.release_id ||
      certification.policy_id !== policy?.id ||
      certification.policy_revision !== policy?.revision ||
      certification.release_manifest_digest !== gate.release_manifest_digest)
  ) {
    return unavailableGate();
  }
  if (
    waiver &&
    (waiver.tenant_id !== gate.tenant_id ||
      waiver.dataset_id !== gate.dataset_id ||
      waiver.release_id !== gate.release_id ||
      waiver.channel_id !== gate.channel_id ||
      waiver.policy_id !== policy?.id ||
      waiver.policy_revision !== policy?.revision ||
      waiver.release_manifest_digest !== gate.release_manifest_digest)
  ) {
    return unavailableGate();
  }
  return gate;
}

function page<T>(value: unknown, projector: (item: unknown) => T | null): QualityPage<T> {
  const row = object(value);
  const rawItems = row && Array.isArray(row.items) ? row.items : [];
  const items = rawItems.map(projector).filter((item): item is T => item !== null);
  return {
    items,
    next_cursor: row ? nullableText(row.next_cursor, 2048) : null,
    invalid_item_count: rawItems.length - items.length,
  };
}

export const projectQualityPolicyPage = (value: unknown): QualityPolicyPage =>
  page(value, projectQualityPolicy);
export const projectQualityBaselinePage = (value: unknown): QualityBaselinePage =>
  page(value, projectQualityBaseline);
export const projectQualityCertificationPage = (value: unknown): QualityCertificationPage =>
  page(value, projectQualityCertification);

export function projectQualityMutationOutcome(value: unknown): QualityMutationOutcome {
  const row = object(value);
  const operation = safeText(row?.operation, 64) ?? "release_quality_certify";
  const state = oneOf(row?.state, [
    "applied",
    "unchanged",
    "approval_required",
    "unavailable",
  ] as const);
  const message = nullableText(row?.message, 512);
  if (!row || !state || (row.message !== null && row.message !== undefined && message === null)) {
    return { state: "unavailable", operation, resource_id: null, message: null, retryable: false };
  }
  const nestedApproval = object(row.approval_request);
  const resourceId = nullableText(row.resource_id, 128);
  const approvalRequestId =
    nullableText(row.approval_request_id, 256) ??
    nullableText(nestedApproval?.id, 256) ??
    (state === "approval_required" ? resourceId : null);
  return {
    state,
    operation,
    resource_id: resourceId,
    approval_request_id: approvalRequestId,
    message,
    retryable: exactBoolean(row.retryable) ?? false,
  };
}
