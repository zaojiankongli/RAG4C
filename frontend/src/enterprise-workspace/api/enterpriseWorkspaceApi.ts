import { request } from "../../api/client";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  projectWorkspace,
  projectWorkspaceDatasetPage,
  projectWorkspaceDetail,
  projectWorkspaceMemberPage,
  projectWorkspacePage,
  type EnterpriseWorkspace,
  type WorkspaceDatasetBindingKind,
  type WorkspaceEnvironment,
  type WorkspaceMemberPage,
  type WorkspaceDatasetPage,
  type WorkspacePage,
  type WorkspaceRole,
} from "../enterpriseWorkspaceModel";

export interface WorkspaceRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export interface WorkspaceListQuery {
  status?: "all" | "active" | "archived";
  cursor?: string;
  limit?: number;
}

export interface WorkspaceRelationListQuery {
  status?: "active" | "removed";
  cursor?: string;
  limit?: number;
}

export interface CreateWorkspaceInput {
  code: string;
  name: string;
  description?: string;
  environment: WorkspaceEnvironment;
  reason: string;
}

export interface UpdateWorkspaceInput {
  revision: number;
  name?: string;
  description?: string;
  environment?: WorkspaceEnvironment;
  reason: string;
}

export interface WorkspaceRevisionInput {
  revision: number;
  reason: string;
}

export interface AddWorkspaceMemberInput {
  account_id: string;
  role: WorkspaceRole;
  reason: string;
}

export interface UpdateWorkspaceMemberInput {
  revision: number;
  role: WorkspaceRole;
  reason: string;
}

export interface BindWorkspaceDatasetInput {
  dataset_id: string;
  binding_kind: WorkspaceDatasetBindingKind;
  reason: string;
}

function authenticatedHeaders(scope: EnterpriseScope): Record<string, string> {
  const tenantId = scope.tenantId.trim();
  const actorToken = scope.actorToken.trim();
  if (!tenantId) throw new Error("tenantId is required for workspace requests");
  if (!actorToken) throw new Error("actorToken is required for workspace requests");
  return { "X-RAG4C-Tenant": tenantId, Authorization: `Bearer ${actorToken}` };
}

export function createWorkspaceIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) return `rag4c-workspace-${cryptoApi.randomUUID()}`;
  if (cryptoApi?.getRandomValues) {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    return `rag4c-workspace-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
  }
  return `rag4c-workspace-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function mutationHeaders(
  scope: EnterpriseScope,
  options: WorkspaceRequestOptions,
): Record<string, string> {
  const supplied = options.idempotencyKey?.trim();
  if (options.idempotencyKey !== undefined && !supplied) {
    throw new Error("idempotencyKey is required for workspace mutations");
  }
  const key = supplied || createWorkspaceIdempotencyKey();
  if (key.length > 128) throw new Error("idempotencyKey must be at most 128 characters");
  return { ...authenticatedHeaders(scope), "Idempotency-Key": key };
}

function required(value: string, name: string, maximum = 512): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`${name} is required`);
  if (normalized.length > maximum) throw new Error(`${name} must be at most ${maximum} characters`);
  return normalized;
}

function optionalText(value: string | undefined, name: string, maximum: number): string {
  const normalized = value?.trim() ?? "";
  if (normalized.length > maximum) {
    throw new Error(`${name} must be at most ${maximum} characters`);
  }
  return normalized;
}

function revision(value: number): number {
  if (!Number.isInteger(value) || value < 1) throw new Error("revision must be a positive integer");
  return value;
}

function workspacePath(workspaceId: string): string {
  return `/api/enterprise/workspaces/${encodeURIComponent(required(workspaceId, "workspaceId", 128))}`;
}

function mutation<T>(
  path: string,
  method: "POST" | "PATCH",
  scope: EnterpriseScope,
  body: Record<string, unknown>,
  options: WorkspaceRequestOptions,
): Promise<T> {
  return request<T>(path, {
    method,
    headers: mutationHeaders(scope, options),
    body: JSON.stringify(body),
    signal: options.signal,
  });
}

function workspaceFromMutation(value: unknown): EnterpriseWorkspace {
  const source =
    typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
  const projected = projectWorkspace(source.workspace ?? source.item ?? value);
  if (!projected) throw new Error("workspace mutation response is missing workspace");
  return projected;
}

export async function fetchEnterpriseWorkspaces(
  scope: EnterpriseScope,
  query: WorkspaceListQuery = {},
  options: WorkspaceRequestOptions = {},
): Promise<WorkspacePage> {
  const params = new URLSearchParams();
  if (query.status && query.status !== "all") params.set("status", query.status);
  if (query.cursor?.trim()) params.set("cursor", query.cursor.trim());
  const limit = query.limit ?? 50;
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
    throw new Error("limit must be between 1 and 200");
  }
  params.set("limit", String(limit));
  const result = await request<unknown>(`/api/enterprise/workspaces?${params.toString()}`, {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  });
  return projectWorkspacePage(result);
}

export async function fetchEnterpriseWorkspaceDetail(
  scope: EnterpriseScope,
  workspaceId: string,
  options: WorkspaceRequestOptions = {},
) {
  const result = await request<unknown>(workspacePath(workspaceId), {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  });
  return projectWorkspaceDetail(result);
}

