import { request } from "../../api/client";
import {
  projectReleaseAuditPage,
  projectReleaseChannelPage,
  projectReleaseDetail,
  projectReleaseHistoryPage,
  projectReleaseImpact,
  projectReleaseMutationOutcome,
  projectReleaseReadiness,
  type ReleaseAuditPage,
  type ReleaseChannelPage,
  type ReleaseDetail,
  type ReleaseHistoryPage,
  type ReleaseImpact,
  type ReleaseMutationOutcome,
  type ReleaseReadiness,
} from "../model/releaseModel";
import { validateReleaseMutation } from "../model/releaseValidation";

export interface ReleaseScope {
  tenantId: string;
  datasetId: string;
  actorToken: string;
}

export interface ReleaseRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export interface ReleaseListQuery {
  cursor?: string;
  limit?: number;
}

export interface ReleaseHistoryQuery extends ReleaseListQuery {
  channelId: string;
}

export interface CaptureReleaseInput {
  expectedProfileRevision: number;
  expectedOwnershipRevision: number;
  expectedWorkspaceRevision: number;
  expectedMutationGeneration: number;
  expectedServingGeneration: number;
  reason: string;
}

export interface PromoteReleaseInput {
  channelId: string;
  expectedChannelRevision: number;
  expectedProfileRevision: number;
  expectedOwnershipRevision: number;
  expectedWorkspaceRevision: number;
  expectedServingGeneration: number;
  reason: string;
}

export interface RollbackReleaseInput {
  targetReleaseId: string;
  expectedChannelRevision: number;
  expectedServingGeneration: number;
  reason: string;
}

export interface ReleaseApi {
  fetchChannels(
    scope: ReleaseScope,
    query?: ReleaseListQuery,
    options?: ReleaseRequestOptions,
  ): Promise<ReleaseChannelPage>;
  fetchHistory(
    scope: ReleaseScope,
    channelId: string,
    query?: Omit<ReleaseListQuery, "cursor"> & { cursor?: string },
    options?: ReleaseRequestOptions,
  ): Promise<ReleaseHistoryPage>;
  fetchDetail(
    scope: ReleaseScope,
    releaseId: string,
    options?: ReleaseRequestOptions,
  ): Promise<ReleaseDetail>;
  fetchReadiness(
    scope: ReleaseScope,
    releaseId: string,
    options?: ReleaseRequestOptions,
  ): Promise<ReleaseReadiness>;
  fetchImpact(
    scope: ReleaseScope,
    releaseId: string,
    options?: ReleaseRequestOptions,
  ): Promise<ReleaseImpact>;
  fetchAudit(
    scope: ReleaseScope,
    releaseId: string,
    options?: ReleaseRequestOptions,
  ): Promise<ReleaseAuditPage>;
  capture(
    scope: ReleaseScope,
    input: CaptureReleaseInput,
    options?: ReleaseRequestOptions,
  ): Promise<ReleaseMutationOutcome>;
  promote(
    scope: ReleaseScope,
    releaseId: string,
    input: PromoteReleaseInput,
    options?: ReleaseRequestOptions,
  ): Promise<ReleaseMutationOutcome>;
  rollback(
    scope: ReleaseScope,
    channelId: string,
    input: RollbackReleaseInput,
    options?: ReleaseRequestOptions,
  ): Promise<ReleaseMutationOutcome>;
}

