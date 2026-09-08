import { describe, expect, it } from "vitest";
import {
  highRiskWorkspaceAuthorizationEvidenceError,
  requiresWorkspaceAuthorizationApproval,
  validateWorkspaceAuthorizationModeChange,
} from "./enterpriseWorkspaceAuthorizationValidation";

const VALID_FINGERPRINT = "d49cb11be241a82e4744fcafae0319b219c61c18cba69f00f33d85d5a2a04fff";

const authority = {
  policy: {
    id: "policy-a",
    tenant_id: "tenant-a",
    workspace_id: "workspace-a",
    mode: "shadow" as const,
    permission_model_version: 1,
    revision: 3,
    created_at: null,
    created_by: null,
    updated_at: null,
    updated_by: null,
    enforced_at: null,
    enforced_by: null,
    disabled_at: null,
    disabled_by: null,
  },
  evidence: {
    active_member_count: 2,
    active_dataset_binding_count: 1,
    catalog_revision: "0027_enterprise_workspace_authorization",
    permission_matrix_fingerprint: VALID_FINGERPRINT,
    matching_approval_policy: null,
    latest_audit_event: null,
  },
};

describe("Stage17 workspace authorization mode validation", () => {
  it("blocks no-op, stale revision and missing reason before mutation", () => {
    expect(
      validateWorkspaceAuthorizationModeChange({
        currentMode: "shadow",
        targetMode: "shadow",
        policyRevision: 3,
        workspaceRevision: 7,
        reason: "观察完成",
      }),
    ).toMatchObject({ targetMode: "请选择不同于当前模式的目标模式" });

    expect(
      validateWorkspaceAuthorizationModeChange({
        currentMode: "shadow",
        targetMode: "enforced",
        policyRevision: 0,
        workspaceRevision: 7,
        reason: "观察完成",
      }),
    ).toMatchObject({ policyRevision: "服务端未返回有效的策略 Revision" });

    expect(
      validateWorkspaceAuthorizationModeChange({
        currentMode: "shadow",
        targetMode: "enforced",
        policyRevision: 3,
        workspaceRevision: 7,
        reason: " ",
      }),
    ).toMatchObject({ reason: "请输入变更原因" });
  });

  it("accepts a bounded revision-fenced mode change", () => {
    expect(
      validateWorkspaceAuthorizationModeChange({
        currentMode: "shadow",
        targetMode: "enforced",
        policyRevision: 3,
        workspaceRevision: 7,
        reason: "Shadow 观察通过，申请启用 Workspace 授权",
      }),
    ).toEqual({});
  });

  it("matches the server approval gate for disabled and enforced targets", () => {
    const approval = {
      state: "active",
      id: "approval-a",
      name: "高风险 Workspace 变更",
      required_approvals: 2,
      execution_adapter_status: "connected",
    };

    expect(requiresWorkspaceAuthorizationApproval("disabled", approval)).toBe(true);
    expect(requiresWorkspaceAuthorizationApproval("enforced", approval)).toBe(true);
    expect(requiresWorkspaceAuthorizationApproval("shadow", approval)).toBe(false);
    expect(
      requiresWorkspaceAuthorizationApproval("enforced", { ...approval, state: "disabled" }),
    ).toBe(false);
  });

  it("blocks high-risk submissions when authority evidence is incomplete", () => {
    expect(
      highRiskWorkspaceAuthorizationEvidenceError({
        authority: {
          ...authority,
          evidence: { ...authority.evidence, permission_matrix_fingerprint: null },
        },
        expectedTenantId: "tenant-a",
        expectedWorkspaceId: "workspace-a",
      }),
    ).toContain("permission matrix fingerprint");
    expect(
      highRiskWorkspaceAuthorizationEvidenceError({
        authority: {
          ...authority,
          evidence: { ...authority.evidence, catalog_revision: null },
        },
        expectedTenantId: "tenant-a",
        expectedWorkspaceId: "workspace-a",
      }),
    ).toContain("catalog revision");
  });
});
