// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  approveApprovalRequest,
  cancelApprovalRequest,
  createApprovalPolicy,
  createApprovalRequest,
  disableApprovalPolicy,
  fetchApprovalPolicies,
  fetchApprovalRequest,
  fetchApprovalRequests,
  rejectApprovalRequest,
  updateApprovalPolicy,
} from "./enterpriseApprovalApi";

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const request = {
  id: "request-1",
  policy_id: "policy-1",
  action_type: "dataset_acl_disable",
  resource_type: "knowledge_base",
  resource_id: "dataset-1",
  requester: { id: "account-1", name: "林澈" },
  reason: "变更访问策略",
  status: "pending",
  required_approvals: 2,
  received_approvals: 0,
  expires_at: "2026-09-01T08:00:00Z",
  created_at: "2026-08-27T08:00:00Z",
  revision: 4,
};
const policy = {
  id: "policy-1",
  name: "知识库 ACL 变更",
  action_type: "dataset_acl_disable",
  resource_scope: "dataset-1",
  status: "active",
  required_approvals: 2,
  request_expiry_minutes: 1440,
  approvers: [{ kind: "role", ref: "owner", label: "租户所有者" }],
  revision: 3,
};

describe("Stage 13 approval API strict contract", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "https://enterprise.test");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ request, policy })),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("sends bounded tenant filters for requests and policies", async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input);
      return url.includes("/policies")
        ? jsonResponse({ items: [policy], count: 1, next_cursor: null })
        : jsonResponse({ items: [request], count: 1, next_cursor: "next" });
    });

    await fetchApprovalRequests(scope, {
      status: "pending",
      actionType: "dataset_acl_disable",
      pendingForMe: true,

      cursor: "cursor/1",
      limit: 25,
    });
    await fetchApprovalPolicies(scope, { status: "active", cursor: "p-1", limit: 20 });

    const calls = vi.mocked(fetch).mock.calls;
    expect(calls).toHaveLength(2);
    const requestUrl = new URL(String(calls[0][0]));
    expect(requestUrl.pathname).toBe("/api/enterprise/approvals/requests");
    expect(Object.fromEntries(requestUrl.searchParams)).toEqual({
      status: "pending",
      action_type: "dataset_acl_disable",
      pending_for_me: "true",
      cursor: "cursor/1",
      limit: "25",
    });
    const policyUrl = new URL(String(calls[1][0]));
    expect(policyUrl.pathname).toBe("/api/enterprise/approvals/policies");
    expect(Object.fromEntries(policyUrl.searchParams)).toEqual({
      status: "active",
      cursor: "p-1",
      limit: "20",
    });
    for (const [, init] of calls) {
      expect(init?.headers).toMatchObject({
        "X-RAG4C-Tenant": "tenant-1",
        Authorization: "Bearer actor-token",
      });
    }
  });

  it("URL-encodes detail ids and uses only the detail read contract", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ request, decisions: [], process: [] }));
    await fetchApprovalRequest(scope, "request/with spaces");
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(String(url)).toBe(
      "https://enterprise.test/api/enterprise/approvals/requests/request%2Fwith%20spaces",
    );
    expect(init?.method).toBe("GET");
    expect(init?.body).toBeUndefined();
  });

  it("uses exact mutation bodies, stable idempotency and revision fencing", async () => {
    vi.mocked(fetch).mockImplementation(async () => jsonResponse({ request, policy }));
    const options = { idempotencyKey: "approval-key-1" };
    await createApprovalPolicy(
      scope,
      {
        name: "ACL 变更",
        action_type: "dataset_acl_disable",
        resource_scope: "dataset-1",
        required_approvals: 2,
        request_expiry_minutes: 1440,
        approvers: [{ kind: "role", ref: "owner" }],
        reason: "治理要求",
      },
      options,
    );
    await updateApprovalPolicy(
      scope,
      "policy-1",
      {
        revision: 3,
        name: "ACL 变更 v2",
        resource_scope: "dataset-1",
        required_approvals: 2,
        request_expiry_minutes: 1440,
        approvers: [{ kind: "role", ref: "owner" }],
        reason: "调整审批人",
      },
      options,
    );
    await disableApprovalPolicy(scope, "policy-1", { revision: 4, reason: "暂时停用" }, options);
    await createApprovalRequest(
      scope,
      {
        policy_id: "policy-1",
        resource_type: "knowledge_base",
        resource_id: "dataset-1",
        reason: "申请变更",
        snapshot: { dataset_id: "dataset-1" },
      },
      options,
    );
    await approveApprovalRequest(scope, "request-1", { revision: 4 }, options);
    await rejectApprovalRequest(
      scope,
      "request-1",
      { revision: 5, comment: "  范围需要重新确认  " },
      options,
    );
    await cancelApprovalRequest(scope, "request-1", { revision: 6, reason: "申请人撤回" }, options);

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(7);
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual({
      name: "ACL 变更",
      action_type: "dataset_acl_disable",
      resource_scope: "dataset-1",
      required_approvals: 2,
      request_expiry_minutes: 1440,
      approvers: [{ kind: "role", ref: "owner" }],
      reason: "治理要求",
    });
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[3][1]?.body))).toEqual({
      policy_id: "policy-1",
      resource_type: "knowledge_base",
      resource_id: "dataset-1",
      reason: "申请变更",
      snapshot: { dataset_id: "dataset-1" },
    });
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[4][1]?.body))).toEqual({
      revision: 4,
    });
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[5][1]?.body))).toEqual({
      revision: 5,
      comment: "范围需要重新确认",
    });
    for (const [, init] of vi.mocked(fetch).mock.calls) {
      expect(init?.headers).toMatchObject({ "Idempotency-Key": "approval-key-1" });
    }
  });

  it("rejects unsafe comments, revisions, limits and blank authority before network", () => {
    expect(() => rejectApprovalRequest(scope, "request-1", { revision: 4, comment: " " })).toThrow(
      /comment/,
    );
    expect(() =>
      rejectApprovalRequest(scope, "request-1", {
        revision: 4,
        comment: "x".repeat(501),
      }),
    ).toThrow(/500/);
    expect(() => fetchApprovalRequests(scope, { limit: 201 })).toThrow(/200/);
    expect(() => approveApprovalRequest(scope, "request-1", { revision: 0 })).toThrow(/revision/);
    expect(() => fetchApprovalRequest({ ...scope, actorToken: "" }, "request-1")).toThrow(
      /actorToken/,
    );
    expect(fetch).not.toHaveBeenCalled();
  });
});
