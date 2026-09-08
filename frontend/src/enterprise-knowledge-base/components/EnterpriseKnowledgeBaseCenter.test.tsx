// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import EnterpriseKnowledgeBaseCenter from "./EnterpriseKnowledgeBaseCenter";
import type {
  EnterpriseKnowledgeBase,
  EnterpriseKnowledgeBaseDetail,
} from "../enterpriseKnowledgeBaseModel";

const center = vi.hoisted(() => ({
  status: "ready",
  page: {
    items: [
      {
        id: "dataset-prod",
        name: "客服知识库",
        description: "正式客服知识",
        status: "active",
        visibility: "tenant",
        profile_revision: 12,
        ownership_revision: 7,
        workspace: {
          id: "workspace-prod",
          name: "生产知识域",
          status: "active",
          revision: 7,
        },
        owning_workspace: {
          id: "workspace-prod",
          name: "生产知识域",
          status: "active",
          revision: 7,
        },
        shared_association_count: 2,
        active_application_reference_count: 3,
        document_count: 128,
        chunk_count: 420,
        source_count: 4,
        updated_at: "2026-08-27T08:00:00Z",
        archive_ready: false,
        archive_blocker_count: 3,
        catalog_revision: "0028_enterprise_knowledge_base_registry",
        capability_state: "ready",
      },
    ],
    count: 1,
    next_cursor: null,
    evidence: {
      knowledge_base_count: 1,
      active_count: 1,
      owned_count: 1,
      active_application_reference_count: 3,
      archive_ready_count: 0,
      catalog_revision: "0028_enterprise_knowledge_base_registry",
      capability_state: "ready",
    },
  },
  selected: null as EnterpriseKnowledgeBaseDetail | null,
  openKnowledgeBase: vi.fn(),
  closeKnowledgeBase: vi.fn(),
  reload: vi.fn(),
  loadMore: vi.fn(),
  addApplicationReference: vi.fn(),
  removeApplicationReference: vi.fn(),
  transferOwnership: vi.fn(),
  lastFilters: null as Record<string, unknown> | null,
  mutation: { saving: false, error: null, success: null, outcome: null },
}));

vi.mock("../hooks/useEnterpriseKnowledgeBaseCenter", () => ({
  useEnterpriseKnowledgeBaseCenter: (_scope: unknown, filters: Record<string, unknown>) => {
    center.lastFilters = filters;
    return center;
  },
}));

const context = {
  tenant: {
    id: "tenant-a",
    name: "星海科技",
    plan: "enterprise",
    status: "active",
    quota_documents: 1000,
    quota_chunks: 10000,
    doc_count: 128,
    chunk_count: 420,
  },
  actor: { id: "owner-a", name: "林澈", email: "lin@example.com", role: "owner" },
  member_count: 3,
  dataset_count: 1,
  effective_permissions: ["knowledge.read", "knowledge.manage"],
  role_permissions: {},
  capabilities: {},
};

const scope = { tenantId: "tenant-a", datasetId: "dataset-prod", actorToken: "actor-token" };
const workspaceOptions = [{ value: "workspace-test", label: "测试知识域", revision: 4 }];

function detail(): EnterpriseKnowledgeBaseDetail {
  return {
    knowledge_base: center.page.items[0] as EnterpriseKnowledgeBase,
    application_references: {
      items: [
        {
          id: "ref-1",
          app_id: "app-support",
          app_name: "客服助手",
          dataset_id: "dataset-prod",
          reference_kind: "knowledge",
          status: "active",
          revision: 4,
          created_at: null,
          updated_at: null,
        },
      ],
      count: 1,
      next_cursor: null,
    },
    dependencies: {
      state: "ready",
      reason: null,
      owning_workspace: center.page.items[0].owning_workspace,
      shared_associations: [
        {
          id: "assoc-1",
          workspace_id: "workspace-test",
          workspace_name: "测试知识域",
          binding_kind: "shared",
          status: "active",
          revision: 2,
        },
      ],
      application_references: [],
      archive: {
        ready: false,
        blocker_count: 3,
        blockers: [
          {
            code: "active_application_references",
            label: "存在活跃 Application 引用",
            count: 3,
            status: null,
          },
        ],
      },
    },
  };
}

beforeEach(() => {
  center.selected = null;
  center.openKnowledgeBase.mockReset();
  center.closeKnowledgeBase.mockReset();
  center.addApplicationReference.mockReset().mockResolvedValue({ state: "applied" });
  center.removeApplicationReference.mockReset().mockResolvedValue({ state: "applied" });
  center.transferOwnership.mockReset().mockResolvedValue({ state: "applied" });
});

afterEach(() => cleanup());

