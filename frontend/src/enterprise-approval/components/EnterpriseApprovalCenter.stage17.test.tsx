// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import EnterpriseApprovalCenter from "./EnterpriseApprovalCenter";

const scope: EnterpriseScope = {
  tenantId: "tenant-stage17",
  datasetId: "dataset-stage17",
  actorToken: "actor-token-stage17",
};

const context = {
  tenant: { id: "tenant-stage17", name: "星海科技", plan: "enterprise", status: "active" },
  actor: { id: "account-stage17", name: "林澈", email: "lin@example.com", role: "admin" },
  member_count: 4,
  dataset_count: 3,
  effective_permissions: ["knowledge.read"],
  role_permissions: { admin: ["knowledge.read"] },
  capabilities: {},
} as unknown as EnterpriseContext;

const request = {
  id: "request-stage17",
  policy_id: "policy-stage17",
  action_type: "workspace_authorization_mode_change",
  resource_type: "tenant_workspace",
  resource_id: "workspace-stage17",
  resource_name: "平台运营 Workspace",
  requester: { id: "requester-stage17", name: "赵宁", email: "zhao@example.com" },
  reason: "将 Workspace 从 shadow 切换为 enforced",
  status: "pending",
  required_approvals: 2,
  received_approvals: 1,
  expires_at: "2026-09-01T08:00:00Z",
  created_at: "2026-08-27T08:00:00Z",
  revision: 4,
  my_approval: "pending",
  request_snapshot: {
    workspace_id: "workspace-stage17",
    from_mode: "shadow",
    target_mode: "enforced",
    metadata: "opaque-ticket-stage17-in-snapshot",
    value: "raw-token-stage17-in-snapshot",
    data: { payload: "raw-secret-stage17-in-snapshot" },
  },
};

const detail = {
  request,
  approvers: [{ id: "approver-stage17", kind: "role", ref: "owner", label: "租户所有者" }],
  decisions: [],
  process: [],
};

const policy = {
  id: "policy-stage17",
  name: "Workspace 授权模式变更",
  action_type: "workspace_authorization_mode_change",
  resource_scope: "workspace-stage17",
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

function setViewport(mobile = false) {
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
      if (url.includes("/requests/request-stage17") && method === "GET")
        return jsonResponse(detail);
      if (url.includes("/policies")) return jsonResponse({ items: [policy], count: 1 });
      if (url.includes("/requests"))
        return jsonResponse({
          items: [request],
          count: 1,
          next_cursor: null,
          evidence: {
            pending_count: 1,
            my_pending_count: 1,
            active_policy_count: 1,
            catalog_revision: "0027_enterprise_workspace_authorization",
            execution_adapter_status: "execution_adapter_not_connected",
          },
        });
      return jsonResponse({ detail: "not found" }, 404);
    }),
  );
}

function closeVisibleDrawer() {
  const button = document.querySelector(".t-drawer__close-btn") as HTMLButtonElement | null;
  if (!button) throw new Error("approval drawer close button was not rendered");
  return userEvent.setup().click(button);
}

function optionLabels() {
  return Array.from(document.querySelectorAll(".t-select-option")).map((option) =>
    option.textContent?.trim(),
  );
}

function storageText(storage: Storage) {
  return Array.from({ length: storage.length }, (_, index) => {
    const key = storage.key(index) ?? "";
    return `${key}:${storage.getItem(key) ?? ""}`;
  }).join("\n");
}

