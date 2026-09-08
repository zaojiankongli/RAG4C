// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./context/ConnectionContext", () => ({
  useConnection: () => ({
    online: true,
    checking: false,
    health: { status: "ok" },
    refresh: vi.fn(async () => undefined),
  }),
}));
vi.mock("./components/ModeBanner", () => ({ default: () => null }));
vi.mock("./components/PageState", () => ({ default: () => <div>loading</div> }));
vi.mock("./components/ErrorBoundary", () => ({
  default: ({ children }: { children: React.ReactNode }) => children,
}));
vi.mock("./pages/QueryPage", () => ({
  default: () => <section aria-label="问答页面" />,
}));
vi.mock("./pages/ConsistencyPage", () => ({
  default: () => <section aria-label="一致性控制台页面" />,
}));
vi.mock("./pages/KnowledgeOverviewPage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeTaxonomyPage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeGovernancePage", () => ({
  default: () => <section aria-label="知识治理页面" />,
}));
vi.mock("./pages/KnowledgeSourcesPage", () => ({ default: () => <section /> }));
vi.mock("./pages/RetrievalLabPage", () => ({ default: () => <section /> }));
vi.mock("./pages/DocumentsPage", () => ({
  default: ({ onDirtyChange }: { onDirtyChange?: (dirty: boolean) => void }) => (
    <section aria-label="文档管理页面">
      <button onClick={() => onDirtyChange?.(true)}>模拟未保存</button>
    </section>
  ),
}));
vi.mock("./pages/VisualizePage", () => ({ default: () => <section /> }));
vi.mock("./pages/MonitorPage", () => ({ default: () => <section aria-label="运行监控页面" /> }));
vi.mock("./pages/EvalPage", () => ({ default: () => <section /> }));
vi.mock("./pages/ConfigPage", () => ({ default: () => <section /> }));
vi.mock("./pages/EnterpriseAdminPage", () => ({
  default: () => <section aria-label="企业管理页面" />,
}));

vi.mock("./pages/EnterpriseKnowledgeBaseWorkspacePage", () => ({
  default: () => <section aria-label="知识库资源工作区页面" />,
}));

const servingPageState = vi.hoisted(() => ({
  props: null as Record<string, unknown> | null,
}));

vi.mock("./enterprise-knowledge-base-shell/KnowledgeBaseResourceShell", () => ({
  default: ({ children, section }: { children: React.ReactNode; section: string }) => (
    <section aria-label={`知识库资源壳 ${section}`} data-section={section}>
      <span>{section === "serving" ? "Serving" : section}</span>
      {children}
    </section>
  ),
}));

vi.mock("./enterprise-knowledge-base-shell/KnowledgeServingPageLoader", () => ({
  default: (props: Record<string, unknown>) => {
    servingPageState.props = props;
    const handoff =
      typeof props.onServingHandoff === "function"
        ? props.onServingHandoff
        : typeof props.onHandoff === "function"
          ? props.onHandoff
          : undefined;
    const emit = (route: unknown) => (handoff as ((value: unknown) => void) | undefined)?.(route);
    return (
      <section aria-label="知识服务可靠性页面">
        <output data-testid="knowledge-serving-context">
          {["tenantId", "accountId", "datasetId", "capabilityReady", "readOnly", "mobile"]
            .map((key) => `${key}=${String(props[key])}`)
            .join("|")}
        </output>
        <button
          type="button"
          onClick={() =>
            emit({
              evidence_kind: "source",
              route_code: "knowledge_sources",
              dataset_id: "dataset-serving",
              resource_id: "source-a",
            })
          }
        >
          知识服务安全交接到来源
        </button>
        <button
          type="button"
          onClick={() =>
            emit({
              evidence_kind: "source",
              route_code: "external_redirect",
              dataset_id: "dataset-serving",
              resource_id: "source-a",
            })
          }
        >
          知识服务拒绝不安全交接
        </button>
        <button
          type="button"
          onClick={() =>
            emit({
              evidence_kind: "source",
              route_code: "knowledge_sources",
              dataset_id: "dataset-serving",
              resource_id: "https://attacker.example",
            })
          }
        >
          知识服务拒绝外部目标
        </button>
        <button
          type="button"
          onClick={() =>
            emit({
              evidence_kind: "release",
              route_code: "knowledge_base_releases",
              dataset_id: "dataset-serving",
              resource_id: "release/a?revision=2&channel=prod",
            })
          }
        >
          知识服务安全交接到发布
        </button>
        <button
          type="button"
          onClick={() =>
            emit({
              evidence_kind: "certification",
              route_code: "release_quality",
              dataset_id: "dataset-serving",
              resource_id: "cert/a?revision=2&policy=strict",
            })
          }
        >
          知识服务安全交接到质量认证
        </button>
      </section>
    );
  },
}));

