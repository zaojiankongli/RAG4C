// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  mobile: false,
  fetchDetail: vi.fn(),
  workspace: {
    tenantId: "tenant-a",
    datasetId: "dataset-prod",
    workspaceId: "workspace-prod",
    workspaceScopeStatus: "verified",
    scope: { tenantId: "tenant-a", datasetId: "dataset-prod" },
  },
}));

vi.mock("../knowledge/KnowledgeWorkspaceContext", () => ({
  useOptionalKnowledgeWorkspace: () => state.workspace,
}));
vi.mock("../knowledge/workspaceScope", () => ({
  readKnowledgeActorToken: () => "signed-token",
}));
vi.mock("../enterprise-knowledge-base/api/enterpriseKnowledgeBaseApi", () => ({
  fetchEnterpriseKnowledgeBaseDetail: state.fetchDetail,
}));

import KnowledgeBaseResourceShell from "./KnowledgeBaseResourceShell";
import type { KnowledgeBaseResourceSection } from "./knowledgeBaseResourceRoute";
import { KnowledgeBaseDrawerCoordinatorProvider } from "./KnowledgeBaseDrawerCoordinator";
import { useKnowledgeBaseDrawerCoordinator } from "./KnowledgeBaseDrawerCoordinatorContext";

function CoordinatorProbe() {
  const { openDrawer, setReleaseContext } = useKnowledgeBaseDrawerCoordinator();
  useEffect(() => {
    setReleaseContext({
      channelName: "UAT 验证",
      effectiveReleaseNumber: 41,
      servingReleaseNumber: 40,
      unavailableReason: null,
    });
    return () => setReleaseContext(null);
  }, [setReleaseContext]);
  return (
    <button type="button" onClick={() => openDrawer("release-detail")}>
      打开 Release Drawer
    </button>
  );
}

const detail = {
  knowledge_base: {
    id: "dataset-prod",
    name: "客服知识库",
    description: "正式客服知识",
    status: "active",
    visibility: "tenant",
    profile_revision: 12,
    ownership_revision: 7,
    owning_workspace: {
      id: "workspace-prod",
      name: "生产知识域",
      status: "active",
      revision: 7,
    },
    workspace: {
      id: "workspace-prod",
      name: "生产知识域",
      status: "active",
      revision: 7,
    },
    shared_association_count: 0,
    active_application_reference_count: 2,
    document_count: 128,
    chunk_count: 420,
    source_count: 4,
    updated_at: "2026-08-27T08:00:00Z",
    archive_ready: false,
    archive_blocker_count: 1,
    catalog_revision: "0028_enterprise_knowledge_base_registry",
    capability_state: "ready",
  },
  application_references: { items: [], count: 0, next_cursor: null },
  dependencies: { items: [], count: 0, next_cursor: null },
};

