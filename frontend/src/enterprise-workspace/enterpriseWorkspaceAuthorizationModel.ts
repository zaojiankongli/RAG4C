export type WorkspaceAuthorizationMode = "disabled" | "shadow" | "enforced";
export const WORKSPACE_PERMISSION_MODEL_VERSION = 1;
const PERMISSION_MATRIX_FINGERPRINT_PATTERN = /^[0-9a-f]{64}$/i;
export type WorkspaceAuthorizationState =
  | "workspace_authorization_not_available"
  | "workspace_authorization_disabled"
  | "workspace_authorization_shadow"
  | "workspace_authorization_enforced";

export interface WorkspaceAuthorizationPolicy {
  id: string;
  tenant_id: string;
  workspace_id: string;
  mode: WorkspaceAuthorizationMode;
  permission_model_version: number;
  revision: number;
  created_at: string | null;
  created_by: string | null;
  updated_at: string | null;
  updated_by: string | null;
  enforced_at: string | null;
  enforced_by: string | null;
  disabled_at: string | null;
  disabled_by: string | null;
}

export interface WorkspaceAuthorizationApprovalPolicyEvidence {
  state: string;
  id: string;
  name: string;
  required_approvals: number;
  execution_adapter_status: string;
}

export interface WorkspaceAuthorizationAuditEvidence {
  sequence: number;
  action: string;
  resource_type: string;
  resource_id: string;
  actor_id: string | null;
  actor_name: string | null;
  occurred_at: string | null;
}

export interface WorkspaceAuthorizationEvidence {
  active_member_count: number | null;
  active_dataset_binding_count: number | null;
  catalog_revision: string | null;
  permission_matrix_fingerprint: string | null;
  matching_approval_policy: WorkspaceAuthorizationApprovalPolicyEvidence | null;
  latest_audit_event: WorkspaceAuthorizationAuditEvidence | null;
}

export interface WorkspaceAuthorizationPolicyResponse {
  policy: WorkspaceAuthorizationPolicy;
  evidence: WorkspaceAuthorizationEvidence;
}

export interface WorkspaceAuthorizationMatchedGrant {
  id: string;
  subject_type: string;
  subject_id: string;
  subject_name: string;
  role: string;
}

export interface WorkspaceAuthorizationRoleEvidence {
  workspace_id: string;
  workspace_name: string;
  role: string;
  binding_kind: string;
  policy_mode: WorkspaceAuthorizationMode;
  policy_revision: number;
}

export interface WorkspaceAuthorizationContribution extends WorkspaceAuthorizationRoleEvidence {
  permissions: string[];
}

export interface WorkspaceAuthorizationImpact {
  dataset_id: string;
  state: WorkspaceAuthorizationState;
  tenant_role: string | null;
  dataset_acl_role: string | null;
  matched_grants: WorkspaceAuthorizationMatchedGrant[];
  workspace_roles: WorkspaceAuthorizationRoleEvidence[];
  contributing_workspaces: WorkspaceAuthorizationContribution[];
  current_effective_permissions: string[];
  candidate_permissions: string[];
  would_grant_permissions: string[];
  granted_permissions: string[];
  warnings: string[];
}

export interface WorkspaceAuthorizationImpactResponse {
  impact: WorkspaceAuthorizationImpact;
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function requiredText(value: unknown, message: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(message);
  return value.trim();
}

function nullableText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function positiveInteger(value: unknown, message: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) throw new Error(message);
  return value;
}

export function isPermissionMatrixFingerprint(value: unknown): value is string {
  return typeof value === "string" && PERMISSION_MATRIX_FINGERPRINT_PATTERN.test(value.trim());
}

function permissionModelVersion(value: unknown): number {
  if (value !== WORKSPACE_PERMISSION_MODEL_VERSION) {
    throw new Error("workspace authorization permission model version must be v1");
  }
  return WORKSPACE_PERMISSION_MODEL_VERSION;
}

function nullablePermissionMatrixFingerprint(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (!isPermissionMatrixFingerprint(value)) {
    throw new Error("workspace authorization permission matrix fingerprint is invalid");
  }
  return value.trim();
}

function nonNegativeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