vi.mock("./pages/EnterpriseKnowledgeBaseWorkspacePage", () => ({
  default: () => <section aria-label="知识库资源工作区页面" />,
}));

vi.mock("./enterprise-automation-workflows/AutomationPage", () => ({
  default: ({
    onAutomationHandoff,
  }: {
    onAutomationHandoff?: (route: Record<string, unknown>) => void;
  }) => (
    <section aria-label="企业自动化中心页面">
      <button
        onClick={() =>
          onAutomationHandoff?.({
            code: "enterprise_tasks",
            path: "/enterprise/tasks",
            query: { task: "task-a" },
            href: "/enterprise/tasks?task=task-a",
          })
        }
      >
        自动化来源安全交接
      </button>
    </section>
  ),
}));
vi.mock("./enterprise-task-operations/TaskOperationsPage", () => ({
  default: ({ onTaskHandoff }: { onTaskHandoff?: (route: Record<string, unknown>) => void }) => (
    <section aria-label="企业任务中心页面">
      <button
        onClick={() =>
          onTaskHandoff?.({
            code: "source_control",
            path: "/sources",
            query: { source: "source-a" },
            href: "/sources?source=source-a",
          })
        }
      >
        任务来源安全交接
      </button>
      <button
        onClick={() =>
          onTaskHandoff?.({
            code: "release_quality",
            path: "/enterprise",
            query: { run: "scan-a" },
            href: "/enterprise?run=scan-a",
          })
        }
      >
        不完整质量交接
      </button>
    </section>
  ),
}));

import App from "./App";

beforeEach(() => {
  servingPageState.props = null;
  localStorage.clear();
  window.history.replaceState(null, "", "/governance");
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

describe("App direct route mounting", () => {
  it("mounts only the initial governance route and exposes its knowledge-base menu entry", async () => {
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(
      await screen.findByRole("region", { name: "知识治理页面" }, { timeout: 5000 }),
    ).toBeTruthy();
    expect(screen.getByText("内容治理")).toBeTruthy();
    expect(screen.getByText("知识组织")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "问答页面" })).toBeNull();
  });

  it("keeps nested Documents routes selected and blocks top-level navigation while dirty", async () => {
    window.history.replaceState(null, "", "/documents/parse?doc=doc-1");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(await screen.findByRole("region", { name: "文档管理页面" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "模拟未保存" }));
    fireEvent.click(screen.getByText("运行监控"));
    expect(confirm).toHaveBeenCalled();
    expect(screen.getByRole("region", { name: "文档管理页面" })).toBeTruthy();

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByText("运行监控"));
    expect(await screen.findByRole("region", { name: "运行监控页面" })).toBeTruthy();
  });
  it("keeps the enterprise page mounted for an invitation accept deep link", async () => {
    window.history.replaceState(null, "", "/enterprise/invitations/accept?token=route-token");
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(await screen.findByRole("region", { name: "企业管理页面" })).toBeTruthy();
    expect(screen.getByText("组织与权限")).toBeTruthy();
  });

  it("mounts Task Center from the enterprise direct route and safely hands off to Sources", async () => {
    window.history.replaceState(null, "", "/enterprise/tasks");
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(await screen.findByRole("region", { name: "企业任务中心页面" })).toBeTruthy();
    expect(screen.getByText("任务中心")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "任务来源安全交接" }));
    expect(window.location.pathname).toBe("/sources");
    expect(window.location.search).toBe("?source=source-a");
    window.history.replaceState(null, "", "/enterprise/tasks");
    fireEvent.click(screen.getByRole("button", { name: "不完整质量交接" }));
    expect(window.location.pathname).toBe("/enterprise/tasks");
    expect(window.location.search).toBe("");
  });

  it("mounts Automation Center and performs an exact Task handoff", async () => {
    window.history.replaceState(null, "", "/enterprise/automations");
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);
    expect(await screen.findByRole("region", { name: "企业自动化中心页面" })).toBeTruthy();
    expect(screen.getByText("自动化中心")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "自动化来源安全交接" }));
    expect(window.location.pathname).toBe("/enterprise/tasks");
    expect(window.location.search).toBe("?task=task-a");
  });

  it("mounts the enterprise administration workspace from a direct route", async () => {
    window.history.replaceState(null, "", "/enterprise");
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(await screen.findByRole("region", { name: "企业管理页面" })).toBeTruthy();
    expect(screen.getByText("组织与权限")).toBeTruthy();
    expect(screen.getByText("内容治理")).toBeTruthy();
  });
});

