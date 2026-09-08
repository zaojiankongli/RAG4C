import { request } from "../../api/client";
import {
  projectQualityBaseline,
  projectQualityBaselinePage,
  projectQualityCertificationPage,
  projectQualityGate,
  projectQualityMutationOutcome,
  projectQualityPolicyPage,
  type QualityBaseline,
  type QualityBaselinePage,
  type QualityCertificationPage,
  type QualityGate,
  type QualityMutationOutcome,
  type QualityPolicyPage,
} from "../model/qualityModel";

export interface QualityScope {
  tenantId: string;
  datasetId: string;
  actorToken: string;
}

export interface QualityRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export interface QualityListQuery {
  cursor?: string | null;
  limit?: number;
}

export interface CreateQualityBaselineInput {
  name: string;
  experimentIds: string[];
  parentBaselineId?: string | null;
  reason: string;
}

export interface CertifyReleaseQualityInput {
  channelId: string;
  baselineId: string;
  policyId: string;
  expectedPolicyRevision: number;
  expectedChannelRevision: number;
  reason: string;
}

export interface RequestQualityWaiverInput {
  channelId: string;
  policyId: string;
  expectedPolicyRevision: number;
  expectedChannelRevision: number;
  approvalPolicyId: string;
  requestedExpiresAt: string;
  reason: string;
}

export interface UpdateQualityPolicyInput {
  expectedRevision: number;
  name?: string;
  minExperimentCount?: number;
  minJudgedResultCount?: number;
  minJudgmentCoverageBps?: number;
  minExactAgreementBps?: number;
  minMeanScoreMilli?: number;
  maxConflictingResults?: number;
  requireAllExperimentsCompleted?: boolean;
  requireNoDegradedResults?: boolean;
  maxCertificationAgeMinutes?: number;
  status?: "active" | "disabled";
  reason: string;
}

export interface ReleaseQualityApi {
  fetchPolicies(
    scope: QualityScope,
    query?: QualityListQuery,
    options?: QualityRequestOptions,
  ): Promise<QualityPolicyPage>;
  fetchBaselines(
    scope: QualityScope,
    query?: QualityListQuery,
    options?: QualityRequestOptions,
  ): Promise<QualityBaselinePage>;
  fetchBaseline(
    scope: QualityScope,
    baselineId: string,
    options?: QualityRequestOptions,
  ): Promise<QualityBaseline>;
  fetchCertifications(
    scope: QualityScope,
    releaseId: string,
    query?: QualityListQuery,
    options?: QualityRequestOptions,
  ): Promise<QualityCertificationPage>;
  fetchGate(
    scope: QualityScope,
    releaseId: string,
    channelId: string,
    options?: QualityRequestOptions,
  ): Promise<QualityGate>;
  createBaseline(
    scope: QualityScope,
    input: CreateQualityBaselineInput,
    options?: QualityRequestOptions,
  ): Promise<QualityMutationOutcome>;
  certify(
    scope: QualityScope,
    releaseId: string,
    input: CertifyReleaseQualityInput,
    options?: QualityRequestOptions,
  ): Promise<QualityMutationOutcome>;
  updatePolicy(
    scope: QualityScope,
    policyId: string,
    input: UpdateQualityPolicyInput,
    options?: QualityRequestOptions,
  ): Promise<QualityMutationOutcome>;
  requestWaiver(
    scope: QualityScope,
    releaseId: string,
    input: RequestQualityWaiverInput,
    options?: QualityRequestOptions,
  ): Promise<QualityMutationOutcome>;
}

function requiredText(value: string, field: string, maximum: number): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(field + " is required");
  if (normalized.length > maximum) throw new Error(field + " is too long");
  return normalized;
}

function exactInteger(value: number, field: string, minimum: number, maximum?: number): number {
  if (!Number.isInteger(value) || value < minimum || (maximum !== undefined && value > maximum)) {
    throw new Error(field + " is invalid");
  }
  return value;
}

function normalizedScope(scope: QualityScope): QualityScope {
  return {
    tenantId: requiredText(scope.tenantId, "tenantId", 64),
    datasetId: requiredText(scope.datasetId, "datasetId", 64),
    actorToken: requiredText(scope.actorToken, "actorToken", 2048),
  };
}

function headers(scope: QualityScope, idempotencyKey?: string): Record<string, string> {
  const normalized = normalizedScope(scope);
  const result: Record<string, string> = {
    "X-RAG4C-Tenant": normalized.tenantId,
    Authorization: "Bearer " + normalized.actorToken,
  };
  if (idempotencyKey)
    result["Idempotency-Key"] = requiredText(idempotencyKey, "idempotencyKey", 128);
  return result;
}

