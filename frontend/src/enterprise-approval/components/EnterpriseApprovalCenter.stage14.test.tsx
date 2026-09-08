// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import EnterpriseApprovalCenter from "./EnterpriseApprovalCenter";

const scope: EnterpriseScope = {
  tenantId: "tenant-stage14",
  datasetId: "dataset-14",
  actorToken: "actor-token-stage14",
};
const context = {
  tenant: { id: "tenant-stage14", name: "星海科技", plan: "enterprise", status: "active" },
  actor: { id: "approver-14", name: "审批人", email: "approver@example.com", role: "owner" },
  member_count: 4,
  dataset_count: 3,
  effective_permissions: ["knowledge.read"],
  role_permissions: { owner: ["knowledge.read"] },
  capabilities: {},
} as unknown as EnterpriseContext;

const pendingRequest = {
  id: "request-14",
  policy_id: "policy-14",
  action_type: "dataset_acl_disable",
  resource_type: "knowledge_base",
  resource_id: "dataset-14",
  resource_name: "产品知识库",
  requester: { id: "requester-14", name: "申请人" },
  reason: "停用知识库 ACL",
  status: "pending",
  required_approvals: 2,
  received_approvals: 1,
  created_at: "2026-08-27T08:00:00Z",
  expires_at: "2026-09-01T08:00:00Z",
  revision: 4,
  my_approval: "pending",
  my_approval_eligible: true,
  snapshot: { dataset_id: "dataset-14", expected_acl_revision: 7 },
};
const approvedRequest = {
  ...pendingRequest,
  status: "approved",
  received_approvals: 2,
  revision: 5,
};
const executedRequest = { ...approvedRequest, status: "executed", revision: 7 };
const policy = {
  id: "policy-14",
  name: "知识库 ACL 变更",
  action_type: "dataset_acl_disable",
  resource_scope: "dataset-14",
  status: "active",
  required_approvals: 2,
  request_expiry_minutes: 1440,
  approvers: [{ kind: "role", ref: "owner", label: "租户所有者" }],
  revision: 1,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function setViewport(width: number) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: width <= 600,
      media: "(max-width: 600px)",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

function installFetch(
  options: {
    adapter?: string;
    actionType?: string;
    finalTicket?: string;
    failConsume?: boolean;
  } = {},
) {
  const actionType = options.actionType ?? "dataset_acl_disable";
  const adapter = options.adapter ?? "connected";
  let currentRequest = { ...pendingRequest, action_type: actionType };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (method === "POST" && url.includes("/approve")) {
        currentRequest = { ...currentRequest, ...approvedRequest, action_type: actionType };
        return jsonResponse({
          request: currentRequest,
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
            adapter,
            ...(options.finalTicket ? { ticket: options.finalTicket } : {}),
          },
        });
      }
      if (method === "POST" && url.includes("/consume-ticket")) {
        if (options.failConsume)
          return jsonResponse(
            { detail: { code: "execution_failed", message: "internal adapter details" } },
            502,
          );
        currentRequest = { ...currentRequest, ...executedRequest, action_type: actionType };
        return jsonResponse({ request: currentRequest, execution: { state: "executed", adapter } });
      }
      if (url.includes("/requests/request-14") && method === "GET")
        return jsonResponse({
          request: currentRequest,
          approvers: [{ kind: "role", ref: "owner", label: "租户所有者" }],
          decisions: [],
          process: [],
          execution: { state: currentRequest.status, adapter },
        });
      if (url.includes("/policies"))
        return jsonResponse({ items: [{ ...policy, action_type: actionType }], count: 1 });
      return jsonResponse({
        items: [currentRequest],
        count: 1,
        next_cursor: null,
        evidence: {
          pending_count: currentRequest.status === "pending" ? 1 : 0,
          my_pending_count: currentRequest.status === "pending" ? 1 : 0,
          active_policy_count: 1,
          catalog_revision: "0025_enterprise_approval_control",
          execution_adapter_status: adapter,
        },
      });
    }),
  );
}

