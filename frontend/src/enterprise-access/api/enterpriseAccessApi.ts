import { request } from "../../api/client";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  projectAccessGrantPage,
  projectDatasetAccessGrantMutation,
  projectDatasetAccessSummary,
  projectEnterpriseGroupMemberPage,
  projectEnterpriseGroupPage,
  projectEnterpriseInvitationMutation,
  projectEnterpriseInvitationPage,
  projectOrganizationUnitPage,
  type AcceptEnterpriseInvitationInput,
  type CreateDatasetAccessGrantInput,
  type CreateEnterpriseInvitationInput,
  type DatasetAccessGrant,
  type DatasetAccessGrantPage,
  type DatasetAccessGrantRevisionInput,
  type PersistentDatasetAccessSummary,
  type EnterpriseAccessCursor,
  type EnterpriseGroupMemberPage,
  type EnterpriseGroupPage,
  type EnterpriseInvitationMutationResult,
  type EnterpriseInvitationPage,
  type OrganizationUnitPage,
  type ResendEnterpriseInvitationInput,
  type RevokeEnterpriseInvitationInput,
  type UpdateDatasetAccessGrantRoleInput,
} from "../enterpriseAccessModel";

export interface EnterpriseAccessListQuery {
  beforeId?: EnterpriseAccessCursor;
  limit?: number;
}

export interface EnterpriseAccessRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}

function authenticatedHeaders(scope: EnterpriseScope): Record<string, string> {
  const tenantId = scope.tenantId.trim();
  const actorToken = scope.actorToken.trim();
  if (!tenantId) throw new Error("tenantId is required for enterprise access requests");
  if (!actorToken) throw new Error("actorToken is required for enterprise access requests");
  return {
    "X-RAG4C-Tenant": tenantId,
    Authorization: `Bearer ${actorToken}`,
  };
}

export interface DisableDatasetAclInput {
  expected_acl_revision: number;
  reason: string;
}

export function createEnterpriseIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) return `rag4c-acl-${cryptoApi.randomUUID()}`;
  if (cryptoApi?.getRandomValues) {
    const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
    return `rag4c-acl-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
  }
  return `rag4c-acl-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function mutationIdempotencyKey(options: EnterpriseAccessRequestOptions): string {
  if (options.idempotencyKey !== undefined) {
    const key = options.idempotencyKey.trim();
    if (!key) throw new Error("idempotencyKey is required for access mutations");
    if (key.length > 128) throw new Error("idempotencyKey must be at most 128 characters");
    return key;
  }
  return createEnterpriseIdempotencyKey();
}

function mutationHeaders(
  scope: EnterpriseScope,
  options: EnterpriseAccessRequestOptions,
): Record<string, string> {
  return {
    ...authenticatedHeaders(scope),
    "Idempotency-Key": mutationIdempotencyKey(options),
  };
}

function listQuery(query: EnterpriseAccessListQuery): string {
  const limit = query.limit ?? 50;
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
    throw new Error("limit must be an integer between 1 and 200");
  }
  const params = new URLSearchParams();
  if (query.beforeId !== undefined) params.set("before_id", String(query.beforeId));
  params.set("limit", String(limit));
  return params.toString();
}

function getPage<T>(
  scope: EnterpriseScope,
  path: string,
  query: EnterpriseAccessListQuery,
  options: EnterpriseAccessRequestOptions,
  project: (input: unknown) => T,
): Promise<T> {
  const headers = authenticatedHeaders(scope);
  const queryString = listQuery(query);
  return request<unknown>(`${path}?${queryString}`, {
    method: "GET",
    headers,
    signal: options.signal,
  }).then(project);
}

export async function fetchOrganizationUnits(
  scope: EnterpriseScope,
  query: EnterpriseAccessListQuery = {},
  options: EnterpriseAccessRequestOptions = {},
): Promise<OrganizationUnitPage> {
  return getPage(
    scope,
    "/api/enterprise/organization-units",
    query,
    options,
    projectOrganizationUnitPage,
  );
}

export async function fetchEnterpriseGroups(
  scope: EnterpriseScope,
  query: EnterpriseAccessListQuery = {},
  options: EnterpriseAccessRequestOptions = {},
): Promise<EnterpriseGroupPage> {
  return getPage(scope, "/api/enterprise/groups", query, options, projectEnterpriseGroupPage);
}

export async function fetchEnterpriseGroupMembers(
  scope: EnterpriseScope,
  groupId: string,
  query: EnterpriseAccessListQuery = {},
  options: EnterpriseAccessRequestOptions = {},
): Promise<EnterpriseGroupMemberPage> {
  const normalizedGroupId = groupId.trim();
  if (!normalizedGroupId) throw new Error("groupId is required for group member requests");
  return getPage(
    scope,
    `/api/enterprise/groups/${encodeURIComponent(normalizedGroupId)}/members`,
    query,
    options,
    projectEnterpriseGroupMemberPage,
  );
}

