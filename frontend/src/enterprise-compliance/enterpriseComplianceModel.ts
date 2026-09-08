export type ComplianceCursor = string | number;
export interface CompliancePage<T> {
  items: T[];
  count: number;
  next_before_id: ComplianceCursor | null;
}
export type RetentionPolicyStatus = "active" | "paused" | (string & {});
export interface AuditRetentionPolicy {
  id: string;
  status: RetentionPolicyStatus;
  audit_retention_days: number;
  export_retention_days: number;
  revision: number;
  last_preview_at?: string;
  last_executed_at?: string;
  last_executed_by?: string;
  updated_at?: string;
}
export interface RetentionPreview {
  policy_revision: number;
  cutoff_at: string;
  candidate_count: number;
  protected_count: number;
  deletable_count: number;
  sequence_start: number | null;
  sequence_end: number | null;
  preview_fingerprint: string;
  generated_at: string;
}
export interface RetentionExecutionResult {
  deleted_count: number;
  protected_count: number;
  policy_revision: number;
  executed_at: string;
  audit_event_id?: string;
}
export type LegalHoldStatus = "active" | "released" | (string & {});
export interface AuditLegalHold {
  id: string;
  name: string;
  reason: string;
  status: LegalHoldStatus;
  revision: number;
  sequence_start?: number;
  sequence_end?: number;
  starts_at?: string;
  ends_at?: string;
  created_at: string;
  created_by: string;
  released_at?: string;
  released_by?: string;
}
export type AuditExportFormat = "ndjson" | "csv";
export type AuditExportStatus =
  "queued" | "running" | "completed" | "failed" | "expired" | (string & {});
export interface AuditExportJob {
  id: string;
  format: AuditExportFormat;
  status: AuditExportStatus;
  filters: Record<string, string>;
  sequence_start?: number;
  sequence_end?: number;
  starts_at?: string;
  ends_at?: string;
  sha256?: string;
  byte_size?: number;
  row_count?: number;
  revision: number;
  requested_at: string;
  requested_by?: string;
  completed_at?: string;
  expires_at: string;
  error?: string;
}
export interface RetentionPolicyInput {
  audit_retention_days: number;
  export_retention_days: number;
  status: RetentionPolicyStatus;
  revision: number;
  reason: string;
}
export interface RetentionPreviewInput {
  policy_revision: number;
}
export interface RetentionExecuteInput {
  policy_revision: number;
  preview_fingerprint: string;
  reason: string;
  confirmation: string;
}
export interface LegalHoldInput {
  name: string;
  reason: string;
  sequence_start?: number;
  sequence_end?: number;
  starts_at?: string;
  ends_at?: string;
}
export interface RevisionReasonInput {
  revision: number;
  reason: string;
}
export interface AuditExportInput {
  format: AuditExportFormat;
  filters: Record<string, string>;
  sequence_start?: number;
  sequence_end?: number;
  starts_at?: string;
  ends_at?: string;
  reason: string;
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}
function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}
function integer(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}
function positive(value: unknown): number | null {
  const result = integer(value);
  return result !== null && result > 0 ? result : null;
}
function optionalText(
  target: Record<string, unknown>,
  source: Record<string, unknown>,
  keys: readonly string[],
) {
  for (const key of keys) {
    const value = text(source[key]);
    if (value) target[key] = value;
  }
}
function cursor(value: unknown): ComplianceCursor | null {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return null;
}
function page<T>(input: unknown, projector: (value: unknown) => T | null): CompliancePage<T> {
  const source = record(input) ?? {};
  const items = (Array.isArray(source.items) ? source.items : [])
    .map(projector)
    .filter((item): item is T => item !== null);
  return {
    items,
    count: integer(source.count) ?? items.length,
    next_before_id: cursor(source.next_before_id),
  };
}

