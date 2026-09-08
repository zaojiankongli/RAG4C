import { request } from "../../api/client";
import {
  projectQualityAlert,
  projectQualityOperationsSummary,
  projectRecertificationJob,
  sanitizeOperationsMessage,
  type OperationsAlertStatus,
  type OperationsScope,
  type QualityOperationsAlert,
  type QualityOperationsSummary,
  type RecertificationJob,
  type RecertificationJobStatus,
  type RecertificationTrigger,
} from "../model/operationsModel";

export interface OperationsApiScope extends OperationsScope {
  actorToken: string;
}

export interface OperationsRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export interface OperationsPage<T> {
  items: T[];
  next_cursor: string | null;
  invalid_item_count: number;
}

export interface AlertListQuery {
  cursor?: string | null;
  limit?: number;
  status?: OperationsAlertStatus;
  severity?: "warning" | "critical";
}

export interface JobListQuery {
  cursor?: string | null;
  limit?: number;
  status?: RecertificationJobStatus;
}

export interface OperationsMutationOutcome {
  state: "applied" | "unchanged" | "coalesced" | "unavailable";
  operation: string;
  resource_id: string | null;
  message: string | null;
  retryable: boolean;
}

export interface AlertAcknowledgeInput {
  expectedRevision: number;
  comment?: string | null;
  reason: string;
}

export interface AlertSuppressInput extends AlertAcknowledgeInput {
  suppressedUntil: string;
}

export interface QueueRecertificationInput {
  releaseId: string;
  channelId: string;
  releaseRole: "active" | "pinned";
  baselineId: string;
  policyId: string;
  expectedPolicyRevision: number;
  sloPolicyId: string;
  expectedSloPolicyRevision: number;
  expectedManifestDigest: string;
  expectedEvidenceDigest: string | null;
  expectedChannelRevision: number;
  trigger: RecertificationTrigger;
  reason: string;
}

export interface CancelRecertificationInput {
  expectedStatus: RecertificationJobStatus;
  reason: string;
}

