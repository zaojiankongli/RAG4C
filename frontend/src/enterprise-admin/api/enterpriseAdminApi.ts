import { request } from "../../api/client";
import {
  projectEnterpriseAuditEventListResponse,
  projectEnterpriseMemberListResponse,
  projectEnterpriseMemberMutationResponse,
  type DatasetAccessSummary,
  type EnterpriseAuditEventListResponse,
  type EnterpriseAuditFilters,
  type EnterpriseContext,
  type EnterpriseMemberFilters,
  type EnterpriseMemberListResponse,
  type EnterpriseMemberMutationRequest,
  type EnterpriseMemberMutationResponse,
  type EnterpriseScope,
  type EnterpriseMemberStatus,
  type TenantRole,
} from "../model/enterpriseAdminModel";

export interface EnterpriseRequestOptions {
  signal?: AbortSignal;
}

function authenticatedHeaders(scope: EnterpriseScope): Record<string, string> {
  const tenantId = scope.tenantId.trim();
  const actorToken = scope.actorToken.trim();
  if (!tenantId) throw new Error("tenantId is required for enterprise requests");
  if (!actorToken) throw new Error("actorToken is required for enterprise requests");
  return {
    "X-RAG4C-Tenant": tenantId,
    Authorization: `Bearer ${actorToken}`,
  };
}

function assertIntegerInRange(
  value: number,
  name: string,
  minimum: number,
  maximum?: number,
): void {
  if (!Number.isInteger(value) || value < minimum || (maximum !== undefined && value > maximum)) {
    const upper = maximum === undefined ? "" : ` and at most ${maximum}`;
    throw new Error(`${name} must be an integer at least ${minimum}${upper}`);
  }
}

function assertMemberMutationPayload(payload: EnterpriseMemberMutationRequest): void {
  if (typeof payload.reason !== "string" || !payload.reason.trim()) {
    throw new Error("reason is required for enterprise member mutations");
  }
  assertIntegerInRange(payload.expected_revision, "expected_revision", 1);
}

type EnterpriseMemberMutationAction = "role" | "suspend" | "resume";

function memberPath(accountId: string, action: EnterpriseMemberMutationAction): string {
  const normalizedAccountId = accountId.trim();
  if (!normalizedAccountId)
    throw new Error("account_id is required for enterprise member mutations");
  return `/api/enterprise/members/${encodeURIComponent(normalizedAccountId)}/${action}`;
}

export function fetchEnterpriseContext(
  scope: EnterpriseScope,
  options: EnterpriseRequestOptions = {},
): Promise<EnterpriseContext> {
  return request<EnterpriseContext>("/api/enterprise/context", {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  });
}

export function fetchEnterpriseMembers(
  scope: EnterpriseScope,
  filters: EnterpriseMemberFilters = {},
  options: EnterpriseRequestOptions = {},
): Promise<EnterpriseMemberListResponse> {
  const query = new URLSearchParams();
  const keyword = filters.query?.trim();
  const role = filters.role?.trim();
  if (keyword) query.set("q", keyword);
  if (role) query.set("role", role);
  if (filters.beforeId !== undefined) query.set("before_id", String(filters.beforeId));
  query.set("limit", String(filters.limit ?? 50));
  return request<unknown>(`/api/enterprise/members?${query.toString()}`, {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  }).then(projectEnterpriseMemberListResponse);
}

export function fetchDatasetAccessSummary(
  scope: EnterpriseScope,
  options: EnterpriseRequestOptions = {},
): Promise<DatasetAccessSummary> {
  const datasetId = scope.datasetId?.trim() ?? "";
  if (!datasetId) throw new Error("datasetId is required for access summary requests");
  return request<DatasetAccessSummary>(
    `/api/knowledge-bases/${encodeURIComponent(datasetId)}/access-summary`,
    {
      method: "GET",
      headers: authenticatedHeaders(scope),
      signal: options.signal,
    },
  );
}