describe("Stage 17 enterprise approval center", () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    setViewport();
    installFetch();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
    localStorage.clear();
    sessionStorage.clear();
  });

  it.each([
    ["direct", "/enterprise/approvals?request=request-stage17"],
    ["hash", "/#/enterprise/approvals?request=request-stage17"],
  ])("opens a %s request deep link after the approval data load", async (_mode, url) => {
    window.history.replaceState(null, "", url);
    render(<EnterpriseApprovalCenter scope={scope} context={context} />);

    expect(
      await screen.findByRole("dialog", { name: "申请详情 request-stage17" }, { timeout: 5000 }),
    ).toBeTruthy();
  });

  it.each([
    ["direct", "/enterprise/approvals?status=pending&request=request-stage17"],
    ["hash", "/#/enterprise/approvals?status=pending&request=request-stage17"],
  ])("cleans a %s request query on close without changing the route mode", async (_mode, url) => {
    window.history.replaceState(null, "", url);
    render(<EnterpriseApprovalCenter scope={scope} context={context} />);
    await screen.findByRole("dialog", { name: "申请详情 request-stage17" }, { timeout: 5000 });

    await closeVisibleDrawer();
    await waitFor(() => expect(screen.queryByRole("dialog", { name: /申请详情/ })).toBeNull());

    if (_mode === "direct") {
      expect(window.location.pathname).toBe("/enterprise/approvals");
      expect(window.location.search).toBe("?status=pending");
      expect(window.location.hash).toBe("");
    } else {
      expect(window.location.pathname).toBe("/");
      expect(window.location.search).toBe("");
      expect(window.location.hash).toBe("#/enterprise/approvals?status=pending");
    }
  });

  it("retries a failed request deep link with the same request id", async () => {
    let detailAttempts = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (url.includes("/requests/request-stage17") && method === "GET") {
          detailAttempts += 1;
          if (detailAttempts === 1) {
            return jsonResponse(
              { detail: { code: "approval_service_unavailable", message: "temporary" } },
              503,
            );
          }
          return jsonResponse(detail);
        }
        if (url.includes("/policies")) return jsonResponse({ items: [policy], count: 1 });
        if (url.includes("/requests")) {
          return jsonResponse({
            items: [request],
            count: 1,
            next_cursor: null,
            evidence: {
              pending_count: 1,
              my_pending_count: 1,
              active_policy_count: 1,
              catalog_revision: "0027_enterprise_workspace_authorization",
              execution_adapter_status: "execution_adapter_not_connected",
            },
          });
        }
        return jsonResponse({ detail: "not found" }, 404);
      }),
    );
    window.history.replaceState(null, "", "/enterprise/approvals?request=request-stage17");
    render(<EnterpriseApprovalCenter scope={scope} context={context} />);

    expect(await screen.findByText("审批服务暂不可用")).toBeTruthy();
    await userEvent.setup().click(screen.getByRole("button", { name: "重新读取" }));
    expect(await screen.findByRole("dialog", { name: "申请详情 request-stage17" })).toBeTruthy();
    expect(detailAttempts).toBe(2);
  });

  it("shows the Stage 17 action in the filter, policy form, and request table while sanitizing facts", async () => {
    render(<EnterpriseApprovalCenter scope={scope} context={context} />);
    const center = await screen.findByRole("region", { name: "企业审批中心" });

    expect(within(center).getByText("变更 Workspace 授权模式")).toBeTruthy();

    const user = userEvent.setup();
    await user.click(within(center).getByRole("button", { name: "查看申请 request-stage17" }));
    await screen.findByRole("dialog", { name: "申请详情 request-stage17" });
    expect(document.body.textContent).not.toContain("opaque-ticket-stage17-in-snapshot");
    expect(document.body.textContent).not.toContain("raw-token-stage17-in-snapshot");
    expect(document.body.textContent).not.toContain("raw-secret-stage17-in-snapshot");
    expect(document.documentElement.innerHTML).not.toContain("opaque-ticket-stage17-in-snapshot");
    expect(document.documentElement.innerHTML).not.toContain("raw-token-stage17-in-snapshot");
    expect(document.documentElement.innerHTML).not.toContain("raw-secret-stage17-in-snapshot");
    expect(storageText(localStorage)).not.toContain("opaque-ticket-stage17-in-snapshot");
    expect(storageText(localStorage)).not.toContain("raw-token-stage17-in-snapshot");
    expect(storageText(localStorage)).not.toContain("raw-secret-stage17-in-snapshot");
    expect(storageText(sessionStorage)).not.toContain("raw-token-stage17-in-snapshot");
    expect(storageText(sessionStorage)).not.toContain("opaque-ticket-stage17-in-snapshot");
    expect(storageText(sessionStorage)).not.toContain("raw-secret-stage17-in-snapshot");
    await closeVisibleDrawer();

    const actionFilter = center.querySelectorAll(".approval-filters .t-select")[1];
    if (!actionFilter) throw new Error("approval action filter was not rendered");
    await user.click(actionFilter);
    await waitFor(() => expect(optionLabels()).toContain("变更 Workspace 授权模式"));

    await user.click(within(center).getByRole("tab", { name: "审批规则" }));
    await user.click(within(center).getByRole("button", { name: "新建审批规则" }));
    const policyDialog = await screen.findByRole("dialog", { name: "新建审批规则" });
    const policyActionSelect = policyDialog.querySelector(".approval-policy-form .t-select");
    if (!policyActionSelect) throw new Error("approval policy action select was not rendered");
    await user.click(policyActionSelect);
    await waitFor(() => expect(optionLabels()).toContain("变更 Workspace 授权模式"));
  });
});
