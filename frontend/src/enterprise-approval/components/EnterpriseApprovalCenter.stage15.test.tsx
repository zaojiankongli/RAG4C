// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import EnterpriseApprovalCenter from "./EnterpriseApprovalCenter";

const scope: EnterpriseScope = {
  tenantId: "tenant-stage15",
  datasetId: "dataset-stage15",
  actorToken: "actor-token-stage15",
};
const context = {
  tenant: { id: "tenant-stage15", name: "星海科技", plan: "enterprise", status: "active" },
  actor: { id: "approver-stage15", name: "审批人", email: "approver@example.com", role: "owner" },
  member_count: 4,
  dataset_count: 3,
  effective_permissions: ["knowledge.read"],
  role_permissions: { owner: ["knowledge.read"] },
  capabilities: {},
} as unknown as EnterpriseContext;

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
  options: { adapter?: string; actionType?: string; resourceType?: string } = {},
) {
  const actionType = options.actionType ?? "member_role_change";
  const resourceType = options.resourceType ?? "tenant_member";
  const adapter = options.adapter ?? "connected";
  let currentRequest = { ...pendingRequest, action_type: actionType, resource_type: resourceType };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (method === "POST" && url.includes("/approve")) {
        currentRequest = {
          ...currentRequest,
          ...approvedRequest,
          action_type: actionType,
          resource_type: resourceType,
        };
        return jsonResponse({
          request: currentRequest,
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
            adapter,
            ticket: "opaque-ticket-component-member-role-15",
          },
        });
      }
      if (method === "POST" && url.includes("/consume-ticket")) {
        currentRequest = {
          ...currentRequest,
          ...executedRequest,
          action_type: actionType,
          resource_type: resourceType,
        };
        return jsonResponse({ request: currentRequest, execution: { state: "executed", adapter } });
      }
      if (url.includes("/requests/request-member-role-15") && method === "GET")
        return jsonResponse({
          request: currentRequest,
          approvers: [{ kind: "role", ref: "owner", label: "租户所有者" }],
          decisions: [],
          process: [],
          execution: { state: currentRequest.status, adapter },
        });
      if (url.includes("/policies"))
        return jsonResponse({
          items: [
            {
              id: "policy-member-role-15",
              name: "成员角色变更",
              action_type: actionType,
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

async function openAndApprove() {
  const user = userEvent.setup();
  const center = await screen.findByRole("region", { name: "企业审批中心" });
  await user.click(within(center).getByRole("button", { name: "查看申请 request-member-role-15" }));
  const drawer = await screen.findByRole("dialog", { name: "申请详情 request-member-role-15" });
  await user.click(within(drawer).getByRole("button", { name: "同意申请" }));
  const approveDialog = await screen.findByRole("dialog", { name: "确认同意申请" });
  await user.click(within(approveDialog).getByRole("button", { name: "确认同意" }));
}

describe("Stage 15 member role approval execution center", () => {
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

  it("shows generic member-role facts without raw ticket and executes the connected request", async () => {
    installFetch();
    await (render(<EnterpriseApprovalCenter scope={scope} context={context} />), openAndApprove());

    const executionDialog = await screen.findByRole("dialog", { name: "一次性执行授权" });
    expect(within(executionDialog).getByText("调整成员角色")).toBeTruthy();
    expect(within(executionDialog).getByText("tenant_member / account-42")).toBeTruthy();
    expect(within(executionDialog).getByText("目标账号")).toBeTruthy();
    expect(within(executionDialog).getByText("申请角色")).toBeTruthy();
    expect(within(executionDialog).getByText("admin")).toBeTruthy();
    expect(within(executionDialog).getByText("ticket 仅显示/使用一次")).toBeTruthy();
    expect(
      within(executionDialog).queryByText("opaque-ticket-component-member-role-15"),
    ).toBeNull();
    expect(executionDialog.querySelector("[data-ticket]")).toBeNull();

    await userEvent
      .setup()
      .click(within(executionDialog).getByRole("button", { name: "执行已批准变更" }));
    await waitFor(() => expect(screen.getAllByText("已执行").length).toBeGreaterThan(0));
    expect(document.body.textContent).not.toContain("opaque-ticket-component-member-role-15");
  }, 15_000);

  it.each([375, 280])(
    "keeps generic member-role execution facts usable at %ipx",
    async (width) => {
      setViewport(width);
      installFetch();
      render(<EnterpriseApprovalCenter scope={scope} context={context} />);
      await openAndApprove();

      const executionDialog = await screen.findByRole("dialog", { name: "一次性执行授权" });
      expect(within(executionDialog).getByText("调整成员角色")).toBeTruthy();
      expect(within(executionDialog).getByText("当前角色")).toBeTruthy();
      expect(within(executionDialog).getByText("申请角色")).toBeTruthy();
      expect(executionDialog.querySelector("[data-ticket]")).toBeNull();
      expect(document.body.textContent).not.toContain("opaque-ticket-component-member-role-15");
    },
    15_000,
  );

  it.each([
    ["execution_adapter_not_connected", "member_role_change", "tenant_member"],
    ["connected", "catalog_upgrade", "tenant_member"],
    ["connected", "member_role_change", "knowledge_base"],
  ])(
    "does not render an execution button for adapter=%s action=%s resource=%s",
    async (adapter, actionType, resourceType) => {
      installFetch({ adapter, actionType, resourceType });
      render(<EnterpriseApprovalCenter scope={scope} context={context} />);
      await openAndApprove();
      await waitFor(() =>
        expect(screen.queryByRole("dialog", { name: "确认同意申请" })).toBeNull(),
      );
      expect(screen.queryByRole("dialog", { name: "一次性执行授权" })).toBeNull();
      expect(screen.queryByRole("button", { name: "执行已批准变更" })).toBeNull();
      expect(document.body.textContent).not.toContain("opaque-ticket-component-member-role-15");
    },
  );
});
