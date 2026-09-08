export type TenantRole = "owner" | "admin" | "editor" | "member" | (string & {});

export type EnterpriseMemberStatus = "active" | "suspended" | "invited" | "unknown" | (string & {});

export type EnterpriseCapabilityState = "ready" | "limited" | "unavailable";

export interface EnterpriseCapability {
  state: EnterpriseCapabilityState;
  label: string;
  reason: string | null;
}

export type EnterpriseCapabilities = Record<string, EnterpriseCapability>;

export interface EnterpriseScope {
  tenantId: string;
  datasetId?: string;
  actorToken: string;
}

export interface EnterpriseTenant {
  id: string;
  name: string;
  plan: string;
  status: string;
  quota_documents: number;
  quota_chunks: number;
  doc_count: number;
  chunk_count: number;
}

export interface EnterpriseActor {
  id: string;
  name: string;
  email: string;
  role: TenantRole;
}

export interface EnterpriseContext {
  tenant: EnterpriseTenant;
  actor: EnterpriseActor;
  member_count: number;
  dataset_count: number;
  effective_permissions: string[];
  role_permissions: Record<string, string[]>;
  capabilities: EnterpriseCapabilities;
}

export interface EnterpriseMember {
  membership_id: number;
  account_id: string;
  name: string;
  email: string;
  role: TenantRole;
  joined_at: string;
  /** Server-owned membership lifecycle status; old responses project to "unknown". */
  status: EnterpriseMemberStatus;
  /** Optimistic-concurrency revision, absent when the server has not exposed it. */
  revision?: number;
  suspended_at?: string | null;
  updated_at?: string | null;
}

export interface EnterpriseMemberListResponse {
  items: EnterpriseMember[];
  count: number;
  next_before_id: number | null;
}

export interface EnterpriseMemberFilters {
  query?: string;
  role?: string;
  beforeId?: number;
  limit?: number;
}

export interface EnterpriseMemberMutationRequest {
  role?: TenantRole;
  status?: EnterpriseMemberStatus;
  reason: string;
  expected_revision: number;
}

export interface EnterpriseAuditMetadata {
  event_id?: string;
  sequence?: number;
  actor_id?: string;
  actor_name?: string;
  action?: string;
  resource_type?: string;
  resource_id?: string;
  request_id?: string;
  occurred_at?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface EnterpriseMemberMutationResponse {
  member: EnterpriseMember;
  audit_metadata?: EnterpriseAuditMetadata | null;
  audit?: EnterpriseAuditMetadata | null;
  [key: string]: unknown;
}

export interface EnterpriseAuditEvent {
  sequence: number;
  event_id?: string;
  actor_id?: string;
  actor_name?: string;
  action?: string;
  resource_type?: string;
  resource_id?: string;
  request_id?: string;
  occurred_at?: string;
  created_at?: string;
  [key: string]: unknown;
}

export interface EnterpriseAuditEventListResponse {
  items: EnterpriseAuditEvent[];
  next_before_sequence: number | null;
}

export interface EnterpriseAuditFilters {
  beforeSequence?: number;
  limit?: number;
  actor?: string;
  action?: string;
  resource?: string;
  request?: string;
  time?: string;
}

export interface DatasetAccessSummary {
  dataset_id: string;
  owner_id: string | null;
  visibility: "private" | "tenant" | "public" | (string & {});
  enforcement_mode: "tenant_role" | "tenant_role_fallback" | "dataset_acl" | (string & {});
  actor_role: TenantRole;
  dataset_role?: "viewer" | "editor" | "manager" | null;
  bypass_reason?: string | null;
  matched_grants?: string[];
  effective_permissions: string[];
  dataset_acl_supported: boolean;
  group_grants_supported: boolean;
  organization_inheritance_supported: boolean;
  warnings: string[];
}

export type EnterpriseLoadState =
  "identity-missing" | "loading" | "ready" | "unauthorized" | "forbidden" | "unavailable" | "error";

export type EnterpriseResourceState =
  "loading" | "ready" | "unauthorized" | "forbidden" | "conflict" | "unavailable" | "error";

export interface EnterpriseResourceError {
  state: Exclude<EnterpriseResourceState, "loading" | "ready">;
  title: string;
  description: string;
  canRetry: boolean;
  status?: number;
  detail?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function normalizedString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function normalizedRevision(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;
}

function normalizedSequence(value: unknown): number | undefined {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : undefined;
}

export function projectEnterpriseMemberStatus(value: unknown): EnterpriseMemberStatus {
  return normalizedString(value)?.trim() ?? "unknown";
}

/**
 * Project a server member without filling fields that the server did not return.
 * The only compatibility default is status="unknown" because callers need an
 * explicit lifecycle state instead of treating a legacy response as active.
 */
export function projectEnterpriseMember(input: unknown): EnterpriseMember {
  const source = isRecord(input) ? input : {};
  const projected = {
    ...source,
    status: projectEnterpriseMemberStatus(source.status),
  } as EnterpriseMember;

  const revision = normalizedRevision(source.revision);
  if (revision !== undefined) projected.revision = revision;
  else if ("revision" in source) projected.revision = undefined;

  if ("suspended_at" in source) {
    projected.suspended_at =
      source.suspended_at === null ? null : normalizedString(source.suspended_at);
  }
  if ("updated_at" in source) {
    projected.updated_at = source.updated_at === null ? null : normalizedString(source.updated_at);
  }
  return projected;
}

export function projectEnterpriseMemberListResponse(input: unknown): EnterpriseMemberListResponse {
  const source = isRecord(input) ? input : {};
  const rawItems = Array.isArray(source.items) ? source.items : [];
  const items = rawItems.map(projectEnterpriseMember);
  const count =
    typeof source.count === "number" && Number.isFinite(source.count) ? source.count : items.length;
  const nextBeforeId =
    typeof source.next_before_id === "number" && Number.isInteger(source.next_before_id)
      ? source.next_before_id
      : null;
  return {
    ...source,
    items,
    count,
    next_before_id: nextBeforeId,
  } as EnterpriseMemberListResponse;
}

export function projectEnterpriseMemberMutationResponse(
  input: unknown,
): EnterpriseMemberMutationResponse {
  const source = isRecord(input) ? input : {};
  const rawMember = source.member ?? source.item ?? source.updated_member;
  if (!rawMember) throw new Error("enterprise member mutation response is missing member");
  return {
    ...source,
    member: projectEnterpriseMember(rawMember),
  } as EnterpriseMemberMutationResponse;
}

export function projectEnterpriseAuditEvent(input: unknown): EnterpriseAuditEvent | null {
  if (!isRecord(input)) return null;
  const sequence = normalizedSequence(input.sequence);
  if (sequence === undefined) return null;
  return { ...input, sequence } as EnterpriseAuditEvent;
}

export function projectEnterpriseAuditEventListResponse(
  input: unknown,
): EnterpriseAuditEventListResponse {
  const source = isRecord(input) ? input : {};
  const seen = new Set<number>();
  const items: EnterpriseAuditEvent[] = [];
  for (const rawItem of Array.isArray(source.items) ? source.items : []) {
    const item = projectEnterpriseAuditEvent(rawItem);
    if (!item || seen.has(item.sequence)) continue;
    seen.add(item.sequence);
    items.push(item);
  }
  return {
    ...source,
    items,
    next_before_sequence: normalizedSequence(source.next_before_sequence) ?? null,
  } as EnterpriseAuditEventListResponse;
}
