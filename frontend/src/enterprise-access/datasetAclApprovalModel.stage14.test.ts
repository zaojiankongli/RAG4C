import { describe, expect, it } from "vitest";
import type { ApprovalPolicy } from "../enterprise-approval/enterpriseApprovalModel";
import {
  datasetAclApprovalPolicyMatches,
  selectDatasetAclApprovalPolicy,
  type DatasetAclApprovalPolicyFacts,
} from "./datasetAclApprovalModel";

const policy = (overrides: Partial<ApprovalPolicy> = {}): ApprovalPolicy => ({
  id: "policy-dataset-acl",
  name: "知识库 ACL 高风险变更",
  action_type: "dataset_acl_disable",
  resource_scope: "knowledge_base:dataset-1",
  status: "active",
  required_approvals: 2,
  request_expiry_minutes: 1440,
  approvers: [{ kind: "role", ref: "owner", label: "租户所有者" }],
  revision: 4,
  ...overrides,
});

describe("Stage 14 Dataset ACL approval policy matching", () => {
  it("matches an exact knowledge_base scope and ignores another dataset", () => {
    expect(datasetAclApprovalPolicyMatches(policy(), "dataset-1")).toBe(true);
    expect(datasetAclApprovalPolicyMatches(policy(), "dataset-2")).toBe(false);
  });

  it.each(["knowledge_base:*", "knowledge_base:wildcard:global", "global", "*"])(
    "matches the supported global/wildcard scope %s",
    (resourceScope) => {
      expect(
        datasetAclApprovalPolicyMatches(policy({ resource_scope: resourceScope }), "dataset-1"),
      ).toBe(true);
    },
  );

  it("requires an active dataset_acl_disable policy and prefers exact scope over wildcard", () => {
    const selected = selectDatasetAclApprovalPolicy(
      [
        policy({ id: "wildcard", resource_scope: "knowledge_base:*" }),
        policy({ id: "disabled", status: "disabled" }),
        policy({ id: "other-action", action_type: "member_role_change" }),
        policy({ id: "exact", resource_scope: "knowledge_base:dataset-1" }),
      ],
      "dataset-1",
    );

    expect(selected?.id).toBe("exact");
  });

  it("projects only authoritative policy facts without inventing missing values", () => {
    const facts: DatasetAclApprovalPolicyFacts = {
      id: "policy-1",
      name: null,
      resource_scope: "global",
      required_approvals: null,
      request_expiry_minutes: null,
      revision: null,
    };
    expect(facts.required_approvals).toBeNull();
    expect(facts.request_expiry_minutes).toBeNull();
  });
});
