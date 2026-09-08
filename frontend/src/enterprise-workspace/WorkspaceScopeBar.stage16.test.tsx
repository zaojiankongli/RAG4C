// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WorkspaceScopeBar from "../ui/enterprise/WorkspaceScopeBar";

afterEach(cleanup);

describe("WorkspaceScopeBar Stage16 selector", () => {
  it("renders only server-returned Workspace options and reports selection", async () => {
    const onWorkspaceChange = vi.fn();
    const user = userEvent.setup();
    render(
      <WorkspaceScopeBar
        organizationLabel="RAG4C 企业"
        knowledgeBaseLabel="产品知识库"
        environmentLabel="生产环境"
        healthLabel="服务正常"
        actorLabel="张三"
        actorRole="所有者"
        workspaceValue="workspace-prod"
        workspaceOptions={[
          { value: "workspace-prod", label: "生产知识域", environment: "production" },
          { value: "workspace-test", label: "测试知识域", environment: "testing" },
        ]}
        onWorkspaceChange={onWorkspaceChange}
      />,
    );

    const selector = screen.getByRole("combobox", { name: "当前 Workspace" });
    await user.click(selector);
    await user.click(await screen.findByText("测试知识域 · testing"));
    expect(onWorkspaceChange).toHaveBeenCalledWith("workspace-test");
    expect(screen.queryByText("默认 Workspace")).toBeNull();
  });
});
