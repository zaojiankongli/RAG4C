// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

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
vi.mock("./pages/EnterpriseKnowledgeBasePage", () => ({
  default: () => <section aria-label="企业知识库注册表页面" />,
}));
vi.mock("./pages/QueryPage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeOverviewPage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeTaxonomyPage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeGovernancePage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeSourcesPage", () => ({ default: () => <section /> }));
vi.mock("./pages/RetrievalLabPage", () => ({ default: () => <section /> }));
vi.mock("./pages/VisualizePage", () => ({ default: () => <section /> }));
vi.mock("./pages/MonitorPage", () => ({ default: () => <section /> }));
vi.mock("./pages/EvalPage", () => ({ default: () => <section /> }));
vi.mock("./pages/ConsistencyPage", () => ({ default: () => <section /> }));
vi.mock("./pages/ConfigPage", () => ({ default: () => <section /> }));

import App from "./App";

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(null, "", "/enterprise/knowledge-bases");
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

afterEach(() => cleanup());

describe("App Stage18 registry route", () => {
  it("mounts the Knowledge Base registry on the direct route without replacing Enterprise Admin", async () => {
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(await screen.findByRole("region", { name: "企业知识库注册表页面" })).toBeTruthy();
    expect(screen.getByText("知识库注册表")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "系统" }));
    expect(screen.getByRole("button", { name: "组织与权限" })).toBeTruthy();
  });
});
