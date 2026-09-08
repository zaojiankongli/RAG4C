// @vitest-environment jsdom

import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  DatasetAccessSummary,
  EnterpriseContext,
  EnterpriseScope,
} from "../../enterprise-admin/model";
import EnterpriseAccessGraph from "./EnterpriseAccessGraph";

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};

const baseContext: EnterpriseContext = {
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
  effective_permissions: ["knowledge.read", "knowledge.manage"],
  role_permissions: { admin: ["knowledge.read", "knowledge.manage"] },
  capabilities: {
    organization_units: { state: "ready", label: "组织架构", reason: null },
    user_groups: { state: "ready", label: "用户组", reason: null },
    dataset_acl: { state: "ready", label: "知识库 ACL", reason: null },
    invitations: { state: "ready", label: "成员邀请", reason: null },
  },
};

const organizationPage = {
  items: [
    {
      id: "ou-hq",
      parent_id: null,
      name: "集团总部",
      code: "HQ",
      status: "active",
      member_count: 12,
      child_count: 1,
    },
    {
      id: "ou-rd",
      parent_id: "ou-hq",
      name: "平台研发部",
      code: "RD",
      status: "active",
      member_count: 8,
      child_count: 0,
    },
  ],
  count: 2,
  next_before_id: null,
};

const groupPage = {
  items: [
    {
      id: "group-rd",
      name: "研发协作组",
      description: "跨团队研发协作",
      status: "active",
      member_count: 2,
    },
  ],
  count: 1,
  next_before_id: null,
};

const groupMemberPage = {
  items: [
    {
      id: "gm-1",
      account_id: "account-2",
      name: "周宁",
      email: "zhou@example.com",
      role: "editor",
      status: "active",
    },
  ],
  count: 1,
  next_before_id: null,
};

const grantPage = {
  items: [
    {
      id: "grant-1",
      dataset_id: "dataset-1",
      subject_type: "group",
      subject_id: "group-rd",
      subject_name: "研发协作组",
      role: "manager",
      status: "active",
      revision: 4,
    },
  ],
  count: 1,
  next_before_id: null,
};

const datasetAclSummary: DatasetAccessSummary = {
  dataset_id: "dataset-1",
  owner_id: "account-1",
  visibility: "private",
  enforcement_mode: "dataset_acl",
  actor_role: "admin",
  effective_permissions: ["knowledge.read"],
  dataset_acl_supported: true,
  group_grants_supported: false,
  organization_inheritance_supported: true,
  warnings: ["组织继承仅对已配置组织单元生效"],
};

const invitationPage = {
  items: [
    {
      id: "invite-1",
      email: "new@example.com",
      role: "editor",
      status: "pending",
      expires_at: "2026-09-01T08:00:00Z",
      invited_by: "林澈",
    },
  ],
  count: 1,
  next_before_id: null,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installGraphFetch(overrides: Partial<Record<string, Response | (() => Response)>> = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const matched = Object.entries(overrides).find(([needle]) => url.includes(needle));
      if (matched) {
        const value = matched[1];
        return Promise.resolve(typeof value === "function" ? value() : value);
      }
      if (url.includes("/organization-units"))
        return Promise.resolve(jsonResponse(organizationPage));
      if (url.includes("/groups/group-rd/members")) {
        return Promise.resolve(jsonResponse(groupMemberPage));
      }
      if (url.includes("/api/enterprise/groups")) return Promise.resolve(jsonResponse(groupPage));
      if (url.includes("/api/enterprise/invitations")) {
        return Promise.resolve(jsonResponse(invitationPage));
      }
      if (url.includes("/access-grants")) return Promise.resolve(jsonResponse(grantPage));
      return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
    }),
  );
}

