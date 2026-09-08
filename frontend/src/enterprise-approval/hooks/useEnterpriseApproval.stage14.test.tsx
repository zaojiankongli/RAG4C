// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import { useEnterpriseApprovalCenter } from "./useEnterpriseApproval";

const scope: EnterpriseScope = {
  tenantId: "tenant-stage14",
  datasetId: "dataset-14",
  actorToken: "actor-token-stage14",
};

const pendingRequest = {
  id: "request-14",
  policy_id: "policy-14",
  action_type: "dataset_acl_disable",
  resource_type: "knowledge_base",
  resource_id: "dataset-14",
  requester: { id: "requester-14", name: "申请人" },
  reason: "停用知识库 ACL",
  status: "pending",
  required_approvals: 2,
  received_approvals: 1,
  created_at: "2026-08-27T08:00:00Z",
  expires_at: "2026-09-01T08:00:00Z",
  revision: 4,
  my_approval: "pending",
  snapshot: { dataset_id: "dataset-14", expected_acl_revision: 7 },
};
const approvedRequest = {
  ...pendingRequest,
  status: "approved",
  received_approvals: 2,
  revision: 5,
};
const executedRequest = {
  ...approvedRequest,
  status: "executed",
  revision: 7,
};
const policy = {
  id: "policy-14",
  name: "知识库 ACL 变更",
  action_type: "dataset_acl_disable",
  resource_scope: "dataset-14",
  status: "active",
  required_approvals: 2,
  request_expiry_minutes: 1440,
  approvers: [{ kind: "role", ref: "owner" }],
  revision: 1,
};
const detail = (request: typeof pendingRequest, execution: Record<string, unknown>) => ({
  request,
  approvers: [{ kind: "role", ref: "owner", label: "租户所有者" }],
  decisions: [],
  process: [
    {
      id: "event-14",
      actor: { id: "requester-14", name: "申请人" },
      status: request.status,
      occurred_at: "2026-08-27T08:00:00Z",
    },
  ],
  execution,
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function Harness() {
  const workspace = useEnterpriseApprovalCenter(scope, {
    actorId: "approver-14",
    actorRole: "owner",
  });
  return (
    <div>
      <output data-testid="stage14-status">{workspace.status}</output>
      <output data-testid="stage14-ticket-available">
        {workspace.executionTicketAvailable ? "available" : "empty"}
      </output>
      <output data-testid="stage14-execution-state">
        {workspace.execution.saving
          ? "saving"
          : workspace.execution.success
            ? "success"
            : (workspace.execution.error?.code ?? "idle")}
      </output>
      <output data-testid="stage14-execution-target">
        {workspace.executionTarget?.resource_id ?? "none"}
      </output>
      <output data-testid="stage14-execution-error">
        {workspace.execution.error?.message ?? "none"}
      </output>
      <button onClick={() => void workspace.approve("request-14", 4)}>approve</button>
      <button onClick={() => void workspace.consumeExecutionTicket()}>execute</button>
      <button onClick={workspace.clearExecutionTicket}>clear</button>
    </div>
  );
}

function installFetch(options: { failConsume?: boolean; networkFailOnce?: boolean } = {}) {
  let currentRequest = pendingRequest;
  let networkFailed = false;
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
        });
      }
      if (method === "POST" && url.includes("/consume-ticket")) {
        if (options.networkFailOnce && !networkFailed) {
          networkFailed = true;
          throw new TypeError("network unavailable");
        }
        if (options.failConsume)
          return jsonResponse(
            { detail: { code: "execution_failed", message: "internal adapter details" } },
            502,
          );
        currentRequest = executedRequest;
        return jsonResponse({ request: executedRequest, execution: { state: "executed" } });
      }
      if (url.includes("/requests/request-14") && method === "GET")
        return jsonResponse(
          detail(
            currentRequest,
            currentRequest.status === "executed"
              ? { state: "executed", adapter: "connected" }
              : {
                  state:
                    currentRequest.status === "approved" ? "ticket_issued" : "awaiting_approval",
                  adapter: "connected",
                },
          ),
        );
      if (url.includes("/policies")) return jsonResponse({ items: [policy], count: 1 });
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