export function projectRetentionPolicy(input: unknown): AuditRetentionPolicy | null {
  const outer = record(input);
  const source = record(outer?.policy) ?? outer;
  if (!source) return null;
  const id = text(source.id);
  const status = text(source.status);
  const auditDays = positive(source.audit_retention_days);
  const exportDays = positive(source.export_retention_days);
  const revision = positive(source.revision);
  if (!id || !status || auditDays === null || exportDays === null || revision === null) return null;
  const policy: AuditRetentionPolicy = {
    id,
    status,
    audit_retention_days: auditDays,
    export_retention_days: exportDays,
    revision,
  };
  optionalText(policy as unknown as Record<string, unknown>, source, [
    "last_preview_at",
    "last_executed_at",
    "last_executed_by",
    "updated_at",
  ]);
  return policy;
}
export function projectRetentionPreview(input: unknown): RetentionPreview | null {
  const source = record(input);
  if (!source) return null;
  const revision = positive(source.policy_revision);
  const cutoff = text(source.cutoff_at);
  const candidate = integer(source.candidate_count);
  const protectedCount = integer(source.protected_count);
  const deletable = integer(source.deletable_count);
  const fingerprint = text(source.preview_fingerprint);
  const generated = text(source.generated_at);
  if (
    revision === null ||
    !cutoff ||
    candidate === null ||
    protectedCount === null ||
    deletable === null ||
    !fingerprint ||
    !generated
  )
    return null;
  return {
    policy_revision: revision,
    cutoff_at: cutoff,
    candidate_count: candidate,
    protected_count: protectedCount,
    deletable_count: deletable,
    sequence_start: integer(source.sequence_start),
    sequence_end: integer(source.sequence_end),
    preview_fingerprint: fingerprint,
    generated_at: generated,
  };
}
export function projectRetentionExecution(input: unknown): RetentionExecutionResult | null {
  const source = record(input);
  if (!source) return null;
  const deleted = integer(source.deleted_count);
  const protectedCount = integer(source.protected_count) ?? 0;
  const revision = positive(source.policy_revision);
  const executed = text(source.executed_at) ?? new Date(0).toISOString();
  if (deleted === null || revision === null) return null;
  const result: RetentionExecutionResult = {
    deleted_count: deleted,
    protected_count: protectedCount,
    policy_revision: revision,
    executed_at: executed,
  };
  const audit = text(source.audit_event_id);
  if (audit) result.audit_event_id = audit;
  return result;
}
export function projectLegalHold(input: unknown): AuditLegalHold | null {
  const outer = record(input);
  const source = record(outer?.hold) ?? outer;
  if (!source) return null;
  const id = text(source.id);
  const name = text(source.name);
  const reason = text(source.reason);
  const status = text(source.status);
  const revision = positive(source.revision);
  const createdAt = text(source.created_at);
  const createdBy = text(source.created_by);
  if (!id || !name || !reason || !status || revision === null || !createdAt || !createdBy)
    return null;
  const hold: AuditLegalHold = {
    id,
    name,
    reason,
    status,
    revision,
    created_at: createdAt,
    created_by: createdBy,
  };
  for (const [key, value] of [
    ["sequence_start", integer(source.sequence_start)],
    ["sequence_end", integer(source.sequence_end)],
  ] as const)
    if (value !== null) hold[key] = value;
  optionalText(hold as unknown as Record<string, unknown>, source, [
    "starts_at",
    "ends_at",
    "released_at",
    "released_by",
  ]);
  return hold;
}
export const projectLegalHoldPage = (input: unknown) => page(input, projectLegalHold);
export function projectAuditExportJob(input: unknown): AuditExportJob | null {
  const outer = record(input);
  const source = record(outer?.job) ?? outer;
  if (!source) return null;
  const id = text(source.id);
  const format = text(source.format);
  const status = text(source.status);
  const revision = positive(source.revision);
  const requestedAt = text(source.requested_at);
  const expiresAt = text(source.expires_at);
  if (
    !id ||
    (format !== "ndjson" && format !== "csv") ||
    !status ||
    revision === null ||
    !requestedAt ||
    !expiresAt
  )
    return null;
  const filtersSource = record(source.filters) ?? {};
  const filters: Record<string, string> = {};
  for (const [key, value] of Object.entries(filtersSource)) {
    const safe = text(value);
    if (safe && ["actor", "action", "resource", "request", "time"].includes(key))
      filters[key] = safe;
  }
  const job: AuditExportJob = {
    id,
    format,
    status,
    filters,
    revision,
    requested_at: requestedAt,
    expires_at: expiresAt,
  };
  for (const [key, value] of [
    ["sequence_start", integer(source.sequence_start)],
    ["sequence_end", integer(source.sequence_end)],
    ["byte_size", integer(source.byte_size)],
    ["row_count", integer(source.row_count)],
  ] as const)
    if (value !== null) job[key] = value;
  optionalText(job as unknown as Record<string, unknown>, source, [
    "starts_at",
    "ends_at",
    "sha256",
    "requested_by",
    "completed_at",
    "error",
  ]);
  return job;
}
export const projectAuditExportPage = (input: unknown) => page(input, projectAuditExportJob);
