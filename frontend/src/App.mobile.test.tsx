// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
vi.mock("./pages/QueryPage", () => ({ default: () => <section aria-label="问答页面" /> }));
vi.mock("./pages/KnowledgeOverviewPage", () => ({
  default: () => <section aria-label="知识概览页面" />,
}));
vi.mock("./pages/KnowledgeTaxonomyPage", () => ({ default: () => <section /> }));
vi.mock("./pages/KnowledgeSourcesPage", () => ({ default: () => <section /> }));
vi.mock("./pages/RetrievalLabPage", () => ({ default: () => <section /> }));
vi.mock("./pages/DocumentsPage", () => ({ default: () => <section /> }));
vi.mock("./pages/VisualizePage", () => ({ default: () => <section /> }));
vi.mock("./pages/MonitorPage", () => ({ default: () => <section /> }));
vi.mock("./pages/EvalPage", () => ({ default: () => <section /> }));
vi.mock("./pages/ConfigPage", () => ({ default: () => <section /> }));

import App from "./App";

function installMatchMedia(matches: boolean) {
  const listeners = new Set<(event: MediaQueryListEvent) => void>();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches,
      media: "(max-width: 768px)",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn((_type: string, listener: (event: MediaQueryListEvent) => void) => {
        listeners.add(listener);
      }),
      removeEventListener: vi.fn(
        (_type: string, listener: (event: MediaQueryListEvent) => void) => {
          listeners.delete(listener);
        },
      ),
      dispatchEvent: vi.fn(),
    })),
  });
}

function aside(): HTMLElement {
  const element = document.querySelector("aside.app-sider");
  if (!(element instanceof HTMLElement)) throw new Error("missing app sider");
  return element;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/query");
});

afterEach(cleanup);

describe("App responsive navigation", () => {
  it("keeps the skip link first in the 375px keyboard order", async () => {
    installMatchMedia(true);
    const user = userEvent.setup();
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "跳到主内容" }));
  });

  it("opens, closes, and auto-closes the 375px navigation after routing", async () => {
    installMatchMedia(true);
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    const toggle = await screen.findByRole("button", { name: "打开导航" });
    const navigation = screen.getByRole("navigation", { name: "主导航" });
    expect(navigation.id).toBe("primary-navigation");
    expect(toggle.getAttribute("aria-controls")).toBe("primary-navigation");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByText("在线")).toBeTruthy();
    expect(aside().style.width).toBe("0px");

    fireEvent.click(toggle);
    await waitFor(() => expect(aside().style.width).toBe("232px"));
    const closeToggle = screen.getByRole("button", { name: "关闭导航" });
    expect(closeToggle.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(within(aside()).getByRole("button", { name: "收起侧栏" }));
    await waitFor(() => expect(aside().style.width).toBe("0px"));

    fireEvent.click(screen.getByRole("button", { name: "打开导航" }));
    await waitFor(() => expect(aside().style.width).toBe("232px"));
    fireEvent.click(screen.getByText("知识概览"));

    await waitFor(() => expect(window.location.pathname).toBe("/overview"));
    await waitFor(() => expect(aside().style.width).toBe("0px"));
    expect(screen.getByRole("region", { name: "知识概览页面" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "问答页面" })).toBeTruthy();
  });

  it("keeps the desktop sider open while routing", async () => {
    installMatchMedia(false);
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    await waitFor(() => expect(aside().style.width).toBe("232px"));
    expect(screen.queryByRole("button", { name: "打开导航" })).toBeNull();
    fireEvent.click(screen.getByText("知识概览"));

    await waitFor(() => expect(window.location.pathname).toBe("/overview"));
    expect(aside().style.width).toBe("232px");
  });
});