function requiredText(value: string, name: string, maximum = 128): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${name} is required`);
  if (normalized.length > maximum) throw new Error(`${name} must be at most ${maximum} characters`);
  return normalized;
}

function requiredScope(scope: ReleaseScope): ReleaseScope {
  const tenantId = requiredText(scope.tenantId, "tenantId", 64);
  const datasetId = requiredText(scope.datasetId, "datasetId", 64);
  const actorToken = requiredText(scope.actorToken, "actorToken", 4096);
  return { tenantId, datasetId, actorToken };
}

function basePath(scope: ReleaseScope): string {
  const normalized = requiredScope(scope);
  return `/api/enterprise/knowledge-bases/${encodeURIComponent(normalized.datasetId)}`;
}

function authenticatedHeaders(scope: ReleaseScope): Record<string, string> {
  const normalized = requiredScope(scope);
  return {
    "X-RAG4C-Tenant": normalized.tenantId,
    Authorization: `Bearer ${normalized.actorToken}`,
  };
}

export function createReleaseIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) return `rag4c-release-${cryptoApi.randomUUID()}`;
  if (cryptoApi?.getRandomValues) {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    return `rag4c-release-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
  }
  return `rag4c-release-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function mutationHeaders(
  scope: ReleaseScope,
  options: ReleaseRequestOptions,
): Record<string, string> {
  const supplied = options.idempotencyKey?.trim();
  if (options.idempotencyKey !== undefined && !supplied) {
    throw new Error("idempotencyKey is required for Release mutations");
  }
  const key = supplied || createReleaseIdempotencyKey();
  if (key.length > 128) throw new Error("idempotencyKey must be at most 128 characters");
  return { ...authenticatedHeaders(scope), "Idempotency-Key": key };
}

function listQuery(query: ReleaseListQuery = {}): string {
  const limit = query.limit ?? 50;
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw new Error("limit must be an integer between 1 and 100");
  }
  const params = new URLSearchParams();
  if (query.cursor?.trim()) params.set("cursor", query.cursor.trim());
  params.set("limit", String(limit));
  return params.toString();
}

function requiredProjected<T>(value: T | null, label: string): T {
  if (value === null) throw new Error(`${label} is invalid or unavailable`);
  return value;
}

function mutation<T>(
  scope: ReleaseScope,
  path: string,
  body: Record<string, unknown>,
  options: ReleaseRequestOptions,
): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: mutationHeaders(scope, options),
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export function fetchReleaseChannels(
  scope: ReleaseScope,
  query: ReleaseListQuery = {},
  options: ReleaseRequestOptions = {},
): Promise<ReleaseChannelPage> {
  return request<unknown>(`/api/enterprise/release-channels?${listQuery(query)}`, {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  }).then(projectReleaseChannelPage);
}

export function fetchReleaseHistory(
  scope: ReleaseScope,
  channelId: string,
  query: Omit<ReleaseListQuery, "cursor"> & { cursor?: string } = {},
  options: ReleaseRequestOptions = {},
): Promise<ReleaseHistoryPage> {
  const channel = requiredText(channelId, "channelId", 128);
  return request<unknown>(
    `${basePath(scope)}/releases?channel_id=${encodeURIComponent(channel)}&${listQuery(query)}`,
    {
      method: "GET",
      headers: authenticatedHeaders(scope),
      signal: options.signal,
    },
  ).then(projectReleaseHistoryPage);
}

export function fetchReleaseDetail(
  scope: ReleaseScope,
  releaseId: string,
  options: ReleaseRequestOptions = {},
): Promise<ReleaseDetail> {
  const id = requiredText(releaseId, "releaseId", 64);
  return request<unknown>(`${basePath(scope)}/releases/${encodeURIComponent(id)}`, {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  }).then((value) => requiredProjected(projectReleaseDetail(value), "Release detail"));
}

function fetchReleaseReadEndpoint<T>(
  scope: ReleaseScope,
  releaseId: string,
  endpoint: "readiness" | "impact" | "audit",
  options: ReleaseRequestOptions,
  projector: (value: unknown) => T,
): Promise<T> {
  const id = requiredText(releaseId, "releaseId", 64);
  const suffix = endpoint === "impact" || endpoint === "audit" ? `?${listQuery()}` : "";
  return request<unknown>(
    `${basePath(scope)}/releases/${encodeURIComponent(id)}/${endpoint}${suffix}`,
    {
      method: "GET",
      headers: authenticatedHeaders(scope),
      signal: options.signal,
    },
  ).then(projector);
}

export function fetchReleaseReadiness(
  scope: ReleaseScope,
  releaseId: string,
  options: ReleaseRequestOptions = {},
): Promise<ReleaseReadiness> {
  return fetchReleaseReadEndpoint(scope, releaseId, "readiness", options, projectReleaseReadiness);
}

export function fetchReleaseImpact(
  scope: ReleaseScope,
  releaseId: string,
  options: ReleaseRequestOptions = {},
): Promise<ReleaseImpact> {
  return fetchReleaseReadEndpoint(scope, releaseId, "impact", options, projectReleaseImpact);
}

export function fetchReleaseAudit(
  scope: ReleaseScope,
  releaseId: string,
  options: ReleaseRequestOptions = {},
): Promise<ReleaseAuditPage> {
  return fetchReleaseReadEndpoint(scope, releaseId, "audit", options, projectReleaseAuditPage);
}

export function captureReleaseCandidate(
  scope: ReleaseScope,
  input: CaptureReleaseInput,
  options: ReleaseRequestOptions = {},
): Promise<ReleaseMutationOutcome> {
  const errors = validateReleaseMutation("capture", input);
  if (Object.keys(errors).length > 0) throw new Error(Object.values(errors)[0]);
  return mutation(
    scope,
    `${basePath(scope)}/releases`,
    {
      expected_profile_revision: input.expectedProfileRevision,
      expected_ownership_revision: input.expectedOwnershipRevision,
      expected_workspace_revision: input.expectedWorkspaceRevision,
      expected_mutation_generation: input.expectedMutationGeneration,
      expected_serving_generation: input.expectedServingGeneration,
      reason: input.reason.trim(),
    },
    options,
  ).then(projectReleaseMutationOutcome);
}

export function promoteRelease(
  scope: ReleaseScope,
  releaseId: string,
  input: PromoteReleaseInput,
  options: ReleaseRequestOptions = {},
): Promise<ReleaseMutationOutcome> {
  const errors = validateReleaseMutation("promote", { ...input, releaseId });
  if (Object.keys(errors).length > 0) throw new Error(Object.values(errors)[0]);
  return mutation(
    scope,
    `${basePath(scope)}/releases/${encodeURIComponent(requiredText(releaseId, "releaseId", 64))}/promote`,
    {
      channel_id: requiredText(input.channelId, "channelId", 128),
      expected_channel_revision: input.expectedChannelRevision,
      expected_profile_revision: input.expectedProfileRevision,
      expected_ownership_revision: input.expectedOwnershipRevision,
      expected_workspace_revision: input.expectedWorkspaceRevision,
      expected_serving_generation: input.expectedServingGeneration,
      reason: input.reason.trim(),
    },
    options,
  ).then(projectReleaseMutationOutcome);
}

export function rollbackRelease(
  scope: ReleaseScope,
  channelId: string,
  input: RollbackReleaseInput,
  options: ReleaseRequestOptions = {},
): Promise<ReleaseMutationOutcome> {
  const errors = validateReleaseMutation("rollback", { ...input, channelId });
  if (Object.keys(errors).length > 0) throw new Error(Object.values(errors)[0]);
  return mutation(
    scope,
    `${basePath(scope)}/channels/${encodeURIComponent(requiredText(channelId, "channelId", 128))}/rollback`,
    {
      target_release_id: requiredText(input.targetReleaseId, "targetReleaseId", 64),
      expected_channel_revision: input.expectedChannelRevision,
      expected_serving_generation: input.expectedServingGeneration,
      reason: input.reason.trim(),
    },
    options,
  ).then(projectReleaseMutationOutcome);
}

export const releaseApi: ReleaseApi = {
  fetchChannels: fetchReleaseChannels,
  fetchHistory: fetchReleaseHistory,
  fetchDetail: fetchReleaseDetail,
  fetchReadiness: fetchReleaseReadiness,
  fetchImpact: fetchReleaseImpact,
  fetchAudit: fetchReleaseAudit,
  capture: captureReleaseCandidate,
  promote: promoteRelease,
  rollback: rollbackRelease,
};
