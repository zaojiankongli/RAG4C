import { describe, expect, it } from "vitest";
import {
  buildMemberRoleApprovalSnapshot,
  memberRoleApprovalPolicyMatches,
  projectMemberRoleApprovalRequiredHint,
  projectMemberRoleApprovalPolicyFacts,
  selectMemberRoleApprovalPolicy,
} from "./memberRoleApprovalModel";

const basePolicy = {
  id: "policy-role-global",
  name: "成员角色高风险变更",
  action_type: "member_role_change" as const,
  resource_scope: "global",
  status: "active" as const,
  required_approvals: 2,
  request_expiry_minutes: 1440,
  approvers: [],
  revision: 4,
};

describe("member role approval model", () => {
  it("matches an active exact tenant member scope", () => {
    expect(
      memberRoleApprovalPolicyMatches(
        { ...basePolicy, resource_scope: "tenant_member:account-42" },
        "account-42",
      ),
    ).toBe(true);
    expect(
      memberRoleApprovalPolicyMatches(
        { ...basePolicy, resource_scope: "tenant_member:account-42" },
        "account-43",
      ),
    ).toBe(false);
  });

  it("matches tenant-member wildcard and global scope forms but not inactive policies", () => {
    for (const resource_scope of [
      "tenant_member:*",
      "tenant_member/*",
      "tenant_member:wildcard:global",
      "global",
      "*",
    ]) {
      expect(memberRoleApprovalPolicyMatches({ ...basePolicy, resource_scope }, "account-42")).toBe(
        true,
      );
    }
    expect(
      memberRoleApprovalPolicyMatches(
        { ...basePolicy, status: "disabled", resource_scope: "global" },
        "account-42",
      ),
    ).toBe(false);
    expect(
      memberRoleApprovalPolicyMatches(
        { ...basePolicy, action_type: "dataset_acl_disable", resource_scope: "global" },
        "account-42",
      ),
    ).toBe(false);
  });

  it("selects exact policy before wildcard and global policy", () => {
    const selected = selectMemberRoleApprovalPolicy(
      [
        { ...basePolicy, id: "policy-global", resource_scope: "global" },
        { ...basePolicy, id: "policy-wildcard", resource_scope: "tenant_member:*" },
        { ...basePolicy, id: "policy-exact", resource_scope: "tenant_member:account-42" },
      ],
      "account-42",
    );
    expect(selected?.id).toBe("policy-exact");
  });

  it("projects a sanitized 409 hint without rendering the backend message", () => {
    expect(
      projectMemberRoleApprovalRequiredHint({
        detail: {
          code: "member_role_approval_required",
          message: "internal policy row details must not be shown",
          policy: {
            id: "policy-role-1",
            name: "成员角色双人复核",
            resource_scope: "tenant_member:account-42",
            required_approvals: 2,
            request_expiry_minutes: 60,
            revision: 9,
            expires_at: "2026-08-27T10:00:00Z",
          },
        },
      }),
    ).toEqual({
      policy_id: "policy-role-1",
      policy_name: "成员角色双人复核",
      policy_revision: 9,
      required_approvals: 2,
      request_expiry_minutes: 60,
      expires_at: "2026-08-27T10:00:00Z",
      resource_scope: "tenant_member:account-42",
    });
  });

  it("builds the exact role-change snapshot and no extra fields", () => {
    const snapshot = buildMemberRoleApprovalSnapshot(
      {
        account_id: "account-42",
        role: "member",
        status: "active",
        revision: 7,
      },
      "editor",
    );
    expect(snapshot).toEqual({
      target_account_id: "account-42",
      expected_member_revision: 7,
      current_role: "member",
      requested_role: "editor",
      current_status: "active",
    });
    expect(Object.keys(snapshot)).toEqual([
      "target_account_id",
      "expected_member_revision",
      "current_role",
      "requested_role",
      "current_status",
    ]);
  });

  it("projects policy evidence with bounded values", () => {
    expect(
      projectMemberRoleApprovalPolicyFacts({
        ...basePolicy,
        resource_scope: "tenant_member:account-42",
      }),
    ).toMatchObject({
      id: "policy-role-global",
      name: "成员角色高风险变更",
      resource_scope: "tenant_member:account-42",
      required_approvals: 2,
      request_expiry_minutes: 1440,
      revision: 4,
    });
  });
});
