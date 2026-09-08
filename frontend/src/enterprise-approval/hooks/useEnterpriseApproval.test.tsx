// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import { useEnterpriseApprovalCenter } from "./useEnterpriseApproval";

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};
const request = {
  id: "request-1",
  policy_id: "policy-1",
  action_type: "dataset_acl_disable",
  resource_type: "knowledge_base",
  resource_id: "dataset-1",
  requester: { id: "account-2", name: "赵宁" },
  reason: "调整知识库 ACL",
  status: "pending",
  required_approvals: 2,
  received_approvals: 1,
  expires_at: "2026-09-01T08:00:00Z",
  created_at: "2026-08-27T08:00:00Z",
  revision: 4,
  my_approval: "pending",
};
const detail = {
  request,
  decisions: [],
  process: [
    {
      id: "event-1",
      actor: { id: "account-2", name: "赵宁" },
      status: "submitted",
      occurred_at: "2026-08-27T08:00:00Z",
    },
  ],
};
const policy = {
  id: "policy-1",
  name: "ACL 变更",
  action_type: "dataset_acl_disable",
  resource_scope: "dataset-1",
  status: "active",
  required_approvals: 2,
  request_expiry_minutes: 1440,
  approvers: [{ kind: "role", ref: "owner" }],
  revision: 3,
};
function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
function Harness() {
  const workspace = useEnterpriseApprovalCenter(scope);
  return (
    <div>
      <output data-testid="status">{workspace.status}</output>
      <output data-testid="requests">{workspace.requests.items.length}</output>
      <output data-testid="detail">{workspace.selectedRequest?.id ?? "none"}</output>
      <output data-testid="error">{workspace.mutation.error?.message ?? "none"}</output>
      <button onClick={() => void workspace.openRequest("request-1")}>open</button>
      <button onClick={() => void workspace.approve("request-1", 4)}>approve</button>
    </div>
  );
}

describe("Stage 13 approval data hook", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "https://enterprise.test");
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (url.includes("/policies")) return jsonResponse({ items: [policy], count: 1 });
        if (url.includes("/requests/request-1") && method === "GET") return jsonResponse(detail);
        if (method === "POST") return jsonResponse({ request });
        return jsonResponse({
          items: [request],
          count: 1,
          next_cursor: null,
          evidence: {
            pending_count: 1,
            my_pending_count: 1,
            active_policy_count: 1,
            catalog_revision: "0024_oidc_sso_runtime",
            execution_adapter_status: "execution_adapter_not_connected",
          },
        });
      }),
    );
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("loads requests and policies without replacing empty server evidence", async () => {
    render(<Harness />);
    expect((await screen.findByTestId("status")).textContent).toBe("ready");
    expect(screen.getByTestId("requests").textContent).toBe("1");
    expect(screen.getByTestId("error").textContent).toBe("none");
  });

  it("loads a detail projection on demand and keeps mutation single-flight", async () => {
    render(<Harness />);
    await screen.findByText("open");
    await act(async () => {
      screen.getByText("open").click();
    });
    await waitFor(() => expect(screen.getByTestId("detail").textContent).toBe("request-1"));
    await act(async () => {
      screen.getByText("approve").click();
      screen.getByText("approve").click();
    });
    await waitFor(() =>
      expect(
        vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === "POST"),
      ).toHaveLength(1),
    );
  });

  it("projects migration, conflict and forbidden errors into safe action copy", async () => {
    vi.mocked(fetch).mockImplementation(async (_input, init) => {
      if (init?.method === "POST")
        return jsonResponse(
          { detail: { code: "migration_required", message: "internal stack" } },
          503,
        );
      return jsonResponse({ items: [], count: 0 });
    });
    render(<Harness />);
    await screen.findByTestId("status");
    await act(async () => {
      screen.getByText("approve").click();
    });
    await waitFor(() => expect(screen.getByTestId("error").textContent).toContain("0025"));
    expect(screen.getByTestId("error").textContent).not.toContain("internal stack");
  });
});
