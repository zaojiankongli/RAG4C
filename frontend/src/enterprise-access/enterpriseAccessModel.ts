import type { DatasetAccessSummary } from "../enterprise-admin/model";

export type EnterpriseAccessCursor = string | number;

export interface EnterpriseAccessPage<T> {
  items: T[];
  count: number;
  next_before_id: EnterpriseAccessCursor | null;
}

export interface OrganizationUnit {
  id: string;
  parent_id: string | null;
  name: string;
  code: string;
  status: string;
  member_count: number;
  child_count: number;
}

export interface EnterpriseGroup {
  id: string;
  name: string;
  description: string;
  status: string;
  member_count: number;
}

export interface EnterpriseGroupMember {
  id: string;
  account_id: string | null;
  name: string | null;
  email: string | null;
  role: string | null;
  status: string | null;
}

export type InvitationActor = string | { id?: string; name?: string; email?: string } | null;

export interface EnterpriseInvitation {
  id: string;
  email: string;
  role: string;
  status: string;
  expires_at: string;
  invited_by: InvitationActor;
  revision?: number;
  send_count?: number;
  last_sent_at?: string;
  created_at?: string;
  updated_at?: string;
  accepted_at?: string;
  accepted_by?: InvitationActor;
  revoked_at?: string;
  revoked_by?: InvitationActor;
}

export type EnterpriseInvitationRole = "owner" | "admin" | "editor" | "member";

export interface CreateEnterpriseInvitationInput {
  email: string;
  role: EnterpriseInvitationRole;
  expires_in_days: number;
  reason: string;
}

export interface ResendEnterpriseInvitationInput {
  revision: number;
  expires_in_days: number;
  reason: string;
}

export interface RevokeEnterpriseInvitationInput {
  revision: number;
  reason: string;
}

export interface AcceptEnterpriseInvitationInput {
  invite_token: string;
}

export interface EnterpriseInvitationDelivery {
  state: "manual_link_required" | (string & {});
  invite_token: string;
  expires_at: string;
}

export interface EnterpriseInvitationMutationResult {
  invitation: EnterpriseInvitation;
  delivery: EnterpriseInvitationDelivery | null;
}

export type DatasetAclMode = "tenant_role" | "dataset_acl";

export interface PersistentDatasetAccessSummary extends DatasetAccessSummary {
  acl_mode?: DatasetAclMode | null;
  acl_revision?: number | null;
  acl_enabled_at?: string | null;
  acl_enabled_by?: string | null;
}

export interface DatasetAccessGrant {
  id: string;
  dataset_id: string;
  subject_type: string;
  subject_id: string;
  subject_name: string;
  role: string;
  status: string;
  revision: number;
}

export type DatasetAccessGrantSubjectType = "account" | "group" | "organization_unit";
export type DatasetAccessGrantRole = "viewer" | "editor" | "manager";

export interface CreateDatasetAccessGrantInput {
  subject_type: DatasetAccessGrantSubjectType;
  subject_id: string;
  role: DatasetAccessGrantRole;
  reason: string;
}

export interface DatasetAccessGrantRevisionInput {
  reason: string;
  revision: number;
}

export interface UpdateDatasetAccessGrantRoleInput extends DatasetAccessGrantRevisionInput {
  role: DatasetAccessGrantRole;
}

export type OrganizationUnitPage = EnterpriseAccessPage<OrganizationUnit>;
export type EnterpriseGroupPage = EnterpriseAccessPage<EnterpriseGroup>;
export type EnterpriseGroupMemberPage = EnterpriseAccessPage<EnterpriseGroupMember>;
export type EnterpriseInvitationPage = EnterpriseAccessPage<EnterpriseInvitation>;
export type DatasetAccessGrantPage = EnterpriseAccessPage<DatasetAccessGrant>;

export type EnterpriseAccessResourceStatus =
  | "idle"
  | "loading"
  | "ready"
  | "unauthorized"
  | "forbidden"
  | "unavailable"
  | "migration-required"
  | "error";

