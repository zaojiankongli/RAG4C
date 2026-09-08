// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import EnterpriseWorkspaceCenter from "./EnterpriseWorkspaceCenter";

const scope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "actor-token" };
const context = {
  tenant: {
    id: "tenant-a",
    name: "RAG4C 企业",
    plan: "enterprise",
    status: "active",
    quota_documents: 1000,
    quota_chunks: 10000,
    doc_count: 20,
    chunk_count: 100,
  },
  actor: { id: "owner-a", name: "张三", email: "owner@example.com", role: "owner" },
  member_count: 3,
  dataset_count: 2,
  effective_permissions: ["knowledge.read", "knowledge.manage"],
  role_permissions: {},
  capabilities: {},
};

const workspace = {
  id: "workspace-prod",
  tenant_id: "tenant-a",
  code: "prod",
  name: "生产知识域",
  description: "面向正式业务的企业知识工作区",
  status: "active",
  environment: "production",
  is_default: true,
  revision: 7,
  member_count: 2,
  dataset_count: 1,
  primary_dataset_count: 1,
  updated_at: "2026-08-27T08:00:00Z",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn((query: string) => ({
      matches: query.includes("max-width: 600px"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/members")) {
        return Promise.resolve(
          jsonResponse({
            items: [
              {
                account_id: "owner-a",
                name: "张三",
                email: "owner@example.com",
                role: "owner",
                status: "active",
                revision: 2,
              },
            ],
            count: 1,
            next_cursor: null,
            authorization_state: "workspace_authorization_not_enforced",
          }),
        );
      }
      if (url.includes("/datasets")) {
        return Promise.resolve(
          jsonResponse({
            items: [
              {
                dataset_id: "dataset-a",
                name: "产品知识库",
                binding_kind: "primary",
                status: "active",
                revision: 3,
              },
            ],
            count: 1,
            next_cursor: null,
            authorization_state: "workspace_authorization_not_enforced",
          }),
        );
      }
      if (url.endsWith("/api/enterprise/workspaces/workspace-prod")) {
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
            active_count: 1,
            default_workspace_id: "workspace-prod",
            primary_dataset_binding_count: 1,
            authorization_state: "workspace_authorization_not_enforced",
          },
        }),
      );
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  window.location.hash = "";
});

