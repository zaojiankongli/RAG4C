// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import { approveApprovalRequest } from "./enterpriseApprovalApi";

const scope: EnterpriseScope = {
  tenantId: "tenant-stage14",
  datasetId: "dataset-14",
  actorToken: "actor-token-stage14",
};

const response = {
  request: {
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
    snapshot: { dataset_id: "dataset-14" },
  },
  decision: {
    id: "decision-14",
    approver_id: "approver-14",
    decision: "approved",
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

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Stage 14 approval execution API response contract", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "https://enterprise.test");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse(response)),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("returns the one-time execution.ticket from a final approval instead of dropping the delivery envelope", async () => {
    const result = await approveApprovalRequest(
      scope,
      "request-14",
      { revision: 4 },
      { idempotencyKey: "approve-stage14" },
    );

    expect(result.request.id).toBe("request-14");
    expect(result.execution.state).toBe("ticket_issued");
    expect(result.execution.adapter).toBe("connected");
    expect(result.execution.ticket).toBe("opaque-ticket-stage14");
  });
});