export interface EnterpriseAccessResourceError {
  state: Exclude<EnterpriseAccessResourceStatus, "idle" | "loading" | "ready">;
  title: string;
  description: string;
  canRetry: boolean;
  status?: number;
  code?: string;
  detail?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function nonNegativeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function projectCursor(value: unknown): EnterpriseAccessCursor | null {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return null;
}

function projectPage<T>(
  input: unknown,
  projectItem: (value: unknown) => T | null,
): EnterpriseAccessPage<T> {
  const source = isRecord(input) ? input : {};
  const items = (Array.isArray(source.items) ? source.items : [])
    .map(projectItem)
    .filter((item): item is T => item !== null);
  const count = nonNegativeInteger(source.count) ?? items.length;
  return {
    items,
    count,
    next_before_id: projectCursor(source.next_before_id),
  };
}

export function projectOrganizationUnit(input: unknown): OrganizationUnit | null {
  if (!isRecord(input)) return null;
  const id = stringValue(input.id);
  const name = stringValue(input.name);
  const code = stringValue(input.code);
  const status = stringValue(input.status);
  const memberCount = nonNegativeInteger(input.member_count);
  const childCount = nonNegativeInteger(input.child_count);
  if (!id || !name || !code || !status || memberCount === null || childCount === null) return null;
  return {
    id,
    parent_id: input.parent_id === null ? null : stringValue(input.parent_id),
    name,
    code,
    status,
    member_count: memberCount,
    child_count: childCount,
  };
}

export function projectEnterpriseGroup(input: unknown): EnterpriseGroup | null {
  if (!isRecord(input)) return null;
  const id = stringValue(input.id);
  const name = stringValue(input.name);
  const status = stringValue(input.status);
  const memberCount = nonNegativeInteger(input.member_count);
  if (!id || !name || !status || memberCount === null) return null;
  return {
    id,
    name,
    description: stringValue(input.description) ?? "",
    status,
    member_count: memberCount,
  };
}

export function projectEnterpriseGroupMember(input: unknown): EnterpriseGroupMember | null {
  if (!isRecord(input)) return null;
  const accountId = stringValue(input.account_id);
  const id =
    stringValue(input.id) ??
    (typeof input.membership_id === "number" ? String(input.membership_id) : null) ??
    accountId;
  if (!id) return null;
  return {
    id,
    account_id: accountId,
    name: stringValue(input.name),
    email: stringValue(input.email),
    role: stringValue(input.role),
    status: stringValue(input.status),
  };
}

function projectInvitationActor(value: unknown): InvitationActor {
  if (typeof value === "string") return value;
  if (!isRecord(value)) return null;
  const actor: { id?: string; name?: string; email?: string } = {};
  const id = stringValue(value.id);
  const name = stringValue(value.name);
  const email = stringValue(value.email);
  if (id) actor.id = id;
  if (name) actor.name = name;
  if (email) actor.email = email;
  return Object.keys(actor).length ? actor : null;
}

export function projectEnterpriseInvitation(input: unknown): EnterpriseInvitation {
  const source = isRecord(input) ? input : {};
  const invitation: EnterpriseInvitation = {
    id: stringValue(source.id) ?? "",
    email: stringValue(source.email) ?? "",
    role: stringValue(source.role) ?? "",
    status: stringValue(source.status) ?? "",
    expires_at: stringValue(source.expires_at) ?? "",
    invited_by: projectInvitationActor(source.invited_by),
  };
  const revision = nonNegativeInteger(source.revision);
  const sendCount = nonNegativeInteger(source.send_count);
  const lifecycleStrings = [
    "last_sent_at",
    "created_at",
    "updated_at",
    "accepted_at",
    "revoked_at",
  ] as const;
  if (revision !== null) invitation.revision = revision;
  if (sendCount !== null) invitation.send_count = sendCount;
  for (const key of lifecycleStrings) {
    const value = stringValue(source[key]);
    if (value) invitation[key] = value;
  }
  const acceptedBy = projectInvitationActor(source.accepted_by);
  const revokedBy = projectInvitationActor(source.revoked_by);
  if (acceptedBy) invitation.accepted_by = acceptedBy;
  if (revokedBy) invitation.revoked_by = revokedBy;
  return invitation;
}

export function projectEnterpriseInvitationMutation(
  input: unknown,
): EnterpriseInvitationMutationResult {
  const source = isRecord(input) ? input : {};
  const candidate = source.invitation ?? source.item ?? input;
  const invitation = projectEnterpriseInvitation(candidate);
  if (!invitation.id || !invitation.email || !invitation.role || !invitation.status) {
    throw new Error("enterprise invitation mutation response is missing invitation facts");
  }
  const deliverySource = isRecord(source.delivery) ? source.delivery : null;
  const deliveryState = deliverySource ? stringValue(deliverySource.state) : null;
  const inviteToken = deliverySource ? stringValue(deliverySource.invite_token) : null;
  const deliveryExpiresAt = deliverySource ? stringValue(deliverySource.expires_at) : null;
  return {
    invitation,
    delivery:
      deliveryState && inviteToken && deliveryExpiresAt
        ? { state: deliveryState, invite_token: inviteToken, expires_at: deliveryExpiresAt }
        : null,
  };
}

function projectValidInvitation(input: unknown): EnterpriseInvitation | null {
  const item = projectEnterpriseInvitation(input);
  return item.id && item.email && item.role && item.status && item.expires_at ? item : null;
}

export function projectDatasetAccessGrant(input: unknown): DatasetAccessGrant | null {
  if (!isRecord(input)) return null;
  const id = stringValue(input.id);
  const datasetId = stringValue(input.dataset_id);
  const subjectType = stringValue(input.subject_type);
  const subjectId = stringValue(input.subject_id);
  const subjectName = stringValue(input.subject_name);
  const role = stringValue(input.role);
  const status = stringValue(input.status);
  const revision = nonNegativeInteger(input.revision);
  if (
    !id ||
    !datasetId ||
    !subjectType ||
    !subjectId ||
    !subjectName ||
    !role ||
    !status ||
    revision === null
  ) {
    return null;
  }
  return {
    id,
    dataset_id: datasetId,
    subject_type: subjectType,
    subject_id: subjectId,
    subject_name: subjectName,
    role,
    status,
    revision,
  };
}

export function projectOrganizationUnitPage(input: unknown): OrganizationUnitPage {
  return projectPage(input, projectOrganizationUnit);
}

export function projectEnterpriseGroupPage(input: unknown): EnterpriseGroupPage {
  return projectPage(input, projectEnterpriseGroup);
}

export function projectEnterpriseGroupMemberPage(input: unknown): EnterpriseGroupMemberPage {
  return projectPage(input, projectEnterpriseGroupMember);
}

export function projectEnterpriseInvitationPage(input: unknown): EnterpriseInvitationPage {
  return projectPage(input, projectValidInvitation);
}

export function projectAccessGrantPage(input: unknown): DatasetAccessGrantPage {
  return projectPage(input, projectDatasetAccessGrant);
}

export function projectDatasetAccessSummary(input: unknown): PersistentDatasetAccessSummary {
  const source = isRecord(input) ? input : {};
  const candidate = isRecord(source.access_summary) ? source.access_summary : source;
  const aclMode =
    candidate.acl_mode === "tenant_role" || candidate.acl_mode === "dataset_acl"
      ? candidate.acl_mode
      : null;
  const aclRevision =
    typeof candidate.acl_revision === "number" &&
    Number.isInteger(candidate.acl_revision) &&
    candidate.acl_revision > 0
      ? candidate.acl_revision
      : null;
  const candidateAclEnabledAt = stringValue(candidate.acl_enabled_at);
  const aclEnabledAt =
    candidateAclEnabledAt && !Number.isNaN(Date.parse(candidateAclEnabledAt))
      ? candidateAclEnabledAt
      : null;
  const aclEnabledBy = stringValue(candidate.acl_enabled_by);
  return {
    ...(candidate as unknown as DatasetAccessSummary),
    acl_mode: aclMode,
    acl_revision: aclRevision,
    acl_enabled_at: aclEnabledAt,
    acl_enabled_by: aclEnabledBy,
  };
}

export function projectDatasetAccessGrantMutation(input: unknown): DatasetAccessGrant {
  const source = isRecord(input) ? input : {};
  const candidate = source.grant ?? source.item ?? source.updated_grant ?? input;
  const projected = projectDatasetAccessGrant(candidate);
  if (!projected) throw new Error("dataset access grant mutation response is missing grant");
  return projected;
}