function mode(value: unknown): WorkspaceAuthorizationMode {
  if (value !== "disabled" && value !== "shadow" && value !== "enforced") {
    throw new Error("workspace authorization policy mode is invalid");
  }
  return value;
}

function state(value: unknown): WorkspaceAuthorizationState {
  if (
    value !== "workspace_authorization_not_available" &&
    value !== "workspace_authorization_disabled" &&
    value !== "workspace_authorization_shadow" &&
    value !== "workspace_authorization_enforced"
  ) {
    throw new Error("workspace authorization impact state is invalid");
  }
  return value;
}

function textArray(value: unknown, message: string): string[] {
  if (!Array.isArray(value)) throw new Error(message);
  return value.map((item) => requiredText(item, message));
}

function projectApprovalEvidence(
  value: unknown,
): WorkspaceAuthorizationApprovalPolicyEvidence | null {
  if (value === null || value === undefined) return null;
  const source = record(value);
  if (!source) throw new Error("workspace authorization approval evidence is invalid");
  return {
    state: requiredText(
      source.state ?? source.status,
      "workspace authorization approval state is invalid",
    ),
    id: requiredText(
      source.id ?? source.policy_id,
      "workspace authorization approval policy id is invalid",
    ),
    name: requiredText(
      source.name ?? source.policy_name,
      "workspace authorization approval policy name is invalid",
    ),
    required_approvals: positiveInteger(
      source.required_approvals,
      "workspace authorization approval count is invalid",
    ),
    execution_adapter_status: requiredText(
      source.execution_adapter_status,
      "workspace authorization approval adapter status is invalid",
    ),
  };
}

function projectAuditEvidence(value: unknown): WorkspaceAuthorizationAuditEvidence | null {
  if (value === null || value === undefined) return null;
  const source = record(value);
  if (!source) throw new Error("workspace authorization audit evidence is invalid");
  return {
    sequence: positiveInteger(source.sequence, "workspace authorization audit sequence is invalid"),
    action: requiredText(source.action, "workspace authorization audit action is invalid"),
    resource_type: requiredText(
      source.resource_type,
      "workspace authorization audit resource type is invalid",
    ),
    resource_id: requiredText(
      source.resource_id,
      "workspace authorization audit resource id is invalid",
    ),
    actor_id: nullableText(source.actor_id),
    actor_name: nullableText(source.actor_name ?? source.actor_name_snapshot),
    occurred_at: nullableText(source.occurred_at ?? source.created_at),
  };
}

export function projectWorkspaceAuthorizationPolicyResponse(
  value: unknown,
): WorkspaceAuthorizationPolicyResponse {
  const outer = record(value);
  const source = record(outer?.policy);
  if (!source) throw new Error("workspace authorization response is missing policy authority");
  const evidence = record(outer?.evidence) ?? {};
  return {
    policy: {
      id: requiredText(source.id, "workspace authorization policy id is invalid"),
      tenant_id: requiredText(source.tenant_id, "workspace authorization tenant id is invalid"),
      workspace_id: requiredText(
        source.workspace_id,
        "workspace authorization workspace id is invalid",
      ),
      mode: mode(source.mode),
      permission_model_version: permissionModelVersion(source.permission_model_version),
      revision: positiveInteger(
        source.revision,
        "workspace authorization policy revision is invalid",
      ),
      created_at: nullableText(source.created_at),
      created_by: nullableText(source.created_by),
      updated_at: nullableText(source.updated_at),
      updated_by: nullableText(source.updated_by),
      enforced_at: nullableText(source.enforced_at),
      enforced_by: nullableText(source.enforced_by),
      disabled_at: nullableText(source.disabled_at),
      disabled_by: nullableText(source.disabled_by),
    },
    evidence: {
      active_member_count: nonNegativeInteger(evidence.active_member_count),
      active_dataset_binding_count: nonNegativeInteger(evidence.active_dataset_binding_count),
      catalog_revision: nullableText(evidence.catalog_revision),
      permission_matrix_fingerprint: nullablePermissionMatrixFingerprint(
        evidence.permission_matrix_fingerprint,
      ),
      matching_approval_policy: projectApprovalEvidence(
        evidence.matching_approval_policy ?? evidence.approval_policy,
      ),
      latest_audit_event: projectAuditEvidence(evidence.latest_audit_event),
    },
  };
}

