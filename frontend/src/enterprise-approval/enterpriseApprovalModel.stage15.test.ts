// @vitest-environment node

import { describe, expect, it } from "vitest";
import {
  approvalChangeFactEntries,
  isApprovalExecutionEligible,
  projectApprovalSnapshot,
} from "./enterpriseApprovalModel";

describe("Stage 15 approval execution model", () => {
  it("allows only connected exact action/resource execution pairs with a ticket", () => {
    const connected = { state: "ticket_issued", adapter: "connected", ticket: "opaque-ticket" };

    expect(
      isApprovalExecutionEligible(
        { action_type: "dataset_acl_disable", resource_type: "knowledge_base" },
        connected,
      ),
    ).toBe(true);
    expect(
      isApprovalExecutionEligible(
        { action_type: "member_role_change", resource_type: "tenant_member" },
        connected,
      ),
    ).toBe(true);
    expect(
      isApprovalExecutionEligible(
        { action_type: "member_role_change", resource_type: "knowledge_base" },
        connected,
      ),
    ).toBe(false);
    expect(
      isApprovalExecutionEligible(
        { action_type: "catalog_upgrade", resource_type: "tenant_member" },
        connected,
      ),
    ).toBe(false);
    expect(
      isApprovalExecutionEligible(
        { action_type: "member_role_change", resource_type: "tenant_member" },
        { ...connected, adapter: "execution_adapter_not_connected" },
      ),
    ).toBe(false);
    expect(
      isApprovalExecutionEligible(
        { action_type: "member_role_change", resource_type: "tenant_member" },
        { ...connected, ticket: "" },
      ),
    ).toBe(false);
  });

  it("projects generic change facts without allowing a raw ticket into the facts surface", () => {
    const snapshot = projectApprovalSnapshot({
      target_account_id: "account-42",
      current_role: "member",
      requested_role: "admin",
      ticket: "opaque-ticket-stage15",
    });

    expect(snapshot).toEqual({
      target_account_id: "account-42",
      current_role: "member",
      requested_role: "admin",
      ticket: "[已脱敏]",
    });
    expect(approvalChangeFactEntries(snapshot)).toEqual([
      { key: "target_account_id", label: "目标账号", value: "account-42" },
      { key: "current_role", label: "当前角色", value: "member" },
      { key: "requested_role", label: "申请角色", value: "admin" },
      { key: "ticket", label: "ticket", value: "[已脱敏]" },
    ]);
  });
});
