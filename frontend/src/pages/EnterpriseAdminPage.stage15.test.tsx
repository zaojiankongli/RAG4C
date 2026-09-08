// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY,
  KNOWLEDGE_DATASET_STORAGE_KEY,
  KNOWLEDGE_TENANT_STORAGE_KEY,
} from "../knowledge/workspaceScope";
import EnterpriseAdminPage from "./EnterpriseAdminPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const context = {
  tenant: {
    id: "tenant-1",
    name: "星海科技",
    plan: "enterprise",
    status: "active",
    quota_documents: 10000,
    quota_chunks: 1000000,
    doc_count: 98,
    chunk_count: 12420,
  },
  actor: { id: "account-1", name: "林澈", email: "lin@example.com", role: "admin" },
  member_count: 1,
  dataset_count: 1,
  effective_permissions: ["knowledge.read", "knowledge.manage"],
  role_permissions: { admin: ["knowledge.read", "knowledge.manage"] },
  capabilities: {
    member_directory: { state: "ready", label: "成员目录", reason: null },
    member_mutations: { state: "ready", label: "成员变更", reason: null },
    organization_units: { state: "unavailable", label: "组织架构", reason: "未接入" },
    user_groups: { state: "unavailable", label: "用户组", reason: "未接入" },
    dataset_acl: { state: "unavailable", label: "知识库 ACL", reason: "未接入" },
    knowledge_audit: { state: "ready", label: "知识库审计", reason: null },
    tenant_audit: { state: "unavailable", label: "企业管理日志", reason: "未接入" },
    invitations: { state: "unavailable", label: "成员邀请", reason: "未接入" },
    sso: { state: "unavailable", label: "企业 SSO", reason: "未接入" },
  },
};

