// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const enterpriseIdentityApi = vi.hoisted(() => ({
  fetchEnterpriseContext: vi.fn(),
}));
vi.mock("./enterprise-admin/api/enterpriseAdminApi", () => enterpriseIdentityApi);
vi.mock("./context/ConnectionContext", () => ({
  useConnection: () => ({
    online: true,
    checking: false,
    health: { status: "ok" },
    refresh: vi.fn(),
  }),
}));
vi.mock("./components/ModeBanner", () => ({ default: () => null }));
vi.mock("./components/PageState", () => ({ default: () => <div>loading</div> }));
vi.mock("./components/ErrorBoundary", () => ({
  default: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("./pages/QueryPage", () => ({ default: () => <section aria-label="问答页面" /> }));
vi.mock("./pages/KnowledgeOverviewPage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeTaxonomyPage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeSourcesPage", () => ({ default: () => <section /> }));
vi.mock("./pages/RetrievalLabPage", () => ({ default: () => <section /> }));
vi.mock("./pages/DocumentsPage", () => ({ default: () => <section /> }));
vi.mock("./pages/VisualizePage", () => ({ default: () => <section /> }));
vi.mock("./pages/MonitorPage", () => ({ default: () => <section /> }));
vi.mock("./pages/EvalPage", () => ({ default: () => <section /> }));
vi.mock("./pages/ConfigPage", () => ({ default: () => <section /> }));

import App from "./App";

beforeEach(() => {
  localStorage.clear();
  enterpriseIdentityApi.fetchEnterpriseContext.mockReset();
  window.history.replaceState(null, "", "/query");
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: false,
      media: "",
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

describe("App document landmarks", () => {
  it("exposes the enterprise workspace scope and global knowledge search", () => {
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(screen.getByRole("searchbox", { name: "全局知识搜索" })).toBeTruthy();
    expect(screen.getByText("RAG4C 工作区")).toBeTruthy();
    expect(screen.getByText("默认知识库")).toBeTruthy();
    expect(screen.getByRole("button", { name: "服务健康状态：已就绪，查看详情" })).toBeTruthy();
    expect(document.querySelector(".mode-banner")).toBeNull();
    expect(screen.getByText("未连接身份")).toBeTruthy();
    expect(screen.getByRole("button", { name: "智能问答" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "知识库" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "质量与运维" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "系统" })).toBeTruthy();

    const search = screen.getByRole("searchbox", { name: "全局知识搜索" });
    fireEvent.change(search, { target: { value: "  员工手册  " } });
    fireEvent.keyDown(search, { key: "Enter", code: "Enter" });
    expect(window.location.pathname).toBe("/documents");
    expect(window.location.search).toBe("?q=%E5%91%98%E5%B7%A5%E6%89%8B%E5%86%8C");
  });

  it("replaces the placeholder actor with the verified enterprise identity", async () => {
    localStorage.setItem("rag4c.knowledge_actor_token", "actor-token");
    localStorage.setItem("rag4c.knowledge_tenant_id", "tenant-1");
    localStorage.setItem("rag4c.knowledge_dataset_id", "dataset-1");
    enterpriseIdentityApi.fetchEnterpriseContext.mockResolvedValue({
      tenant: {
        id: "tenant-1",
        name: "星海科技",
        plan: "enterprise",
        status: "active",
        quota_documents: 100,
        quota_chunks: 1000,
        doc_count: 8,
        chunk_count: 80,
      },
      actor: { id: "account-1", name: "林澈", email: "lin@example.com", role: "admin" },
      member_count: 4,
      dataset_count: 2,
      effective_permissions: ["knowledge.read"],
      role_permissions: { admin: ["knowledge.read"] },
      capabilities: {},
    });

    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(await screen.findByText("林澈")).toBeTruthy();
    expect(screen.getByText("管理员")).toBeTruthy();
    expect(screen.getByText("星海科技")).toBeTruthy();
    expect(screen.queryByText("未连接身份")).toBeNull();
  });

  it("distinguishes an existing token whose enterprise context is still loading", () => {
    localStorage.setItem("rag4c.knowledge_actor_token", "actor-token");
    localStorage.setItem("rag4c.knowledge_tenant_id", "tenant-1");
    enterpriseIdentityApi.fetchEnterpriseContext.mockReturnValue(new Promise(() => undefined));

    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(screen.getByText("身份验证中")).toBeTruthy();
    expect(screen.queryByText("未连接身份")).toBeNull();
  });
  it.each([
    { label: "Ctrl+K", event: { key: "k", code: "KeyK", ctrlKey: true } },
    { label: "Cmd+K", event: { key: "k", code: "KeyK", metaKey: true } },
  ])("focuses global search with $label and exposes the shortcut", ({ event }) => {
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    const search = screen.getByRole("searchbox", { name: "全局知识搜索" });
    expect(search.getAttribute("aria-keyshortcuts")).toBe("Control+K Meta+K");

    fireEvent.keyDown(window, event);

    expect(document.activeElement).toBe(search);
  });

  it("does not advertise notification or audit destinations before those pages exist", () => {
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(screen.queryByRole("button", { name: "打开审计日志" })).toBeNull();
    expect(screen.queryByRole("button", { name: "打开通知中心" })).toBeNull();
  });

  it("focuses the skip link first and moves focus to main content on Enter", async () => {
    const user = userEvent.setup();
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    await user.tab();
    const skipLink = screen.getByRole("link", { name: "跳到主内容" });
    expect(document.activeElement).toBe(skipLink);

    await user.keyboard("{Enter}");
    expect(document.activeElement).toBe(screen.getByRole("main"));
  });

  it("renders exactly one document main and keeps the skip-link target", () => {
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    const mains = screen.getAllByRole("main");
    expect(mains).toHaveLength(1);
    expect(mains[0].getAttribute("id")).toBe("main-content");
    const content = document.querySelector(".app-content");
    expect(content?.tagName).toBe("DIV");
    expect(content?.classList.contains("ant-layout-content")).toBe(false);
    expect(content?.contains(mains[0])).toBe(true);
    expect(document.querySelector('.skip-link[href="#main-content"]')).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "主导航" }).getAttribute("tabindex")).toBe("0");
  });
});