describe("Stage26 Knowledge Serving App integration", () => {
  it("mounts the direct serving route and forwards the full enterprise scope contract", async () => {
    window.history.replaceState(
      null,
      "",
      "/enterprise/knowledge-base?dataset=dataset-serving&section=serving",
    );
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(await screen.findByRole("region", { name: "知识服务可靠性页面" })).toBeTruthy();
    expect(screen.getByTestId("knowledge-serving-context").textContent).toBe(
      "tenantId=default|accountId=undefined|datasetId=dataset-serving|capabilityReady=false|readOnly=true|mobile=false",
    );
    expect(servingPageState.props).toEqual(
      expect.objectContaining({
        tenantId: "default",
        accountId: undefined,
        actorToken: "",
        datasetId: "dataset-serving",
        capabilityReady: false,
        tenantLabel: "RAG4C 工作区",
        readOnly: true,
        mobile: false,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "知识服务拒绝不安全交接" }));
    fireEvent.click(screen.getByRole("button", { name: "知识服务拒绝外部目标" }));
    expect(window.location.pathname).toBe("/enterprise/knowledge-base");
    expect(window.location.search).toBe("?dataset=dataset-serving&section=serving");
  });

  it("preserves opaque Release identity in the official knowledge-base shell", async () => {
    window.history.replaceState(
      null,
      "",
      "/enterprise/knowledge-base?dataset=dataset-serving&section=serving",
    );
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(await screen.findByRole("region", { name: "知识服务可靠性页面" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "知识服务安全交接到发布" }));
    expect(window.location.pathname).toBe("/enterprise/knowledge-base");
    const query = new URLSearchParams(window.location.search);
    expect(query.get("dataset")).toBe("dataset-serving");
    expect(query.get("section")).toBe("releases");
    expect(query.get("release")).toBe("release/a?revision=2&channel=prod");
  });

  it("mounts the hash serving route and only permits an allowlisted internal handoff", async () => {
    window.history.replaceState(
      null,
      "",
      "/#/enterprise/knowledge-base?dataset=dataset-serving&section=serving",
    );
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(await screen.findByRole("region", { name: "知识服务可靠性页面" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "知识服务安全交接到来源" }));
    expect(window.location.pathname).toBe("/");
    expect(window.location.search).toBe("");
    expect(window.location.hash).toBe(
      "#/enterprise/knowledge-base?dataset=dataset-serving&section=sources&source=source-a",
    );
    fireEvent.click(screen.getByRole("button", { name: "知识服务安全交接到质量认证" }));
    expect(window.location.hash).toBe(
      "#/enterprise/knowledge-base?dataset=dataset-serving&section=releases&certification=cert%2Fa%3Frevision%3D2%26policy%3Dstrict",
    );
  });
});
