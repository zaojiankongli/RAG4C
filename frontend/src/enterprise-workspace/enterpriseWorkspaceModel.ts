export type WorkspaceStatus = "active" | "archived" | (string & {});
export type WorkspaceEnvironment = "development" | "testing" | "production" | (string & {});
export type WorkspaceRole = "owner" | "admin" | "editor" | "viewer" | (string & {});
export type WorkspaceMemberStatus = "active" | "removed" | (string & {});
export type WorkspaceDatasetBindingKind = "primary" | "shared" | (string & {});
export type WorkspaceAuthorizationState = "workspace_authorization_not_enforced" | (string & {});

export interface EnterpriseWorkspace {
  id: string;
  tenant_id: string;
  code: string;
  name: string;
  description: string;
  status: WorkspaceStatus;
  environment: WorkspaceEnvironment;
  is_default: boolean;
  revision: number;
  member_count: number | null;
  dataset_count: number | null;
  primary_dataset_count: number | null;
  actor_role: WorkspaceRole | null;
  created_at: string | null;
  updated_at: string | null;
  archived_at: string | null;
}

export interface WorkspaceMember {
  account_id: string;
  name: string;
  email: string;
  role: WorkspaceRole;
  status: WorkspaceMemberStatus;
  revision: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface WorkspaceDatasetBinding {
  dataset_id: string;
  name: string;
  binding_kind: WorkspaceDatasetBindingKind;
  status: WorkspaceMemberStatus;
  revision: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface WorkspaceEvidence {
  workspace_count: number | null;
  active_count: number | null;
  default_workspace_id: string | null;
  primary_dataset_binding_count: number | null;
  authorization_state: WorkspaceAuthorizationState;
}

export interface WorkspacePage {
  items: EnterpriseWorkspace[];
  count: number | null;
  next_cursor: string | null;
  evidence: WorkspaceEvidence;
}

export interface WorkspaceMemberPage {
  items: WorkspaceMember[];
  count: number | null;
  next_cursor: string | null;
  authorization_state: WorkspaceAuthorizationState;
}

export interface WorkspaceDatasetPage {
  items: WorkspaceDatasetBinding[];
  count: number | null;
  next_cursor: string | null;
  authorization_state: WorkspaceAuthorizationState;
}

export interface WorkspaceDetail {
  workspace: EnterpriseWorkspace;
  members: WorkspaceMemberPage;
  datasets: WorkspaceDatasetPage;
  authorization_state: WorkspaceAuthorizationState;
}

const AUTHORIZATION_NOT_ENFORCED = "workspace_authorization_not_enforced" as const;

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function integer(value: unknown, minimum = 0): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= minimum ? value : null;
}

function nullableDate(value: unknown): string | null {
  return text(value);
}

function authorizationState(value: unknown): WorkspaceAuthorizationState {
  return text(value) ?? AUTHORIZATION_NOT_ENFORCED;
}

export function projectWorkspace(value: unknown): EnterpriseWorkspace | null {
  const source = record(value);
  if (!source) return null;
  const id = text(source.id ?? source.workspace_id);
  const tenantId = text(source.tenant_id);
  const code = text(source.code);
  const name = text(source.name);
  const status = text(source.status);
  const environment = text(source.environment);
  const revision = integer(source.revision, 1);
  if (!id || !tenantId || !code || !name || !status || !environment || revision === null) {
    return null;
  }
  return {
    id,
    tenant_id: tenantId,
    code,
    name,
    description: text(source.description) ?? "",
    status,
    environment,
    is_default: source.is_default === true || source.default_slot === "default",
    revision,
    member_count: integer(source.member_count),
    dataset_count: integer(source.dataset_count),
    primary_dataset_count: integer(source.primary_dataset_count),
    actor_role: text(source.actor_role ?? source.workspace_role),
    created_at: nullableDate(source.created_at),
    updated_at: nullableDate(source.updated_at),
    archived_at: nullableDate(source.archived_at),
  };
}

export function projectWorkspaceMember(value: unknown): WorkspaceMember | null {
  const source = record(value);
  if (!source) return null;
  const accountId = text(source.account_id);
  const role = text(source.role);
  const status = text(source.status);
  const revision = integer(source.revision, 1);
  if (!accountId || !role || !status || revision === null) return null;
  return {
    account_id: accountId,
    name: text(source.name ?? source.account_name) ?? accountId,
    email: text(source.email) ?? "",
    role,
    status,
    revision,
    created_at: nullableDate(source.created_at),
    updated_at: nullableDate(source.updated_at),
  };
}

export function projectWorkspaceDataset(value: unknown): WorkspaceDatasetBinding | null {
  const source = record(value);
  if (!source) return null;
  const datasetId = text(source.dataset_id ?? source.id);
  const bindingKind = text(source.binding_kind);
  const status = text(source.status);
  const revision = integer(source.revision, 1);
  if (!datasetId || !bindingKind || !status || revision === null) return null;
  return {
    dataset_id: datasetId,
    name: text(source.name ?? source.dataset_name) ?? datasetId,
    binding_kind: bindingKind,
    status,
    revision,
    created_at: nullableDate(source.created_at),
    updated_at: nullableDate(source.updated_at),
  };
}

function pageItems<T>(value: unknown, projector: (item: unknown) => T | null): T[] {
  const source = record(value);
  const items = Array.isArray(source?.items) ? source.items : [];
  return items.flatMap((item) => {
    const projected = projector(item);
    return projected ? [projected] : [];
  });
}

function pageCount(value: unknown): number | null {
  return integer(record(value)?.count);
}

function pageCursor(value: unknown): string | null {
  return text(record(value)?.next_cursor ?? record(value)?.next_before_id);
}

function strictDatasetPageCursor(value: unknown): string | null {
  const source = record(value);
  if (!source) throw new Error("Workspace Dataset page response is missing authority");
  const hasCursor =
    Object.prototype.hasOwnProperty.call(source, "next_cursor") ||
    Object.prototype.hasOwnProperty.call(source, "next_before_id");
  if (!hasCursor) throw new Error("Workspace Dataset page response is missing next_cursor");
  const rawCursor = Object.prototype.hasOwnProperty.call(source, "next_cursor")
    ? source.next_cursor
    : source.next_before_id;
  if (rawCursor === null) return null;
  const cursor = text(rawCursor);
  if (!cursor) throw new Error("Workspace Dataset page response has an invalid next_cursor");
  return cursor;
}

export function projectWorkspaceEvidence(
  value: unknown,
  fallbackCount: number | null,
): WorkspaceEvidence {
  const source = record(value);
  return {
    workspace_count: integer(source?.workspace_count) ?? integer(source?.count) ?? fallbackCount,
    active_count: integer(source?.active_count),
    default_workspace_id: text(source?.default_workspace_id),
    primary_dataset_binding_count: integer(source?.primary_dataset_binding_count),
    authorization_state: authorizationState(source?.authorization_state),
  };
}

export function projectWorkspacePage(value: unknown): WorkspacePage {
  const source = record(value) ?? {};
  const items = pageItems(source, projectWorkspace);
  const count = pageCount(source);
  return {
    items,
    count,
    next_cursor: pageCursor(source),
    evidence: projectWorkspaceEvidence(source.evidence ?? source, count),
  };
}

export function projectWorkspaceMemberPage(value: unknown): WorkspaceMemberPage {
  const source = record(value) ?? {};
  const items = pageItems(source, projectWorkspaceMember);
  return {
    items,
    count: pageCount(source),
    next_cursor: pageCursor(source),
    authorization_state: authorizationState(source.authorization_state),
  };
}

export function projectWorkspaceDatasetPage(value: unknown): WorkspaceDatasetPage {
  const source = record(value) ?? {};
  const items = pageItems(source, projectWorkspaceDataset);
  return {
    items,
    count: pageCount(source),
    next_cursor: strictDatasetPageCursor(source),
    authorization_state: authorizationState(source.authorization_state),
  };
}

const EMPTY_MEMBER_PAGE: WorkspaceMemberPage = {
  items: [],
  count: 0,
  next_cursor: null,
  authorization_state: AUTHORIZATION_NOT_ENFORCED,
};
const EMPTY_DATASET_PAGE: WorkspaceDatasetPage = {
  items: [],
  count: 0,
  next_cursor: null,
  authorization_state: AUTHORIZATION_NOT_ENFORCED,
};

export function projectWorkspaceDetail(value: unknown): WorkspaceDetail {
  const source = record(value) ?? {};
  const workspace = projectWorkspace(source.workspace ?? source.item ?? source);
  if (!workspace) throw new Error("workspace detail response is missing workspace authority");
  const members = source.members
    ? projectWorkspaceMemberPage(source.members)
    : { ...EMPTY_MEMBER_PAGE };
  const datasets = source.datasets
    ? projectWorkspaceDatasetPage(source.datasets)
    : { ...EMPTY_DATASET_PAGE };
  return {
    workspace,
    members,
    datasets,
    authorization_state: authorizationState(
      source.authorization_state ??
        record(source.authorization)?.state ??
        members.authorization_state ??
        datasets.authorization_state,
    ),
  };
}

export const WORKSPACE_AUTHORIZATION_NOT_ENFORCED = AUTHORIZATION_NOT_ENFORCED;
