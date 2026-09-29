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
  let currentMatches = matches;
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      get matches() {
        return currentMatches;
      },
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
  return {
    setMatches(next: boolean) {
      currentMatches = next;
      const event = { matches: next, media: "(max-width: 768px)" } as MediaQueryListEvent;
      listeners.forEach((listener) => listener(event));
    },
  };
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
    const navigation = document.getElementById("primary-navigation");
    if (!navigation) throw new Error("missing primary navigation");
    expect(screen.queryByRole("navigation", { name: "主导航" })).toBeNull();
    expect(navigation.id).toBe("primary-navigation");
    expect(toggle.getAttribute("aria-controls")).toBe("primary-navigation");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByRole("button", { name: "服务健康状态：已就绪，查看详情" })).toBeTruthy();
    expect(aside().style.width).toBe("0px");
    expect(aside().hasAttribute("inert")).toBe(true);

    fireEvent.click(toggle);
    await waitFor(() => expect(aside().style.width).toBe("256px"));
    expect(screen.getByRole("navigation", { name: "主导航" })).toBe(navigation);
    await waitFor(() => expect(document.activeElement).toBe(navigation));
    expect(document.querySelector(".app-content")?.hasAttribute("inert")).toBe(true);
    const closeToggle = screen.getByRole("button", { name: "关闭导航" });
    expect(closeToggle.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(within(aside()).getByRole("button", { name: "收起侧栏" }));
    await waitFor(() => expect(aside().style.width).toBe("0px"));
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "打开导航" }));
    });
    expect(aside().hasAttribute("inert")).toBe(true);
    expect(document.querySelector(".app-content")?.hasAttribute("inert")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "打开导航" }));
    await waitFor(() => expect(aside().style.width).toBe("256px"));
    fireEvent.click(screen.getByRole("button", { name: "知识库" }));
    fireEvent.click(screen.getByRole("button", { name: "知识概览" }));

    await waitFor(() => expect(window.location.pathname).toBe("/overview"));
    await waitFor(() => expect(aside().style.width).toBe("0px"));
    await waitFor(() => {
      expect(document.activeElement).toBe(document.getElementById("main-content"));
    });
    expect(screen.getByRole("region", { name: "知识概览页面" })).toBeTruthy();
    expect(screen.getByRole("region", { name: "问答页面" })).toBeTruthy();
  });

  it("keeps the desktop sider open while routing", async () => {
    installMatchMedia(false);
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    await waitFor(() => expect(aside().style.width).toBe("256px"));
    expect(screen.queryByRole("button", { name: "打开导航" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "知识库" }));
    fireEvent.click(screen.getByRole("button", { name: "知识概览" }));

    await waitFor(() => expect(window.location.pathname).toBe("/overview"));
    expect(aside().style.width).toBe("256px");
  });

  it("dismisses mobile navigation from the backdrop and restores focus", async () => {
    installMatchMedia(true);
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "打开导航" }));
    fireEvent.click(screen.getByRole("button", { name: "关闭导航菜单" }));
    await waitFor(() => expect(aside().style.width).toBe("0px"));
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "打开导航" }));
    });
    expect(screen.queryByRole("button", { name: "关闭导航菜单" })).toBeNull();
  });

  it("keeps keyboard focus inside the open mobile rail", async () => {
    installMatchMedia(true);
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "打开导航" }));
    const navigation = await screen.findByRole("navigation", { name: "主导航" });
    await waitFor(() => expect(document.activeElement).toBe(navigation));
    const lastAction = within(aside()).getByRole("button", { name: "重看新手引导" });
    const user = userEvent.setup();

    lastAction.focus();
    await user.tab();
    expect(document.activeElement).toBe(navigation);

    await user.tab({ shift: true });
    expect(document.activeElement).toBe(lastAction);
  });

  it("closes the overlay when the browser route changes externally", async () => {
    installMatchMedia(true);
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "打开导航" }));
    window.history.pushState(null, "", "/overview");
    window.dispatchEvent(new PopStateEvent("popstate"));

    await waitFor(() => expect(aside().style.width).toBe("0px"));
    await waitFor(() =>
      expect(document.activeElement).toBe(document.getElementById("main-content")),
    );
    expect(screen.getByRole("region", { name: "知识概览页面" })).toBeTruthy();
  });

  it("hands mobile focus to the onboarding dialog instead of the nav trap", async () => {
    installMatchMedia(true);
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "打开导航" }));
    fireEvent.click(within(aside()).getByRole("button", { name: "重看新手引导" }));

    await screen.findByRole("dialog", { name: "新手引导" });
    const closeGuide = screen.getByRole("button", { name: "关闭引导" });
    const user = userEvent.setup();
    await waitFor(() => expect(aside().style.width).toBe("0px"));
    await waitFor(() => expect(document.activeElement).toBe(closeGuide));
    expect(screen.queryByRole("navigation", { name: "主导航" })).toBeNull();

    await user.tab();
    await user.tab();
    await user.tab();
    expect(document.activeElement).toBe(closeGuide);
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "下一步" }));

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "新手引导" })).toBeNull());
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "打开导航" })),
    );
  });

  it("preserves focus when switching from mobile overlay to desktop rail", async () => {
    const media = installMatchMedia(true);
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    const toggle = await screen.findByRole("button", { name: "打开导航" });
    toggle.focus();
    media.setMatches(false);

    await waitFor(() => expect(screen.queryByRole("button", { name: "打开导航" })).toBeNull());
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("navigation", { name: "主导航" })),
    );
  });

  it("keeps new-page focus after an onboarding navigation handoff", async () => {
    installMatchMedia(true);
    window.history.replaceState(null, "", "/overview");
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "打开导航" }));
    fireEvent.click(within(aside()).getByRole("button", { name: "重看新手引导" }));
    fireEvent.click(await screen.findByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "下一步" }));
    fireEvent.click(screen.getByRole("button", { name: "去文档管理" }));

    await waitFor(() => expect(window.location.pathname).toBe("/documents"));
    await waitFor(() => {
      expect(document.activeElement).not.toBe(screen.getByRole("button", { name: "打开导航" }));
      expect(document.activeElement?.tagName).toBe("H1");
    });
  });

  it("keeps onboarding focus when the browser route changes externally", async () => {
    installMatchMedia(true);
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "打开导航" }));
    fireEvent.click(within(aside()).getByRole("button", { name: "重看新手引导" }));
    await screen.findByRole("dialog", { name: "新手引导" });
    const closeGuide = screen.getByRole("button", { name: "关闭引导" });
    await waitFor(() => expect(document.activeElement).toBe(closeGuide));

    window.history.pushState(null, "", "/monitor");
    window.dispatchEvent(new PopStateEvent("popstate"));

    await waitFor(() => expect(screen.getByRole("dialog", { name: "新手引导" })).toBeTruthy());
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(document.activeElement).toBe(closeGuide);
  });

  it("closes mobile navigation on Escape and restores focus", async () => {
    installMatchMedia(true);
    render(<App themeMode="light" onToggleTheme={vi.fn()} />);

    fireEvent.click(await screen.findByRole("button", { name: "打开导航" }));
    await waitFor(() => expect(aside().style.width).toBe("256px"));
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(aside().style.width).toBe("0px"));
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByRole("button", { name: "打开导航" }));
    });
    expect(screen.queryByRole("button", { name: "关闭导航菜单" })).toBeNull();
  });
});
