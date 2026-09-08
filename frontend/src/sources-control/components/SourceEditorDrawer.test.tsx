// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import SourceEditorDrawer from "./SourceEditorDrawer";

afterEach(cleanup);

it("submits a strict local connector without arbitrary metadata or inline credentials", async () => {
  const onSave = vi.fn().mockResolvedValue(true);
  render(<SourceEditorDrawer open source={null} saving={false} error={null} onClose={vi.fn()} onSave={onSave} />);
  await userEvent.type(screen.getByLabelText("来源名称"), "员工手册");
  await userEvent.type(screen.getByLabelText("本地目录"), "C:/knowledge/handbook");
  await userEvent.type(screen.getByLabelText("包含规则"), "**/*.md\npolicies/**");
  await userEvent.type(screen.getByLabelText("扩展名"), "md, txt");
  await userEvent.type(screen.getByLabelText("凭据引用"), "secret://sources/handbook");
  fireEvent.click(screen.getByRole("button", { name: "创建来源" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledWith({
    name: "员工手册", kind: "local_dir", enabled: true, metadata: {},
    config: { path: "C:/knowledge/handbook", include: ["**/*.md", "policies/**"], exclude: [], extensions: [".md", ".txt"], max_files: 0, credential_ref: "secret://sources/handbook" },
  }));
});

it("validates GitHub owner/repository and safe credential references", async () => {
  const onSave = vi.fn().mockResolvedValue(false);
  render(<SourceEditorDrawer open source={null} saving={false} error={null} onClose={vi.fn()} onSave={onSave} />);
  fireEvent.change(screen.getByLabelText("连接器类型"), { target: { value: "github_repo" } });
  fireEvent.change(screen.getByLabelText("来源名称"), { target: { value: "Repo" } });
  fireEvent.change(screen.getByLabelText("GitHub 仓库"), { target: { value: "https://evil.test/repo" } });
  fireEvent.change(screen.getByLabelText("凭据引用"), { target: { value: "plain-token" } });
  fireEvent.click(screen.getByRole("button", { name: "创建来源" }));
  expect(await screen.findByText("仓库必须使用 owner/repository 格式。" )).toBeTruthy();
  expect(screen.getByText("凭据必须是无查询参数的 secret:// 或 vault:// 引用。" )).toBeTruthy();
  expect(onSave).not.toHaveBeenCalled();
});
it("matches backend bounds, list limits and NUL rejection while focusing the first invalid field", async () => {
  const onSave = vi.fn();
  render(<SourceEditorDrawer open source={null} saving={false} error={null} onClose={vi.fn()} onSave={onSave} />);
  fireEvent.change(screen.getByLabelText("来源名称"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("本地目录"), { target: { value: "C:/ok\0bad" } });
  fireEvent.change(screen.getByLabelText("包含规则"), { target: { value: Array.from({ length: 257 }, (_, index) => `file-${index}.md`).join("\n") } });
  fireEvent.change(screen.getByLabelText("扩展名"), { target: { value: Array.from({ length: 65 }, (_, index) => `x${index}`).join(",") } });
  fireEvent.click(screen.getByRole("button", { name: "创建来源" }));
  expect(await screen.findByText("来源名称不能为空。")).toBeTruthy();
  expect(screen.getByText("本地目录不能包含 NUL 字符。")).toBeTruthy();
  expect(screen.getByText("包含规则最多 256 条。")).toBeTruthy();
  expect(screen.getByText("扩展名最多 64 个。")).toBeTruthy();
  expect(document.activeElement).toBe(screen.getByLabelText("来源名称"));
  expect(onSave).not.toHaveBeenCalled();
});

it("accepts credential hosts with ports and exposes bounded numeric/name input semantics", async () => {
  const onSave = vi.fn().mockResolvedValue(false);
  render(<SourceEditorDrawer open source={null} saving={false} error={null} onClose={vi.fn()} onSave={onSave} />);
  const name = screen.getByLabelText("来源名称");
  expect(name.getAttribute("name")).toBe("source-name");
  expect(name.getAttribute("autocomplete")).toBe("off");
  fireEvent.change(name, { target: { value: "Vault source" } });
  fireEvent.change(screen.getByLabelText("本地目录"), { target: { value: "C:/allowed" } });
  fireEvent.change(screen.getByLabelText("凭据引用"), { target: { value: "vault://localhost:8200/sources/docs" } });
  const maxFiles = screen.getByLabelText("最大文件数");
  expect(maxFiles.getAttribute("type")).toBe("number");
  expect(maxFiles.getAttribute("inputmode")).toBe("numeric");
  fireEvent.click(screen.getByRole("button", { name: "创建来源" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ config: expect.objectContaining({ credential_ref: "vault://localhost:8200/sources/docs" }) })));
});
