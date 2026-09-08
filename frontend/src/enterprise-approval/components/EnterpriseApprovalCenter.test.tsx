// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import EnterpriseApprovalCenter from "./EnterpriseApprovalCenter";

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};
const context = {
  tenant: { id: "tenant-1", name: "星海科技", plan: "enterprise", status: "active" },
  actor: { id: "account-1", name: "林澈", email: "lin@example.com", role: "admin" },
  member_count: 4,
  dataset_count: 3,
  effective_permissions: ["knowledge.read"],
  role_permissions: { admin: ["knowledge.read"] },
  capabilities: {},
} as unknown as EnterpriseContext;
const request = {
  id: "request-1",
  policy_id: "policy-1",
  action_type: "dataset_acl_disable",
  resource_type: "knowledge_base",
  resource_id: "dataset-1",
  resource_name: "产品知识库",
  requester: { id: "account-2", name: "赵宁", email: "zhao@example.com" },
  reason: "准备停用知识库 ACL",
  status: "pending",
  required_approvals: 2,
  received_approvals: 1,
  expires_at: "2026-09-01T08:00:00Z",
  created_at: "2026-08-27T08:00:00Z",
  revision: 4,
  my_approval: "pending",
  request_snapshot: { dataset_id: "dataset-1", authorization: "secret" },
};
const detail = {
  request,
  approvers: [
    { id: "approver-1", kind: "account", ref: "account-1", status: "active", revision: 1 },
  ],
  decisions: [
    {
      id: "decision-1",
      approver: { id: "account-3", name: "顾问" },
      decision: "approved",
      comment: "范围已核对",
      decided_at: "2026-08-27T08:20:00Z",
    },
  ],
  process: [
    {
      id: "event-1",
      actor: { id: "account-2", name: "赵宁" },
      status: "submitted",
      occurred_at: "2026-08-27T08:00:00Z",
    },
    {
      id: "event-2",
      actor: { id: "account-3", name: "顾问" },
      status: "approved",
      occurred_at: "2026-08-27T08:20:00Z",
    },
  ],
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
function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
function setViewport(mobile: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: mobile,
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
function installFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url.includes("/policies")) return jsonResponse({ items: [policy], count: 1 });
      if (url.includes("/requests/request-1") && method === "GET") return jsonResponse(detail);
      if (method === "POST" || method === "PATCH") return jsonResponse({ request, policy });
      return jsonResponse({
        items: [request],
        count: 1,
        next_cursor: null,
        evidence: {
          pending_count: 8,
          my_pending_count: 2,
          active_policy_count: 1,
          catalog_revision: "0024_oidc_sso_runtime",
          execution_adapter_status: "execution_adapter_not_connected",
        },
      });
    }),
  );
}

