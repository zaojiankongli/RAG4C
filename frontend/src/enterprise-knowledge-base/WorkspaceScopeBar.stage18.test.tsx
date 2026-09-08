// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import WorkspaceScopeBar from "../ui/enterprise/WorkspaceScopeBar";

afterEach(cleanup);

beforeEach(() => {
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

describe("WorkspaceScopeBar Stage18 verified states", () => {
  it("does not fabricate a Workspace option when the server selector is unavailable", () => {
    render(
      <WorkspaceScopeBar
        organizationLabel="星海科技"
        knowledgeBaseLabel="未选择主知识库"
        environmentLabel="生产环境"
        healthLabel="服务正常"
        actorLabel="林澈"
        actorRole="所有者"
        workspaceStatus="error"
        workspaceOptions={[]}
      />,
    );

    expect(
      (screen.getByRole("combobox", { name: "当前 Workspace" }) as HTMLInputElement).disabled,
    ).toBe(true);
    expect(screen.getByPlaceholderText("Workspace 不可用")).toBeTruthy();
    expect(screen.queryByText("默认 Workspace")).toBeNull();
    expect(screen.getByText("未选择主知识库")).toBeTruthy();
  });
});
