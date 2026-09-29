// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MENU_ITEMS } from "../run/appNav";
import type { PageKey } from "../run/appRoute";
import { WorkspaceNavigation } from "./WorkspaceNavigation";

// Use the real manifest so every new entry automatically gains reachability coverage.
const groups = (MENU_ITEMS ?? []) as {
  label: string;
  children: { key: PageKey; label: string }[];
}[];
const entries = groups.flatMap((group) => group.children);

afterEach(cleanup);

describe("WorkspaceNavigation", () => {
  it("shows only the route workspace initially and exposes every manifest entry through section selection", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<WorkspaceNavigation page="query" collapsed={false} onNavigate={onNavigate} />);
    expect(screen.getByRole("heading", { name: "功能分区" })).toBeTruthy();
    expect(screen.getAllByRole("list")).toHaveLength(1);
    expect(screen.getByRole("list", { name: "智能问答" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "文档管理" })).toBeNull();
    expect(screen.getByRole("button", { current: "page" }).getAttribute("aria-label")).toBe(
      "知识问答",
    );
    for (const group of groups) {
      const section = screen.getByRole("button", { name: group.label });
      expect(section.getAttribute("title")).toBe(group.label);
      expect(document.getElementById(section.getAttribute("aria-describedby")!)?.textContent).toBe(
        "共 " + group.children.length + " 个功能",
      );
      await user.click(section);
      expect(screen.getByRole("button", { pressed: true })).toBe(section);
      expect(screen.getAllByRole("list")).toHaveLength(1);
      const list = screen.getByRole("list", { name: group.label });
      expect(within(list).getAllByRole("button")).toHaveLength(group.children.length);
      for (const entry of group.children) {
        const buttons = screen.getAllByRole("button", { name: entry.label });
        expect(buttons).toHaveLength(1);
        await user.click(buttons[0]);
      }
    }
    expect(onNavigate.mock.calls).toEqual(entries.map((entry) => [entry.key]));
  });

  it.each(groups)("initializes the selected workspace from the $label route", (group) => {
    render(
      <WorkspaceNavigation page={group.children[0].key} collapsed={false} onNavigate={vi.fn()} />,
    );
    expect(screen.getByRole("button", { pressed: true }).getAttribute("aria-label")).toBe(
      group.label,
    );
    expect(screen.getAllByRole("list")).toHaveLength(1);
    expect(screen.getByRole("list", { name: group.label })).toBeTruthy();
  });

  it("lets users browse without navigating or being reset by same-page rerenders, then syncs external routes", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    const { rerender } = render(
      <WorkspaceNavigation page="query" collapsed={false} onNavigate={onNavigate} />,
    );
    await user.click(screen.getByRole("button", { name: "系统" }));
    expect(screen.getByRole("button", { name: "系统", pressed: true })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "系统设置" }).getAttribute("aria-current"),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "知识问答" })).toBeNull();
    rerender(<WorkspaceNavigation page="query" collapsed={false} onNavigate={onNavigate} />);
    expect(screen.getByRole("button", { name: "系统", pressed: true })).toBeTruthy();
    // Both routes belong to 智能问答: sync must track page, not only the route's group.
    rerender(<WorkspaceNavigation page="visualize" collapsed={false} onNavigate={onNavigate} />);
    expect(screen.getByRole("button", { name: "智能问答", pressed: true })).toBeTruthy();
    expect(screen.getByRole("button", { name: "回答过程", current: "page" })).toBeTruthy();
    rerender(<WorkspaceNavigation page="config" collapsed={false} onNavigate={onNavigate} />);
    expect(screen.getByRole("button", { name: "系统", pressed: true })).toBeTruthy();
    expect(screen.getByRole("button", { name: "系统设置", current: "page" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "知识库" }));
    expect(screen.getByRole("list", { name: "知识库" })).toBeTruthy();
    expect(screen.queryByRole("button", { current: "page" })).toBeNull();
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("uses the parent-navigation mapping initially and syncs even when the mapped entry stays the same", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    const { rerender } = render(
      <WorkspaceNavigation
        page="knowledge-base-workspace"
        collapsed={false}
        onNavigate={onNavigate}
      />,
    );
    expect(screen.getByRole("button", { name: "知识库", pressed: true })).toBeTruthy();
    expect(screen.getByRole("button", { name: "知识库注册表", current: "page" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "系统" }));
    rerender(
      <WorkspaceNavigation page="knowledge-bases" collapsed={false} onNavigate={onNavigate} />,
    );
    expect(screen.getByRole("button", { name: "知识库", pressed: true })).toBeTruthy();
    expect(screen.getAllByRole("button", { current: "page" })).toHaveLength(1);
    expect(screen.getByRole("button", { name: "知识库注册表", current: "page" })).toBeTruthy();
  });

  it("searches across all workspaces and returns to the selected workspace after clearing", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<WorkspaceNavigation page="query" collapsed={false} onNavigate={onNavigate} />);
    await user.click(screen.getByRole("button", { name: "知识库" }));
    const input = screen.getByRole("searchbox", { name: "功能搜索" });
    await user.type(input, "中心");
    expect(screen.getAllByRole("list")).toHaveLength(2);
    expect(screen.getByRole("list", { name: "质量与运维" })).toBeTruthy();
    expect(screen.getByRole("list", { name: "系统" })).toBeTruthy();
    const matches = entries.filter((entry) => entry.label.includes("中心"));
    expect(screen.getAllByRole("listitem")).toHaveLength(matches.length);
    for (const entry of matches)
      expect(screen.getAllByRole("button", { name: entry.label })).toHaveLength(1);
    expect(screen.getByRole("status").textContent).toBe("找到 " + matches.length + " 个功能");
    await user.click(screen.getByRole("button", { name: "通知中心" }));
    expect(onNavigate).toHaveBeenCalledExactlyOnceWith("notifications");
    await user.click(screen.getByRole("button", { name: "系统" }));
    expect(screen.getAllByRole("list")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "任务中心" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "清除" }));
    expect(document.activeElement).toBe(input);
    expect(screen.getByRole("button", { name: "系统", pressed: true })).toBeTruthy();
    expect(screen.getAllByRole("list")).toHaveLength(1);
    expect(screen.getByRole("list", { name: "系统" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "任务中心" })).toBeNull();
  });

  it("syncs the selected workspace during search without changing global results", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    const { rerender } = render(
      <WorkspaceNavigation page="query" collapsed={false} onNavigate={onNavigate} />,
    );
    await user.type(screen.getByRole("searchbox"), "中心");
    rerender(<WorkspaceNavigation page="monitor" collapsed={false} onNavigate={onNavigate} />);
    expect(screen.getByRole("button", { name: "质量与运维", pressed: true })).toBeTruthy();
    expect(screen.getAllByRole("list")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "通知中心" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "清除" }));
    expect(screen.getAllByRole("list")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "运行监控", current: "page" })).toBeTruthy();
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("supports group search, case-insensitive keys and whitespace without duplicating results", async () => {
    const user = userEvent.setup();
    render(<WorkspaceNavigation page="query" collapsed={false} onNavigate={vi.fn()} />);
    const input = screen.getByRole("searchbox", { name: "功能搜索" });
    await user.type(input, "  系统  ");
    const system = groups.find((group) => group.label === "系统")!;
    expect(screen.getAllByRole("listitem")).toHaveLength(system.children.length);
    for (const entry of system.children)
      expect(screen.getAllByRole("button", { name: entry.label })).toHaveLength(1);
    await user.clear(input);
    await user.type(input, "  知识库   KNOWLEDGE-BASES  ");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "知识库注册表" })).toBeTruthy();
    await user.clear(input);
    await user.type(input, "   ");
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("button", { name: "知识问答" })).toBeTruthy();
  });

  it("offers a keyboard-operable empty-state reset and returns focus to search", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<WorkspaceNavigation page="query" collapsed={false} onNavigate={onNavigate} />);
    const input = screen.getByRole("searchbox", { name: "功能搜索" });
    expect(document.getElementById(input.getAttribute("aria-describedby")!)?.textContent).toContain(
      "不搜索知识内容",
    );
    await user.type(input, "没有这个功能");
    expect(screen.getByRole("status").textContent).toContain("未找到匹配功能");
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "清除" }));
    await user.keyboard("{Enter}");
    expect(document.activeElement).toBe(input);
    expect((input as HTMLInputElement).value).toBe("");
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("button", { name: "知识问答" })).toBeTruthy();
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("keeps all icon-only destinations accessible even with a previous empty search", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    const { rerender } = render(
      <WorkspaceNavigation page="monitor" collapsed={false} onNavigate={onNavigate} />,
    );
    await user.type(screen.getByRole("searchbox"), "无匹配项");
    rerender(<WorkspaceNavigation page="monitor" collapsed onNavigate={onNavigate} />);
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getAllByRole("button")).toHaveLength(entries.length);
    for (const entry of entries) {
      const button = screen.getByRole("button", { name: entry.label });
      expect(button.getAttribute("title")).toBe(entry.label);
      expect(button.getAttribute("aria-label")).toBe(entry.label);
      expect(button.querySelector(".workspace-navigation__label")).toBeNull();
      expect(button.querySelector('[aria-hidden="true"]')).not.toBeNull();
      await user.click(button);
    }
    expect(onNavigate.mock.calls).toEqual(entries.map((entry) => [entry.key]));
    expect(screen.getByRole("button", { current: "page" }).getAttribute("aria-label")).toBe(
      "运行监控",
    );
    rerender(<WorkspaceNavigation page="monitor" collapsed={false} onNavigate={onNavigate} />);
    expect(screen.getByRole("status").textContent).toContain("未找到匹配功能");
  });

  it("preserves section browsing across collapse and syncs route changes while collapsed", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    const { rerender } = render(
      <WorkspaceNavigation page="query" collapsed={false} onNavigate={onNavigate} />,
    );
    await user.click(screen.getByRole("button", { name: "知识库" }));
    rerender(<WorkspaceNavigation page="query" collapsed onNavigate={onNavigate} />);
    expect(screen.getAllByRole("button")).toHaveLength(entries.length);
    rerender(<WorkspaceNavigation page="query" collapsed={false} onNavigate={onNavigate} />);
    expect(screen.getByRole("button", { name: "知识库", pressed: true })).toBeTruthy();
    rerender(<WorkspaceNavigation page="query" collapsed onNavigate={onNavigate} />);
    rerender(<WorkspaceNavigation page="config" collapsed onNavigate={onNavigate} />);
    expect(screen.getByRole("button", { name: "系统设置", current: "page" })).toBeTruthy();
    rerender(<WorkspaceNavigation page="config" collapsed={false} onNavigate={onNavigate} />);
    expect(screen.getByRole("button", { name: "系统", pressed: true })).toBeTruthy();
    expect(screen.getAllByRole("list")).toHaveLength(1);
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it("supports native Enter, Space and Tab with only the selected workspace's features in the tab order", async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    render(<WorkspaceNavigation page="query" collapsed={false} onNavigate={onNavigate} />);
    const quality = screen.getByRole("button", { name: "质量与运维" });
    quality.focus();
    await user.keyboard("{Enter}");
    expect(quality.getAttribute("aria-pressed")).toBe("true");
    expect(document.activeElement).toBe(quality);
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "系统" }));
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("searchbox"));
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "质量评测" }));
    await user.keyboard(" ");
    expect(onNavigate).toHaveBeenCalledExactlyOnceWith("eval");
    screen.getByRole("button", { name: "知识库" }).focus();
    await user.keyboard(" ");
    expect(screen.getByRole("button", { name: "知识库", pressed: true })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "质量评测" })).toBeNull();
    expect(screen.queryByRole("button", { name: "知识问答" })).toBeNull();
  });

  it("does not capture a global knowledge-search shortcut", async () => {
    const user = userEvent.setup();
    render(<WorkspaceNavigation page="query" collapsed={false} onNavigate={vi.fn()} />);
    const cancelled: boolean[] = [];
    const observe = (event: KeyboardEvent) => cancelled.push(event.defaultPrevented);
    window.addEventListener("keydown", observe);
    try {
      await user.keyboard("{Control>}k{/Control}{Meta>}k{/Meta}");
      expect(cancelled.every((value) => !value)).toBe(true);
      expect(document.activeElement).toBe(document.body);
      expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("");
    } finally {
      window.removeEventListener("keydown", observe);
    }
  });

  it("uses distinct input and panel IDs with local ARIA references when desktop and mobile instances coexist", () => {
    render(
      <>
        <WorkspaceNavigation page="query" collapsed={false} onNavigate={vi.fn()} />
        <WorkspaceNavigation page="config" collapsed={false} onNavigate={vi.fn()} />
      </>,
    );
    const ids: string[] = [];
    for (const nav of screen.getAllByRole("navigation", { name: "工作区功能导航" })) {
      ids.push(...Array.from(nav.querySelectorAll("[id]"), (element) => element.id));
      expect(within(nav).getByRole("searchbox", { name: "功能搜索" })).toBeTruthy();
      for (const button of within(nav).getAllByRole("button")) {
        const id = button.getAttribute("aria-controls");
        if (!id) continue;
        expect(nav.contains(document.getElementById(id))).toBe(true);
      }
    }
    expect(new Set(ids).size).toBe(ids.length);
  });
});