function basePath(scope: QualityScope): string {
  return "/api/enterprise/knowledge-bases/" + encodeURIComponent(normalizedScope(scope).datasetId);
}

function queryString(query: QualityListQuery = {}): string {
  const params = new URLSearchParams();
  if (query.cursor) params.set("cursor", requiredText(query.cursor, "cursor", 2048));
  params.set("limit", String(exactInteger(query.limit ?? 50, "limit", 1, 200)));
  return params.toString();
}

export function createQualityIdempotencyKey(): string {
  const random = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);
  return "rag4c-quality-" + random;
}

function mutation(
  scope: QualityScope,
  path: string,
  method: "POST" | "PATCH",
  body: Record<string, unknown>,
  options: QualityRequestOptions,
): Promise<QualityMutationOutcome> {
  const idempotencyKey = options.idempotencyKey ?? createQualityIdempotencyKey();
  return request<unknown>(path, {
    method,
    headers: headers(scope, idempotencyKey),
    body: JSON.stringify(body),
    signal: options.signal,
  }).then(projectQualityMutationOutcome);
}

export function fetchQualityPolicies(
  scope: QualityScope,
  query: QualityListQuery = {},
  options: QualityRequestOptions = {},
): Promise<QualityPolicyPage> {
  return request<unknown>("/api/enterprise/release-quality/policies?" + queryString(query), {
    method: "GET",
    headers: headers(scope),
    signal: options.signal,
  }).then(projectQualityPolicyPage);
}

export function fetchQualityBaselines(
  scope: QualityScope,
  query: QualityListQuery = {},
  options: QualityRequestOptions = {},
): Promise<QualityBaselinePage> {
  return request<unknown>(basePath(scope) + "/quality-baselines?" + queryString(query), {
    method: "GET",
    headers: headers(scope),
    signal: options.signal,
  }).then(projectQualityBaselinePage);
}

export function fetchQualityBaseline(
  scope: QualityScope,
  baselineId: string,
  options: QualityRequestOptions = {},
): Promise<QualityBaseline> {
  const id = requiredText(baselineId, "baselineId", 64);
  return request<unknown>(basePath(scope) + "/quality-baselines/" + encodeURIComponent(id), {
    method: "GET",
    headers: headers(scope),
    signal: options.signal,
  }).then((value) => {
    const row =
      typeof value === "object" && value !== null && "baseline" in value
        ? (value as { baseline?: unknown }).baseline
        : value;
    const projected = projectQualityBaseline(row);
    if (!projected) throw new Error("Quality Baseline authority is invalid");
    return projected;
  });
}

export function fetchReleaseCertifications(
  scope: QualityScope,
  releaseId: string,
  query: QualityListQuery = {},
  options: QualityRequestOptions = {},
): Promise<QualityCertificationPage> {
  const id = requiredText(releaseId, "releaseId", 64);
  return request<unknown>(
    basePath(scope) +
      "/releases/" +
      encodeURIComponent(id) +
      "/certifications?" +
      queryString(query),
    { method: "GET", headers: headers(scope), signal: options.signal },
  ).then(projectQualityCertificationPage);
}

export function fetchQualityGate(
  scope: QualityScope,
  releaseId: string,
  channelId: string,
  options: QualityRequestOptions = {},
): Promise<QualityGate> {
  const release = requiredText(releaseId, "releaseId", 64);
  const channel = requiredText(channelId, "channelId", 128);
  return request<unknown>(
    basePath(scope) +
      "/releases/" +
      encodeURIComponent(release) +
      "/quality-gate?channel_id=" +
      encodeURIComponent(channel),
    { method: "GET", headers: headers(scope), signal: options.signal },
  ).then(projectQualityGate);
}

export function createQualityBaseline(
  scope: QualityScope,
  input: CreateQualityBaselineInput,
  options: QualityRequestOptions = {},
): Promise<QualityMutationOutcome> {
  const experiments = input.experimentIds.map((id) => requiredText(id, "experimentId", 64));
  if (experiments.length === 0 || new Set(experiments).size !== experiments.length) {
    throw new Error("experimentIds must be nonempty and unique");
  }
  return mutation(
    scope,
    basePath(scope) + "/quality-baselines",
    "POST",
    {
      name: requiredText(input.name, "name", 128),
      experiment_ids: experiments,
      parent_baseline_id: input.parentBaselineId
        ? requiredText(input.parentBaselineId, "parentBaselineId", 64)
        : null,
      reason: requiredText(input.reason, "reason", 512),
    },
    options,
  );
}