beforeEach(() => {
  state.mobile = false;
  state.fetchDetail.mockReset();
  state.fetchDetail.mockResolvedValue(detail);
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: state.mobile,
      media: "(max-width: 768px)",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

afterEach(cleanup);

describe("Stage19 Knowledge Base resource shell", () => {
  it("renders one authoritative page heading and desktop TDesign resource Tabs", async () => {
    render(
      <KnowledgeBaseResourceShell active section="documents">
        <section aria-label="嵌入式文档内容" />
      </KnowledgeBaseResourceShell>,
    );

    expect(await screen.findByRole("heading", { name: "客服知识库" })).toBeTruthy();
    expect(screen.getAllByRole("heading", { name: "客服知识库" })).toHaveLength(1);
    expect(screen.getByText("生产知识域")).toBeTruthy();
    expect(screen.getByRole("tablist", { name: "知识库资源导航" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Documents" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "嵌入式文档内容" })).toBeTruthy();
    expect(state.fetchDetail).toHaveBeenCalledTimes(1);
  });

  /**
   * 桌面档此前有 tablist 和 role=tab，但 tab 上**没有 aria-controls**、内容容器也**没有
   * role=tabpanel**——按 WAI-ARIA 是不完整的 tabs，读屏拿到的是"关联不到面板的 tab"。
   * 视觉门禁 327 个结构违规里 210 个来自这里（7 个 tab × 6 次加载 × 5 条路由）。
   *
   * 这里刻意钉住"所有 tab 指向同一个 panel"：本组件只渲染一份内容容器（随 section 换内容），
   * 若哪天有人"按每个 section 各造一个 panel id"，未选中的 tab 就会指向不存在的元素——
   * 那是从 no-aria-controls 退化成 dangling-aria-controls，数字不会变好。
   */
  it("wires every desktop resource tab to the one rendered panel", async () => {
    render(
      <KnowledgeBaseResourceShell active section="documents">
        <section aria-label="嵌入式文档内容" />
      </KnowledgeBaseResourceShell>,
    );
    await screen.findByRole("heading", { name: "客服知识库" });

    const tabs = screen.getAllByRole("tab");
    expect(tabs.length).toBeGreaterThan(1);
    const panel = screen.getByRole("tabpanel");
    for (const tab of tabs) {
      const controls = tab.getAttribute("aria-controls");
      expect(controls, `tab "${tab.textContent}" 缺 aria-controls`).toBeTruthy();
      const target = document.getElementById(controls!);
      expect(target, `aria-controls 指向不存在的元素：${controls}`).toBeTruthy();
      expect(target).toBe(panel);
    }

    const labelledBy = panel.getAttribute("aria-labelledby");
    expect(labelledBy).toBeTruthy();
    const owner = document.getElementById(labelledBy!);
    expect(owner, "panel 的 aria-labelledby 指向不存在的 tab").toBeTruthy();
    expect(owner?.getAttribute("aria-selected")).toBe("true");
  });

  it("drops the tabpanel along with the tablist in the compact Select layout", async () => {
    state.mobile = true;
    render(
      <KnowledgeBaseResourceShell active section="documents">
        <section aria-label="嵌入式文档内容" />
      </KnowledgeBaseResourceShell>,
    );
    await screen.findByRole("heading", { name: "客服知识库" });

    expect(screen.queryByRole("tablist", { name: "知识库资源导航" })).toBeNull();
    // 没有 tablist 就不该留下一个孤零零的 tabpanel（aria-labelledby 会指向不存在的 tab）
    expect(screen.queryByRole("tabpanel")).toBeNull();
    expect(screen.getByRole("region", { name: "嵌入式文档内容" })).toBeTruthy();
  });

  it("uses one visibly labelled mobile Select instead of a second navigation Drawer", async () => {
    state.mobile = true;
    render(
      <KnowledgeBaseResourceShell active section="sources">
        <section aria-label="嵌入式来源内容" />
      </KnowledgeBaseResourceShell>,
    );

    expect(await screen.findByRole("heading", { name: "客服知识库" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "知识库资源" })).toBeTruthy();
    expect(screen.queryByRole("tablist", { name: "知识库资源导航" })).toBeNull();
    expect(screen.queryByRole("dialog", { name: /资源导航/ })).toBeNull();
  });

  it("does not request or render kept-alive children while inactive", () => {
    render(
      <KnowledgeBaseResourceShell active={false} section="governance">
        <section aria-label="不应激活的内容" />
      </KnowledgeBaseResourceShell>,
    );

    expect(state.fetchDetail).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "不应激活的内容" })).toBeNull();
  });

  it("returns focus to the resource trigger when the dirty-state guard cancels navigation", async () => {
    const onSectionChange = vi.fn(() => false);
    render(
      <KnowledgeBaseResourceShell active section="documents" onSectionChange={onSectionChange}>
        <section aria-label="嵌入式文档内容" />
      </KnowledgeBaseResourceShell>,
    );

    await screen.findByRole("heading", { name: "客服知识库" });
    const trigger = screen.getByRole("tab", { name: "Sources" });
    fireEvent.click(trigger);
    await waitFor(() => expect(document.activeElement).toBe(trigger));
    expect(onSectionChange).toHaveBeenCalledWith("sources", expect.any(HTMLElement));
  });

  it("supports arrow-key tab navigation through the governed resource list", async () => {
    const onSectionChange = vi.fn();
    render(
      <KnowledgeBaseResourceShell active section="documents" onSectionChange={onSectionChange}>
        <section aria-label="嵌入式文档内容" />
      </KnowledgeBaseResourceShell>,
    );
    await screen.findByRole("heading", { name: "客服知识库" });
    const documents = screen.getByRole("tab", { name: "Documents" });
    fireEvent.keyDown(documents, { key: "ArrowRight" });
    expect(onSectionChange).toHaveBeenCalledWith("taxonomy", expect.any(HTMLElement));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Taxonomy" })),
    );
  });

  it("coordinates Facts and Release Drawers, publishes real Release context, and restores Facts focus", async () => {
    render(
      <KnowledgeBaseDrawerCoordinatorProvider>
        <KnowledgeBaseResourceShell active section="releases">
          <CoordinatorProbe />
        </KnowledgeBaseResourceShell>
      </KnowledgeBaseDrawerCoordinatorProvider>,
    );
    await screen.findByRole("heading", { name: "客服知识库" });
    expect(screen.getByText(/UAT 验证 · 发布 40/)).toBeTruthy();
    const factsTrigger = screen.getByRole("button", { name: "查看权威事实" });
    fireEvent.click(factsTrigger);
    const facts = await screen.findByRole("dialog", { name: "Knowledge Base 权威事实" });
    await waitFor(() => expect(document.activeElement).toBe(facts));
    expect(screen.getByText(/生效发布 41 · 服务中发布 40/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "打开 Release Drawer" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Knowledge Base 权威事实" })).toBeNull(),
    );

    fireEvent.click(factsTrigger);
    await screen.findByRole("dialog", { name: "Knowledge Base 权威事实" });
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    await waitFor(() => expect(document.activeElement).toBe(factsTrigger));
  });
});

describe("Stage26 Knowledge Serving resource shell", () => {
  it("clears the previous Dataset authority before requesting the next Dataset authority", async () => {
    const requestObservations: Array<{ datasetId: string; oldHeadingVisible: boolean }> = [];
    state.fetchDetail.mockImplementation((_scope: unknown, requestedDatasetId: string) => {
      requestObservations.push({
        datasetId: requestedDatasetId,
        oldHeadingVisible: Boolean(screen.queryByRole("heading", { name: "客服知识库" })),
      });
      return Promise.resolve(
        requestedDatasetId === "dataset-prod"
          ? detail
          : {
              ...detail,
              knowledge_base: {
                ...detail.knowledge_base,
                id: requestedDatasetId,
                name: "下一知识库",
              },
            },
      );
    });

    const view = render(
      <KnowledgeBaseResourceShell active section="serving" datasetId="dataset-prod">
        <section aria-label="知识服务可靠性内容" />
      </KnowledgeBaseResourceShell>,
    );
    await screen.findByRole("heading", { name: "客服知识库" });

    view.rerender(
      <KnowledgeBaseResourceShell active section="serving" datasetId="dataset-next">
        <section aria-label="知识服务可靠性内容" />
      </KnowledgeBaseResourceShell>,
    );
    await screen.findByRole("heading", { name: "下一知识库" });

    expect(requestObservations).toEqual([
      { datasetId: "dataset-prod", oldHeadingVisible: false },
      { datasetId: "dataset-next", oldHeadingVisible: false },
    ]);
    expect(screen.queryByRole("heading", { name: "客服知识库" })).toBeNull();
  });

  it("exposes the Serving resource with an enterprise reliability label", async () => {
    render(
      <KnowledgeBaseResourceShell active section={"serving" as KnowledgeBaseResourceSection}>
        <section aria-label="知识服务可靠性内容" />
      </KnowledgeBaseResourceShell>,
    );

    expect(await screen.findByRole("heading", { name: "客服知识库" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Serving" })).toBeTruthy();
    expect(screen.getByText("知识服务可靠性")).toBeTruthy();
    expect(screen.getByRole("region", { name: "知识服务可靠性内容" })).toBeTruthy();
  });
});
