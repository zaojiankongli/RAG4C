// @vitest-environment node

import { describe, expect, it } from "vitest";
import { projectApprovalMutation, projectApprovalRequest } from "./enterpriseApprovalModel";

const request = {
  id: "request-14",
  policy_id: "policy-14",
  action_type: "dataset_acl_disable",
  resource_type: "knowledge_base",
  resource_id: "dataset-14",
  requester: { id: "requester-14", name: "申请人" },
  reason: "停用知识库 ACL",
  status: "approved",
  required_approvals: 2,
  received_approvals: 2,
  created_at: "2026-08-27T08:00:00Z",
  expires_at: "2026-09-01T08:00:00Z",
  revision: 5,
  snapshot: { dataset_id: "dataset-14", expected_acl_revision: 7 },
};

const finalApproval = {
  request,
  decision: {
    id: "decision-14",
    approver_id: "approver-14",
    decision: "approved",
    comment: "已核验变更范围",
    decided_at: "2026-08-27T08:10:00Z",
    created_at: "2026-08-27T08:10:00Z",
    revision: 1,
  },
  execution: {
    state: "ticket_issued",
    adapter: "connected",
    ticket: "opaque-ticket-stage14",
  },
};

describe("Stage 14 approval execution model", () => {
  it("preserves final approval execution ticket delivery without attaching it to the request projection", () => {
    const result = projectApprovalMutation(finalApproval);

    expect(result?.request).toMatchObject({ id: "request-14", status: "approved", revision: 5 });
    expect(result?.execution).toEqual({
      state: "ticket_issued",
      adapter: "connected",
      ticket: "opaque-ticket-stage14",
    });
    expect(projectApprovalRequest(finalApproval)).not.toHaveProperty("execution");
    expect(projectApprovalRequest(finalApproval)).not.toHaveProperty("ticket");
  });

  it("does not invent a ticket when the server only returns the non-final execution state", () => {
    const result = projectApprovalMutation({
      request: { ...request, status: "pending", received_approvals: 1 },
      execution: { state: "awaiting_approval", adapter: "connected" },
    });

    expect(result?.execution.ticket).toBeUndefined();
    expect(result?.execution.state).toBe("awaiting_approval");
  });
});
