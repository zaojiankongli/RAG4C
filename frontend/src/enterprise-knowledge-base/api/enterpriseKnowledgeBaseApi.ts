import { request } from "../../api/client";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  projectKnowledgeBaseDependencies,
  projectKnowledgeBaseDetail,
  projectKnowledgeBaseMutationResponse,
  projectKnowledgeBasePage,
  type KnowledgeBaseDependencies,
  type KnowledgeBaseDetail,
  type KnowledgeBaseMutationResponse,
  type KnowledgeBasePage,
} from "../enterpriseKnowledgeBaseModel";

export interface KnowledgeBaseRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export interface KnowledgeBaseListQuery {
  workspaceId?: string;
  status?: string;
  keyword?: string;
  cursor?: string;
  limit?: number;
}

export interface ApplicationReferenceInput {
  reason: string;
}

export interface RemoveApplicationReferenceInput {
  expectedRevision: number;
  reason: string;
}

export interface WorkspaceTransferInput {
  /** Canonical camelCase names for the backend target_* wire contract. */
  targetWorkspaceId?: string;
  expectedDatasetProfileRevision?: number;
  /** Legacy aliases are accepted at the call boundary while callers migrate. */
  workspaceId?: string;
  expectedProfileRevision?: number;
  expectedOwnershipRevision: number;
  expectedSourceWorkspaceRevision: number;
  expectedTargetWorkspaceRevision: number;
  reason: string;
}

function authenticatedHeaders(scope: EnterpriseScope): Record<string, string> {
  const tenantId = scope.tenantId.trim();
  const actorToken = scope.actorToken.trim();
  if (!tenantId) throw new Error("tenantId is required for Knowledge Base requests");
  if (!actorToken) throw new Error("actorToken is required for Knowledge Base requests");
  return { "X-RAG4C-Tenant": tenantId, Authorization: `Bearer ${actorToken}` };
}