export function patchEnterpriseMember(
  scope: EnterpriseScope,
  accountId: string,
  payload: EnterpriseMemberMutationRequest,
  options: EnterpriseRequestOptions = {},
): Promise<EnterpriseMemberMutationResponse> {
  assertMemberMutationPayload(payload);
  const hasRole = typeof payload.role === "string";
  const hasStatus = payload.status !== undefined;
  if (hasRole === hasStatus) {
    throw new Error("member mutation must specify exactly one of role or status");
  }

  const action: EnterpriseMemberMutationAction = hasRole
    ? "role"
    : payload.status === "suspended"
      ? "suspend"
      : "resume";
  const method = action === "role" ? "PATCH" : "POST";
  const body = hasRole
    ? {
        role: payload.role,
        reason: payload.reason.trim(),
        expected_revision: payload.expected_revision,
      }
    : { reason: payload.reason.trim(), expected_revision: payload.expected_revision };

  return request<unknown>(memberPath(accountId, action), {
    method,
    headers: authenticatedHeaders(scope),
    body: JSON.stringify(body),
    signal: options.signal,
  }).then(projectEnterpriseMemberMutationResponse);
}

/** Alias that reads naturally at call sites while retaining the PATCH contract name. */
export const updateEnterpriseMember = patchEnterpriseMember;

export function updateEnterpriseMemberRole(
  scope: EnterpriseScope,
  accountId: string,
  role: TenantRole,
  reason: string,
  expectedRevision: number,
  options: EnterpriseRequestOptions = {},
): Promise<EnterpriseMemberMutationResponse> {
  return patchEnterpriseMember(
    scope,
    accountId,
    { role, reason, expected_revision: expectedRevision },
    options,
  );
}

export function suspendEnterpriseMember(
  scope: EnterpriseScope,
  accountId: string,
  reason: string,
  expectedRevision: number,
  options: EnterpriseRequestOptions = {},
): Promise<EnterpriseMemberMutationResponse> {
  return patchEnterpriseMember(
    scope,
    accountId,
    {
      status: "suspended" satisfies EnterpriseMemberStatus,
      reason,
      expected_revision: expectedRevision,
    },
    options,
  );
}

export function restoreEnterpriseMember(
  scope: EnterpriseScope,
  accountId: string,
  reason: string,
  expectedRevision: number,
  options: EnterpriseRequestOptions = {},
): Promise<EnterpriseMemberMutationResponse> {
  return patchEnterpriseMember(
    scope,
    accountId,
    {
      status: "active" satisfies EnterpriseMemberStatus,
      reason,
      expected_revision: expectedRevision,
    },
    options,
  );
}

export function fetchEnterpriseAuditEvents(
  scope: EnterpriseScope,
  filters: EnterpriseAuditFilters = {},
  options: EnterpriseRequestOptions = {},
): Promise<EnterpriseAuditEventListResponse> {
  const limit = filters.limit ?? 50;
  assertIntegerInRange(limit, "limit", 1, 200);
  if (filters.beforeSequence !== undefined) {
    assertIntegerInRange(filters.beforeSequence, "before_sequence", 0);
  }

  const query = new URLSearchParams();
  if (filters.beforeSequence !== undefined) {
    query.set("before_sequence", String(filters.beforeSequence));
  }
  query.set("limit", String(limit));
  const filterValues: Array<
    [
      keyof Pick<EnterpriseAuditFilters, "actor" | "action" | "resource" | "request" | "time">,
      string,
    ]
  > = [
    ["actor", "actor"],
    ["action", "action"],
    ["resource", "resource"],
    ["request", "request"],
    ["time", "time"],
  ];
  for (const [field, queryKey] of filterValues) {
    const value = filters[field]?.trim();
    if (value) query.set(queryKey, value);
  }

  return request<unknown>(`/api/enterprise/audit-events?${query.toString()}`, {
    method: "GET",
    headers: authenticatedHeaders(scope),
    signal: options.signal,
  }).then(projectEnterpriseAuditEventListResponse);
}
