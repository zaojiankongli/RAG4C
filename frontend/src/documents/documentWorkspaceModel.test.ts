import { describe, expect, it } from "vitest";
import { buildDocumentFacets, chunkDisplayTitle, filterDocuments, removeChunk, replaceChunk } from "./documentWorkspaceModel";
import type { DocumentChunkItem, DocumentItem } from "../types/rag";

const docs: DocumentItem[] = [
  { id: "1", name: "handbook.pdf", status: "completed", progress: 1, chunk_count: 10, doc_type: "pdf", error_message: "", parser_meta: { engine: "vision" }, logical_folder_path: "制度/人力", tags: ["员工", "制度"] },
  { id: "2", name: "rules.md", status: "completed", progress: 1, chunk_count: 5, doc_type: "markdown", error_message: "", parser_meta: { engine: "fast" }, logical_folder_path: "产品", tags: ["规范"] },
  { id: "3", name: "ledger.xlsx", status: "error", progress: 0.4, chunk_count: 0, doc_type: "excel", error_message: "failed", logical_folder_path: "", tags: [] },
];

describe("Tencent-style document facets", () => {
  it("builds stable status, type, and parser-engine counts", () => {
    expect(buildDocumentFacets(docs)).toEqual({
      statuses: { all: 3, completed: 2, processing: 0, error: 1 },
      types: { pdf: 1, markdown: 1, excel: 1 },
      engines: { vision: 1, fast: 1, unknown: 1 },
      categories: { "制度/人力": 1, "产品": 1, "未分类": 1 },
      tags: { "员工": 1, "制度": 1, "规范": 1 },
    });
  });

  it("combines keyword, status, type, and engine filters", () => {
    expect(filterDocuments(docs, { keyword: "book", status: "completed", type: "pdf", engine: "vision", category: "制度/人力", tag: "员工" }).map((doc) => doc.id)).toEqual(["1"]);
    expect(filterDocuments(docs, { keyword: "", status: "all", type: "excel", engine: "all", category: "all", tag: "all" }).map((doc) => doc.id)).toEqual(["3"]);
  });

  it("replaces and removes chunks without disturbing neighboring order", () => {
    const first = { chunk_id: "a", seq: 0, text: "a" } as DocumentChunkItem;
    const second = { chunk_id: "b", seq: 1, text: "b" } as DocumentChunkItem;
    expect(replaceChunk([first, second], { ...second, text: "updated" })[1].text).toBe("updated");
    expect(removeChunk([first, second], "a").map((chunk) => chunk.chunk_id)).toEqual(["b"]);
  });

});

describe("chunk display", () => {
  it("prefers heading and keeps sequence visible", () => {
    const chunk = { seq: 2, heading: "Expense Policy", chunk_id: "doc::0002" } as DocumentChunkItem;
    expect(chunkDisplayTitle(chunk)).toBe("切片 3 · Expense Policy");
  });
});