export function createKnowledgeBaseIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) return `rag4c-knowledge-base-${cryptoApi.randomUUID()}`;
  if (cryptoApi?.getRandomValues) {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    return `rag4c-knowledge-base-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
  }
  return `rag4c-knowledge-base-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function mutationHeaders(
  scope: EnterpriseScope,
  options: KnowledgeBaseRequestOptions,
): Record<string, string> {
  const supplied = options.idempotencyKey?.trim();
  if (options.idempotencyKey !== undefined && !supplied) {
    throw new Error("idempotencyKey is required for Knowledge Base mutations");
  }
  const key = supplied || createKnowledgeBaseIdempotencyKey();
  if (key.length > 128) throw new Error("idempotencyKey must be at most 128 characters");
  return { ...authenticatedHeaders(scope), "Idempotency-Key": key };
}

function required(value: string, name: string, maximum: number): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${name} is required`);
  if (normalized.length > maximum) throw new Error(`${name} must be at most ${maximum} characters`);
  return normalized;
}

function positiveRevision(value: number, name: string): number {
  if (!Number.isInteger(value) || value < 1) throw new Error(`${name} must be a positive integer`);
  return value;
}

function datasetPath(datasetId: string): string {
  return `/api/enterprise/knowledge-bases/${encodeURIComponent(required(datasetId, "datasetId", 128))}`;
}

function applicationPath(appId: string, datasetId: string): string {
  return `/api/enterprise/apps/${encodeURIComponent(required(appId, "appId", 128))}/knowledge-bases/${encodeURIComponent(required(datasetId, "datasetId", 128))}`;
}

function mutation<T>(
  scope: EnterpriseScope,
  path: string,
  method: "POST" | "DELETE",
  body: Record<string, unknown>,
  options: KnowledgeBaseRequestOptions,
): Promise<T> {
  return request<T>(path, {
    method,
    headers: mutationHeaders(scope, options),
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

export async function fetchEnterpriseKnowledgeBases(
  scope: EnterpriseScope,
  query: KnowledgeBaseListQuery = {},
  options: KnowledgeBaseRequestOptions = {},
): Promise<KnowledgeBasePage> {
  const params = new URLSearchParams();
  if (query.workspaceId?.trim()) params.set("workspace_id", query.workspaceId.trim());
  if (query.status?.trim() && query.status !== "all") params.set("status", query.status.trim());
  if (query.keyword?.trim()) params.set("q", query.keyword.trim());
  if (query.cursor?.trim()) params.set("cursor", query.cursor.trim());
  const limit = query.limit ?? 50;
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw new Error("limit must be between 1 and 100");
  }
  params.set("limit", String(limit));
  const result = await request<unknown>(`/api/enterprise/knowledge-bases?${params.toString()}`, {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  });
  return projectKnowledgeBasePage(result);
}

export async function fetchEnterpriseKnowledgeBaseDetail(
  scope: EnterpriseScope,
  datasetId: string,
  options: KnowledgeBaseRequestOptions = {},
): Promise<KnowledgeBaseDetail> {
  const result = await request<unknown>(datasetPath(datasetId), {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  });
  return projectKnowledgeBaseDetail(result);
}

export async function fetchEnterpriseKnowledgeBaseDependencies(
  scope: EnterpriseScope,
  datasetId: string,
  options: KnowledgeBaseRequestOptions = {},
): Promise<KnowledgeBaseDependencies> {
  const result = await request<unknown>(`${datasetPath(datasetId)}/dependencies`, {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  });
  return projectKnowledgeBaseDependencies(result);
}

export async function createApplicationReference(
  scope: EnterpriseScope,
  appId: string,
  datasetId: string,
  input: ApplicationReferenceInput,
  options: KnowledgeBaseRequestOptions = {},
): Promise<KnowledgeBaseMutationResponse> {
  const body = { reason: required(input.reason, "reason", 512) };
  const result = await mutation<unknown>(
    scope,
    applicationPath(appId, datasetId),
    "POST",
    body,
    options,
  );
  return projectKnowledgeBaseMutationResponse(result);
}

export async function removeApplicationReference(
  scope: EnterpriseScope,
  appId: string,
  datasetId: string,
  input: RemoveApplicationReferenceInput,
  options: KnowledgeBaseRequestOptions = {},
): Promise<KnowledgeBaseMutationResponse> {
  const body = {
    expected_revision: positiveRevision(input.expectedRevision, "expected_revision"),
    reason: required(input.reason, "reason", 512),
  };
  const result = await mutation<unknown>(
    scope,
    applicationPath(appId, datasetId),
    "DELETE",
    body,
    options,
  );
  return projectKnowledgeBaseMutationResponse(result);
}

export async function transferKnowledgeBaseOwnership(
  scope: EnterpriseScope,
  datasetId: string,
  input: WorkspaceTransferInput,
  options: KnowledgeBaseRequestOptions = {},
): Promise<KnowledgeBaseMutationResponse> {
  const targetWorkspaceId = input.targetWorkspaceId ?? input.workspaceId ?? "";
  const expectedDatasetProfileRevision =
    input.expectedDatasetProfileRevision ?? input.expectedProfileRevision;
  const result = await mutation<unknown>(
    scope,
    `${datasetPath(datasetId)}/workspace-transfer`,
    "POST",
    {
      target_workspace_id: required(targetWorkspaceId, "targetWorkspaceId", 128),
      expected_dataset_profile_revision: positiveRevision(
        expectedDatasetProfileRevision as number,
        "expectedDatasetProfileRevision",
      ),
      expected_ownership_revision: positiveRevision(
        input.expectedOwnershipRevision,
        "expectedOwnershipRevision",
      ),
      expected_source_workspace_revision: positiveRevision(
        input.expectedSourceWorkspaceRevision,
        "expectedSourceWorkspaceRevision",
      ),
      expected_target_workspace_revision: positiveRevision(
        input.expectedTargetWorkspaceRevision,
        "expectedTargetWorkspaceRevision",
      ),
      reason: required(input.reason, "reason", 512),
    },
    options,
  );
  return projectKnowledgeBaseMutationResponse(result);
}

export const fetchKnowledgeBaseRegistry = fetchEnterpriseKnowledgeBases;
export const fetchKnowledgeBaseDetail = fetchEnterpriseKnowledgeBaseDetail;
export const fetchKnowledgeBaseDependencies = fetchEnterpriseKnowledgeBaseDependencies;
export const addApplicationReference = createApplicationReference;
export const transferOwnership = transferKnowledgeBaseOwnership;
