// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
vi.mock("./pages/EnterpriseKnowledgeBaseWorkspacePage", () => ({
  default: ({
    active,
    section,
    datasetId,
  }: {
    active: boolean;
    section: string;
    datasetId: string;
  }) =>
    active ? (
      <section aria-label="知识库资源工作区">
        <h1>资源工作区 Fixture</h1>
        <output>{`${datasetId}:${section}`}</output>
      </section>
    ) : null,
}));
vi.mock("./pages/QueryPage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeOverviewPage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeTaxonomyPage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeGovernancePage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeSourcesPage", () => ({ default: () => <section /> }));
vi.mock("./pages/DocumentsPage", () => ({ default: () => <section /> }));
vi.mock("./pages/RetrievalLabPage", () => ({ default: () => <section /> }));
vi.mock("./pages/VisualizePage", () => ({ default: () => <section /> }));
vi.mock("./pages/MonitorPage", () => ({ default: () => <section /> }));
vi.mock("./pages/EvalPage", () => ({ default: () => <section /> }));
vi.mock("./pages/ConsistencyPage", () => ({ default: () => <section /> }));
vi.mock("./pages/ConfigPage", () => ({ default: () => <section /> }));

import App from "./App";

beforeEach(() => {
  localStorage.clear();
  window.history.replaceState(
    null,
    "",
    "/enterprise/knowledge-base?dataset=dataset-prod&section=releases",
  );
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

describe("App Stage19 resource workspace route", () => {
  it("mounts the hidden workspace PageKey, preserves Dataset/section and selects the Registry domain", async () => {
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    expect(await screen.findByRole("region", { name: "知识库资源工作区" })).toBeTruthy();
    expect(screen.getByText("dataset-prod:releases")).toBeTruthy();
    expect(screen.getByText("知识库注册表")).toBeTruthy();
  });
});