function projectMatchedGrant(value: unknown): WorkspaceAuthorizationMatchedGrant {
  const source = record(value);
  if (!source) throw new Error("workspace authorization matched grant is invalid");
  return {
    id: requiredText(source.id, "workspace authorization matched grant id is invalid"),
    subject_type: requiredText(
      source.subject_type,
      "workspace authorization matched grant subject type is invalid",
    ),
    subject_id: requiredText(
      source.subject_id,
      "workspace authorization matched grant subject id is invalid",
    ),
    subject_name:
      nullableText(source.subject_name) ??
      requiredText(
        source.subject_id,
        "workspace authorization matched grant subject id is invalid",
      ),
    role: requiredText(source.role, "workspace authorization matched grant role is invalid"),
  };
}

function projectRoleEvidence(value: unknown): WorkspaceAuthorizationRoleEvidence {
  const source = record(value);
  if (!source) throw new Error("workspace authorization role evidence is invalid");
  return {
    workspace_id: requiredText(
      source.workspace_id,
      "workspace authorization role workspace id is invalid",
    ),
    workspace_name:
      nullableText(source.workspace_name) ??
      requiredText(source.workspace_id, "workspace authorization role workspace id is invalid"),
    role: requiredText(source.role, "workspace authorization role is invalid"),
    binding_kind: requiredText(
      source.binding_kind,
      "workspace authorization binding kind is invalid",
    ),
    policy_mode: mode(source.policy_mode ?? source.mode),
    policy_revision: positiveInteger(
      source.policy_revision,
      "workspace authorization role policy revision is invalid",
    ),
  };
}

function projectContribution(value: unknown): WorkspaceAuthorizationContribution {
  const source = record(value);
  const roleEvidence = projectRoleEvidence(value);
  return {
    ...roleEvidence,
    permissions: textArray(
      source?.permissions,
      "workspace authorization contribution permissions are invalid",
    ),
  };
}

export function projectWorkspaceAuthorizationImpactResponse(
  value: unknown,
): WorkspaceAuthorizationImpactResponse {
  const outer = record(value);
  const source = record(outer?.impact);
  if (!source) throw new Error("workspace authorization response is missing impact authority");
  const matchedGrants = Array.isArray(source.matched_grants) ? source.matched_grants : null;
  const workspaceRoles = Array.isArray(source.workspace_roles) ? source.workspace_roles : null;
  const contributions = Array.isArray(source.contributing_workspaces)
    ? source.contributing_workspaces
    : null;
  if (!matchedGrants || !workspaceRoles || !contributions) {
    throw new Error("workspace authorization impact relation evidence is invalid");
  }
  return {
    impact: {
      dataset_id: requiredText(
        source.dataset_id,
        "workspace authorization impact dataset id is invalid",
      ),
      state: state(source.state),
      tenant_role: nullableText(source.tenant_role),
      dataset_acl_role: nullableText(source.dataset_acl_role),
      matched_grants: matchedGrants.map(projectMatchedGrant),
      workspace_roles: workspaceRoles.map(projectRoleEvidence),
      contributing_workspaces: contributions.map(projectContribution),
      current_effective_permissions: textArray(
        source.current_effective_permissions,
        "workspace authorization current permissions are invalid",
      ),
      candidate_permissions: textArray(
        source.candidate_permissions,
        "workspace authorization candidate permissions are invalid",
      ),
      would_grant_permissions: textArray(
        source.would_grant_permissions,
        "workspace authorization would-grant permissions are invalid",
      ),
      granted_permissions: textArray(
        source.granted_permissions,
        "workspace authorization granted permissions are invalid",
      ),
      warnings: textArray(source.warnings, "workspace authorization warnings are invalid"),
    },
  };
}

export function workspaceAuthorizationStateForMode(
  value: WorkspaceAuthorizationMode,
): WorkspaceAuthorizationState {
  return `workspace_authorization_${value}` as WorkspaceAuthorizationState;
}