describe("Enterprise Workspace Center", () => {
  it("renders honest authority evidence, dense table and narrow-screen cards", async () => {
    render(<EnterpriseWorkspaceCenter scope={scope} context={context} />);

    expect(screen.queryByRole("heading", { name: "Enterprise Workspace Center" })).toBeNull();
    const evidence = await screen.findByRole("region", { name: "Workspace 权威证据" });
    expect(evidence).toBeTruthy();
    expect(within(evidence).getByText("生产知识域")).toBeTruthy();
    expect(within(evidence).queryByText("workspace-prod")).toBeNull();
    expect(screen.getAllByText("生产知识域").length).toBeGreaterThan(0);
    expect(screen.getByRole("table", { name: "企业 Workspace 列表" })).toBeTruthy();
    expect(screen.getByRole("list", { name: "移动端 Workspace 列表" })).toBeTruthy();
    expect(screen.getByText("Workspace Authorization rollout 已接入")).toBeTruthy();
    expect(screen.getByText(/current \/ candidate \/ delta/)).toBeTruthy();
    expect(screen.queryByText(/示例 Workspace|演示 Workspace/)).toBeNull();
  });

  it("opens a detail drawer with overview, members, knowledge bases and permissions", async () => {
    render(<EnterpriseWorkspaceCenter scope={scope} context={context} />);

    fireEvent.click(
      (await screen.findAllByRole("button", { name: "查看 Workspace 生产知识域" }))[0],
    );
    const drawer = await screen.findByRole("dialog", { name: "Workspace 详情：生产知识域" });
    expect(within(drawer).getByRole("tab", { name: "概览" })).toBeTruthy();
    expect(within(drawer).getByRole("tab", { name: "成员" })).toBeTruthy();
    expect(within(drawer).getByRole("tab", { name: "知识库" })).toBeTruthy();
    expect(within(drawer).getByRole("tab", { name: "权限" })).toBeTruthy();

    fireEvent.click(within(drawer).getByRole("tab", { name: "成员" }));
    expect(await within(drawer).findByText("owner@example.com")).toBeTruthy();
    fireEvent.click(within(drawer).getByRole("tab", { name: "知识库" }));
    expect(await within(drawer).findByText("产品知识库")).toBeTruthy();
    fireEvent.click(within(drawer).getByRole("tab", { name: "权限" }));
    expect(await within(drawer).findByText("Workspace 授权事实读取失败")).toBeTruthy();
  });

  it("consumes direct workspace deep links and clears the workspace query when the drawer closes", async () => {
    window.history.replaceState(null, "", "/enterprise/workspaces?workspace=workspace-prod");
    render(<EnterpriseWorkspaceCenter scope={scope} context={context} />);

    const drawer = await screen.findByRole("dialog", { name: "Workspace 详情：生产知识域" });
    expect(drawer).toBeTruthy();

    fireEvent.click(within(drawer).getByRole("button", { name: "关闭" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Workspace 详情：生产知识域" })).toBeNull(),
    );
    expect(window.location.pathname + window.location.search).toBe("/enterprise/workspaces");
  });

  it("opens a tenant-scoped workspace deep link even when it is not in the first list page", async () => {
    const hiddenWorkspace = {
      ...workspace,
      id: "workspace-hidden",
      code: "hidden",
      name: "分页外知识域",
      is_default: false,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/enterprise/workspaces/workspace-hidden")) {
        return Promise.resolve(
          jsonResponse({
            workspace: hiddenWorkspace,
            authorization_state: "workspace_authorization_not_enforced",
          }),
        );
      }
      if (url.includes("/members") || url.includes("/datasets")) {
        return Promise.resolve(
          jsonResponse({
            items: [],
            count: 0,
            next_cursor: null,
            authorization_state: "workspace_authorization_not_enforced",
          }),
        );
      }
      return Promise.resolve(
        jsonResponse({
          items: [workspace],
          count: 101,
          next_cursor: "next-page",
          evidence: {
            active_count: 101,
            default_workspace_id: "workspace-prod",
            primary_dataset_binding_count: 1,
            authorization_state: "workspace_authorization_not_enforced",
          },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    window.history.replaceState(null, "", "/enterprise/workspaces?workspace=workspace-hidden");
    render(<EnterpriseWorkspaceCenter scope={scope} context={context} />);

    expect(
      await screen.findByRole("dialog", { name: "Workspace 详情：分页外知识域" }),
    ).toBeTruthy();
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).endsWith("/api/enterprise/workspaces/workspace-hidden"),
      ),
    ).toBe(true);
  });

  it("consumes hash workspace deep links and clears the hash query when the drawer closes", async () => {
    window.history.replaceState(null, "", "/");
    window.location.hash = "/enterprise/workspaces?workspace=workspace-prod";
    render(<EnterpriseWorkspaceCenter scope={scope} context={context} />);

    const drawer = await screen.findByRole("dialog", { name: "Workspace 详情：生产知识域" });
    expect(drawer).toBeTruthy();

    fireEvent.click(within(drawer).getByRole("button", { name: "关闭" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Workspace 详情：生产知识域" })).toBeNull(),
    );
    expect(window.location.hash).toBe("#/enterprise/workspaces");
  });

  it("supports keyboard tab activation and linked tabpanel semantics", async () => {
    render(<EnterpriseWorkspaceCenter scope={scope} context={context} />);
    fireEvent.click(
      (await screen.findAllByRole("button", { name: "查看 Workspace 生产知识域" }))[0],
    );
    const drawer = await screen.findByRole("dialog", { name: "Workspace 详情：生产知识域" });
    const membersTab = within(drawer).getByRole("tab", { name: "成员" });
    membersTab.focus();
    fireEvent.keyDown(membersTab, { key: "Enter" });
    const membersPanel = await within(drawer).findByRole("tabpanel", { name: "成员" });
    expect(within(membersPanel).getByText("owner@example.com")).toBeTruthy();
    expect(membersTab.getAttribute("aria-controls")).toBe(membersPanel.id);
    expect(membersPanel.getAttribute("aria-labelledby")).toBe(membersTab.id);
  });

  it("exposes create, edit, archive, member and binding dialogs without optimistic fake rows", async () => {
    render(<EnterpriseWorkspaceCenter scope={scope} context={context} />);

    fireEvent.click(await screen.findByRole("button", { name: "创建 Workspace" }));
    const createDialog = screen.getByRole("dialog", { name: "创建 Workspace" });
    expect(createDialog).toBeTruthy();
    expect(
      within(createDialog).getByRole("textbox", { name: "描述" }).getAttribute("maxlength"),
    ).toBe("512");
    expect(
      within(createDialog).getByRole("textbox", { name: "Workspace Code" }).closest(".t-input"),
    ).not.toBeNull();
    expect(
      within(createDialog).getByRole("textbox", { name: "描述" }).closest(".t-textarea"),
    ).not.toBeNull();
    expect(
      within(createDialog).getByRole("combobox", { name: "环境" }).closest(".t-select"),
    ).not.toBeNull();
    expect(
      within(createDialog).getAllByText((content) => content.replace(/\s/g, "") === "0/512"),
    ).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    fireEvent.click(screen.getAllByRole("button", { name: "查看 Workspace 生产知识域" })[0]);
    const drawer = await screen.findByRole("dialog", { name: "Workspace 详情：生产知识域" });
    fireEvent.click(within(drawer).getByRole("button", { name: "编辑 Workspace" }));
    const editDialog = screen.getByRole("dialog", { name: "编辑 Workspace" });
    expect(editDialog).toBeTruthy();
    expect(
      within(editDialog).getByRole("textbox", { name: "描述" }).getAttribute("maxlength"),
    ).toBe("512");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    fireEvent.click(within(drawer).getByRole("tab", { name: "成员" }));
    fireEvent.click(within(drawer).getByRole("button", { name: "添加成员" }));
    expect(screen.getByRole("dialog", { name: "添加 Workspace 成员" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    fireEvent.click(within(drawer).getByRole("tab", { name: "知识库" }));
    fireEvent.click(within(drawer).getByRole("button", { name: "绑定知识库" }));
    expect(screen.getByRole("dialog", { name: "绑定知识库" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    fireEvent.click(within(drawer).getByRole("button", { name: "归档 Workspace" }));
    expect(screen.getByRole("dialog", { name: "归档 Workspace" })).toBeTruthy();
    expect(screen.getByText("默认 Workspace 拥有主知识库绑定时不能归档")).toBeTruthy();
  });

  it("fails closed on Edit with an explicit tenant-manager explanation", async () => {
    const limitedContext = {
      ...context,
      actor: { ...context.actor, role: "editor" },
    };
    render(<EnterpriseWorkspaceCenter scope={scope} context={limitedContext} />);
    fireEvent.click(
      (await screen.findAllByRole("button", { name: "查看 Workspace 生产知识域" }))[0],
    );
    const drawer = await screen.findByRole("dialog", { name: "Workspace 详情：生产知识域" });
    const edit = within(drawer).getByRole("button", {
      name: "编辑 Workspace",
    }) as HTMLButtonElement;
    expect(edit.disabled).toBe(true);
    expect(edit.getAttribute("title")).toContain("租户所有者或管理员");
  });

  it("blocks empty create locally, exposes field errors, focuses Code, and submits after correction", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/enterprise/workspaces") && init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({ workspace: { ...workspace, id: "workspace-new" } }, 201),
        );
      }
      return Promise.resolve(
        jsonResponse({
          items: [workspace],
          next_cursor: null,
          evidence: {
            default_workspace_id: "workspace-prod",
            authorization_state: "workspace_authorization_not_enforced",
          },
        }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<EnterpriseWorkspaceCenter scope={scope} context={context} />);
    fireEvent.click(await screen.findByRole("button", { name: "创建 Workspace" }));
    const dialog = screen.getByRole("dialog", { name: "创建 Workspace" });
    const callsBeforeSubmit = fetchMock.mock.calls.length;
    fireEvent.click(within(dialog).getByRole("button", { name: "创建 Workspace" }));

    expect(fetchMock).toHaveBeenCalledTimes(callsBeforeSubmit);
    expect(within(dialog).getByRole("alert", { name: "Workspace Code 为必填项" })).toBeTruthy();
    expect(within(dialog).getByRole("alert", { name: "Workspace 名称为必填项" })).toBeTruthy();
    expect(within(dialog).getByRole("alert", { name: "变更原因为必填项" })).toBeTruthy();
    const codeInput = within(dialog).getByRole("textbox", { name: "Workspace Code" });
    expect(document.activeElement).toBe(codeInput);
    expect(codeInput.getAttribute("aria-invalid")).toBe("true");
    const describedBy = codeInput.getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    expect(document.getElementById(describedBy ?? "")?.textContent).toContain(
      "Workspace Code 为必填项",
    );

    fireEvent.change(within(dialog).getByRole("textbox", { name: "Workspace Code" }), {
      target: { value: "Bad_Code" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Workspace 名称" }), {
      target: { value: "生产知识域 2" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "变更原因" }), {
      target: { value: "建立正式治理边界" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建 Workspace" }));
    expect(within(dialog).getByText(/仅允许小写字母、数字和连字符/)).toBeTruthy();

    fireEvent.change(within(dialog).getByRole("textbox", { name: "Workspace Code" }), {
      target: { value: "prod-2" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "创建 Workspace" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            String(input).endsWith("/api/enterprise/workspaces") && init?.method === "POST",
        ),
      ).toBe(true),
    );
  });

  it("requests active-only Workspace member and dataset management views", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        urls.push(url);
        if (url.includes("/members")) {
          return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
        }
        if (url.includes("/datasets")) {
          return Promise.resolve(jsonResponse({ items: [], next_cursor: null }));
        }
        if (url.endsWith("/api/enterprise/workspaces/workspace-prod")) {
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
            next_cursor: null,
            evidence: {
              default_workspace_id: "workspace-prod",
              authorization_state: "workspace_authorization_not_enforced",
            },
          }),
        );
      }),
    );

    render(<EnterpriseWorkspaceCenter scope={scope} context={context} />);
    fireEvent.click(
      (await screen.findAllByRole("button", { name: "查看 Workspace 生产知识域" }))[0],
    );
    await screen.findByRole("dialog", { name: "Workspace 详情：生产知识域" });

    expect(urls.some((url) => url.includes("/members?status=active&limit=100"))).toBe(true);
    expect(urls.some((url) => url.includes("/datasets?status=active&limit=100"))).toBe(true);
  });

  it("renders keyset load-more controls for Workspace, members, and knowledge bases", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/workspaces?") && url.includes("cursor=workspace-next")) {
          return Promise.resolve(
            jsonResponse({
              items: [
                { ...workspace },
                {
                  ...workspace,
                  id: "workspace-test",
                  code: "test",
                  name: "测试知识域",
                  is_default: false,
                  environment: "testing",
                },
              ],
              next_cursor: null,
              evidence: {
                authorization_state: "workspace_authorization_not_enforced",
              },
            }),
          );
        }
        if (url.includes("/members?") && url.includes("cursor=member-next")) {
          return Promise.resolve(
            jsonResponse({
              items: [
                {
                  account_id: "owner-a",
                  name: "张三",
                  email: "owner@example.com",
                  role: "owner",
                  status: "active",
                  revision: 2,
                },
                {
                  account_id: "admin-a",
                  name: "李四",
                  email: "admin@example.com",
                  role: "admin",
                  status: "active",
                  revision: 1,
                },
              ],
              next_cursor: null,
              authorization_state: "workspace_authorization_not_enforced",
            }),
          );
        }
        if (url.includes("/datasets?") && url.includes("cursor=dataset-next")) {
          return Promise.resolve(
            jsonResponse({
              items: [
                {
                  dataset_id: "dataset-a",
                  name: "产品知识库",
                  binding_kind: "primary",
                  status: "active",
                  revision: 3,
                },
                {
                  dataset_id: "dataset-b",
                  name: "支持知识库",
                  binding_kind: "shared",
                  status: "active",
                  revision: 1,
                },
              ],
              next_cursor: null,
              authorization_state: "workspace_authorization_not_enforced",
            }),
          );
        }
        if (url.includes("/members")) {
          return Promise.resolve(
            jsonResponse({
              items: [
                {
                  account_id: "owner-a",
                  name: "张三",
                  email: "owner@example.com",
                  role: "owner",
                  status: "active",
                  revision: 2,
                },
              ],
              next_cursor: "member-next",
              authorization_state: "workspace_authorization_not_enforced",
            }),
          );
        }
        if (url.includes("/datasets")) {
          return Promise.resolve(
            jsonResponse({
              items: [
                {
                  dataset_id: "dataset-a",
                  name: "产品知识库",
                  binding_kind: "primary",
                  status: "active",
                  revision: 3,
                },
              ],
              next_cursor: "dataset-next",
              authorization_state: "workspace_authorization_not_enforced",
            }),
          );
        }
        if (url.endsWith("/api/enterprise/workspaces/workspace-prod")) {
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
            next_cursor: "workspace-next",
            evidence: {
              default_workspace_id: "workspace-prod",
              authorization_state: "workspace_authorization_not_enforced",
            },
          }),
        );
      }),
    );

    render(<EnterpriseWorkspaceCenter scope={scope} context={context} />);
    const evidence = await screen.findByRole("region", { name: "Workspace 权威证据" });
    expect(within(evidence).getAllByText("未返回").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: "加载更多 Workspace" }));
    expect((await screen.findAllByText("测试知识域")).length).toBeGreaterThan(0);

    fireEvent.click(screen.getAllByRole("button", { name: "查看 Workspace 生产知识域" })[0]);
    const drawer = await screen.findByRole("dialog", { name: "Workspace 详情：生产知识域" });
    fireEvent.click(within(drawer).getByRole("tab", { name: "成员" }));
    fireEvent.click(within(drawer).getByRole("button", { name: "加载更多成员" }));
    expect(await within(drawer).findByText("admin@example.com")).toBeTruthy();

    fireEvent.click(within(drawer).getByRole("tab", { name: "知识库" }));
    fireEvent.click(within(drawer).getByRole("button", { name: "加载更多知识库" }));
    expect(await within(drawer).findByText("支持知识库")).toBeTruthy();
  });

  it("keeps loaded Workspace rows visible when continuation fails", async () => {
    let listRequests = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/enterprise/workspaces?")) {
          listRequests += 1;
          if (listRequests > 1) return Promise.reject(new TypeError("network unavailable"));
          return Promise.resolve(
            jsonResponse({
              items: [workspace],
              next_cursor: "workspace-next",
              evidence: {
                default_workspace_id: "workspace-prod",
                authorization_state: "workspace_authorization_not_enforced",
              },
            }),
          );
        }
        return Promise.resolve(jsonResponse({ detail: "not found" }, 404));
      }),
    );

    render(<EnterpriseWorkspaceCenter scope={scope} context={context} />);
    expect((await screen.findAllByText("生产知识域")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "加载更多 Workspace" }));

    expect(await screen.findByText("无法加载更多 Workspace")).toBeTruthy();
    expect(screen.getAllByText("生产知识域").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "重试加载更多 Workspace" })).toBeTruthy();
  });
});
