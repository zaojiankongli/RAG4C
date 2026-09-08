import { describe, expect, it } from "vitest";
import { approvalActionLabel, isApprovalExecutionEligible } from "./enterpriseApprovalModel";

describe("Stage19 Release approval execution scopes", () => {
  it("labels Release publication and rollback as first-class enterprise actions", () => {
    expect(approvalActionLabel("knowledge_base_release_publish")).toBe(
      "发布 Knowledge Base Release",
    );
    expect(approvalActionLabel("knowledge_base_release_rollback")).toBe(
      "回滚 Knowledge Base Release",
    );
  });

  it.each(["knowledge_base_release_publish", "knowledge_base_release_rollback"])(
    "accepts only connected one-time tickets for %s",
    (actionType) => {
      expect(
        isApprovalExecutionEligible(
          { action_type: actionType, resource_type: "knowledge_base" },
          { state: "ticket_issued", adapter: "connected", ticket: "opaque-ticket" },
        ),
      ).toBe(true);
      expect(
        isApprovalExecutionEligible(
          { action_type: actionType, resource_type: "tenant_workspace" },
          { state: "ticket_issued", adapter: "connected", ticket: "opaque-ticket" },
        ),
      ).toBe(false);
    },
  );
});
