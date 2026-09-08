// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  approveApprovalRequest,
  consumeApprovalTicket,
  fetchApprovalPolicies,
  fetchApprovalRequest,
  fetchApprovalRequests,
} from "./enterpriseApprovalApi";
import {
  canApproveApprovalRequest,
  projectApprovalPolicyPage,
  projectApprovalRequestDetail,
  projectApprovalRequestPage,
} from "../enterpriseApprovalModel";

const scope: EnterpriseScope = {
  tenantId: "tenant-approval-a",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};

const backendRequest = {
  id: "approval-request-1",
  policy_id: "approval-policy-1",
  requester_id: "member-a",
  action_type: "catalog_upgrade",
  resource_type: "catalog",
  resource_id: "catalog-main",
  snapshot: { revision: 24, safe: "value" },
  snapshot_hash: "sha256:request",
  reason: "申请目录升级",
  status: "pending",
  required_approvals: 2,
  received_approvals: 1,
  requested_at: "2026-08-27T12:00:00Z",
  expires_at: "2026-08-27T13:00:00Z",
  decided_at: null,
  rejected_at: null,
  rejected_by: null,
  cancelled_at: null,
  cancelled_by: null,
  executed_at: null,
  revision: 1,
  updated_at: "2026-08-27T12:00:00Z",
};
const backendPolicy = {
  id: "approval-policy-1",
  name: "Catalog changes",
  action_type: "catalog_upgrade",
  resource_scope: "catalog:*",
  status: "active",
  required_approvals: 2,
  request_expiry_minutes: 60,
  revision: 1,
  created_at: "2026-08-27T12:00:00Z",
  created_by: "admin-a",
  updated_at: "2026-08-27T12:00:00Z",
  updated_by: "admin-a",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Stage 13 strict backend response contract", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "https://enterprise.test");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ items: [], count: 0 })),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("reads next_cursor and sends only backend-supported request/policy filters", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/policies"))
        return jsonResponse({ items: [backendPolicy], count: 1, next_cursor: "policy-next" });
      return jsonResponse({ items: [backendRequest], count: 1, next_cursor: "request-next" });
    });

    const requestPage = await fetchApprovalRequests(scope, {
      status: "pending",
      actionType: "catalog_upgrade",
      pendingForMe: true,
      cursor: "request-cursor",
      limit: 50,
    });
    const policyPage = await fetchApprovalPolicies(scope, { cursor: "policy-cursor", limit: 50 });
    expect(requestPage.next_cursor).toBe("request-next");
    expect(policyPage.next_cursor).toBe("policy-next");

    const requestUrl = new URL(String(vi.mocked(fetch).mock.calls[0][0]));
    expect(Object.fromEntries(requestUrl.searchParams)).toEqual({
      status: "pending",
      action_type: "catalog_upgrade",
      pending_for_me: "true",
      cursor: "request-cursor",
      limit: "50",
    });
    const policyUrl = new URL(String(vi.mocked(fetch).mock.calls[1][0]));
    expect(Object.fromEntries(policyUrl.searchParams)).toEqual({
      cursor: "policy-cursor",
      limit: "50",
    });
  });

  it("projects backend requester_id/approver_id with ID-only fallback and never drops the request", () => {
    const page = projectApprovalRequestPage({
      items: [backendRequest],
      count: 1,
      next_cursor: null,
    });
    expect(page.items).toHaveLength(1);
    expect(page.items[0]?.requester).toEqual({ id: "member-a", name: "member-a" });
    expect(page.items[0]?.my_approval).toBeNull();
    expect(canApproveApprovalRequest(page.items[0]!)).toBe(false);

    const detail = projectApprovalRequestDetail({
      request: { ...backendRequest, execution_adapter_status: "connected" },
      policy: backendPolicy,
      approvers: [
        { id: "approver-1", kind: "account", ref: "owner-a", status: "active", revision: 1 },
      ],
      decisions: [
        {
          id: "decision-1",
          approver_id: "owner-a",
          decision: "approved",
          comment: "一审",
          decided_at: "2026-08-27T12:10:00Z",
          revision: 1,
        },
      ],
      execution: { state: "awaiting_approval" },
    });
    expect(detail?.decisions[0]?.approver).toEqual({ id: "owner-a", name: "owner-a" });
    expect(detail?.execution_adapter_status).toBe("connected");
    expect(
      projectApprovalPolicyPage({
        items: [{ ...backendPolicy, approvers: [{ kind: "account", ref: "owner-a" }] }],
        count: 1,
      }).items[0]?.approvers[0]?.ref,
    ).toBe("owner-a");
  });

  it("reads the real backend detail response and keeps ID-only actors inspectable", async () => {
    vi.mocked(fetch).mockImplementation(async () =>
      jsonResponse({
        request: backendRequest,
        policy: backendPolicy,
        approvers: [
          { id: "approver-1", kind: "account", ref: "owner-a", status: "active", revision: 1 },
        ],
        decisions: [
          {
            id: "decision-1",
            approver_id: "owner-a",
            decision: "approved",
            comment: "一审",
            decided_at: "2026-08-27T12:10:00Z",
            revision: 1,
          },
        ],
        execution: { state: "awaiting_approval" },
      }),
    );
    const detail = await fetchApprovalRequest(scope, "approval-request-1");
    expect(detail.requester.name).toBe("member-a");
    expect(detail.decisions[0]?.approver.name).toBe("owner-a");
    expect(detail.process[0]?.status).toBe("submitted");
  });

  it("sends the complete ticket scope with revision and never expected_revision", async () => {
    vi.mocked(fetch).mockImplementation(async () =>
      jsonResponse({ request: backendRequest, execution: { state: "executed" } }),
    );
    await consumeApprovalTicket(
      scope,
      "approval-request-1",
      {
        ticket: "opaque-ticket",
        revision: 3,
        action_type: "catalog_upgrade",
        resource_type: "catalog",
        resource_id: "catalog-main",
      },
      { idempotencyKey: "consume-key" },
    );
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(JSON.parse(String(init?.body))).toEqual({
      ticket: "opaque-ticket",
      revision: 3,
      action_type: "catalog_upgrade",
      resource_type: "catalog",
      resource_id: "catalog-main",
    });
    expect(JSON.parse(String(init?.body))).not.toHaveProperty("expected_revision");
  });

  it("uses revision for decisions and does not send action_type on create request", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ request: backendRequest }));
    await approveApprovalRequest(
      scope,
      "approval-request-1",
      { revision: 1 },
      { idempotencyKey: "approve-key" },
    );
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(JSON.parse(String(init?.body))).toEqual({ revision: 1 });
    expect(JSON.parse(String(init?.body))).not.toHaveProperty("expected_revision");
  });
});