describe("EnterpriseAccessGraph", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("renders Tencent-console information hierarchy with real facts and four TDesign tabs", async () => {
    installGraphFetch();

    render(<EnterpriseAccessGraph scope={scope} context={baseContext} />);

    const graph = screen.getByRole("region", { name: "企业访问图谱" });
    expect(within(graph).getByText("访问事实按当前租户范围读取")).toBeTruthy();
    expect(within(graph).getAllByRole("tab")).toHaveLength(4);
    expect(within(graph).getByRole("tab", { name: /组织架构/ })).toBeTruthy();
    expect(within(graph).getByRole("tab", { name: /用户组/ })).toBeTruthy();
    expect(within(graph).getByRole("tab", { name: /知识库 ACL/ })).toBeTruthy();
    expect(within(graph).getByRole("tab", { name: /成员邀请/ })).toBeTruthy();

    const organizationFact = within(graph).getByRole("group", { name: "组织节点事实" });
    const groupFact = within(graph).getByRole("group", { name: "用户组事实" });
    const grantFact = within(graph).getByRole("group", { name: "ACL 授权事实" });
    const invitationFact = within(graph).getByRole("group", { name: "邀请事实" });
    await within(organizationFact).findByText("2");
    expect(within(groupFact).getByText("1")).toBeTruthy();
    expect(within(grantFact).getByText("1")).toBeTruthy();
    expect(within(invitationFact).getByText("1")).toBeTruthy();
    expect(within(graph).getByText("集团总部")).toBeTruthy();
    expect(within(graph).getByText("平台研发部")).toBeTruthy();
    expect(within(graph).queryByText(/立即购买|升级套餐|营销/)).toBeNull();
  });

  it("renders server-owned ACL enforcement evidence instead of inferring it from grant rows", async () => {
    installGraphFetch({
      "/access-grants": jsonResponse({
        ...grantPage,
        items: [{ ...grantPage.items[0], role: "manager" }],
      }),
    });

    render(
      <EnterpriseAccessGraph
        scope={scope}
        context={baseContext}
        accessSummary={datasetAclSummary}
      />,
    );

    const graph = screen.getByRole("region", { name: "企业访问图谱" });
    await userEvent.click(within(graph).getByRole("tab", { name: /知识库 ACL/ }));

    const evidence = within(graph).getByRole("group", { name: "知识库 ACL 生效证据" });
    expect(within(evidence).getByText("知识库 ACL 已生效")).toBeTruthy();
    expect(within(evidence).getByText("管理员")).toBeTruthy();
    expect(within(evidence).getByText("knowledge.read")).toBeTruthy();
    expect(within(evidence).queryByText("knowledge.manage")).toBeNull();
    expect(within(evidence).getByText("dataset_acl_supported")).toBeTruthy();
    expect(within(evidence).getByText("group_grants_supported")).toBeTruthy();
    expect(within(evidence).getByText("organization_inheritance_supported")).toBeTruthy();
    expect(within(evidence).getAllByText("支持")).toHaveLength(2);
    expect(within(evidence).getByText("不支持")).toBeTruthy();
    expect(within(evidence).getByText("组织继承仅对已配置组织单元生效")).toBeTruthy();
    expect(within(evidence).queryByText("租户角色回退")).toBeNull();
  });

  it.each([
    ["tenant_role", "租户角色回退"],
    ["tenant_role_fallback", "租户角色回退"],
  ] as const)("maps server enforcement mode %s to %s", async (mode, label) => {
    installGraphFetch();
    const accessSummary: DatasetAccessSummary = {
      ...datasetAclSummary,
      enforcement_mode: mode,
    };

    render(
      <EnterpriseAccessGraph scope={scope} context={baseContext} accessSummary={accessSummary} />,
    );

    const graph = screen.getByRole("region", { name: "企业访问图谱" });
    await userEvent.click(within(graph).getByRole("tab", { name: /知识库 ACL/ }));
    const evidence = within(graph).getByRole("group", { name: "知识库 ACL 生效证据" });
    expect(within(evidence).getByText(label)).toBeTruthy();
  });

  it("shows no-dataset enforcement without requesting ACL grants", async () => {
    installGraphFetch();
    const noDatasetScope: EnterpriseScope = { ...scope, datasetId: undefined };

    render(
      <EnterpriseAccessGraph scope={noDatasetScope} context={baseContext} accessSummary={null} />,
    );

    const graph = screen.getByRole("region", { name: "企业访问图谱" });
    await userEvent.click(within(graph).getByRole("tab", { name: /知识库 ACL/ }));
    const evidence = within(graph).getByRole("group", { name: "知识库 ACL 生效证据" });
    expect(within(evidence).getByText("未选择知识库")).toBeTruthy();
    expect(within(evidence).getByText("选择知识库后读取服务端访问摘要")).toBeTruthy();
    expect(
      vi
        .mocked(fetch)
        .mock.calls.map(([input]) => String(input))
        .some((url) => url.includes("/access-grants")),
    ).toBe(false);
  });

  it("filters only the loaded organization facts and keeps a dense scrollable list", async () => {
    installGraphFetch();
    const user = userEvent.setup();

    render(<EnterpriseAccessGraph scope={scope} context={baseContext} />);

    await screen.findByText("平台研发部");
    const search = screen.getByRole("searchbox", { name: "筛选已加载的组织单元" });
    await user.type(search, "平台");

    expect(screen.getByText("平台研发部")).toBeTruthy();
    expect(screen.queryByText("集团总部")).toBeNull();
    const tableViewport = screen.getByTestId("enterprise-access-organization-table");
    expect(tableViewport.getAttribute("tabindex")).toBe("0");
    expect(screen.getByText("仅筛选当前已加载结果")).toBeTruthy();
  });

  it("loads group members only from an explicit group selection and exposes ACL/invitation tabs", async () => {
    installGraphFetch();
    const user = userEvent.setup();

    render(<EnterpriseAccessGraph scope={scope} context={baseContext} />);
    const graph = screen.getByRole("region", { name: "企业访问图谱" });

    await user.click(within(graph).getByRole("tab", { name: /用户组/ }));
    await user.click(await within(graph).findByRole("button", { name: "查看研发协作组成员" }));
    expect(await within(graph).findByText("zhou@example.com")).toBeTruthy();

    await user.click(within(graph).getByRole("tab", { name: /知识库 ACL/ }));
    expect(await within(graph).findByText("研发协作组")).toBeTruthy();
    expect(within(graph).getByText("知识库管理员")).toBeTruthy();
    expect(within(graph).getByText("revision 4")).toBeTruthy();

    await user.click(within(graph).getByRole("tab", { name: /成员邀请/ }));
    expect(await within(graph).findByText("new@example.com")).toBeTruthy();
    expect(within(graph).queryByText("must-never-render")).toBeNull();
  });

  it("does not request unavailable capabilities and renders their server-owned reasons", async () => {
    vi.stubGlobal("fetch", vi.fn());
    const unavailableContext: EnterpriseContext = {
      ...baseContext,
      capabilities: {
        organization_units: {
          state: "unavailable",
          label: "组织架构",
          reason: "尚未接入企业组织目录",
        },
        user_groups: { state: "unavailable", label: "用户组", reason: "用户组迁移未完成" },
        dataset_acl: {
          state: "unavailable",
          label: "知识库 ACL",
          reason: "ACL 存储尚未接入",
        },
        invitations: {
          state: "unavailable",
          label: "成员邀请",
          reason: "邀请流程尚未接入",
        },
      },
    };

    render(<EnterpriseAccessGraph scope={scope} context={unavailableContext} />);

    expect(screen.getByText("尚未接入企业组织目录")).toBeTruthy();
    await waitFor(() => expect(fetch).not.toHaveBeenCalled());
    expect(screen.queryByText(/0\s*个组织/)).toBeNull();
  });

  it("shows a real empty state rather than a fabricated organization topology", async () => {
    installGraphFetch({
      "/organization-units": jsonResponse({ items: [], count: 0, next_before_id: null }),
    });

    render(<EnterpriseAccessGraph scope={scope} context={baseContext} />);

    expect(await screen.findByText("暂无组织单元")).toBeTruthy();
    expect(screen.getByText("真实接口已返回空列表")).toBeTruthy();
    expect(screen.queryByText("默认部门")).toBeNull();
  });

  it.each([
    ["/organization-units", 401, "组织架构身份已失效"],
    ["/api/enterprise/groups", 403, "没有读取用户组的权限"],
    ["/access-grants", 503, "知识库 ACL 服务暂不可用"],
  ] as const)("renders %s HTTP %s as a distinct honest state", async (path, status, message) => {
    installGraphFetch({ [path]: jsonResponse({ detail: "unavailable" }, status) });

    render(<EnterpriseAccessGraph scope={scope} context={baseContext} />);

    if (path.includes("groups")) {
      await userEvent.click(screen.getByRole("tab", { name: /用户组/ }));
    } else if (path.includes("access-grants")) {
      await userEvent.click(screen.getByRole("tab", { name: /知识库 ACL/ }));
    }
    expect(await screen.findByText(message)).toBeTruthy();
  });

  it("renders the schema migration required state for invitations", async () => {
    installGraphFetch({
      "/api/enterprise/invitations": jsonResponse(
        { detail: { code: "enterprise_access_graph_migration_required" } },
        503,
      ),
    });

    render(<EnterpriseAccessGraph scope={scope} context={baseContext} />);

    await userEvent.click(screen.getByRole("tab", { name: /成员邀请/ }));
    expect(await screen.findByText("访问图谱数据库版本尚未就绪")).toBeTruthy();
    expect(screen.getByText("完成企业访问图谱迁移后再重新读取")).toBeTruthy();
  });

  it("loads organization keyset pages and keeps previously loaded rows", async () => {
    let organizationRequestCount = 0;
    const organizationUrls: string[] = [];
    installGraphFetch({
      "/organization-units": () => {
        organizationRequestCount += 1;
        const calls = vi.mocked(fetch).mock.calls;
        const url = calls[calls.length - 1]?.[0];
        organizationUrls.push(String(url));
        return organizationRequestCount === 1
          ? jsonResponse({
              items: [organizationPage.items[0]],
              count: 2,
              next_before_id: "ou-rd",
            })
          : jsonResponse({
              items: [organizationPage.items[1]],
              count: 2,
              next_before_id: null,
            });
      },
    });

    render(<EnterpriseAccessGraph scope={scope} context={baseContext} />);

    expect(await screen.findByText("集团总部")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "加载更多组织单元" }));
    expect(await screen.findByText("平台研发部")).toBeTruthy();
    expect(screen.getByText("集团总部")).toBeTruthy();
    expect(organizationUrls[1]).toContain("before_id=ou-rd");
  });
});
