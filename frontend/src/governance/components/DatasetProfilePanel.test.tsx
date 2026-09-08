// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);
import type { DatasetProfile } from "../model/governanceModel";
import DatasetProfilePanel from "./DatasetProfilePanel";

const profile: DatasetProfile = {
  id: "dataset-a", tenant_id: "tenant-a", name: "Support", description: "Trusted support",
  status: "active", profile_revision: 7, owner_id: "owner-a", visibility: "private",
  profile: { domain: "support", api_key: "profile-secret" },
  policies: {
    parser: { engine: "mineru", credential_ref: "secret-manager://parser-prod", api_key: "must-not-render" },
    chunk: { max_tokens: 512 }, retrieval: { top_k: 12 }, retention: { days: 365 }, metadata: { language: "zh-CN" },
  },
  default_language: "zh-CN", graph_enabled: true, qa_enabled: true,
  usage: { documents: 12, chunks: 320 },
  timestamps: { created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-25T08:00:00Z", archived_at: null, archived_by: null },
};

function props() {
  return {
    profile,
    error: null,
    mutating: false,
    onRefresh: vi.fn(async () => true),
    onUpdate: vi.fn(async () => true),
    onArchive: vi.fn(async () => true),
    onRestore: vi.fn(async () => true),
    onDisable: vi.fn(async () => true),
  };
}

describe("DatasetProfilePanel", () => {
  it("renders revision and safe policy facts while exposing credential references only as badges", () => {
    render(<DatasetProfilePanel {...props()} />);

    expect(screen.getByText("Revision 7")).toBeTruthy();
    expect(screen.getByText("运行中")).toBeTruthy();
    expect(
      screen.getByRole("group", { name: "解析策略凭据引用" }).contains(
        screen.getByText("secret-manager://parser-prod"),
      ),
    ).toBe(true);
    expect(screen.getByText("mineru")).toBeTruthy();
    expect(document.body.textContent).not.toContain("must-not-render");
    expect(document.body.textContent).not.toContain("profile-secret");
  });

  it("submits safe editable fields with the current expected revision and closes only after success", async () => {
    const callbacks = props();
    const user = userEvent.setup();
    render(<DatasetProfilePanel {...callbacks} />);

    await user.click(screen.getByRole("button", { name: "编辑数据集资料" }));
    expect(screen.getByRole("group", { name: "数据集能力开关" })).toBeTruthy();
    const owner = screen.getByRole("textbox", { name: "负责人 ID" });
    fireEvent.change(owner, { target: { value: "owner-b" } });
    await user.click(screen.getByRole("button", { name: "保存资料" }));

    await waitFor(() => expect(callbacks.onUpdate).toHaveBeenCalledWith({
      expected_revision: 7,
      owner_id: "owner-b",
      visibility: "private",
      default_language: "zh-CN",
      graph_enabled: true,
      qa_enabled: true,
    }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "编辑数据集资料" })).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "编辑数据集资料" })));
  });

  it("requires confirmation before archiving and sends the visible profile revision", async () => {
    const callbacks = props();
    const user = userEvent.setup();
    render(<DatasetProfilePanel {...callbacks} />);

    await user.click(screen.getByRole("button", { name: "归档数据集" }));
    expect(callbacks.onArchive).not.toHaveBeenCalled();
    await user.click(await screen.findByRole("button", { name: "确认归档" }));
    expect(callbacks.onArchive).toHaveBeenCalledWith({ expected_revision: 7 });
  });

  it("shows stale revision copy and refresh action without claiming success", async () => {
    const callbacks = props();
    render(
      <DatasetProfilePanel
        {...callbacks}
        error={{ kind: "conflict", title: "资料已被更新", description: "当前修订已过期，请刷新后重新提交。", canRetry: true }}
      />,
    );
    expect(screen.getByText("资料已被更新")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "刷新数据集资料" }));
    expect(callbacks.onRefresh).toHaveBeenCalledTimes(1);
  });

  it("makes archived datasets read-only except restore", () => {
    render(<DatasetProfilePanel {...props()} profile={{ ...profile, status: "archived" }} />);
    expect(screen.queryByRole("button", { name: "编辑数据集资料" })).toBeNull();
    expect(screen.getByRole("button", { name: "恢复数据集" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "停用数据集" })).toBeNull();
  });


  it("associates an empty default-language error with the field", async () => {
    const user=userEvent.setup(); render(<DatasetProfilePanel {...props()} />); await user.click(screen.getByRole("button",{name:"编辑数据集资料"}));
    fireEvent.change(screen.getByRole("textbox",{name:/默认语言/}),{target:{value:""}}); await user.click(screen.getByRole("button",{name:"保存资料"}));
    const field=screen.getByRole("textbox",{name:/默认语言/}); expect(field.getAttribute("aria-invalid")).toBe("true"); expect(field.getAttribute("aria-describedby")).toBe("dataset-language-error");
  });

});
