import { ApiError, getBaseUrl, request } from "../../api/client";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  projectAuditExportJob,
  projectAuditExportPage,
  projectLegalHold,
  projectLegalHoldPage,
  projectRetentionExecution,
  projectRetentionPolicy,
  projectRetentionPreview,
  type AuditExportInput,
  type AuditExportJob,
  type AuditLegalHold,
  type AuditRetentionPolicy,
  type CompliancePage,
  type LegalHoldInput,
  type RetentionExecuteInput,
  type RetentionExecutionResult,
  type RetentionPolicyInput,
  type RetentionPreview,
  type RetentionPreviewInput,
  type RevisionReasonInput,
} from "../enterpriseComplianceModel";

export interface ComplianceRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}
export interface ComplianceListQuery {
  beforeId?: string | number;
  limit?: number;
}
export interface AuditExportDownload {
  blob: Blob;
  filename: string;
}

function authHeaders(scope: EnterpriseScope): Record<string, string> {
  const tenantId = scope.tenantId.trim();
  const actorToken = scope.actorToken.trim();
  if (!tenantId) throw new Error("tenantId is required for compliance requests");
  if (!actorToken) throw new Error("actorToken is required for compliance requests");
  return { "X-RAG4C-Tenant": tenantId, Authorization: `Bearer ${actorToken}` };
}
export function createComplianceIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) return `rag4c-compliance-${cryptoApi.randomUUID()}`;
  return `rag4c-compliance-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}
function mutationHeaders(scope: EnterpriseScope, options: ComplianceRequestOptions) {
  const supplied = options.idempotencyKey?.trim();
  if (options.idempotencyKey !== undefined && !supplied)
    throw new Error("idempotencyKey is required");
  const key = supplied || createComplianceIdempotencyKey();
  if (key.length > 128) throw new Error("idempotencyKey must be at most 128 characters");
  return { ...authHeaders(scope), "Idempotency-Key": key };
}
function reason(value: string): string {
  const next = value.trim();
  if (!next) throw new Error("reason is required");
  return next;
}
function revision(value: number): number {
  if (!Number.isInteger(value) || value < 1) throw new Error("revision must be at least 1");
  return value;
}
function bounded(value: number, min: number, max: number, label: string): number {
  if (!Number.isInteger(value) || value < min || value > max)
    throw new Error(`${label} must be between ${min} and ${max}`);
  return value;
}
function requiredId(value: string, label: string): string {
  const next = value.trim();
  if (!next) throw new Error(`${label} is required`);
  return next;
}
function queryString(query: ComplianceListQuery): string {
  const limit = query.limit ?? 50;
  bounded(limit, 1, 200, "limit");
  const params = new URLSearchParams({ limit: String(limit) });
  if (query.beforeId !== undefined) params.set("before_id", String(query.beforeId));
  return params.toString();
}
function requireProjected<T>(value: T | null, message: string): T {
  if (!value) throw new Error(message);
  return value;
}
async function mutation<T>(
  scope: EnterpriseScope,
  path: string,
  body: unknown,
  options: ComplianceRequestOptions,
  projector: (input: unknown) => T | null,
  method: "POST" | "PUT" = "POST",
): Promise<T> {
  return request<unknown>(path, {
    method,
    headers: mutationHeaders(scope, options),
    body: JSON.stringify(body),
    signal: options.signal,
  }).then((value) =>
    requireProjected(
      projector(value),
      "compliance mutation response is missing authoritative facts",
    ),
  );
}

export async function fetchRetentionPolicy(
  scope: EnterpriseScope,
  options: ComplianceRequestOptions = {},
): Promise<AuditRetentionPolicy> {
  const value = await request<unknown>("/api/enterprise/compliance/retention-policy", {
    method: "GET",
    headers: authHeaders(scope),
    signal: options.signal,
  });
  return requireProjected(projectRetentionPolicy(value), "retention policy response is invalid");
}
export function updateRetentionPolicy(
  scope: EnterpriseScope,
  input: RetentionPolicyInput,
  options: ComplianceRequestOptions = {},
): Promise<AuditRetentionPolicy> {
  return mutation(
    scope,
    "/api/enterprise/compliance/retention-policy",
    {
      audit_retention_days: bounded(input.audit_retention_days, 30, 3650, "audit_retention_days"),
      export_retention_days: bounded(input.export_retention_days, 1, 365, "export_retention_days"),
      status: input.status,
      revision: revision(input.revision),
      reason: reason(input.reason),
    },
    options,
    projectRetentionPolicy,
    "PUT",
  );
}
export async function previewRetention(
  scope: EnterpriseScope,
  input: RetentionPreviewInput,
  options: ComplianceRequestOptions = {},
): Promise<RetentionPreview> {
  const value = await request<unknown>("/api/enterprise/compliance/retention/preview", {
    method: "POST",
    headers: authHeaders(scope),
    body: JSON.stringify({ policy_revision: revision(input.policy_revision) }),
    signal: options.signal,
  });
  return requireProjected(projectRetentionPreview(value), "retention preview response is invalid");
}
export function executeRetention(
  scope: EnterpriseScope,
  input: RetentionExecuteInput,
  options: ComplianceRequestOptions = {},
): Promise<RetentionExecutionResult> {
  const fingerprint = input.preview_fingerprint.trim();
  if (!fingerprint) throw new Error("preview_fingerprint is required");
  if (input.confirmation !== "EXECUTE RETENTION")
    throw new Error("confirmation must be EXECUTE RETENTION");
  return mutation(
    scope,
    "/api/enterprise/compliance/retention/execute",
    {
      policy_revision: revision(input.policy_revision),
      preview_fingerprint: fingerprint,
      reason: reason(input.reason),
      confirmation: input.confirmation,
    },
    options,
    projectRetentionExecution,
  );
}
export async function fetchLegalHolds(
  scope: EnterpriseScope,
  query: ComplianceListQuery = {},
  options: ComplianceRequestOptions = {},
): Promise<CompliancePage<AuditLegalHold>> {
  const value = await request<unknown>(
    `/api/enterprise/compliance/legal-holds?${queryString(query)}`,
    { method: "GET", headers: authHeaders(scope), signal: options.signal },
  );
  return projectLegalHoldPage(value);
}
export function createLegalHold(
  scope: EnterpriseScope,
  input: LegalHoldInput,
  options: ComplianceRequestOptions = {},
): Promise<AuditLegalHold> {
  const name = input.name.trim();
  if (!name) throw new Error("legal hold name is required");
  return mutation(
    scope,
    "/api/enterprise/compliance/legal-holds",
    {
      name,
      reason: reason(input.reason),
      ...(input.sequence_start !== undefined
        ? {
            sequence_start: bounded(
              input.sequence_start,
              0,
              Number.MAX_SAFE_INTEGER,
              "sequence_start",
            ),
          }
        : {}),
      ...(input.sequence_end !== undefined
        ? { sequence_end: bounded(input.sequence_end, 0, Number.MAX_SAFE_INTEGER, "sequence_end") }
        : {}),
      ...(input.starts_at?.trim() ? { starts_at: input.starts_at.trim() } : {}),
      ...(input.ends_at?.trim() ? { ends_at: input.ends_at.trim() } : {}),
    },
    options,
    projectLegalHold,
  );
}
export function releaseLegalHold(
  scope: EnterpriseScope,
  id: string,
  input: RevisionReasonInput,
  options: ComplianceRequestOptions = {},
): Promise<AuditLegalHold> {
  return mutation(
    scope,
    `/api/enterprise/compliance/legal-holds/${encodeURIComponent(requiredId(id, "holdId"))}/release`,
    { revision: revision(input.revision), reason: reason(input.reason) },
    options,
    projectLegalHold,
  );
}
export async function fetchAuditExportJobs(
  scope: EnterpriseScope,
  query: ComplianceListQuery = {},
  options: ComplianceRequestOptions = {},
): Promise<CompliancePage<AuditExportJob>> {
  const value = await request<unknown>(
    `/api/enterprise/compliance/audit-exports?${queryString(query)}`,
    { method: "GET", headers: authHeaders(scope), signal: options.signal },
  );
  return projectAuditExportPage(value);
}
export function createAuditExport(
  scope: EnterpriseScope,
  input: AuditExportInput,
  options: ComplianceRequestOptions = {},
): Promise<AuditExportJob> {
  const filters = Object.fromEntries(
    Object.entries(input.filters)
      .map(([key, value]) => [key, value.trim()])
      .filter(
        ([key, value]) =>
          ["actor", "action", "resource", "request", "time"].includes(key) && Boolean(value),
      ),
  );
  return mutation(
    scope,
    "/api/enterprise/compliance/audit-exports",
    {
      format: input.format,
      filters,
      ...(input.sequence_start !== undefined ? { sequence_start: input.sequence_start } : {}),
      ...(input.sequence_end !== undefined ? { sequence_end: input.sequence_end } : {}),
      ...(input.starts_at?.trim() ? { starts_at: input.starts_at.trim() } : {}),
      ...(input.ends_at?.trim() ? { ends_at: input.ends_at.trim() } : {}),
      reason: reason(input.reason),
    },
    options,
    projectAuditExportJob,
  );
}
export async function fetchAuditExportJob(
  scope: EnterpriseScope,
  id: string,
  options: ComplianceRequestOptions = {},
): Promise<AuditExportJob> {
  const value = await request<unknown>(
    `/api/enterprise/compliance/audit-exports/${encodeURIComponent(requiredId(id, "exportId"))}`,
    { method: "GET", headers: authHeaders(scope), signal: options.signal },
  );
  return requireProjected(projectAuditExportJob(value), "audit export response is invalid");
}
export async function downloadAuditExport(
  scope: EnterpriseScope,
  id: string,
  options: Omit<ComplianceRequestOptions, "idempotencyKey"> = {},
): Promise<AuditExportDownload> {
  const response = await fetch(
    `${getBaseUrl()}/api/enterprise/compliance/audit-exports/${encodeURIComponent(requiredId(id, "exportId"))}/download`,
    { method: "GET", headers: authHeaders(scope), signal: options.signal },
  );
  if (!response.ok) throw new ApiError("audit export download failed", "http", response.status);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/i.exec(disposition);
  return { blob: await response.blob(), filename: match?.[1] ?? `audit-export-${id}` };
}