const member = {
  membership_id: 9,
  account_id: "account-42",
  name: "周宁",
  email: "zhou@example.com",
  role: "member",
  joined_at: "2026-08-20T08:00:00Z",
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

const approvalRequest = {
  id: "request-role-1",
  policy_id: "policy-role-1",
  action_type: "member_role_change",
  resource_type: "tenant_member",
  resource_id: "account-42",
  requester: { id: "account-1", name: "林澈", role: "admin" },
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

const access = {
  dataset_id: "dataset-1",
  owner_id: "account-1",
  visibility: "private",
  enforcement_mode: "tenant_role",
  actor_role: "admin",
  effective_permissions: ["knowledge.read", "knowledge.manage"],
  dataset_acl_supported: false,
  group_grants_supported: false,
  organization_inheritance_supported: false,
  warnings: [],
};

function installScope() {
  localStorage.setItem(KNOWLEDGE_TENANT_STORAGE_KEY, "tenant-1");
  localStorage.setItem(KNOWLEDGE_DATASET_STORAGE_KEY, "dataset-1");
  localStorage.setItem(KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY, "actor-token");
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  window.history.replaceState(null, "", "/");
});

describe("Stage15 member directory approval UX", () => {
  beforeEach(() => {
    installScope();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
  });

  it("loads member_role_change policy, submits a sanitized snapshot, and leaves the member role unchanged", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        calls.push({ url, init });
        if (url.includes("/api/enterprise/context")) return Promise.resolve(jsonResponse(context));
        if (url.includes("/api/enterprise/members") && (init?.method ?? "GET") === "GET")
          return Promise.resolve(jsonResponse({ items: [member], count: 1, next_before_id: null }));
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        if (url.includes("/api/enterprise/readiness"))
          return Promise.resolve(jsonResponse({ status: "ready", mutations_safe: true }));
        if (url.includes("/api/enterprise/approvals/policies"))
          return Promise.resolve(jsonResponse({ items: [policy], count: 1, next_cursor: null }));
        if (url.includes("/api/enterprise/approvals/requests") && init?.method === "POST")
          return Promise.resolve(jsonResponse(approvalRequest));
        if (url.includes("/api/enterprise/audit-events"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_sequence: null }));
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);
    const directory = await screen.findByRole("region", { name: "成员目录" });
    await userEvent.click(within(directory).getByRole("button", { name: /修改角色/ }));
    const dialog = await screen.findByRole("dialog", { name: "修改成员角色" });
    expect(await within(dialog).findByText("成员角色双人复核")).toBeTruthy();
    expect(within(dialog).getByText("2 人")).toBeTruthy();
    expect(within(dialog).getByText("60 分钟")).toBeTruthy();
    expect(within(dialog).getByText("revision 9")).toBeTruthy();

    await userEvent.selectOptions(
      within(dialog).getByRole("combobox", { name: "新角色" }),
      "editor",
    );
    await userEvent.type(within(dialog).getByLabelText("变更原因"), "职责调整");
    await userEvent.click(within(dialog).getByRole("button", { name: "提交审批申请" }));

    await waitFor(() => expect(within(dialog).getByText("request-role-1")).toBeTruthy());
    const requestCall = calls.find(
      ({ url, init }) =>
        url.includes("/api/enterprise/approvals/requests") && init?.method === "POST",
    );
    expect(requestCall).toBeTruthy();
    expect(JSON.parse(String(requestCall?.init?.body))).toMatchObject({
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
    });
    expect(within(directory).getAllByText("成员").length).toBeGreaterThan(0);
    expect(within(directory).queryByText("编辑者")).toBeNull();
    expect(calls.some(({ url }) => url.includes("/api/enterprise/members/account-42/role"))).toBe(
      false,
    );
  }, 10000);

  it("keeps suspend/resume on the existing member endpoints without loading role approval policies", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        urls.push(url);
        if (url.includes("/api/enterprise/context")) return Promise.resolve(jsonResponse(context));
        if (url.includes("/api/enterprise/members") && (init?.method ?? "GET") === "GET")
          return Promise.resolve(jsonResponse({ items: [member], count: 1, next_before_id: null }));
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        if (url.includes("/api/enterprise/readiness"))
          return Promise.resolve(jsonResponse({ status: "ready", mutations_safe: true }));
        if (url.includes("/api/enterprise/audit-events"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_sequence: null }));
        if (url.includes("/api/enterprise/members/account-42/suspend"))
          return Promise.resolve(
            jsonResponse({ member: { ...member, status: "suspended", revision: 8 } }),
          );
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);
    const directory = await screen.findByRole("region", { name: "成员目录" });
    await userEvent.click(within(directory).getByRole("button", { name: /暂停成员/ }));
    const dialog = await screen.findByRole("dialog", { name: "暂停成员" });
    await userEvent.type(within(dialog).getByLabelText("变更原因"), "临时停用");
    await userEvent.click(within(dialog).getByRole("button", { name: "提交成员变更" }));

    await waitFor(() => expect(urls.some((url) => url.includes("/suspend"))).toBe(true));
    expect(urls.some((url) => url.includes("/api/enterprise/approvals/policies"))).toBe(false);
  });

  it("turns a server-side 409 approval hint into approval mode without persisting request data", async () => {
    const storageSetItem = vi.spyOn(Storage.prototype, "setItem");
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context")) return Promise.resolve(jsonResponse(context));
        if (url.includes("/api/enterprise/members") && (init?.method ?? "GET") === "GET")
          return Promise.resolve(jsonResponse({ items: [member], count: 1, next_before_id: null }));
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        if (url.includes("/api/enterprise/readiness"))
          return Promise.resolve(jsonResponse({ status: "ready", mutations_safe: true }));
        if (url.includes("/api/enterprise/approvals/policies"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }));
        if (url.includes("/api/enterprise/members/account-42/role"))
          return Promise.resolve(
            jsonResponse(
              {
                detail: {
                  code: "member_role_approval_required",
                  message: "internal policy details must not render",
                  policy: {
                    id: "policy-role-fallback",
                    name: "服务端角色变更复核",
                    resource_scope: "tenant_member:account-42",
                    required_approvals: 2,
                    request_expiry_minutes: 30,
                    revision: 11,
                  },
                },
              },
              409,
            ),
          );
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);
    const directory = await screen.findByRole("region", { name: "成员目录" });
    await userEvent.click(within(directory).getByRole("button", { name: /修改角色/ }));
    const dialog = await screen.findByRole("dialog", { name: "修改成员角色" });
    await userEvent.selectOptions(
      within(dialog).getByRole("combobox", { name: "新角色" }),
      "editor",
    );
    await userEvent.type(within(dialog).getByLabelText("变更原因"), "服务端策略复核");
    await userEvent.click(within(dialog).getByRole("button", { name: "提交成员变更" }));

    expect(await within(dialog).findByText("服务端角色变更复核")).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "提交审批申请" })).toBeTruthy();
    expect(within(directory).getAllByText("成员").length).toBeGreaterThan(0);
    expect(storageSetItem).not.toHaveBeenCalledWith(
      expect.stringMatching(/approval|ticket|request/i),
      expect.anything(),
    );
  });
});