export function certifyReleaseQuality(
  scope: QualityScope,
  releaseId: string,
  input: CertifyReleaseQualityInput,
  options: QualityRequestOptions = {},
): Promise<QualityMutationOutcome> {
  const release = requiredText(releaseId, "releaseId", 64);
  return mutation(
    scope,
    basePath(scope) + "/releases/" + encodeURIComponent(release) + "/certifications",
    "POST",
    {
      channel_id: requiredText(input.channelId, "channelId", 128),
      baseline_id: requiredText(input.baselineId, "baselineId", 64),
      policy_id: requiredText(input.policyId, "policyId", 64),
      expected_policy_revision: exactInteger(
        input.expectedPolicyRevision,
        "expectedPolicyRevision",
        1,
      ),
      expected_channel_revision: exactInteger(
        input.expectedChannelRevision,
        "expectedChannelRevision",
        1,
      ),
      reason: requiredText(input.reason, "reason", 512),
    },
    options,
  );
}

export function updateQualityPolicy(
  scope: QualityScope,
  policyId: string,
  input: UpdateQualityPolicyInput,
  options: QualityRequestOptions = {},
): Promise<QualityMutationOutcome> {
  const body: Record<string, unknown> = {
    expected_revision: exactInteger(input.expectedRevision, "expectedRevision", 1),
    reason: requiredText(input.reason, "reason", 512),
  };
  const mapping: Array<[keyof UpdateQualityPolicyInput, string]> = [
    ["name", "name"],
    ["minExperimentCount", "min_experiment_count"],
    ["minJudgedResultCount", "min_judged_result_count"],
    ["minJudgmentCoverageBps", "min_judgment_coverage_bps"],
    ["minExactAgreementBps", "min_exact_agreement_bps"],
    ["minMeanScoreMilli", "min_mean_score_milli"],
    ["maxConflictingResults", "max_conflicting_results"],
    ["requireAllExperimentsCompleted", "require_all_experiments_completed"],
    ["requireNoDegradedResults", "require_no_degraded_results"],
    ["maxCertificationAgeMinutes", "max_certification_age_minutes"],
    ["status", "status"],
  ];
  for (const [source, target] of mapping) {
    if (input[source] !== undefined) body[target] = input[source];
  }
  if (Object.keys(body).length === 2) throw new Error("policy update requires a changed field");
  return mutation(
    scope,
    "/api/enterprise/release-quality/policies/" +
      encodeURIComponent(requiredText(policyId, "policyId", 64)),
    "PATCH",
    body,
    options,
  );
}

export function requestQualityWaiver(
  scope: QualityScope,
  releaseId: string,
  input: RequestQualityWaiverInput,
  options: QualityRequestOptions = {},
): Promise<QualityMutationOutcome> {
  const release = requiredText(releaseId, "releaseId", 64);
  const parsedExpiry = new Date(requiredText(input.requestedExpiresAt, "requestedExpiresAt", 64));
  if (Number.isNaN(parsedExpiry.getTime())) throw new Error("requestedExpiresAt is invalid");
  return mutation(
    scope,
    basePath(scope) + "/releases/" + encodeURIComponent(release) + "/quality-waivers",
    "POST",
    {
      channel_id: requiredText(input.channelId, "channelId", 128),
      policy_id: requiredText(input.policyId, "policyId", 64),
      expected_policy_revision: exactInteger(
        input.expectedPolicyRevision,
        "expectedPolicyRevision",
        1,
      ),
      expected_channel_revision: exactInteger(
        input.expectedChannelRevision,
        "expectedChannelRevision",
        1,
      ),
      approval_policy_id: requiredText(input.approvalPolicyId, "approvalPolicyId", 64),
      requested_expires_at: parsedExpiry.toISOString(),
      reason: requiredText(input.reason, "reason", 512),
    },
    options,
  );
}

export const releaseQualityApi: ReleaseQualityApi = {
  fetchPolicies: fetchQualityPolicies,
  fetchBaselines: fetchQualityBaselines,
  fetchBaseline: fetchQualityBaseline,
  fetchCertifications: fetchReleaseCertifications,
  fetchGate: fetchQualityGate,
  createBaseline: createQualityBaseline,
  certify: certifyReleaseQuality,
  updatePolicy: updateQualityPolicy,
  requestWaiver: requestQualityWaiver,
};
