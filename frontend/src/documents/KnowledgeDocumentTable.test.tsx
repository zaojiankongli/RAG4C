// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DocumentItem } from "../types/rag";
import KnowledgeDocumentTable from "./KnowledgeDocumentTable";

function makeDocument(index: number): DocumentItem {
  return {
    id: `doc-${index}`,
    name: `文档-${index}.md`,
    status: "completed",
    progress: 1,
    chunk_count: index,
    doc_type: "markdown",
    error_message: "",
  };
}

function table(documents: DocumentItem[], selectedIds: string[] = [], onSelectionChange = vi.fn(), onOpenParse = vi.fn()) {
  return (
    <KnowledgeDocumentTable
      documents={documents}
      loading={false}
      selectedIds={selectedIds}
      onSelectionChange={onSelectionChange}
      renderFile={(item) => item.name}
      renderParserProfile={() => "解析画像"}
      renderStatus={() => "已完成"}
      renderActions={() => null}
      renderExpanded={() => null}
      canExpand={(item) => item.status === "completed"}
      onOpenParse={onOpenParse}
      canOpenParse={(item) => item.status === "completed"}
      emptyContent="暂无文档"
    />
  );
}

afterEach(cleanup);

describe("KnowledgeDocumentTable", () => {
  it("paginates more than ten documents and clamps current page after filtering", async () => {
    const documents = Array.from({ length: 11 }, (_, index) => makeDocument(index + 1));
    const view = render(table(documents));

    expect(screen.getByText("文档-1.md")).toBeTruthy();
    expect(screen.queryByText("文档-11.md")).toBeNull();
    fireEvent.click(document.querySelectorAll(".t-pagination__number")[1] as HTMLElement);
    expect(await screen.findByText("文档-11.md")).toBeTruthy();

    view.rerender(table([documents[0]]));
    await waitFor(() => expect(screen.getByText("文档-1.md")).toBeTruthy());
    expect(screen.queryByText("文档-11.md")).toBeNull();
  });

  it("renders server-provided rows without client-side slicing and delegates opaque pagination", () => {
    const onPrevious = vi.fn();
    const onNext = vi.fn();
    const documents = Array.from({ length: 10 }, (_, index) => makeDocument(index + 11));

    render(
      <KnowledgeDocumentTable
        documents={documents}
        loading={false}
        selectedIds={[]}
        onSelectionChange={vi.fn()}
        renderFile={(item) => item.name}
        renderParserProfile={() => "解析画像"}
        renderStatus={() => "已完成"}
        renderActions={() => null}
        emptyContent="暂无文档"
        serverPagination={{
          current: 2,
          total: 25,
          pageSize: 10,
          hasPrevious: true,
          hasNext: true,
          onPrevious,
          onNext,
        }}
      />,
    );

    expect(screen.getByText("文档-11.md")).toBeTruthy();
    expect(screen.queryByText("文档-1.md")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    fireEvent.click(screen.getByRole("button", { name: "上一页" }));
    expect(onNext).toHaveBeenCalledTimes(1);
    expect(onPrevious).toHaveBeenCalledTimes(1);
  });

  it("owns accessible selection controls and enforces the 100-document ceiling", () => {
    const item = makeDocument(101);
    const selectedIds = Array.from({ length: 100 }, (_, index) => `selected-${index}`);
    render(table([item], selectedIds));

    expect(screen.getByRole("checkbox", { name: "选择当前页可操作文档" })).toBeTruthy();
    expect(
      (screen.getByRole("checkbox", { name: `选择 ${item.name}` }) as HTMLInputElement).disabled,
    ).toBe(true);
    expect(screen.getByRole("status").textContent).toContain("单次最多选择 100 篇文档");
  });


  it("opens the dedicated parse workspace from a completed document row", () => {
    const item = makeDocument(1);
    const onOpenParse = vi.fn();
    render(table([item], [], vi.fn(), onOpenParse));

    fireEvent.click(screen.getByRole("button", { name: `解析干预 ${item.name}` }));
    expect(onOpenParse).toHaveBeenCalledWith(item);
  });
  it("only exposes aria-expanded on expandable buttons and includes the row name", () => {
    render(table([makeDocument(1), { ...makeDocument(2), status: "parsing" }]));

    const expandable = screen.getByRole("button", { name: "展开 文档-1.md 详情" });
    const disabled = screen.getByRole("button", { name: "文档-2.md 暂不可展开" });
    expect(expandable.tagName).toBe("BUTTON");
    expect(expandable.getAttribute("aria-expanded")).toBe("false");
    expect(disabled.tagName).toBe("BUTTON");
    expect(disabled.hasAttribute("aria-expanded")).toBe(false);
    expect((disabled as HTMLButtonElement).disabled).toBe(true);
  });
});
