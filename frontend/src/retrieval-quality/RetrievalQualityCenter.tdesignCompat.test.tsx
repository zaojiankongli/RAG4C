// @vitest-environment jsdom

import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

let online: boolean | null = true;
let actorToken = "";
const workspace = { scope: { tenantId: "tenant-a", datasetId: "dataset-a" } };

vi.mock("../context/ConnectionContext", () => ({ useConnection: () => ({ online }) }));
vi.mock("../knowledge/KnowledgeWorkspaceContext", () => ({
  useKnowledgeWorkspace: () => workspace,
}));
vi.mock("../knowledge/workspaceScope", () => ({ readKnowledgeActorToken: () => actorToken }));
vi.mock("./api/retrievalQualityApi", () => ({
  fetchExperiments: vi.fn().mockResolvedValue({ items: [], next_before_sequence: null }),
  runRetrievalComparison: vi.fn(),
}));
vi.mock("../ui/rendererPolicy", () => ({
  uiRendererAdapter: {
    useTDesign: (component: string) => ["button", "tag"].includes(component),
  },
}));

import RetrievalQualityCenter from "./RetrievalQualityCenter";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

beforeEach(() => {
  online = true;
  actorToken = `${btoa(JSON.stringify({ sub: "judge-a", tenant: "tenant-a", iat: 1, exp: 9999999999, jti: "x" })).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_")}.sig`;
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query.includes("max-width: 600px"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  );
});

describe("RetrievalQualityCenter TDesign facade compatibility", () => {
  it("keeps TDesign tag and selected/unselected mobile tab semantics", () => {
    render(<RetrievalQualityCenter />);

    expect(document.querySelector(".t-tag--light-outline")).not.toBeNull();
    const tablist = screen.getByRole("tablist", { name: "检索质量中心视图" });
    expect(within(tablist).getAllByRole("tab")).toHaveLength(3);
    expect(within(tablist).getByRole("tab", { name: "配置" }).className).toContain(
      "t-button--theme-primary",
    );
    expect(screen.getByRole("tab", { name: "配置" }).getAttribute("aria-selected")).toBe("true");
  });
});