function object(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function requiredText(value: string, field: string, maximum: number): string {
  const normalized = value.trim();
  if (!normalized || normalized.length > maximum || /[\r\n\0]/.test(normalized)) {
    throw new Error(`${field} is invalid`);
  }
  return normalized;
}

function exactInteger(value: number, field: string, minimum = 1): number {
  if (!Number.isInteger(value) || value < minimum) throw new Error(`${field} is invalid`);
  return value;
}

function normalizedScope(scope: OperationsApiScope): OperationsApiScope {
  return {
    tenantId: requiredText(scope.tenantId, "tenantId", 64),
    datasetId: requiredText(scope.datasetId, "datasetId", 64),
    actorToken: requiredText(scope.actorToken, "actorToken", 2048),
  };
}

function headers(scope: OperationsApiScope, idempotencyKey?: string): Record<string, string> {
  const normalized = normalizedScope(scope);
  const result: Record<string, string> = {
    "X-RAG4C-Tenant": normalized.tenantId,
    Authorization: `Bearer ${normalized.actorToken}`,
  };
  if (idempotencyKey !== undefined) {
    result["Idempotency-Key"] = requiredText(idempotencyKey, "idempotencyKey", 128);
  }
  return result;
}

function basePath(scope: OperationsApiScope): string {
  return `/api/enterprise/knowledge-bases/${encodeURIComponent(normalizedScope(scope).datasetId)}`;
}

function mutationKey(options?: OperationsRequestOptions): string {
  if (!options?.idempotencyKey) throw new Error("idempotencyKey is required");
  return requiredText(options.idempotencyKey, "idempotencyKey", 128);
}

function bodyReason(value: string): string {
  return requiredText(value, "reason", 512);
}

function optionalComment(value?: string | null): string | null {
  return value === undefined || value === null ? null : requiredText(value, "comment", 512);
}

function pageQuery(query: {
  cursor?: string | null;
  limit?: number;
  status?: string;
  severity?: string;
}): string {
  const params = new URLSearchParams();
  if (query.cursor) params.set("cursor", requiredText(query.cursor, "cursor", 2048));
  params.set("limit", String(exactInteger(query.limit ?? 50, "limit")));
  if (query.status) params.set("status", requiredText(query.status, "status", 32));
  if (query.severity) params.set("severity", requiredText(query.severity, "severity", 16));
  return `?${params.toString()}`;
}

function projectPage<T>(raw: unknown, projector: (value: unknown) => T): OperationsPage<T> {
  const source = object(raw);
  if (!source || !Array.isArray(source.items)) throw new Error("page payload is unavailable");
  const items: T[] = [];
  let invalid = 0;
  for (const item of source.items) {
    try {
      items.push(projector(item));
    } catch {
      invalid += 1;
    }
  }
  return {
    items,
    next_cursor:
      source.next_cursor === null || source.next_cursor === undefined
        ? null
        : requiredText(String(source.next_cursor), "next_cursor", 2048),
    invalid_item_count: invalid,
  };
}

function projectMutation(raw: unknown): OperationsMutationOutcome {
  const source = object(raw);
  if (!source) throw new Error("mutation payload is unavailable");
  const rawState = requiredText(String(source.state ?? "unavailable"), "state", 32);
  const state = ["applied", "unchanged", "coalesced", "unavailable"].includes(rawState)
    ? (rawState as OperationsMutationOutcome["state"])
    : "unavailable";
  return {
    state,
    operation: requiredText(String(source.operation ?? "quality_operations"), "operation", 64),
    resource_id:
      source.resource_id === null || source.resource_id === undefined
        ? null
        : requiredText(String(source.resource_id), "resource_id", 64),
    message:
      source.message === null || source.message === undefined
        ? null
        : sanitizeOperationsMessage(source.message, "Unavailable"),
    retryable: source.retryable === true,
  };
}

async function mutate(
  scope: OperationsApiScope,
  path: string,
  payload: Record<string, unknown>,
  options?: OperationsRequestOptions,
): Promise<OperationsMutationOutcome> {
  const key = mutationKey(options);
  const raw = await request<unknown>(path, {
    method: "POST",
    headers: headers(scope, key),
    body: JSON.stringify(payload),
    signal: options?.signal,
  });
  return projectMutation(raw);
}

export function createOperationsIdempotencyKey(): string {
  const random = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);
  return `rag4c-quality-operations-${random}`;
}

export async function fetchQualityOperationsSummary(
  scope: OperationsApiScope,
  options: OperationsRequestOptions = {},
): Promise<QualityOperationsSummary> {
  const raw = await request<unknown>(`${basePath(scope)}/quality-operations/summary`, {
    method: "GET",
    headers: headers(scope),
    signal: options.signal,
  });
  return projectQualityOperationsSummary(raw, normalizedScope(scope));
}

export async function fetchQualityAlerts(
  scope: OperationsApiScope,
  query: AlertListQuery = {},
  options: OperationsRequestOptions = {},
): Promise<OperationsPage<QualityOperationsAlert>> {
  const raw = await request<unknown>(`${basePath(scope)}/quality-alerts${pageQuery(query)}`, {
    method: "GET",
    headers: headers(scope),
    signal: options.signal,
  });
  return projectPage(raw, (item) => projectQualityAlert(item, normalizedScope(scope)));
}

export async function fetchRecertificationJobs(
  scope: OperationsApiScope,
  query: JobListQuery = {},
  options: OperationsRequestOptions = {},
): Promise<OperationsPage<RecertificationJob>> {
  const raw = await request<unknown>(`${basePath(scope)}/recertification-jobs${pageQuery(query)}`, {
    method: "GET",
    headers: headers(scope),
    signal: options.signal,
  });
  return projectPage(raw, (item) => projectRecertificationJob(item, normalizedScope(scope)));
}