export async function fetchEnterpriseInvitations(
  scope: EnterpriseScope,
  query: EnterpriseAccessListQuery = {},
  options: EnterpriseAccessRequestOptions = {},
): Promise<EnterpriseInvitationPage> {
  return getPage(
    scope,
    "/api/enterprise/invitations",
    query,
    options,
    projectEnterpriseInvitationPage,
  );
}

export async function fetchDatasetAccessSummary(
  scope: EnterpriseScope,
  options: EnterpriseAccessRequestOptions = {},
): Promise<PersistentDatasetAccessSummary> {
  const datasetId = scope.datasetId?.trim();
  if (!datasetId) throw new Error("datasetId is required for access summary requests");
  const headers = authenticatedHeaders(scope);
  return request<unknown>(`/api/knowledge-bases/${encodeURIComponent(datasetId)}/access-summary`, {
    method: "GET",
    headers,
    signal: options.signal,
  }).then(projectDatasetAccessSummary);
}

export async function fetchDatasetAccessGrants(
  scope: EnterpriseScope,
  query: EnterpriseAccessListQuery = {},
  options: EnterpriseAccessRequestOptions = {},
): Promise<DatasetAccessGrantPage> {
  const datasetId = scope.datasetId?.trim();
  if (!datasetId) throw new Error("datasetId is required for access grant requests");
  return getPage(
    scope,
    `/api/knowledge-bases/${encodeURIComponent(datasetId)}/access-grants`,
    query,
    options,
    projectAccessGrantPage,
  );
}

function datasetPath(scope: EnterpriseScope): string {
  const datasetId = scope.datasetId?.trim();
  if (!datasetId) throw new Error("datasetId is required for access grant requests");
  return `/api/knowledge-bases/${encodeURIComponent(datasetId)}/access-grants`;
}

function grantPath(scope: EnterpriseScope, grantId: string): string {
  const normalizedGrantId = grantId.trim();
  if (!normalizedGrantId) throw new Error("grantId is required for access grant mutations");
  return `${datasetPath(scope)}/${encodeURIComponent(normalizedGrantId)}`;
}

function requiredReason(reason: string): string {
  const normalizedReason = reason.trim();
  if (!normalizedReason) throw new Error("reason is required for access grant mutations");
  return normalizedReason;
}

function requiredRevision(revision: number): number {
  if (!Number.isInteger(revision) || revision < 1) {
    throw new Error("revision must be an integer at least 1 for access grant mutations");
  }
  return revision;
}

function requiredSubjectId(subjectId: string): string {
  const normalizedSubjectId = subjectId.trim();
  if (!normalizedSubjectId) throw new Error("subject_id is required for access grant mutations");
  return normalizedSubjectId;
}

function mutateDatasetAccessGrant(
  scope: EnterpriseScope,
  path: string,
  method: "PATCH" | "POST",
  body: unknown,
  options: EnterpriseAccessRequestOptions,
): Promise<DatasetAccessGrant> {
  return request<unknown>(path, {
    method,
    headers: mutationHeaders(scope, options),
    body: JSON.stringify(body),
    signal: options.signal,
  }).then(projectDatasetAccessGrantMutation);
}

export async function createDatasetAccessGrant(
  scope: EnterpriseScope,
  payload: CreateDatasetAccessGrantInput,
  options: EnterpriseAccessRequestOptions = {},
): Promise<DatasetAccessGrant> {
  const subjectId = requiredSubjectId(payload.subject_id);
  const reason = requiredReason(payload.reason);
  if (!payload.subject_type) throw new Error("subject_type is required for access grant mutations");
  if (!payload.role) throw new Error("role is required for access grant mutations");
  return mutateDatasetAccessGrant(
    scope,
    datasetPath(scope),
    "POST",
    {
      subject_type: payload.subject_type,
      subject_id: subjectId,
      role: payload.role,
      reason,
    },
    options,
  );
}

export async function updateDatasetAccessGrantRole(
  scope: EnterpriseScope,
  grantId: string,
  payload: UpdateDatasetAccessGrantRoleInput,
  options: EnterpriseAccessRequestOptions = {},
): Promise<DatasetAccessGrant> {
  return mutateDatasetAccessGrant(
    scope,
    grantPath(scope, grantId),
    "PATCH",
    {
      role: payload.role,
      reason: requiredReason(payload.reason),
      revision: requiredRevision(payload.revision),
    },
    options,
  );
}

export async function revokeDatasetAccessGrant(
  scope: EnterpriseScope,
  grantId: string,
  payload: DatasetAccessGrantRevisionInput,
  options: EnterpriseAccessRequestOptions = {},
): Promise<DatasetAccessGrant> {
  return mutateDatasetAccessGrant(
    scope,
    `${grantPath(scope, grantId)}/revoke`,
    "POST",
    { reason: requiredReason(payload.reason), revision: requiredRevision(payload.revision) },
    options,
  );
}