export async function fetchWorkspaceMembers(
  scope: EnterpriseScope,
  workspaceId: string,
  query: WorkspaceRelationListQuery = {},
  options: WorkspaceRequestOptions = {},
): Promise<WorkspaceMemberPage> {
  const params = new URLSearchParams();
  if (query.status) params.set("status", query.status);
  if (query.cursor?.trim()) params.set("cursor", query.cursor.trim());
  params.set("limit", String(query.limit ?? 50));
  const value = await request<unknown>(`${workspacePath(workspaceId)}/members?${params}`, {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  });
  return projectWorkspaceMemberPage(value);
}

export async function fetchWorkspaceDatasets(
  scope: EnterpriseScope,
  workspaceId: string,
  query: WorkspaceRelationListQuery = {},
  options: WorkspaceRequestOptions = {},
): Promise<WorkspaceDatasetPage> {
  const params = new URLSearchParams();
  if (query.status) params.set("status", query.status);
  if (query.cursor?.trim()) params.set("cursor", query.cursor.trim());
  params.set("limit", String(query.limit ?? 50));
  const value = await request<unknown>(`${workspacePath(workspaceId)}/datasets?${params}`, {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  });
  return projectWorkspaceDatasetPage(value);
}

export async function createEnterpriseWorkspace(
  scope: EnterpriseScope,
  input: CreateWorkspaceInput,
  options: WorkspaceRequestOptions = {},
): Promise<EnterpriseWorkspace> {
  return workspaceFromMutation(
    await mutation<unknown>(
      "/api/enterprise/workspaces",
      "POST",
      scope,
      {
        code: required(input.code, "code", 64),
        name: required(input.name, "name", 128),
        description: optionalText(input.description, "description", 512),
        environment: required(input.environment, "environment", 16),
        reason: required(input.reason, "reason"),
      },
      options,
    ),
  );
}

export async function updateEnterpriseWorkspace(
  scope: EnterpriseScope,
  workspaceId: string,
  input: UpdateWorkspaceInput,
  options: WorkspaceRequestOptions = {},
): Promise<EnterpriseWorkspace> {
  return workspaceFromMutation(
    await mutation<unknown>(
      workspacePath(workspaceId),
      "PATCH",
      scope,
      {
        revision: revision(input.revision),
        ...(input.name !== undefined ? { name: required(input.name, "name", 128) } : {}),
        ...(input.description !== undefined
          ? { description: optionalText(input.description, "description", 512) }
          : {}),
        ...(input.environment !== undefined
          ? { environment: required(input.environment, "environment", 16) }
          : {}),
        reason: required(input.reason, "reason"),
      },
      options,
    ),
  );
}

export async function archiveWorkspace(
  scope: EnterpriseScope,
  workspaceId: string,
  input: WorkspaceRevisionInput,
  options: WorkspaceRequestOptions = {},
): Promise<EnterpriseWorkspace> {
  return workspaceFromMutation(
    await mutation<unknown>(
      `${workspacePath(workspaceId)}/archive`,
      "POST",
      scope,
      { revision: revision(input.revision), reason: required(input.reason, "reason") },
      options,
    ),
  );
}

export function addWorkspaceMember(
  scope: EnterpriseScope,
  workspaceId: string,
  input: AddWorkspaceMemberInput,
  options: WorkspaceRequestOptions = {},
): Promise<unknown> {
  return mutation(
    `${workspacePath(workspaceId)}/members`,
    "POST",
    scope,
    {
      account_id: required(input.account_id, "account_id", 64),
      role: required(input.role, "role", 16),
      reason: required(input.reason, "reason"),
    },
    options,
  );
}

export function updateWorkspaceMember(
  scope: EnterpriseScope,
  workspaceId: string,
  accountId: string,
  input: UpdateWorkspaceMemberInput,
  options: WorkspaceRequestOptions = {},
): Promise<unknown> {
  return mutation(
    `${workspacePath(workspaceId)}/members/${encodeURIComponent(required(accountId, "accountId", 64))}`,
    "PATCH",
    scope,
    {
      revision: revision(input.revision),
      role: required(input.role, "role", 16),
      reason: required(input.reason, "reason"),
    },
    options,
  );
}

export function removeWorkspaceMember(
  scope: EnterpriseScope,
  workspaceId: string,
  accountId: string,
  input: WorkspaceRevisionInput,
  options: WorkspaceRequestOptions = {},
): Promise<unknown> {
  return mutation(
    `${workspacePath(workspaceId)}/members/${encodeURIComponent(required(accountId, "accountId", 64))}/remove`,
    "POST",
    scope,
    { revision: revision(input.revision), reason: required(input.reason, "reason") },
    options,
  );
}

export function bindWorkspaceDataset(
  scope: EnterpriseScope,
  workspaceId: string,
  input: BindWorkspaceDatasetInput,
  options: WorkspaceRequestOptions = {},
): Promise<unknown> {
  return mutation(
    `${workspacePath(workspaceId)}/datasets`,
    "POST",
    scope,
    {
      dataset_id: required(input.dataset_id, "dataset_id", 64),
      binding_kind: required(input.binding_kind, "binding_kind", 16),
      reason: required(input.reason, "reason"),
    },
    options,
  );
}

export function removeWorkspaceDataset(
  scope: EnterpriseScope,
  workspaceId: string,
  datasetId: string,
  input: WorkspaceRevisionInput,
  options: WorkspaceRequestOptions = {},
): Promise<unknown> {
  return mutation(
    `${workspacePath(workspaceId)}/datasets/${encodeURIComponent(required(datasetId, "datasetId", 64))}/remove`,
    "POST",
    scope,
    { revision: revision(input.revision), reason: required(input.reason, "reason") },
    options,
  );
}