export function acknowledgeQualityAlert(
  scope: OperationsApiScope,
  alertId: string,
  input: AlertAcknowledgeInput,
  options?: OperationsRequestOptions,
): Promise<OperationsMutationOutcome> {
  return mutate(
    scope,
    `${basePath(scope)}/quality-alerts/${encodeURIComponent(requiredText(alertId, "alertId", 64))}/acknowledge`,
    {
      expected_revision: exactInteger(input.expectedRevision, "expectedRevision"),
      comment: optionalComment(input.comment),
      reason: bodyReason(input.reason),
    },
    options,
  );
}

export function resolveQualityAlert(
  scope: OperationsApiScope,
  alertId: string,
  input: AlertAcknowledgeInput,
  options?: OperationsRequestOptions,
): Promise<OperationsMutationOutcome> {
  return mutate(
    scope,
    `${basePath(scope)}/quality-alerts/${encodeURIComponent(requiredText(alertId, "alertId", 64))}/resolve`,
    {
      expected_revision: exactInteger(input.expectedRevision, "expectedRevision"),
      comment: optionalComment(input.comment),
      reason: bodyReason(input.reason),
    },
    options,
  );
}

export function suppressQualityAlert(
  scope: OperationsApiScope,
  alertId: string,
  input: AlertSuppressInput,
  options?: OperationsRequestOptions,
): Promise<OperationsMutationOutcome> {
  return mutate(
    scope,
    `${basePath(scope)}/quality-alerts/${encodeURIComponent(requiredText(alertId, "alertId", 64))}/suppress`,
    {
      expected_revision: exactInteger(input.expectedRevision, "expectedRevision"),
      suppressed_until: requiredText(input.suppressedUntil, "suppressedUntil", 64),
      comment: optionalComment(input.comment),
      reason: bodyReason(input.reason),
    },
    options,
  );
}

export function queueRecertificationJob(
  scope: OperationsApiScope,
  input: QueueRecertificationInput,
  options?: OperationsRequestOptions,
): Promise<OperationsMutationOutcome> {
  return mutate(
    scope,
    `${basePath(scope)}/recertification-jobs`,
    {
      release_id: requiredText(input.releaseId, "releaseId", 64),
      channel_id: requiredText(input.channelId, "channelId", 128),
      release_role: input.releaseRole,
      baseline_id: requiredText(input.baselineId, "baselineId", 64),
      policy_id: requiredText(input.policyId, "policyId", 64),
      expected_policy_revision: exactInteger(
        input.expectedPolicyRevision,
        "expectedPolicyRevision",
      ),
      slo_policy_id: requiredText(input.sloPolicyId, "sloPolicyId", 64),
      expected_slo_policy_revision: exactInteger(
        input.expectedSloPolicyRevision,
        "expectedSloPolicyRevision",
      ),
      expected_manifest_digest: requiredText(
        input.expectedManifestDigest,
        "expectedManifestDigest",
        64,
      ),
      expected_evidence_digest:
        input.expectedEvidenceDigest === null
          ? null
          : requiredText(input.expectedEvidenceDigest, "expectedEvidenceDigest", 64),
      expected_channel_revision: exactInteger(
        input.expectedChannelRevision,
        "expectedChannelRevision",
      ),
      trigger: input.trigger,
      reason: bodyReason(input.reason),
    },
    options,
  );
}

export function cancelRecertificationJob(
  scope: OperationsApiScope,
  jobId: string,
  input: CancelRecertificationInput,
  options?: OperationsRequestOptions,
): Promise<OperationsMutationOutcome> {
  return mutate(
    scope,
    `${basePath(scope)}/recertification-jobs/${encodeURIComponent(requiredText(jobId, "jobId", 64))}/cancel`,
    { expected_status: input.expectedStatus, reason: bodyReason(input.reason) },
    options,
  );
}

export function requestQualityOperationsScan(
  scope: OperationsApiScope,
  input: { reason: string },
  options?: OperationsRequestOptions,
): Promise<OperationsMutationOutcome> {
  return mutate(
    scope,
    `${basePath(scope)}/quality-operations/scan`,
    { reason: bodyReason(input.reason) },
    options,
  );
}
