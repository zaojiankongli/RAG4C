// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { useMemberRoleApprovalGate } from "./useMemberRoleApprovalGate";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const scope = { tenantId: "tenant-1", actorToken: "actor-token" };
const member = {
  account_id: "account-42",
  role: "member",
  status: "active",
  revision: 7,
};
const policy = {
  id: "policy-role-1",
  name: "成员角色双人复核",
  action_type: "member_role_change",
  resource_scope: "tenant_member:account-42",
  status: "active",
  required_approvals: 2,
  request_expiry_minutes: 60,
  approvers: [{ kind: "role", ref: "owner" }],
  revision: 9,
};
const request = {
  id: "request-role-1",
  policy_id: "policy-role-1",
  action_type: "member_role_change",
  resource_type: "tenant_member",
  resource_id: "account-42",
  requester: { id: "account-1", name: "林澈" },
  reason: "职责调整",
  status: "pending",
  required_approvals: 2,
  received_approvals: 0,
  expires_at: "2026-08-27T10:00:00Z",
  created_at: "2026-08-27T09:00:00Z",
  revision: 1,
  execution_adapter_status: "execution_adapter_not_connected",
  snapshot: {
    target_account_id: "account-42",
    expected_member_revision: 7,
    current_role: "member",
    requested_role: "editor",
    current_status: "active",
  },
};

describe("useMemberRoleApprovalGate", () => {
  beforeEach(() => {
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("loads only active member role policies and enters approval mode for an exact match", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        return Promise.resolve(jsonResponse({ items: [policy], count: 1, next_cursor: null }));
      }),
    );

    const { result } = renderHook(() =>
      useMemberRoleApprovalGate({ visible: true, scope, member }),
    );

    await waitFor(() => expect(result.current.mode).toBe("approval"));
    expect(calls).toHaveLength(1);
    expect(calls[0]).toContain("status=active");
    expect(calls[0]).toContain("action_type=member_role_change");
    expect(result.current.policy).toMatchObject({
      id: "policy-role-1",
      required_approvals: 2,
      request_expiry_minutes: 60,
      revision: 9,
    });
  });

  it("enters direct mode when the policy list has no matching policy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }))),
    );

    const { result } = renderHook(() =>
      useMemberRoleApprovalGate({ visible: true, scope, member }),
    );

    await waitFor(() => expect(result.current.mode).toBe("direct"));
    expect(result.current.policy).toBeNull();
  });

  it("submits an approval request with the exact sanitized snapshot and keeps the request in memory", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, init });
        if (init?.method === "POST") return Promise.resolve(jsonResponse(request));
        return Promise.resolve(jsonResponse({ items: [policy], count: 1, next_cursor: null }));
      }),
    );
    const setItem = vi.spyOn(Storage.prototype, "setItem");

    const { result } = renderHook(() =>
      useMemberRoleApprovalGate({ visible: true, scope, member }),
    );
    await waitFor(() => expect(result.current.mode).toBe("approval"));

    let submitted: unknown;
    await act(async () => {
      submitted = await result.current.submitApproval("editor", "职责调整");
    });

    expect(submitted).toMatchObject({ id: "request-role-1", status: "pending" });
    expect(result.current.mode).toBe("submitted");
    expect(result.current.request?.id).toBe("request-role-1");
    const body = JSON.parse(String(calls[1].init?.body));
    expect(body).toEqual({
      policy_id: "policy-role-1",
      resource_type: "tenant_member",
      resource_id: "account-42",
      snapshot: {
        target_account_id: "account-42",
        expected_member_revision: 7,
        current_role: "member",
        requested_role: "editor",
        current_status: "active",
      },
      reason: "职责调整",
    });
    expect(setItem).not.toHaveBeenCalledWith(
      expect.stringMatching(/approval|ticket|request/i),
      expect.anything(),
    );
  });

  it("fails closed when the approval policy endpoint is missing instead of assuming direct mutation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ detail: "not found" }, 404))),
    );

    const { result } = renderHook(() =>
      useMemberRoleApprovalGate({ visible: true, scope, member }),
    );

    await waitFor(() => expect(result.current.mode).toBe("error"));
    expect(result.current.error).toContain("审批规则事实暂时不可用");
    expect(result.current.mode).not.toBe("direct");
  });

  it("reuses a retry key for the same draft but rotates it when role or reason changes", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    let postCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, init });
        if (init?.method === "POST") {
          postCount += 1;
          if (postCount < 3) return Promise.reject(new TypeError("offline"));
          return Promise.resolve(jsonResponse(request));
        }
        return Promise.resolve(jsonResponse({ items: [policy], count: 1, next_cursor: null }));
      }),
    );

    const { result } = renderHook(() =>
      useMemberRoleApprovalGate({ visible: true, scope, member }),
    );
    await waitFor(() => expect(result.current.mode).toBe("approval"));

    await act(async () => {
      await result.current.submitApproval("editor", "职责调整");
    });
    await act(async () => {
      await result.current.submitApproval("editor", "职责调整");
    });
    await act(async () => {
      await result.current.submitApproval("admin", "组织架构调整");
    });

    const postCalls = calls.filter(({ init }) => init?.method === "POST");
    expect(postCalls).toHaveLength(3);
    const keyOf = (call: { init?: RequestInit }) =>
      (call.init?.headers as Record<string, string>)["Idempotency-Key"];
    expect(keyOf(postCalls[0])).toBe(keyOf(postCalls[1]));
    expect(keyOf(postCalls[2])).not.toBe(keyOf(postCalls[1]));
  });

  it("switches from direct mode to approval mode on member_role_approval_required", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/policies?"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }));
        return Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }));
      }),
    );

    const { result } = renderHook(() =>
      useMemberRoleApprovalGate({ visible: true, scope, member }),
    );
    await waitFor(() => expect(result.current.mode).toBe("direct"));

    let changed = false;
    await act(async () => {
      changed = result.current.applyApprovalRequired(
        new ApiError("审批规则要求二次确认", "http", 409, {
          detail: {
            code: "member_role_approval_required",
            policy: {
              id: "policy-role-fallback",
              name: "服务端角色变更复核",
              resource_scope: "tenant_member:account-42",
              required_approvals: 2,
              request_expiry_minutes: 30,
              revision: 11,
            },
          },
        }),
      );
    });

    expect(changed).toBe(true);
    expect(result.current.mode).toBe("approval");
    expect(result.current.policy?.id).toBe("policy-role-fallback");
  });
});
