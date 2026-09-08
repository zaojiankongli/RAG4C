// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import { useEnterpriseApprovalCenter } from "./useEnterpriseApproval";

const scope: EnterpriseScope = {
  tenantId: "tenant-stage15",
  datasetId: "dataset-stage15",
  actorToken: "actor-token-stage15",
};

const pendingRequest = {
  id: "request-member-role-15",
  policy_id: "policy-member-role-15",
  action_type: "member_role_change",
  resource_type: "tenant_member",
  resource_id: "account-42",
  resource_name: "成员 42",
  requester: { id: "requester-stage15", name: "申请人" },
  reason: "授予成员管理员权限",
  status: "pending",
  required_approvals: 2,
  received_approvals: 1,
  created_at: "2026-08-27T08:00:00Z",
  expires_at: "2026-09-01T08:00:00Z",
  revision: 4,
  my_approval: "pending",
  my_approval_eligible: true,
  snapshot: {
    target_account_id: "account-42",
    expected_member_revision: 7,
    current_role: "member",
    requested_role: "admin",
    current_status: "active",
  },
};
const approvedRequest = {
  ...pendingRequest,
  status: "approved",
  received_approvals: 2,
  revision: 5,
};
const executedRequest = { ...approvedRequest, status: "executed", revision: 7 };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function Harness() {
  const workspace = useEnterpriseApprovalCenter(scope, {
    actorId: "approver-stage15",
    actorRole: "owner",
  });
  return (
    <div>
      <output data-testid="stage15-status">{workspace.status}</output>
      <output data-testid="stage15-ticket-available">
        {workspace.executionTicketAvailable ? "available" : "empty"}
      </output>
      <output data-testid="stage15-execution-state">
        {workspace.execution.saving
          ? "saving"
          : workspace.execution.success
            ? "success"
            : (workspace.execution.error?.code ?? "idle")}
      </output>
      <output data-testid="stage15-execution-target">
        {workspace.executionTarget?.resource_id ?? "none"}
      </output>
      <output data-testid="stage15-execution-facts">
        {JSON.stringify(workspace.executionTarget?.change_facts ?? null)}
      </output>
      <button onClick={() => void workspace.approve(pendingRequest.id, 4)}>approve</button>
      <button onClick={() => void workspace.consumeExecutionTicket()}>execute</button>
    </div>
  );
}

function installFetch(
  options: { networkFailOnce?: boolean; failRefreshAfterApprove?: boolean } = {},
) {
  let currentRequest = pendingRequest;
  let networkFailed = false;
  let refreshFailed = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (method === "POST" && url.includes("/approve")) {
        currentRequest = approvedRequest;
        return jsonResponse({
          request: approvedRequest,
          decision: {
            id: "decision-member-role-15",
            approver_id: "approver-stage15",
            decision: "approved",
            decided_at: "2026-08-27T08:10:00Z",
            created_at: "2026-08-27T08:10:00Z",
            revision: 1,
          },
          execution: {
            state: "ticket_issued",
            adapter: "connected",
            ticket: "opaque-ticket-member-role-15",
          },
        });
      }
      if (method === "POST" && url.includes("/consume-ticket")) {
        if (options.networkFailOnce && !networkFailed) {
          networkFailed = true;
          throw new TypeError("network unavailable");
        }
        currentRequest = executedRequest;
        return jsonResponse({
          request: executedRequest,
          execution: { state: "executed", adapter: "connected" },
        });
      }
      if (url.includes(`/requests/${pendingRequest.id}`) && method === "GET")
        return jsonResponse({
          request: currentRequest,
          approvers: [{ kind: "role", ref: "owner", label: "租户所有者" }],
          decisions: [],
          process: [],
          execution: { state: currentRequest.status, adapter: "connected" },
        });
      if (url.includes("/policies"))
        return jsonResponse({
          items: [
            {
              id: "policy-member-role-15",
              name: "成员角色变更",
              action_type: "member_role_change",
              resource_scope: "account-42",
              status: "active",
              required_approvals: 2,
              request_expiry_minutes: 1440,
              approvers: [{ kind: "role", ref: "owner", label: "租户所有者" }],
              revision: 1,
            },
          ],
          count: 1,
        });
      if (
        options.failRefreshAfterApprove &&
        currentRequest.status === "approved" &&
        !refreshFailed
      ) {
        refreshFailed = true;
        throw new TypeError("refresh unavailable");
      }
      return jsonResponse({
        items: [currentRequest],
        count: 1,
        next_cursor: null,
        evidence: {
          pending_count: currentRequest.status === "pending" ? 1 : 0,
          my_pending_count: currentRequest.status === "pending" ? 1 : 0,
          active_policy_count: 1,
          catalog_revision: "0025_enterprise_approval_control",
          execution_adapter_status: "connected",
        },
      });
    }),
  );
}

