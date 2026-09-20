import {
  isPermissionMatrixFingerprint,
  WORKSPACE_PERMISSION_MODEL_VERSION,
  type WorkspaceAuthorizationApprovalPolicyEvidence,
  type WorkspaceAuthorizationMode,
  type WorkspaceAuthorizationPolicyResponse,
} from "./enterpriseWorkspaceAuthorizationModel";

export interface WorkspaceAuthorizationModeChangeValues {
  currentMode: WorkspaceAuthorizationMode;
  targetMode: WorkspaceAuthorizationMode;
  policyRevision: number;
  workspaceRevision: number;
  reason: string;
}

export interface WorkspaceAuthorizationModeChangeErrors {
  targetMode?: string;
  policyRevision?: string;
  workspaceRevision?: string;
  reason?: string;
}

export function requiresWorkspaceAuthorizationApproval(
  targetMode: WorkspaceAuthorizationMode,
  approval: WorkspaceAuthorizationApprovalPolicyEvidence | null | undefined,
): boolean {
  return Boolean(
    approval?.state === "active" && (targetMode === "disabled" || targetMode === "enforced"),
  );
}

export function highRiskWorkspaceAuthorizationEvidenceError({
  authority,
  expectedTenantId,
  expectedWorkspaceId,
}: {
  authority: WorkspaceAuthorizationPolicyResponse;
  expectedTenantId: string;
  expectedWorkspaceId: string;
}): string | null {
  if (authority.policy.permission_model_version !== WORKSPACE_PERMISSION_MODEL_VERSION) {
    return "permission model 必须为 v1，无法提交高风险变更。";
  }
  if (authority.policy.tenant_id !== expectedTenantId.trim()) {
    return "授权事实的 tenant id 与当前租户不一致，无法提交高风险变更。";
  }
  if (authority.policy.workspace_id !== expectedWorkspaceId.trim()) {
    return "授权事实的 Workspace id 与当前详情不一致，无法提交高风险变更。";
  }
  if (!authority.evidence.catalog_revision?.trim()) {
    return "缺少 catalog revision，无法提交高风险变更。";
  }
  if (!authority.evidence.permission_matrix_fingerprint) {
    return "缺少 permission matrix fingerprint，无法提交高风险变更。";
  }
  if (!isPermissionMatrixFingerprint(authority.evidence.permission_matrix_fingerprint)) {
    return "permission matrix fingerprint 必须是 64 位 hex，无法提交高风险变更。";
  }
  return null;
}

export function validateWorkspaceAuthorizationModeChange(
  values: WorkspaceAuthorizationModeChangeValues,
): WorkspaceAuthorizationModeChangeErrors {
  const errors: WorkspaceAuthorizationModeChangeErrors = {};
  if (values.targetMode === values.currentMode) {
    errors.targetMode = "请选择不同于当前模式的目标模式";
  }
  if (!Number.isInteger(values.policyRevision) || values.policyRevision < 1) {
    errors.policyRevision = "服务端未返回有效的策略 Revision";
  }
  if (!Number.isInteger(values.workspaceRevision) || values.workspaceRevision < 1) {
    errors.workspaceRevision = "服务端未返回有效的 Workspace Revision";
  }
  const reason = values.reason.trim();
  if (!reason) errors.reason = "请输入变更原因";
  else if (reason.length > 512) errors.reason = "变更原因不能超过 512 个字符";
  return errors;
}
