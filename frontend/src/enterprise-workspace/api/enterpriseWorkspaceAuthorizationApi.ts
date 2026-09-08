import { request } from "../../api/client";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  projectWorkspaceAuthorizationImpactResponse,
  projectWorkspaceAuthorizationPolicyResponse,
  type WorkspaceAuthorizationImpactResponse,
  type WorkspaceAuthorizationMode,
  type WorkspaceAuthorizationPolicyResponse,
} from "../enterpriseWorkspaceAuthorizationModel";

export interface WorkspaceAuthorizationRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export interface WorkspaceAuthorizationModeChangeInput {
  expected_revision: number;
  target_mode: WorkspaceAuthorizationMode;
  reason: string;
}

function authenticatedHeaders(scope: EnterpriseScope): Record<string, string> {
  const tenantId = scope.tenantId.trim();
  const actorToken = scope.actorToken.trim();
  if (!tenantId) throw new Error("tenantId is required for workspace authorization requests");
  if (!actorToken) throw new Error("actorToken is required for workspace authorization requests");
  return { "X-RAG4C-Tenant": tenantId, Authorization: `Bearer ${actorToken}` };
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

function workspaceAuthorizationPath(workspaceId: string): string {
  return `/api/enterprise/workspaces/${encodeURIComponent(required(workspaceId, "workspaceId", 128))}/authorization`;
}

export function createWorkspaceAuthorizationIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) return `rag4c-workspace-authorization-${cryptoApi.randomUUID()}`;
  return `rag4c-workspace-authorization-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export async function fetchWorkspaceAuthorizationPolicy(
  scope: EnterpriseScope,
  workspaceId: string,
  options: WorkspaceAuthorizationRequestOptions = {},
): Promise<WorkspaceAuthorizationPolicyResponse> {
  const value = await request<unknown>(workspaceAuthorizationPath(workspaceId), {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  });
  return projectWorkspaceAuthorizationPolicyResponse(value);
}

export async function fetchWorkspaceAuthorizationImpact(
  scope: EnterpriseScope,
  workspaceId: string,
  options: WorkspaceAuthorizationRequestOptions = {},
): Promise<WorkspaceAuthorizationImpactResponse> {
  const datasetId = required(scope.datasetId ?? "", "datasetId", 64);
  const params = new URLSearchParams({ dataset_id: datasetId });
  const value = await request<unknown>(
    `${workspaceAuthorizationPath(workspaceId)}/impact?${params.toString()}`,
    {
      method: "GET",
      headers: authenticatedHeaders(scope),
      signal: options.signal,
    },
  );
  return projectWorkspaceAuthorizationImpactResponse(value);
}

export async function updateWorkspaceAuthorizationMode(
  scope: EnterpriseScope,
  workspaceId: string,
  input: WorkspaceAuthorizationModeChangeInput,
  options: WorkspaceAuthorizationRequestOptions = {},
): Promise<WorkspaceAuthorizationPolicyResponse> {
  const targetMode = input.target_mode;
  if (targetMode !== "disabled" && targetMode !== "shadow" && targetMode !== "enforced") {
    throw new Error("target_mode is invalid");
  }
  const suppliedKey = options.idempotencyKey?.trim();
  if (options.idempotencyKey !== undefined && !suppliedKey) {
    throw new Error("idempotencyKey is required for workspace authorization mutations");
  }
  const idempotencyKey = suppliedKey || createWorkspaceAuthorizationIdempotencyKey();
  if (idempotencyKey.length > 128) throw new Error("idempotencyKey must be at most 128 characters");
  const value = await request<unknown>(workspaceAuthorizationPath(workspaceId), {
    method: "PATCH",
    headers: {
      ...authenticatedHeaders(scope),
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify({
      expected_revision: positiveRevision(input.expected_revision, "expected_revision"),
      target_mode: targetMode,
      reason: required(input.reason, "reason", 512),
    }),
    signal: options.signal,
  });
  return projectWorkspaceAuthorizationPolicyResponse(value);
}