describe("Stage 13 TDesign enterprise approval center", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "https://enterprise.test");
    setViewport(false);
    installFetch();
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("renders evidence, request filters and the dense request table with honest adapter status", async () => {
    render(<EnterpriseApprovalCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业审批中心" });
    expect(within(center).getByRole("tab", { name: "审批申请" })).toBeTruthy();
    expect(within(center).getByRole("tab", { name: "审批规则" })).toBeTruthy();
    expect(within(center).getAllByText("execution_adapter_not_connected").length).toBeGreaterThan(
      0,
    );
    expect(within(center).getByText("0024")).toBeTruthy();
    expect(within(center).getByText("停用知识库 ACL")).toBeTruthy();
    expect(within(center).getByRole("button", { name: "查看申请 request-1" })).toBeTruthy();
    expect(within(center).getByLabelText("审批申请关键词")).toBeTruthy();
  });

  it("opens detail tabs, requires rejection comment, and confirms approve/cancel actions", async () => {
    const user = userEvent.setup();
    render(<EnterpriseApprovalCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业审批中心" });
    await user.click(within(center).getByRole("button", { name: "查看申请 request-1" }));
    const drawer = await screen.findByRole("dialog", { name: "申请详情 request-1" });
    expect(within(drawer).getByText("申请详情")).toBeTruthy();
    await user.click(within(drawer).getByRole("tab", { name: "审批流程" }));
    expect(within(drawer).getByText("赵宁")).toBeTruthy();
    await user.click(within(drawer).getByRole("tab", { name: "申请详情" }));
    await user.click(within(drawer).getByRole("button", { name: "同意申请" }));
    const approveDialog = await screen.findByRole("dialog", { name: "确认同意申请" });
    expect(within(approveDialog).getByText("不会自动执行下游变更")).toBeTruthy();
    await user.click(within(approveDialog).getByRole("button", { name: "确认同意" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "申请详情 request-1" })).toBeNull(),
    );
    await user.click(within(center).getByRole("button", { name: "查看申请 request-1" }));
    const reopenedDrawer = await screen.findByRole("dialog", { name: "申请详情 request-1" });
    await user.click(within(reopenedDrawer).getByRole("button", { name: "拒绝申请" }));
    const rejectDialog = await screen.findByRole("dialog", { name: "拒绝申请 request-1" });
    expect(
      (within(rejectDialog).getByRole("button", { name: "提交拒绝" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    await user.type(within(rejectDialog).getByLabelText("拒绝原因"), "范围需要重新确认");
    expect(within(rejectDialog).getByText("8 / 500")).toBeTruthy();
    expect(
      (within(rejectDialog).getByRole("button", { name: "提交拒绝" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  }, 15_000);

  it("switches to rules and uses mobile priority cards without desktop duplication", async () => {
    setViewport(true);
    render(<EnterpriseApprovalCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业审批中心" });
    expect(within(center).getByTestId("approval-request-mobile-list")).toBeTruthy();
    expect(within(center).queryByTestId("approval-request-desktop-table")).toBeNull();
    const user = userEvent.setup();
    await user.click(within(center).getByRole("tab", { name: "审批规则" }));
    expect(within(center).getByRole("button", { name: "新建审批规则" })).toBeTruthy();
    expect(within(center).getByText("租户所有者")).toBeTruthy();
  });

  it("keeps backend ID-only actors visible and disables decisions without eligible approval facts", async () => {
    const backendRequest = {
      id: "request-1",
      policy_id: "policy-1",
      requester_id: "member-a",
      action_type: "dataset_acl_disable",
      resource_type: "knowledge_base",
      resource_id: "dataset-1",
      reason: "准备停用知识库 ACL",
      status: "pending",
      required_approvals: 2,
      received_approvals: 1,
      requested_at: "2026-08-27T08:00:00Z",
      expires_at: "2026-09-01T08:00:00Z",
      revision: 4,
      snapshot: { dataset_id: "dataset-1" },
    };
    const backendPolicy = {
      id: "policy-1",
      name: "知识库 ACL 变更",
      action_type: "dataset_acl_disable",
      resource_scope: "dataset-1",
      status: "active",
      required_approvals: 2,
      request_expiry_minutes: 1440,
      revision: 3,
    };
    const backendDetail = {
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
          decided_at: "2026-08-27T08:20:00Z",
          revision: 1,
        },
      ],
      execution: { state: "awaiting_approval" },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (url.includes("/requests/request-1") && method === "GET")
          return jsonResponse(backendDetail);
        if (url.includes("/policies"))
          return jsonResponse({ items: [backendPolicy], count: 1, next_cursor: null });
        if (url.includes("/requests"))
          return jsonResponse({ items: [backendRequest], count: 1, next_cursor: null });
        return jsonResponse({ detail: "not found" }, 404);
      }),
    );
    render(<EnterpriseApprovalCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业审批中心" });
    expect(await within(center).findByText(/member-a/)).toBeTruthy();
    const user = userEvent.setup();
    await user.click(within(center).getByRole("tab", { name: "审批规则" }));
    expect(await within(center).findByText("服务端未返回审批人")).toBeTruthy();
    await user.click(within(center).getByRole("tab", { name: "审批申请" }));
    await user.click(within(center).getByRole("button", { name: "查看申请 request-1" }));
    const drawer = await screen.findByRole("dialog", { name: "申请详情 request-1" });
    expect(
      within(drawer).getByRole("button", { name: "同意申请" }).getAttribute("aria-disabled"),
    ).toBe("true");
  });
});
