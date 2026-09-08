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
  actor: {
    id: "account-1",
    name: "林澈",
    email: "lin@example.com",
    role: "admin",
  },
  member_count: 4,
  dataset_count: 3,
  effective_permissions: ["knowledge.read", "knowledge.audit"],
  role_permissions: {
    admin: ["knowledge.read", "knowledge.audit"],
    member: ["knowledge.read"],
  },
  capabilities: {
    member_directory: { state: "ready", label: "成员目录", reason: null },
    member_mutations: { state: "limited", label: "成员变更", reason: "当前仅支持只读目录" },
    organization_units: { state: "unavailable", label: "组织架构", reason: "尚未接入企业组织目录" },
    user_groups: { state: "unavailable", label: "用户组", reason: "缺少用户组存储与授权关系" },
    dataset_acl: { state: "unavailable", label: "知识库 ACL", reason: "当前仍按租户角色授权" },
    knowledge_audit: { state: "ready", label: "知识库审计", reason: null },
    tenant_audit: { state: "unavailable", label: "企业管理日志", reason: "尚未接入组织级审计" },
    invitations: { state: "unavailable", label: "成员邀请", reason: "邀请流程尚未接入" },
    sso: { state: "unavailable", label: "企业 SSO", reason: "尚未配置企业身份提供商" },
  },
};

const members = {
  items: [
    {
      membership_id: 9,
      account_id: "account-1",
      name: "林澈",
      email: "lin@example.com",
      role: "admin",
      joined_at: "2026-08-20T08:00:00Z",
      status: "active",
      revision: 7,
    },
  ],
  count: 1,
  next_before_id: null,
};

const access = {
  dataset_id: "dataset-1",
  owner_id: "account-1",
  visibility: "private",
  enforcement_mode: "tenant_role",
  actor_role: "admin",
  member_count: 4,
  dataset_count: 3,
  effective_permissions: ["knowledge.read", "knowledge.audit"],
  dataset_acl_supported: false,
  group_grants_supported: false,
  organization_inheritance_supported: false,
  warnings: ["private 当前仍是资料字段，尚未形成知识库级隔离"],
};

const workspace = {
  id: "workspace-prod",
  tenant_id: "tenant-1",
  code: "prod",
  name: "生产知识域",
  description: "正式业务 Workspace",
  status: "active",
  environment: "production",
  is_default: true,
  revision: 7,
  member_count: 2,
  dataset_count: 1,
  primary_dataset_count: 1,
  actor_role: "admin",
  created_at: "2026-08-20T08:00:00Z",
  updated_at: "2026-08-27T08:00:00Z",
  archived_at: null,
};

const mutationReadyContext = {
  ...context,
  actor: { ...context.actor, role: "admin" },
  effective_permissions: [...context.effective_permissions, "knowledge.manage"],
  capabilities: {
    ...context.capabilities,
    member_mutations: { state: "ready", label: "成员变更", reason: null },
  },
};

const auditReadyContext = {
  ...mutationReadyContext,
  capabilities: {
    ...mutationReadyContext.capabilities,
    tenant_audit: { state: "ready", label: "企业管理日志", reason: null },
  },
};

const accessGraphReadyContext = {
  ...context,
  capabilities: {
    ...context.capabilities,
    organization_units: { state: "ready", label: "组织架构", reason: null },
    user_groups: { state: "ready", label: "用户组", reason: null },
    dataset_acl: { state: "ready", label: "知识库 ACL", reason: null },
    invitations: { state: "ready", label: "成员邀请", reason: null },
  },
};

const auditEvent = {
  sequence: 12,
  id: "audit-12",
  tenant_id: "tenant-1",
  dataset_id: null,
  actor_id: "account-1",
  action: "member.role.updated",
  resource_type: "tenant_member",
  resource_id: "9",
  before_snapshot: { role: "member" },
  after_snapshot: { role: "editor" },
  request_id: "request-12",
  request_ip: "192.0.2.12",
  occurred_at: "2026-08-26T08:00:00Z",
};

const secondAuditEvent = {
  ...auditEvent,
  sequence: 11,
  id: "audit-11",
  action: "member.suspended",
  occurred_at: "2026-08-26T07:00:00Z",
};

function installAuthenticatedScope() {
  localStorage.setItem(KNOWLEDGE_TENANT_STORAGE_KEY, "tenant-1");
  localStorage.setItem(KNOWLEDGE_DATASET_STORAGE_KEY, "dataset-1");
  localStorage.setItem(KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY, "actor-token");
}

function installSuccessFetch(memberResponse: unknown = members) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/enterprise/context")) return Promise.resolve(jsonResponse(context));
      if (url.includes("/api/enterprise/members"))
        return Promise.resolve(jsonResponse(memberResponse));
      if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
      return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
    }),
  );
}

function installWorkspaceCenterFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/enterprise/context")) return Promise.resolve(jsonResponse(context));
      if (url.includes("/api/enterprise/members")) return Promise.resolve(jsonResponse(members));
      if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
      if (url.includes("/api/enterprise/workspaces")) {
        if (url.includes("/members")) {
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }));
        }
        if (url.includes("/datasets")) {
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }));
        }
        if (url.includes("/workspace-prod")) {
          return Promise.resolve(
            jsonResponse({
              workspace,
              authorization_state: "workspace_authorization_not_enforced",
            }),
          );
        }
        return Promise.resolve(
          jsonResponse({
            items: [workspace],
            count: 1,
            next_cursor: null,
            evidence: {
              workspace_count: 1,
              active_count: 1,
              default_workspace_id: "workspace-prod",
              primary_dataset_binding_count: 1,
              authorization_state: "workspace_authorization_not_enforced",
            },
          }),
        );
      }
      return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
    }),
  );
}

function installMobileMedia(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: matches && query.includes("max-width: 720px"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
}

describe("EnterpriseAdminPage", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  it("renders one PageTopbar heading and blocks all enterprise requests when identity is missing", () => {
    vi.stubGlobal("fetch", vi.fn());

    render(<EnterpriseAdminPage />);

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(document.querySelector("main")).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "企业管理" })).toBeTruthy();
    expect(screen.getByText("身份未连接")).toBeTruthy();
    expect(screen.getByText("连接企业身份后才能读取成员、角色和权限事实")).toBeTruthy();
    expect(screen.queryByText(/0\s*位成员/)).toBeNull();
    expect(screen.getByRole("region", { name: "企业能力接入范围" })).toBeTruthy();
    expect(screen.getByText("企业成员目录")).toBeTruthy();
    expect(screen.getByText("连接身份后读取真实成员关系，不展示模拟成员")).toBeTruthy();
    expect(screen.queryByText(/0\s*个知识库/)).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("uses non-contradictory rollout copy on the Workspace Center route", async () => {
    installAuthenticatedScope();
    installWorkspaceCenterFetch();
    window.history.replaceState(null, "", "/enterprise/workspaces");

    render(<EnterpriseAdminPage />);

    await screen.findByText("Workspace Authorization rollout 已接入");
    expect(screen.queryByText("Workspace 角色尚未接入 Dataset 授权引擎")).toBeNull();
    expect(screen.queryByText(/workspace_authorization_not_enforced/)).toBeNull();
    expect(screen.getAllByText(/current \/ candidate \/ delta/).length).toBeGreaterThan(0);
  });

  it("renders four enterprise work surfaces with real context, member and access data", async () => {
    installAuthenticatedScope();
    installSuccessFetch();

    render(<EnterpriseAdminPage />);

    await screen.findByText("星海科技");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(document.querySelector("main")).toBeNull();
    const summary = screen.getByRole("region", { name: "企业摘要" });
    const memberDirectory = screen.getByRole("region", { name: "成员目录" });
    const permissions = screen.getByRole("region", { name: "权限模型" });
    expect(summary).toBeTruthy();
    expect(memberDirectory).toBeTruthy();
    expect(permissions).toBeTruthy();
    expect(screen.getByRole("region", { name: "能力状态" })).toBeTruthy();
    expect(within(summary).getByText("林澈")).toBeTruthy();
    expect(within(summary).getByText("lin@example.com")).toBeTruthy();
    expect(within(memberDirectory).getByText("林澈")).toBeTruthy();
    expect(within(memberDirectory).getByText("lin@example.com")).toBeTruthy();
    expect(within(permissions).getAllByText("租户固定角色").length).toBeGreaterThan(0);
    expect(
      within(permissions).getByText("private 当前仍是资料字段，尚未形成知识库级隔离"),
    ).toBeTruthy();
  });

  it("keeps the enterprise context usable and shows an explicit no-dataset state", async () => {
    installAuthenticatedScope();
    localStorage.removeItem(KNOWLEDGE_DATASET_STORAGE_KEY);
    installSuccessFetch();

    render(<EnterpriseAdminPage />);

    const permissions = await screen.findByRole("region", { name: "权限模型" });
    expect(within(permissions).getByText("未选择知识库")).toBeTruthy();
    expect(within(permissions).getByText("选择知识库后读取当前访问策略")).toBeTruthy();
    expect(screen.queryByText("企业服务暂不可用")).toBeNull();
    expect(
      vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes("/access-summary")),
    ).toBe(false);
  });

  it("uses the context member total and loads cursor pages without duplicate memberships", async () => {
    installAuthenticatedScope();
    const firstMember = {
      membership_id: 9,
      account_id: "account-1",
      name: "林澈",
      email: "lin@example.com",
      role: "admin",
      joined_at: "2026-08-20T08:00:00Z",
    };
    const secondMember = {
      membership_id: 8,
      account_id: "account-2",
      name: "周宁",
      email: "zhou@example.com",
      role: "editor",
      joined_at: "2026-08-19T08:00:00Z",
    };
    const memberPages = [
      { items: [firstMember], count: 4, next_before_id: 8 },
      { items: [firstMember, secondMember], count: 4, next_before_id: null },
    ];
    let memberRequestCount = 0;
    const memberUrls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context")) return Promise.resolve(jsonResponse(context));
        if (url.includes("/api/enterprise/members")) {
          memberUrls.push(url);
          const page = memberPages[Math.min(memberRequestCount++, memberPages.length - 1)];
          return Promise.resolve(jsonResponse(page));
        }
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    const directory = await screen.findByRole("region", { name: "成员目录" });
    expect(within(directory).getByText("4 位成员")).toBeTruthy();
    const loadMore = within(directory).getByRole("button", { name: "加载更多成员" });

    await userEvent.click(loadMore);

    expect(await within(directory).findByText("周宁")).toBeTruthy();
    expect(memberUrls[1]).toContain("before_id=8");
    expect(within(directory).getAllByText("林澈")).toHaveLength(1);
    expect(within(directory).queryByRole("button", { name: "加载更多成员" })).toBeNull();
  });

  it("shows continuation loading and error states without discarding the loaded members", async () => {
    installAuthenticatedScope();
    const firstPage = {
      items: [members.items[0]],
      count: 4,
      next_before_id: 8,
    };
    let resolveNext: ((response: Response) => void) | undefined;
    const nextPage = new Promise<Response>((resolve) => {
      resolveNext = resolve;
    });
    let memberRequestCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context")) return Promise.resolve(jsonResponse(context));
        if (url.includes("/api/enterprise/members")) {
          memberRequestCount += 1;
          return memberRequestCount === 1 ? Promise.resolve(jsonResponse(firstPage)) : nextPage;
        }
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    const directory = await screen.findByRole("region", { name: "成员目录" });
    const loadMore = within(directory).getByRole("button", { name: "加载更多成员" });
    await userEvent.click(loadMore);

    await waitFor(() => {
      const loadingButton = within(directory).getByRole("button", { name: "正在加载更多成员" });
      expect(loadingButton.getAttribute("aria-busy")).toBe("true");
      expect(loadingButton.getAttribute("aria-disabled")).toBe("true");
    });

    resolveNext?.(jsonResponse({ detail: "temporary outage" }, 503));

    expect(await within(directory).findByText("成员加载失败")).toBeTruthy();
    expect(within(directory).getByText("林澈")).toBeTruthy();
    expect(within(directory).getByRole("button", { name: "重试加载成员" })).toBeTruthy();
  });

  it("builds the permission matrix only from role_permissions returned by context", async () => {
    installAuthenticatedScope();
    installSuccessFetch();

    render(<EnterpriseAdminPage />);

    const matrix = await screen.findByRole("region", { name: "服务端角色权限矩阵" });
    expect(matrix.getAttribute("role")).toBe("region");
    expect(within(matrix).getByText("knowledge.audit")).toBeTruthy();
    expect(within(matrix).getAllByText("knowledge.read").length).toBeGreaterThan(0);
    expect(within(matrix).queryByText("knowledge.manage")).toBeNull();
  });

  it("distinguishes a real empty member list from missing identity and unavailable capability", async () => {
    installAuthenticatedScope();
    installSuccessFetch({ items: [], count: 0, next_before_id: null });

    render(<EnterpriseAdminPage />);

    expect(await screen.findByText("尚未添加企业成员")).toBeTruthy();
    expect(screen.getByText("成员接口已成功返回空列表")).toBeTruthy();
    expect(screen.getAllByText("组织架构").length).toBeGreaterThan(0);
    expect(screen.getAllByText("尚未接入企业组织目录").length).toBeGreaterThan(0);
    expect(screen.queryByText("身份未连接")).toBeNull();
  });

  it.each([
    [401, "身份已失效", "重新连接企业身份后再访问此工作面"],
    [403, "没有查看企业管理数据的权限", "当前身份未获得企业目录读取权限"],
    [503, "企业服务暂不可用", "数据库或企业目录服务尚未就绪"],
  ] as const)("renders a distinct HTTP %s state", async (status, title, description) => {
    installAuthenticatedScope();
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ detail: "raw backend detail" }, status))),
    );

    render(<EnterpriseAdminPage />);

    expect(await screen.findByText(title)).toBeTruthy();
    expect(screen.getByText(description)).toBeTruthy();
    expect(screen.queryByText("尚未添加企业成员")).toBeNull();
  });

  it("explains the real audit capability without fabricating audit events", async () => {
    installAuthenticatedScope();
    installSuccessFetch();

    render(<EnterpriseAdminPage />);

    expect(await screen.findByText("知识库治理审计已具备后端能力")).toBeTruthy();
    expect(
      screen.getByText("本工作面暂不伪造事件；独立审计页接入后将读取正式审计接口"),
    ).toBeTruthy();
    expect(screen.queryByText(/张三.*更新了/)).toBeNull();
  });

  it("shows member status and revision while fail-closed mutation controls explain why they are disabled", async () => {
    installAuthenticatedScope();
    installSuccessFetch({
      ...members,
      items: [{ ...members.items[0], status: "active", revision: 7 }],
    });

    render(<EnterpriseAdminPage />);

    const directory = await screen.findByRole("region", { name: "成员目录" });
    expect(within(directory).getByText("启用")).toBeTruthy();
    expect(within(directory).getByText("7")).toBeTruthy();
    expect(
      (within(directory).getByRole("button", { name: /修改角色/ }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (within(directory).getByRole("button", { name: /暂停成员/ }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(within(directory).getByText("成员变更能力尚未接入")).toBeTruthy();
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([, init]) => String(init?.method ?? "GET").toUpperCase() !== "GET"),
    ).toBe(false);
  });

  it("submits a role mutation with the displayed expected revision and reason", async () => {
    installAuthenticatedScope();
    const mutationCalls: Array<[RequestInfo | URL, RequestInit | undefined]> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context"))
          return Promise.resolve(jsonResponse(mutationReadyContext));
        if (url.includes("/api/enterprise/members") && (init?.method ?? "GET") === "GET") {
          return Promise.resolve(jsonResponse(members));
        }
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        if (url.includes("/api/enterprise/readiness")) {
          return Promise.resolve(jsonResponse({ status: "ready", mutations_safe: true }));
        }
        if (url.includes("/api/enterprise/approvals/policies")) {
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }));
        }
        if (url.includes("/api/enterprise/members/account-1/role") && init?.method === "PATCH") {
          mutationCalls.push([input, init]);
          return Promise.resolve(
            jsonResponse({ member: { ...members.items[0], role: "editor", revision: 8 } }),
          );
        }
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    const directory = await screen.findByRole("region", { name: "成员目录" });
    await userEvent.click(within(directory).getByRole("button", { name: /修改角色/ }));
    const dialog = await screen.findByRole("dialog", { name: "修改成员角色" });
    expect((within(dialog).getByLabelText("expected_revision") as HTMLInputElement).value).toBe(
      "7",
    );

    const roleSelect = within(dialog).getByRole("combobox", { name: "新角色" });
    expect(within(roleSelect).getByRole("option", { name: "编辑者" })).toBeTruthy();
    await userEvent.selectOptions(roleSelect, "editor");
    await userEvent.type(within(dialog).getByLabelText("变更原因"), "完成权限交接");
    await userEvent.click(within(dialog).getByRole("button", { name: "提交成员变更" }));

    await waitFor(() => expect(mutationCalls).toHaveLength(1));
    expect(mutationCalls[0][0]).toContain("/api/enterprise/members/account-1/role");
    expect(mutationCalls[0][1]?.method).toBe("PATCH");
    expect(JSON.parse(String(mutationCalls[0][1]?.body))).toMatchObject({
      role: "editor",
      expected_revision: 7,
      reason: "完成权限交接",
    });
    expect(await within(directory).findByText("成员变更已提交")).toBeTruthy();
  });

  it("keeps the member list after a 409 mutation conflict and asks the operator to refresh", async () => {
    installAuthenticatedScope();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context"))
          return Promise.resolve(jsonResponse(mutationReadyContext));
        if (url.includes("/api/enterprise/members") && (init?.method ?? "GET") === "GET") {
          return Promise.resolve(jsonResponse(members));
        }
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        if (url.includes("/api/enterprise/readiness")) {
          return Promise.resolve(jsonResponse({ status: "ready", mutations_safe: true }));
        }
        if (url.includes("/api/enterprise/approvals/policies")) {
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }));
        }
        if (url.includes("/api/enterprise/members/account-1/role") && init?.method === "PATCH") {
          return Promise.resolve(jsonResponse({ detail: "conflict" }, 409));
        }
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    const directory = await screen.findByRole("region", { name: "成员目录" });
    await userEvent.click(within(directory).getByRole("button", { name: /修改角色/ }));
    const dialog = await screen.findByRole("dialog", { name: "修改成员角色" });
    await userEvent.type(within(dialog).getByLabelText("变更原因"), "冲突测试");
    await userEvent.click(within(dialog).getByRole("button", { name: "提交成员变更" }));

    expect(await screen.findByText("成员信息已发生变化，请刷新后重试")).toBeTruthy();
    expect(within(directory).getByText("林澈")).toBeTruthy();
  });

  it("renders a clear unavailable message for a 503 mutation without turning the member list into an empty state", async () => {
    installAuthenticatedScope();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context"))
          return Promise.resolve(jsonResponse(mutationReadyContext));
        if (url.includes("/api/enterprise/members") && (init?.method ?? "GET") === "GET") {
          return Promise.resolve(jsonResponse(members));
        }
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        if (url.includes("/api/enterprise/readiness")) {
          return Promise.resolve(jsonResponse({ status: "ready", mutations_safe: true }));
        }
        if (url.includes("/api/enterprise/approvals/policies")) {
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }));
        }
        if (url.includes("/api/enterprise/members/account-1/role") && init?.method === "PATCH") {
          return Promise.resolve(jsonResponse({ detail: "unmounted" }, 503));
        }
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    const directory = await screen.findByRole("region", { name: "成员目录" });
    await userEvent.click(within(directory).getByRole("button", { name: /修改角色/ }));
    const dialog = await screen.findByRole("dialog", { name: "修改成员角色" });
    await userEvent.type(within(dialog).getByLabelText("变更原因"), "服务不可用测试");
    await userEvent.click(within(dialog).getByRole("button", { name: "提交成员变更" }));

    expect(await screen.findByText("成员接口尚未挂载/企业服务暂不可用")).toBeTruthy();
    expect(within(directory).getByText("林澈")).toBeTruthy();
    expect(within(directory).queryByText("尚未添加企业成员")).toBeNull();
  });

  it("loads real management audit events by keyset and opens an event detail drawer", async () => {
    installAuthenticatedScope();
    const auditUrls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context"))
          return Promise.resolve(jsonResponse(auditReadyContext));
        if (url.includes("/api/enterprise/members") && (init?.method ?? "GET") === "GET") {
          return Promise.resolve(jsonResponse(members));
        }
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        if (url.includes("/api/enterprise/readiness")) {
          return Promise.resolve(jsonResponse({ status: "ready", mutations_safe: true }));
        }
        if (url.includes("/api/enterprise/approvals/policies")) {
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }));
        }
        if (url.includes("/api/enterprise/audit-events")) {
          auditUrls.push(url);
          return url.includes("before_sequence=11")
            ? Promise.resolve(
                jsonResponse({ items: [secondAuditEvent], count: 2, next_before_sequence: null }),
              )
            : Promise.resolve(
                jsonResponse({ items: [auditEvent], count: 2, next_before_sequence: 11 }),
              );
        }
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    const audit = await screen.findByRole("region", { name: "管理审计" });
    expect(within(audit).getByText("member.role.updated")).toBeTruthy();
    await userEvent.click(within(audit).getByRole("button", { name: "加载更多审计事件" }));
    expect(await within(audit).findByText("member.suspended")).toBeTruthy();
    expect(auditUrls[1]).toContain("before_sequence=11");
    expect(within(audit).getAllByText("member.role.updated")).toHaveLength(1);

    await userEvent.click(within(audit).getAllByRole("button", { name: /查看审计事件详情/ })[0]);
    expect(await screen.findByRole("dialog", { name: "管理审计事件详情" })).toBeTruthy();
    expect(screen.getByText("request-12")).toBeTruthy();
  });

  it("shows the real audit empty state without fabricating events", async () => {
    installAuthenticatedScope();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context"))
          return Promise.resolve(jsonResponse(auditReadyContext));
        if (url.includes("/api/enterprise/members") && (init?.method ?? "GET") === "GET") {
          return Promise.resolve(jsonResponse(members));
        }
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        if (url.includes("/api/enterprise/readiness")) {
          return Promise.resolve(jsonResponse({ status: "behind", mutations_safe: false }));
        }
        if (url.includes("/api/enterprise/audit-events")) {
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_sequence: null }));
        }
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    const audit = await screen.findByRole("region", { name: "管理审计" });
    expect(within(audit).getByText("暂无管理审计事件")).toBeTruthy();
    expect(within(audit).getByText("真实接口已返回空列表")).toBeTruthy();
    expect(within(audit).queryByText("张三")).toBeNull();
  });

  it.each([
    [401, "管理审计身份已失效"],
    [403, "没有读取管理审计的权限"],
    [503, "管理审计接口尚未挂载/企业服务暂不可用"],
  ] as const)(
    "renders the management audit HTTP %s state without fake events",
    async (status, message) => {
      installAuthenticatedScope();
      vi.stubGlobal(
        "fetch",
        vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
          const url = String(input);
          if (url.includes("/api/enterprise/context"))
            return Promise.resolve(jsonResponse(auditReadyContext));
          if (url.includes("/api/enterprise/members") && (init?.method ?? "GET") === "GET") {
            return Promise.resolve(jsonResponse(members));
          }
          if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
          if (url.includes("/api/enterprise/readiness")) {
            return Promise.resolve(jsonResponse({ status: "ready", mutations_safe: true }));
          }
          if (url.includes("/api/enterprise/audit-events")) {
            return Promise.resolve(jsonResponse({ detail: "audit unavailable" }, status));
          }
          return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
        }),
      );

      render(<EnterpriseAdminPage />);

      const audit = await screen.findByRole("region", { name: "管理审计" });
      expect(await within(audit).findByText(message)).toBeTruthy();
      expect(within(audit).queryByText("member.role.updated")).toBeNull();
    },
  );

  it("uses mobile audit summary cards instead of the desktop table", async () => {
    installAuthenticatedScope();
    installMobileMedia(true);
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context"))
          return Promise.resolve(jsonResponse(auditReadyContext));
        if (url.includes("/api/enterprise/members") && (init?.method ?? "GET") === "GET") {
          return Promise.resolve(jsonResponse(members));
        }
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        if (url.includes("/api/enterprise/readiness")) {
          return Promise.resolve(jsonResponse({ status: "ready", mutations_safe: true }));
        }
        if (url.includes("/api/enterprise/approvals/policies")) {
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }));
        }
        if (url.includes("/api/enterprise/audit-events")) {
          return Promise.resolve(
            jsonResponse({ items: [auditEvent], count: 1, next_before_sequence: null }),
          );
        }
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    const audit = await screen.findByRole("region", { name: "管理审计" });
    expect(within(audit).getByTestId("enterprise-audit-mobile-list")).toBeTruthy();
    expect(within(audit).queryByTestId("enterprise-audit-desktop-table")).toBeNull();
    expect(within(audit).getByText("member.role.updated")).toBeTruthy();
  });

  it("integrates the full-width enterprise access graph with the authenticated page context", async () => {
    installAuthenticatedScope();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context")) {
          return Promise.resolve(jsonResponse(accessGraphReadyContext));
        }
        if (url.includes("/api/enterprise/members") && (init?.method ?? "GET") === "GET") {
          return Promise.resolve(jsonResponse(members));
        }
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        if (url.includes("/api/enterprise/readiness")) {
          return Promise.resolve(jsonResponse({ status: "behind", mutations_safe: false }));
        }
        if (url.includes("/organization-units")) {
          return Promise.resolve(
            jsonResponse({
              items: [
                {
                  id: "ou-1",
                  parent_id: null,
                  name: "集团总部",
                  code: "HQ",
                  status: "active",
                  member_count: 4,
                  child_count: 0,
                },
              ],
              count: 1,
              next_before_id: null,
            }),
          );
        }
        if (url.includes("/api/enterprise/groups")) {
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
        }
        if (url.includes("/api/enterprise/invitations")) {
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
        }
        if (url.includes("/access-grants")) {
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
        }
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    const graph = await screen.findByRole("region", { name: "企业访问图谱" });
    expect(within(graph).getByText("集团总部")).toBeTruthy();
    expect(within(graph).getAllByRole("tab")).toHaveLength(4);
    expect(within(graph).getByText("访问事实按当前租户范围读取")).toBeTruthy();
    await userEvent.click(within(graph).getByRole("tab", { name: /知识库 ACL/ }));
    const aclEvidence = within(graph).getByRole("group", { name: "知识库 ACL 生效证据" });
    expect(within(aclEvidence).getByText("租户角色回退")).toBeTruthy();
    expect(within(aclEvidence).getByText("knowledge.read")).toBeTruthy();
  });

  it("keeps page-level permission evidence atomic after disabling persistent ACL", async () => {
    installAuthenticatedScope();
    const aclContext = {
      ...accessGraphReadyContext,
      effective_permissions: ["knowledge.read", "knowledge.manage"],
      role_permissions: { admin: ["knowledge.read", "knowledge.manage"] },
      actor: { ...accessGraphReadyContext.actor, role: "admin" },
    };
    let disabled = false;
    const persistentAccess = {
      ...access,
      enforcement_mode: "dataset_acl",
      actor_role: "admin",
      effective_permissions: ["knowledge.read", "knowledge.manage"],
      dataset_acl_supported: true,
      group_grants_supported: true,
      organization_inheritance_supported: true,
      warnings: [],
      acl_mode: "dataset_acl",
      acl_revision: 7,
      acl_enabled_at: "2026-08-26T08:00:00Z",
      acl_enabled_by: "account-1",
    };
    const disabledAccess = {
      ...persistentAccess,
      enforcement_mode: "tenant_role",
      acl_mode: "tenant_role",
      acl_revision: 8,
      acl_enabled_at: null,
      acl_enabled_by: null,
    };
    const graphGrant = {
      id: "grant-1",
      dataset_id: "dataset-1",
      subject_type: "account",
      subject_id: "account-2",
      subject_name: "审计成员",
      role: "viewer",
      status: "active",
      revision: 2,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context"))
          return Promise.resolve(jsonResponse(aclContext));
        if (url.includes("/api/enterprise/members") && (init?.method ?? "GET") === "GET")
          return Promise.resolve(jsonResponse(members));
        if (url.includes("/access-summary"))
          return Promise.resolve(jsonResponse(disabled ? disabledAccess : persistentAccess));
        if (url.includes("/api/enterprise/readiness"))
          return Promise.resolve(jsonResponse({ status: "ready", mutations_safe: true }));
        if (url.includes("/api/enterprise/audit-events"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_sequence: null }));
        if (url.includes("/organization-units"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
        if (url.includes("/api/enterprise/groups"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
        if (url.includes("/api/enterprise/invitations"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
        if (url.includes("/api/enterprise/approvals/policies"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_cursor: null }));
        if (url.includes("/access-control/disable") && init?.method === "POST") {
          disabled = true;
          return Promise.resolve(jsonResponse(disabledAccess));
        }
        if (url.includes("/access-grants"))
          return Promise.resolve(
            jsonResponse({ items: [graphGrant], count: 1, next_before_id: null }),
          );
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);
    const graph = await screen.findByRole("region", { name: "企业访问图谱" });
    await userEvent.click(within(graph).getByRole("tab", { name: /知识库 ACL/ }));
    await within(graph).findByText("持久 ACL 模式");
    const permissions = await screen.findByRole("region", { name: "权限模型" });
    expect(within(permissions).getByText("dataset_acl")).toBeTruthy();

    await userEvent.click(within(graph).getByRole("button", { name: "停用 ACL" }));
    const dialog = await screen.findByRole("dialog", { name: "停用知识库 ACL" });
    await userEvent.type(within(dialog).getByLabelText("停用原因"), "完成治理切换");
    await userEvent.click(within(dialog).getByLabelText("我确认停用此知识库的持久 ACL 模式"));
    await userEvent.click(within(dialog).getByText("确认停用 ACL"));

    await waitFor(() =>
      expect(
        within(screen.getByRole("region", { name: "企业访问图谱" })).getByText("租户角色模式"),
      ).toBeTruthy(),
    );
    await waitFor(() =>
      expect(
        within(screen.getByRole("region", { name: "权限模型" })).queryByText("dataset_acl"),
      ).toBeNull(),
    );
    expect(
      within(screen.getByRole("region", { name: "权限模型" })).getAllByText("租户固定角色").length,
    ).toBeGreaterThan(0);
  });

  it("handles the OIDC callback before membership context and clears URL code/state evidence", async () => {
    localStorage.clear();
    localStorage.setItem(KNOWLEDGE_TENANT_STORAGE_KEY, "tenant-1");
    localStorage.setItem(KNOWLEDGE_DATASET_STORAGE_KEY, "dataset-1");
    window.history.replaceState(
      null,
      "",
      "/enterprise/sso/oidc/callback?code=secret-code&state=secret-state",
    );
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/enterprise/sso/oidc/callback"))
          return Promise.resolve(
            jsonResponse({
              status: "authenticated",
              knowledge_actor_token: "knowledge-actor-stage12",
              id_token: "never-render",
              access_token: "never-render",
              actor: { id: "account-1", name: "林澈", email: "lin@example.com" },
              tenant: { id: "tenant-1", name: "星海科技" },
              provider: { id: "idp-1", name: "Company OIDC" },
              session: {
                id: "session-1",
                status: "active",
                revision: 1,
                expires_at: "2026-08-26T12:00:00Z",
              },
            }),
          );
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    const surface = await screen.findByRole("region", { name: "OIDC 登录回调结果" });
    expect(within(surface).getByText("OIDC 登录成功")).toBeTruthy();
    expect(window.location.pathname).toBe("/enterprise/identity");
    expect(window.location.search).toBe("");
    expect(localStorage.getItem(KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY)).toBe("knowledge-actor-stage12");
    expect(surface.textContent).not.toContain("secret-code");
    expect(surface.textContent).not.toContain("secret-state");
    expect(surface.textContent).not.toContain("never-render");
  });

  it("renders the invitation acceptance surface from a direct route even before membership exists", async () => {
    installAuthenticatedScope();
    window.history.replaceState(
      null,
      "",
      "/enterprise/invitations/accept?token=route-one-time-token",
    );
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context"))
          return Promise.resolve(
            jsonResponse({ detail: { code: "enterprise_membership_required" } }, 403),
          );
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    const surface = await screen.findByRole("region", { name: "接受企业邀请" });
    expect(within(surface).getByText("签名身份邮箱校验")).toBeTruthy();
    expect(within(surface).getByRole("button", { name: "确认接受邀请" })).toBeTruthy();
    expect(surface.textContent).not.toContain("route-one-time-token");
  });

  it("mounts the enterprise identity federation center on the nested identity route", async () => {
    installAuthenticatedScope();
    window.history.replaceState(null, "", "/enterprise/identity");
    const identityContext = {
      ...accessGraphReadyContext,
      actor: { ...accessGraphReadyContext.actor, role: "owner" },
      capabilities: {
        ...accessGraphReadyContext.capabilities,
        identity_federation: { state: "ready", label: "企业身份联合", reason: null },
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context"))
          return Promise.resolve(jsonResponse(identityContext));
        if (url.includes("/api/enterprise/members") && (init?.method ?? "GET") === "GET")
          return Promise.resolve(jsonResponse(members));
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        if (url.includes("/api/enterprise/readiness"))
          return Promise.resolve(
            jsonResponse({
              expected_head: "0022_scim_provisioning_data_plane",
              current_revision: "0022_scim_provisioning_data_plane",
              status: "ready",
              missing_capability_groups: [],
              mutations_safe: true,
            }),
          );
        if (url.includes("/api/enterprise/audit-events"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_sequence: null }));
        if (url.includes("/api/enterprise/identity/domains"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
        if (url.includes("/api/enterprise/identity/providers"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
        if (url.includes("/api/enterprise/identity/scim-tokens"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
        if (
          [
            "/organization-units",
            "/api/enterprise/groups",
            "/api/enterprise/invitations",
            "/access-grants",
          ].some((part) => url.includes(part))
        )
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    expect(await screen.findByRole("region", { name: "企业身份联合中心" })).toBeTruthy();
    expect(screen.getByText("runtime_not_connected")).toBeTruthy();
    expect(screen.getByText("sso_runtime_not_connected")).toBeTruthy();
    expect(screen.getByText("scim_data_plane_ready")).toBeTruthy();
    expect(screen.getByDisplayValue("/scim/v2")).toBeTruthy();
  });

  it("mounts the enterprise audit compliance center with 0023 readiness evidence", async () => {
    installAuthenticatedScope();
    window.history.replaceState(null, "", "/enterprise/compliance");
    const complianceContext = {
      ...context,
      actor: { ...context.actor, role: "owner" },
      effective_permissions: [...context.effective_permissions, "enterprise.manage"],
      capabilities: {
        ...context.capabilities,
        audit_compliance: { state: "ready", label: "审计合规", reason: null },
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context"))
          return Promise.resolve(jsonResponse(complianceContext));
        if (url.includes("/api/enterprise/members")) return Promise.resolve(jsonResponse(members));
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        if (url.includes("/api/enterprise/readiness"))
          return Promise.resolve(
            jsonResponse({
              expected_head: "0023_enterprise_audit_compliance",
              current_revision: "0023_enterprise_audit_compliance",
              status: "ready",
              missing_capability_groups: [],
              mutations_safe: true,
            }),
          );
        if (url.includes("retention-policy"))
          return Promise.resolve(
            jsonResponse({
              policy: {
                id: "policy-1",
                status: "active",
                audit_retention_days: 365,
                export_retention_days: 30,
                revision: 7,
              },
            }),
          );
        if (url.includes("legal-holds"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
        if (url.includes("audit-exports"))
          return Promise.resolve(jsonResponse({ items: [], count: 0, next_before_id: null }));
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    const center = await screen.findByRole("region", { name: "企业审计合规中心" });
    expect(within(center).getByText("manual_execution_only")).toBeTruthy();
    expect(within(center).getByText("revision 7")).toBeTruthy();
  });

  it("keeps access graph capabilities honest and sends no graph requests when unavailable", async () => {
    installAuthenticatedScope();
    installSuccessFetch();

    render(<EnterpriseAdminPage />);

    const graph = await screen.findByRole("region", { name: "企业访问图谱" });
    expect(within(graph).getAllByText("尚未接入企业组织目录").length).toBeGreaterThan(0);
    const graphRequests = vi
      .mocked(fetch)
      .mock.calls.map(([input]) => String(input))
      .filter((url) =>
        [
          "/organization-units",
          "/api/enterprise/groups",
          "/api/enterprise/invitations",
          "/access-grants",
        ].some((path) => url.includes(path)),
      );
    expect(graphRequests).toEqual([]);
  });
  it.each([
    ["direct", "/enterprise/approvals"],
    ["hash", "/#/enterprise/approvals?tab=requests"],
  ])("mounts the approval center from the %s nested route", async (_label, route) => {
    installAuthenticatedScope();
    window.history.replaceState(null, "", route);
    const approvalRequest = {
      id: "request-1",
      policy_id: "policy-1",
      action_type: "dataset_acl_disable",
      resource_type: "knowledge_base",
      resource_id: "dataset-1",
      resource_name: "产品知识库",
      requester_id: "account-2",
      reason: "准备停用知识库 ACL",
      status: "pending",
      required_approvals: 2,
      received_approvals: 1,
      expires_at: "2026-09-01T08:00:00Z",
      requested_at: "2026-08-27T08:00:00Z",
      revision: 4,
    };
    const approvalPolicy = {
      id: "policy-1",
      name: "知识库 ACL 变更",
      action_type: "dataset_acl_disable",
      resource_scope: "dataset-1",
      status: "active",
      required_approvals: 2,
      request_expiry_minutes: 1440,
      approvers: [{ kind: "role", ref: "owner" }],
      revision: 3,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/enterprise/context")) return Promise.resolve(jsonResponse(context));
        if (url.includes("/api/enterprise/members")) return Promise.resolve(jsonResponse(members));
        if (url.includes("/access-summary")) return Promise.resolve(jsonResponse(access));
        if (url.includes("/api/enterprise/readiness"))
          return Promise.resolve(
            jsonResponse({
              expected_head: "0025_enterprise_approval_control",
              current_revision: "0024_oidc_sso_runtime",
              status: "ready",
              missing_capability_groups: [],
              mutations_safe: true,
            }),
          );
        if (url.includes("/api/enterprise/approvals/policies"))
          return Promise.resolve(
            jsonResponse({ items: [approvalPolicy], count: 1, next_cursor: null }),
          );
        if (url.includes("/api/enterprise/approvals/requests"))
          return Promise.resolve(
            jsonResponse({
              items: [approvalRequest],
              count: 1,
              next_cursor: null,
              evidence: {
                pending_count: 1,
                my_pending_count: 1,
                active_policy_count: 1,
                catalog_revision: "0024_oidc_sso_runtime",
                execution_adapter_status: "execution_adapter_not_connected",
              },
            }),
          );
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseAdminPage />);

    const center = await screen.findByRole("region", { name: "企业审批中心" });
    expect(center).toBeTruthy();
    expect(screen.getAllByRole("heading", { level: 1, name: "企业审批中心" }).length).toBe(1);
    expect(screen.queryByRole("region", { name: "企业摘要" })).toBeNull();
  });
});
