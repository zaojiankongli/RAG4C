import { describe, expect, it } from "vitest";
import {
  projectWorkspaceAuthorizationImpactResponse,
  projectWorkspaceAuthorizationPolicyResponse,
} from "./enterpriseWorkspaceAuthorizationModel";

const VALID_FINGERPRINT = "d49cb11be241a82e4744fcafae0319b219c61c18cba69f00f33d85d5a2a04fff";

const policyResponse = {
  policy: {
    id: "workspace-auth-workspace-prod",
    tenant_id: "tenant-a",
    workspace_id: "workspace-prod",
    mode: "shadow",
    permission_model_version: 1,
    revision: 3,
    created_at: "2026-08-27T08:00:00Z",
    created_by: "owner-a",
    updated_at: "2026-08-27T09:00:00Z",
    updated_by: "owner-a",
    enforced_at: null,
    enforced_by: null,
    disabled_at: null,
    disabled_by: null,
  },
  evidence: {
    active_member_count: 4,
    active_dataset_binding_count: 2,
    catalog_revision: "0027_enterprise_workspace_authorization",
    permission_matrix_fingerprint: VALID_FINGERPRINT,
    matching_approval_policy: {
      state: "active",
      id: "approval-policy-workspace",
      name: "Workspace 强制授权",
      required_approvals: 2,
      execution_adapter_status: "connected",
    },
    latest_audit_event: {
      sequence: 91,
      action: "workspace.authorization.mode.changed",
      resource_type: "tenant_workspace_authorization_policy",
      resource_id: "workspace-auth-workspace-prod",
      actor_id: "owner-a",
      actor_name: "张三",
      occurred_at: "2026-08-27T09:00:00Z",
    },
  },
};

describe("Stage17 workspace authorization strict model", () => {
  it("projects policy authority without inventing omitted lifecycle evidence", () => {
    const projected = projectWorkspaceAuthorizationPolicyResponse(policyResponse);

    expect(projected.policy.mode).toBe("shadow");
    expect(projected.policy.revision).toBe(3);
    expect(projected.evidence.matching_approval_policy?.id).toBe("approval-policy-workspace");
    expect(projected.evidence.latest_audit_event?.sequence).toBe(91);
    expect(projected.policy.enforced_at).toBeNull();
    expect(projected.policy.disabled_at).toBeNull();
  });

  it("rejects malformed authority instead of defaulting mode or revision", () => {
    expect(() =>
      projectWorkspaceAuthorizationPolicyResponse({
        ...policyResponse,
        policy: { ...policyResponse.policy, mode: "active" },
      }),
    ).toThrow("workspace authorization policy mode is invalid");

    expect(() =>
      projectWorkspaceAuthorizationPolicyResponse({
        ...policyResponse,
        policy: { ...policyResponse.policy, revision: 0 },
      }),
    ).toThrow("workspace authorization policy revision is invalid");

    expect(() =>
      projectWorkspaceAuthorizationPolicyResponse({
        ...policyResponse,
        policy: { ...policyResponse.policy, permission_model_version: 2 },
      }),
    ).toThrow("workspace authorization permission model version must be v1");

    expect(() =>
      projectWorkspaceAuthorizationPolicyResponse({
        ...policyResponse,
        evidence: { ...policyResponse.evidence, permission_matrix_fingerprint: "sha256:matrix-v1" },
      }),
    ).toThrow("workspace authorization permission matrix fingerprint is invalid");
  });

  it("projects impact evidence and never retains raw execution tickets", () => {
    const projected = projectWorkspaceAuthorizationImpactResponse({
      impact: {
        dataset_id: "dataset-a",
        state: "workspace_authorization_shadow",
        tenant_role: "owner",
        dataset_acl_role: "manager",
        matched_grants: [
          {
            id: "grant-a",
            subject_type: "account",
            subject_id: "owner-a",
            subject_name: "张三",
            role: "manager",
          },
        ],
        workspace_roles: [
          {
            workspace_id: "workspace-prod",
            workspace_name: "生产知识域",
            role: "owner",
            binding_kind: "primary",
            policy_mode: "shadow",
            policy_revision: 3,
          },
        ],
        contributing_workspaces: [],
        current_effective_permissions: ["knowledge.read", "knowledge.manage"],
        candidate_permissions: ["knowledge.read", "knowledge.write", "knowledge.delete"],
        would_grant_permissions: ["knowledge.write", "knowledge.delete"],
        granted_permissions: [],
        warnings: ["shadow 模式不会改变 effective_permissions"],
        ticket: "never-project-this-ticket",
      },
      execution: { ticket: "never-project-this-ticket-either" },
    });

    expect(projected.impact.state).toBe("workspace_authorization_shadow");
    expect(projected.impact.would_grant_permissions).toEqual([
      "knowledge.write",
      "knowledge.delete",
    ]);
    expect(JSON.stringify(projected)).not.toContain("never-project-this-ticket");
  });
});