describe("Stage 14 approval execution hook", () => {
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

  it("keeps the final ticket transient, consumes it once under StrictMode, and sends the complete scope", async () => {
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
      expect(screen.getByTestId("stage14-ticket-available").textContent).toBe("available"),
    );

    expect(localStorage.getItem("rag4c.approval.ticket")).toBeNull();
    expect(sessionStorage.getItem("rag4c.approval.ticket")).toBeNull();
    expect(document.body.textContent).not.toContain("opaque-ticket-stage14");

    await act(async () => {
      screen.getByText("execute").click();
      screen.getByText("execute").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage14-execution-state").textContent).toBe("success"),
    );

    const consumeCalls = vi
      .mocked(fetch)
      .mock.calls.filter(
        ([input, init]) => init?.method === "POST" && String(input).includes("/consume-ticket"),
      );
    expect(consumeCalls).toHaveLength(1);
    expect(JSON.parse(String(consumeCalls[0]?.[1]?.body))).toEqual({
      ticket: "opaque-ticket-stage14",
      revision: 5,
      action_type: "dataset_acl_disable",
      resource_type: "knowledge_base",
      resource_id: "dataset-14",
    });
    expect(screen.getByTestId("stage14-ticket-available").textContent).toBe("empty");
    expect(screen.getByTestId("stage14-execution-target").textContent).toBe("dataset-14");
  });

  it("clears the transient ticket on close and maps an execution failure to stable copy", async () => {
    cleanup();
    vi.unstubAllGlobals();
    installFetch({ failConsume: true });
    render(<Harness />);
    await screen.findByText("ready");
    await act(async () => {
      screen.getByText("approve").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage14-ticket-available").textContent).toBe("available"),
    );
    await act(async () => {
      screen.getByText("clear").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage14-ticket-available").textContent).toBe("empty"),
    );
    expect(screen.getByTestId("stage14-execution-target").textContent).toBe("none");

    await act(async () => {
      screen.getByText("approve").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage14-ticket-available").textContent).toBe("available"),
    );
    await act(async () => {
      screen.getByText("execute").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage14-execution-state").textContent).toBe("execution_failed"),
    );
    expect(screen.getByTestId("stage14-execution-error").textContent).toContain("未完成");
    expect(document.body.textContent).not.toContain("internal adapter details");
  });

  it("retains the same transient ticket and idempotency key after an unconfirmed network failure", async () => {
    cleanup();
    vi.unstubAllGlobals();
    installFetch({ networkFailOnce: true });
    render(<Harness />);
    await screen.findByText("ready");
    await act(async () => {
      screen.getByText("approve").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage14-ticket-available").textContent).toBe("available"),
    );
    await act(async () => {
      screen.getByText("execute").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage14-ticket-available").textContent).toBe("available"),
    );
    expect(screen.getByTestId("stage14-execution-target").textContent).toBe("dataset-14");
    await act(async () => {
      screen.getByText("execute").click();
    });
    await waitFor(() =>
      expect(screen.getByTestId("stage14-execution-state").textContent).toBe("success"),
    );
    const calls = vi
      .mocked(fetch)
      .mock.calls.filter(
        ([input, init]) => init?.method === "POST" && String(input).includes("/consume-ticket"),
      );
    expect(calls).toHaveLength(2);
    const firstKey = new Headers(calls[0]?.[1]?.headers).get("Idempotency-Key");
    const secondKey = new Headers(calls[1]?.[1]?.headers).get("Idempotency-Key");
    expect(firstKey).toBeTruthy();
    expect(secondKey).toBe(firstKey);
  });
});