export async function resumeDatasetAccessGrant(
  scope: EnterpriseScope,
  grantId: string,
  payload: DatasetAccessGrantRevisionInput,
  options: EnterpriseAccessRequestOptions = {},
): Promise<DatasetAccessGrant> {
  return mutateDatasetAccessGrant(
    scope,
    `${grantPath(scope, grantId)}/resume`,
    "POST",
    { reason: requiredReason(payload.reason), revision: requiredRevision(payload.revision) },
    options,
  );
}
export async function disableDatasetAcl(
  scope: EnterpriseScope,
  payload: DisableDatasetAclInput,
  options: EnterpriseAccessRequestOptions = {},
): Promise<PersistentDatasetAccessSummary> {
  const datasetId = scope.datasetId?.trim();
  if (!datasetId) throw new Error("datasetId is required for access control mutations");
  const revision = requiredRevision(payload.expected_acl_revision);
  const reason = requiredReason(payload.reason);
  return request<unknown>(
    `/api/knowledge-bases/${encodeURIComponent(datasetId)}/access-control/disable`,
    {
      method: "POST",
      headers: mutationHeaders(scope, options),
      body: JSON.stringify({ expected_acl_revision: revision, reason }),
      signal: options.signal,
    },
  ).then(projectDatasetAccessSummary);
}

function requiredInvitationEmail(email: string): string {
  const normalized = email.trim().toLocaleLowerCase("en-US");
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalized)) {
    throw new Error("a valid invitation email is required");
  }
  return normalized;
}

function requiredExpiresInDays(value: number): number {
  if (!Number.isInteger(value) || value < 1 || value > 90) {
    throw new Error("expires_in_days must be an integer between 1 and 90");
  }
  return value;
}

function requiredInvitationToken(token: string): string {
  const normalized = token.trim();
  if (!normalized) throw new Error("invite_token is required");
  return normalized;
}

function invitationPath(invitationId: string): string {
  const normalized = invitationId.trim();
  if (!normalized) throw new Error("invitationId is required");
  return `/api/enterprise/invitations/${encodeURIComponent(normalized)}`;
}

function mutateEnterpriseInvitation(
  scope: EnterpriseScope,
  path: string,
  body: unknown,
  options: EnterpriseAccessRequestOptions,
): Promise<EnterpriseInvitationMutationResult> {
  return request<unknown>(path, {
    method: "POST",
    headers: mutationHeaders(scope, options),
    body: JSON.stringify(body),
    signal: options.signal,
  }).then(projectEnterpriseInvitationMutation);
}

export function createEnterpriseInvitation(
  scope: EnterpriseScope,
  payload: CreateEnterpriseInvitationInput,
  options: EnterpriseAccessRequestOptions = {},
): Promise<EnterpriseInvitationMutationResult> {
  return mutateEnterpriseInvitation(
    scope,
    "/api/enterprise/invitations",
    {
      email: requiredInvitationEmail(payload.email),
      role: payload.role,
      expires_in_days: requiredExpiresInDays(payload.expires_in_days),
      reason: requiredReason(payload.reason),
    },
    options,
  );
}

export function resendEnterpriseInvitation(
  scope: EnterpriseScope,
  invitationId: string,
  payload: ResendEnterpriseInvitationInput,
  options: EnterpriseAccessRequestOptions = {},
): Promise<EnterpriseInvitationMutationResult> {
  return mutateEnterpriseInvitation(
    scope,
    `${invitationPath(invitationId)}/resend`,
    {
      revision: requiredRevision(payload.revision),
      expires_in_days: requiredExpiresInDays(payload.expires_in_days),
      reason: requiredReason(payload.reason),
    },
    options,
  );
}

export function revokeEnterpriseInvitation(
  scope: EnterpriseScope,
  invitationId: string,
  payload: RevokeEnterpriseInvitationInput,
  options: EnterpriseAccessRequestOptions = {},
): Promise<EnterpriseInvitationMutationResult> {
  return mutateEnterpriseInvitation(
    scope,
    `${invitationPath(invitationId)}/revoke`,
    {
      revision: requiredRevision(payload.revision),
      reason: requiredReason(payload.reason),
    },
    options,
  );
}

export function acceptEnterpriseInvitation(
  scope: EnterpriseScope,
  payload: AcceptEnterpriseInvitationInput,
  options: EnterpriseAccessRequestOptions = {},
): Promise<EnterpriseInvitationMutationResult> {
  return mutateEnterpriseInvitation(
    scope,
    "/api/enterprise/invitations/accept",
    { invite_token: requiredInvitationToken(payload.invite_token) },
    options,
  );
}