describe("Stage18 Enterprise Knowledge Base Center", () => {
  it("renders authority evidence, dense table, mobile cards and the dependency rail contract", () => {
    render(
      <EnterpriseKnowledgeBaseCenter
        scope={scope}
        context={context}
        workspaceOptions={workspaceOptions}
      />,
    );

    expect(screen.getByRole("region", { name: "Knowledge Base 权威证据" })).toBeTruthy();
    expect(screen.getByRole("table", { name: "企业知识库列表" })).toBeTruthy();
    expect(screen.getByRole("list", { name: "移动端知识库列表" })).toBeTruthy();
    expect(screen.getByText("3 个活跃 Application 引用")).toBeTruthy();
    expect(screen.getByText("知识库真账")).toBeTruthy();
  });

  it("exposes a TDesign filter Drawer for narrow layouts", async () => {
    render(
      <EnterpriseKnowledgeBaseCenter
        scope={scope}
        context={context}
        workspaceOptions={workspaceOptions}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /打开筛选/ }));
    const drawer = await screen.findByRole("dialog", { name: "知识库筛选" });
    const search = within(drawer).getByRole("textbox", { name: /搜索知识库/ });
    const status = within(drawer).getByRole("combobox", { name: "知识库状态" });
    const workspace = within(drawer).getByRole("combobox", { name: "知识库 Workspace" });
    expect(status.closest("label")?.textContent).toContain("状态");
    expect(workspace.closest("label")?.textContent).toContain("Owning Workspace");
    expect(status.contains(search)).toBe(false);
    fireEvent.change(search, { target: { value: "客服" } });
    await waitFor(() => expect(center.lastFilters?.keyword).toBe("客服"));
    fireEvent.click(within(drawer).getByRole("button", { name: "完成" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "知识库筛选" })).toBeNull());
  });

  it("disables Application reference controls without knowledge.manage and explains the read-only state", async () => {
    center.selected = detail();
    const readOnlyContext = {
      ...context,
      actor: { ...context.actor, role: "member" },
      effective_permissions: ["knowledge.read"],
    };
    render(
      <EnterpriseKnowledgeBaseCenter
        scope={scope}
        context={readOnlyContext}
        workspaceOptions={workspaceOptions}
      />,
    );

    const drawer = await screen.findByRole("dialog", { name: "知识库详情：客服知识库" });
    fireEvent.click(within(drawer).getByRole("tab", { name: "Application references" }));
    expect(within(drawer).getByRole("button", { name: "添加 Application 引用" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(within(drawer).getByRole("button", { name: /移除 Application 引用/ })).toHaveProperty(
      "disabled",
      true,
    );
    expect(within(drawer).getByText(/knowledge\.manage/)).toBeTruthy();
  });

  it("renders detail tabs, application reference controls and an archive blocker", async () => {
    center.selected = detail();
    render(
      <EnterpriseKnowledgeBaseCenter
        scope={scope}
        context={context}
        workspaceOptions={workspaceOptions}
      />,
    );

    const drawer = await screen.findByRole("dialog", { name: "知识库详情：客服知识库" });
    expect(within(drawer).getByRole("tab", { name: "Overview" })).toBeTruthy();
    expect(within(drawer).getByRole("tab", { name: "Workspace" })).toBeTruthy();
    expect(within(drawer).getByRole("tab", { name: "Application references" })).toBeTruthy();
    expect(within(drawer).getByRole("tab", { name: "Dependencies" })).toBeTruthy();
    expect(within(drawer).getByRole("tab", { name: "Operations" })).toBeTruthy();
    fireEvent.click(within(drawer).getByRole("tab", { name: "Dependencies" }));
    expect(within(drawer).getByRole("region", { name: "依赖证据链" })).toBeTruthy();
    fireEvent.click(within(drawer).getByRole("tab", { name: "Operations" }));
    expect(within(drawer).getByText(/当前无法归档知识库/)).toBeTruthy();
    expect(within(drawer).getByText(/审批不能绕过该依赖/)).toBeTruthy();
  });

  it("opens the persistent Knowledge Base workspace from the Registry quick view", async () => {
    window.history.replaceState(null, "", "/enterprise/knowledge-bases?dataset=dataset-prod");
    center.selected = detail();
    render(
      <EnterpriseKnowledgeBaseCenter
        scope={scope}
        context={context}
        workspaceOptions={workspaceOptions}
      />,
    );

    const drawer = await screen.findByRole("dialog", { name: "知识库详情：客服知识库" });
    fireEvent.click(within(drawer).getByRole("button", { name: "打开 Knowledge Base 工作台" }));

    expect(center.closeKnowledgeBase).toHaveBeenCalled();
    expect(window.location.pathname).toBe("/enterprise/knowledge-base");
    expect(new URLSearchParams(window.location.search).get("dataset")).toBe("dataset-prod");
    expect(new URLSearchParams(window.location.search).get("section")).toBe("overview");
  });

  it("submits Application reference creation with the selected Dataset", async () => {
    center.selected = detail();
    render(<EnterpriseKnowledgeBaseCenter scope={scope} context={context} />);
    const drawer = await screen.findByRole("dialog", { name: "知识库详情：客服知识库" });
    fireEvent.click(within(drawer).getByRole("tab", { name: "Application references" }));
    fireEvent.click(within(drawer).getByRole("button", { name: "添加 Application 引用" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Application ID" }), {
      target: { value: "app-new" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: "变更原因" }), {
      target: { value: "关联新客服应用" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加引用" }));
    expect(center.addApplicationReference).toHaveBeenCalledWith({
      appId: "app-new",
      datasetId: "dataset-prod",
      reason: "关联新客服应用",
    });
  });

  it("shows approval UX for a gated ownership transfer without exposing a ticket", async () => {
    center.selected = detail();
    center.transferOwnership.mockResolvedValue({
      state: "approval_required",
      approval_request_id: "request-1",
    });
    render(
      <EnterpriseKnowledgeBaseCenter
        scope={scope}
        context={context}
        workspaceOptions={workspaceOptions}
      />,
    );
    const drawer = await screen.findByRole("dialog", { name: "知识库详情：客服知识库" });
    fireEvent.click(within(drawer).getByRole("tab", { name: "Workspace" }));
    fireEvent.click(within(drawer).getByRole("button", { name: "转移所有权" }));
    const targetWorkspace = screen.getByRole("combobox", { name: "目标 Workspace" });
    fireEvent.click(targetWorkspace);
    fireEvent.click(await screen.findByText(/测试知识域/));
    fireEvent.change(screen.getByRole("textbox", { name: "变更原因" }), {
      target: { value: "组织调整" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交所有权转移" }));
    expect(center.transferOwnership).toHaveBeenCalledWith("dataset-prod", {
      targetWorkspaceId: "workspace-test",
      expectedDatasetProfileRevision: 12,
      expectedOwnershipRevision: 7,
      expectedSourceWorkspaceRevision: 7,
      expectedTargetWorkspaceRevision: 4,
      reason: "组织调整",
    });
    expect(await screen.findByText("已提交审批，等待 Workspace 所有者审核")).toBeTruthy();
    expect(screen.queryByText(/request-1|ticket|审批票据/i)).toBeNull();
  });

  it("fails closed when authoritative Workspace revisions are unavailable", async () => {
    center.selected = detail();
    const missingSourceRevision = detail();
    missingSourceRevision.knowledge_base = {
      ...missingSourceRevision.knowledge_base,
      owning_workspace: {
        ...missingSourceRevision.knowledge_base.owning_workspace!,
        revision: null,
      },
    };
    center.selected = missingSourceRevision;
    const { rerender } = render(
      <EnterpriseKnowledgeBaseCenter
        scope={scope}
        context={context}
        workspaceOptions={workspaceOptions}
      />,
    );

    let drawer = await screen.findByRole("dialog", { name: "知识库详情：客服知识库" });
    fireEvent.click(within(drawer).getByRole("tab", { name: "Workspace" }));
    expect(within(drawer).getByRole("button", { name: "转移所有权" })).toHaveProperty(
      "disabled",
      true,
    );

    center.selected = detail();
    rerender(
      <EnterpriseKnowledgeBaseCenter
        scope={scope}
        context={context}
        workspaceOptions={[{ value: "workspace-test", label: "测试知识域" }]}
      />,
    );
    drawer = await screen.findByRole("dialog", { name: "知识库详情：客服知识库" });
    fireEvent.click(within(drawer).getByRole("tab", { name: "Workspace" }));
    expect(within(drawer).getByRole("button", { name: "转移所有权" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(center.transferOwnership).not.toHaveBeenCalled();
  });

  it("focuses a mutation dialog, closes it with Escape and restores the trigger", async () => {
    center.selected = detail();
    render(
      <EnterpriseKnowledgeBaseCenter
        scope={scope}
        context={context}
        workspaceOptions={workspaceOptions}
      />,
    );
    const drawer = await screen.findByRole("dialog", { name: "知识库详情：客服知识库" });
    fireEvent.click(within(drawer).getByRole("tab", { name: "Workspace" }));
    const trigger = within(drawer).getByRole("button", { name: "转移所有权" });
    fireEvent.click(trigger);
    const target = await screen.findByRole("combobox", { name: "目标 Workspace" });
    expect(target).toBeTruthy();
    expect(document.querySelector(".enterprise-knowledge-base-dialog select")).toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "转移知识库所有权" })).toBeNull(),
    );
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("closes detail and mutation dialogs with Escape", () => {
    center.selected = detail();
    render(<EnterpriseKnowledgeBaseCenter scope={scope} context={context} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(center.closeKnowledgeBase).toHaveBeenCalled();
  });
});