describe("Stage 15 member role execution hook", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem("rag4c.base_url", "https://enterprise.test");
    installFetch();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
    sessionStorage.clear();
  });

  it("delivers a member-role ticket transiently and consumes the complete scope exactly once", async () => {
    render(
      <StrictMode>
        <Harness />
      </StrictMode>,
    );
    await screen.findByText("ready");
    await act(async () => {
      screen.getByText("approve").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage15-ticket-available").textContent).toBe("available"),
    );

    expect(document.body.textContent).not.toContain("opaque-ticket-member-role-15");
    expect(screen.getByTestId("stage15-execution-target").textContent).toBe("account-42");
    expect(screen.getByTestId("stage15-execution-facts").textContent).toContain(
      '"requested_role":"admin"',
    );

    await act(async () => {
      screen.getByText("execute").click();
      screen.getByText("execute").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage15-execution-state").textContent).toBe("success"),
    );

    const consumeCalls = vi
      .mocked(fetch)
      .mock.calls.filter(
        ([input, init]) => init?.method === "POST" && String(input).includes("/consume-ticket"),
      );
    expect(consumeCalls).toHaveLength(1);
    expect(JSON.parse(String(consumeCalls[0]?.[1]?.body))).toEqual({
      ticket: "opaque-ticket-member-role-15",
      revision: 5,
      action_type: "member_role_change",
      resource_type: "tenant_member",
      resource_id: "account-42",
    });
  });

  it("retains the same member-role ticket and idempotency key after an unconfirmed network failure", async () => {
    cleanup();
    vi.unstubAllGlobals();
    installFetch({ networkFailOnce: true });
    render(<Harness />);
    await screen.findByText("ready");
    await act(async () => {
      screen.getByText("approve").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage15-ticket-available").textContent).toBe("available"),
    );

    await act(async () => {
      screen.getByText("execute").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage15-ticket-available").textContent).toBe("available"),
    );
    await act(async () => {
      screen.getByText("execute").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage15-execution-state").textContent).toBe("success"),
    );

    const calls = vi
      .mocked(fetch)
      .mock.calls.filter(
        ([input, init]) => init?.method === "POST" && String(input).includes("/consume-ticket"),
      );
    expect(calls).toHaveLength(2);
    expect(new Headers(calls[1]?.[1]?.headers).get("Idempotency-Key")).toBe(
      new Headers(calls[0]?.[1]?.headers).get("Idempotency-Key"),
    );
    expect(JSON.parse(String(calls[1]?.[1]?.body))).toMatchObject({
      action_type: "member_role_change",
      resource_type: "tenant_member",
      resource_id: "account-42",
    });
  });

  it("captures the one-time ticket before an approval-success refresh failure", async () => {
    cleanup();
    vi.unstubAllGlobals();
    installFetch({ failRefreshAfterApprove: true });
    render(<Harness />);
    await screen.findByText("ready");
    await act(async () => {
      screen.getByText("approve").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage15-ticket-available").textContent).toBe("available"),
    );
    expect(screen.getByTestId("stage15-execution-target").textContent).toBe("account-42");
    expect(document.body.textContent).not.toContain("opaque-ticket-member-role-15");
  });
});