describe("Stage 14 approval execution center", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    localStorage.setItem("rag4c.base_url", "https://enterprise.test");
    setViewport(1440);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
    sessionStorage.clear();
  });

  it("shows a connected dataset ACL one-time dialog and refreshes the request to executed", async () => {
    installFetch({ finalTicket: "opaque-ticket-component" });
    const user = userEvent.setup();
    render(<EnterpriseApprovalCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业审批中心" });
    await user.click(within(center).getByRole("button", { name: "查看申请 request-14" }));
    const drawer = await screen.findByRole("dialog", { name: "申请详情 request-14" });
    await user.click(within(drawer).getByRole("button", { name: "同意申请" }));
    const approveDialog = await screen.findByRole("dialog", { name: "确认同意申请" });
    await user.click(within(approveDialog).getByRole("button", { name: "确认同意" }));

    const executionDialog = await screen.findByRole("dialog", { name: "一次性执行授权" });
    expect(screen.queryByRole("dialog", { name: "申请详情 request-14" })).toBeNull();
    expect(within(executionDialog).getByText("ticket 仅显示/使用一次")).toBeTruthy();
    expect(within(executionDialog).getByText("执行适配器已连接")).toBeTruthy();
    expect(within(executionDialog).queryByText("opaque-ticket-component")).toBeNull();
    expect(within(executionDialog).getByRole("button", { name: "执行已批准变更" })).toBeTruthy();

    await user.click(within(executionDialog).getByRole("button", { name: "执行已批准变更" }));
    await waitFor(() => expect(screen.getAllByText("已执行").length).toBeGreaterThan(0));
    expect(screen.queryByRole("button", { name: "执行已批准变更" })).toBeNull();
    expect(document.body.textContent).not.toContain("opaque-ticket-component");
  }, 15_000);

  it.each([375, 280])("keeps the one-time execution surface usable at %ipx", async (width) => {
    setViewport(width);
    installFetch({ finalTicket: `opaque-ticket-${width}` });
    const user = userEvent.setup();
    render(<EnterpriseApprovalCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业审批中心" });
    await user.click(within(center).getByRole("button", { name: "查看申请 request-14" }));
    const drawer = await screen.findByRole("dialog", { name: "申请详情 request-14" });
    await user.click(within(drawer).getByRole("button", { name: "同意申请" }));
    const approveDialog = await screen.findByRole("dialog", { name: "确认同意申请" });
    await user.click(within(approveDialog).getByRole("button", { name: "确认同意" }));
    const executionDialog = await screen.findByRole("dialog", { name: "一次性执行授权" });
    expect(within(executionDialog).getByRole("button", { name: "执行已批准变更" })).toBeTruthy();
    expect(executionDialog.querySelector("[data-ticket]")).toBeNull();
    expect(document.body.textContent).not.toContain(`opaque-ticket-${width}`);
  });

  it.each([
    ["execution_adapter_not_connected", "dataset_acl_disable"],
    ["connected", "catalog_upgrade"],
  ])(
    "does not render an execution button for adapter=%s action=%s",
    async (adapter, actionType) => {
      installFetch({ adapter, actionType, finalTicket: "opaque-ticket-no-button" });
      const user = userEvent.setup();
      render(<EnterpriseApprovalCenter scope={scope} context={context} />);
      const center = await screen.findByRole("region", { name: "企业审批中心" });
      await user.click(within(center).getByRole("button", { name: "查看申请 request-14" }));
      const drawer = await screen.findByRole("dialog", { name: "申请详情 request-14" });
      await user.click(within(drawer).getByRole("button", { name: "同意申请" }));
      const approveDialog = await screen.findByRole("dialog", { name: "确认同意申请" });
      await user.click(within(approveDialog).getByRole("button", { name: "确认同意" }));
      await waitFor(() =>
        expect(screen.queryByRole("dialog", { name: "确认同意申请" })).toBeNull(),
      );
      expect(screen.queryByRole("dialog", { name: "一次性执行授权" })).toBeNull();
      expect(screen.queryByRole("button", { name: "执行已批准变更" })).toBeNull();
      expect(document.body.textContent).not.toContain("opaque-ticket-no-button");
    },
  );
});
