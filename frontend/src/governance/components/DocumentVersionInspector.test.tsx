// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DocumentVersion } from "../model/governanceModel";
import DocumentVersionInspector from "./DocumentVersionInspector";

afterEach(cleanup);

const versions: DocumentVersion[] = [
  {
    id: "version-3", tenant_id: "tenant-a", dataset_id: "dataset-a", document_id: "doc-a", revision: 3,
    source_identity: "upload:guide-v3.pdf", source_hash: "c".repeat(64), parser_policy_snapshot: { engine: "mineru", api_key: "hidden" },
    parser_metadata: { pages: 8 }, source_content_ref: "https://user:pass@example.test/doc/v3?token=secret#frag", created_by: "editor-a", change_reason: "parser update", created_at: "2026-08-25T08:00:00Z",
  },
  {
    id: "version-1", tenant_id: "tenant-a", dataset_id: "dataset-a", document_id: "doc-a", revision: 1,
    source_identity: "upload:guide.pdf", source_hash: "a".repeat(64), parser_policy_snapshot: {}, parser_metadata: {},
    source_content_ref: "", created_by: "editor-a", change_reason: "initial", created_at: "2026-08-20T08:00:00Z",
  },
];

function props() {
  return {
    status: "ready" as const,
    documentId: "",
    versions: [] as DocumentVersion[],
    error: null,
    creating: false,
    onInspect: vi.fn(async () => true),
    onRefresh: vi.fn(async () => true),
    onCreate: vi.fn(async () => true),
  };
}

describe("DocumentVersionInspector", () => {
  it("requires an explicit document ID before inspection", async () => {
    const callbacks = props();
    const user = userEvent.setup();
    render(<DocumentVersionInspector {...callbacks} />);
    expect(screen.getByText("输入文档 ID 后读取不可变版本时间线")).toBeTruthy();
    expect(callbacks.onInspect).not.toHaveBeenCalled();
    expect(screen.getByLabelText("检查文档版本").tagName).toBe("BUTTON");

    fireEvent.change(screen.getByRole("textbox", { name: "文档 ID" }), { target: { value: " doc-a " } });
    await user.click(screen.getByRole("button", { name: "检查文档版本" }));
    expect(callbacks.onInspect).toHaveBeenCalledWith("doc-a");
  });

  it("renders immutable revisions newest first without exposing parser secrets", () => {
    render(<DocumentVersionInspector {...props()} documentId="doc-a" versions={versions} />);
    const revisionLabels = screen.getAllByText(/Revision [13]/).map((node) => node.textContent);
    expect(revisionLabels).toEqual(["Revision 3", "Revision 1"]);
    expect(screen.getByText("https://example.test/doc/v3")).toBeTruthy();
    expect(document.body.textContent).not.toContain("token=secret");
    expect(document.body.textContent).not.toContain("hidden");
  });

  it("submits the exact advanced version payload using the current immutable head fences", async () => {
    const callbacks = props();
    const user = userEvent.setup();
    render(<DocumentVersionInspector {...callbacks} documentId="doc-a" versions={versions} />);
    await user.click(screen.getByRole("button", { name: "创建文档版本" }));
    fireEvent.change(screen.getByRole("textbox", { name: "来源标识" }), { target: { value: "upload:guide-v4.pdf" } });
    fireEvent.change(screen.getByRole("textbox", { name: "来源 SHA-256" }), { target: { value: "d".repeat(64) } });
    fireEvent.change(screen.getByRole("textbox", { name: "解析策略快照 JSON" }), { target: { value: '{"engine":"mineru"}' } });
    fireEvent.change(screen.getByRole("textbox", { name: "解析元数据 JSON" }), { target: { value: '{"pages":10}' } });
    fireEvent.change(screen.getByRole("textbox", { name: "来源内容引用" }), { target: { value: "vault://documents/doc-a/v4" } });
    fireEvent.change(screen.getByRole("textbox", { name: "变更原因" }), { target: { value: "new source" } });
    await user.click(screen.getByRole("button", { name: "提交新版本" }));

    await waitFor(() => expect(callbacks.onCreate).toHaveBeenCalledWith({
      expected_current_revision: 3,
      expected_current_version_id: "version-3",
      source_identity: "upload:guide-v4.pdf",
      source_hash: "d".repeat(64),
      parser_policy_snapshot: { engine: "mineru" },
      parser_metadata: { pages: 10 },
      source_content_ref: "vault://documents/doc-a/v4",
      change_reason: "new source",
    }));
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "创建文档版本" })));
  });

  it("shows a truncation warning at the 100-version boundary", () => {
    render(<DocumentVersionInspector {...props()} documentId="doc-a" versions={versions} truncated />);
    expect(screen.getByText("结果可能被截断")).toBeTruthy();
  });


  it("associates invalid hash and date errors with advanced fields", async () => {
    const user=userEvent.setup(); render(<DocumentVersionInspector {...props()} documentId="doc-a" versions={versions} />); await user.click(screen.getByRole("button",{name:"创建文档版本"}));
    fireEvent.change(screen.getByRole("textbox",{name:"来源标识"}),{target:{value:"source"}}); fireEvent.change(screen.getByRole("textbox",{name:"来源 SHA-256"}),{target:{value:"bad"}}); await user.click(screen.getByRole("button",{name:"提交新版本"}));
    expect(screen.getByRole("textbox",{name:"来源 SHA-256"}).getAttribute("aria-invalid")).toBe("true");
  });


  it("associates empty identity and invalid JSON errors to version controls", async () => {
    const user=userEvent.setup(); render(<DocumentVersionInspector {...props()} documentId="doc-a" versions={versions} />); await user.click(screen.getByRole("button",{name:"创建文档版本"})); await user.click(screen.getByRole("button",{name:"提交新版本"}));
    expect(screen.getByRole("textbox",{name:"来源标识"}).getAttribute("aria-invalid")).toBe("true");
    fireEvent.change(screen.getByRole("textbox",{name:"来源标识"}),{target:{value:"source"}}); fireEvent.change(screen.getByRole("textbox",{name:"来源 SHA-256"}),{target:{value:"d".repeat(64)}}); fireEvent.change(screen.getByRole("textbox",{name:"解析策略快照 JSON"}),{target:{value:"[]"}}); await user.click(screen.getByRole("button",{name:"提交新版本"}));
    expect(screen.getByRole("textbox",{name:"解析策略快照 JSON"}).getAttribute("aria-invalid")).toBe("true");
  });

});
