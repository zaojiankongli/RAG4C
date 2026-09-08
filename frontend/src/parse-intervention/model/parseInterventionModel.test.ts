import { describe, expect, it } from "vitest";
import type { DocumentChunkItem } from "../../types/rag";
import {
  buildChunkOutline,
  buildDiff,
  chunkWarnings,
  estimateTokens,
  projectChunkLifecycle,
  projectProjectionLifecycle,
  requireParseScope,
  safeMetadataFacts,
  sanitizeSourceFact,
} from "./parseInterventionModel";

function chunk(overrides: Partial<DocumentChunkItem> = {}): DocumentChunkItem {
  return {
    chunk_id: "chunk-1",
    doc_id: "doc-1",
    text: "正文",
    text_hash: "hash",
    content_revision: 4,
    seq: 0,
    context: "",
    char_count: 2,
    metadata: {},
    ...overrides,
  };
}

describe("parse intervention model", () => {
  it("fails closed without a complete tenant, dataset, and document scope", () => {
    expect(requireParseScope({ tenantId: "", datasetId: "default", actorToken: "token", docId: "doc" })).toBeNull();
    expect(requireParseScope({ tenantId: "tenant", datasetId: "", actorToken: "token", docId: "doc" })).toBeNull();
    expect(requireParseScope({ tenantId: "tenant", datasetId: "dataset", actorToken: "token", docId: "" })).toBeNull();
    expect(requireParseScope({ tenantId: " tenant ", datasetId: " data ", actorToken: " token ", docId: " doc " })).toEqual({
      tenantId: "tenant",
      datasetId: "data",
      actorToken: "token",
      docId: "doc",
    });
  });

  it("sanitizes source facts and allowlists safe metadata", () => {
    expect(sanitizeSourceFact("https://user:secret@example.com/a?token=abc#x")).toBe(
      "https://example.com/a",
    );
    expect(sanitizeSourceFact("C:\\vault\\credentials\\handbook.pdf")).toBe("handbook.pdf");
    expect(sanitizeSourceFact("ssh://user:secret@example.com/repo?token=x#frag")).toBe("ssh://example.com/repo");
    expect(sanitizeSourceFact("vault:opaque-secret")).toBe("受保护的来源引用");
    expect(
      safeMetadataFacts({
        heading: "Policy",
        page: 2,
        language: "zh-CN",
        api_key: "secret",
        nested: { token: "x" },
      }),
    ).toEqual([
      { label: "标题", key: "heading", value: "Policy" },
      { label: "页码", key: "page", value: "2" },
      { label: "语言", key: "language", value: "zh-CN" },
    ]);
  });

  it("omits non-string headings and container source metadata", () => {
    expect(safeMetadataFacts({
      heading: 123,
      title: ["nested"],
      section: { secret: "hidden" },
      language: 7,
      mime_type: ["text/plain"],
      source: { token: "must-not-leak" },
      page: 4,
      seq: 9,
    })).toEqual([
      { label: "页码", key: "page", value: "4" },
      { label: "顺序", key: "seq", value: "9" },
    ]);
  });

  it("projects lifecycle, warnings, and deterministic estimates without claiming tokenizer truth", () => {
    expect(estimateTokens("中文知识库 ABC 123")).toBe(7);
    expect(projectChunkLifecycle(chunk({ enabled: false }))).toBe("tombstone");
    expect(
      projectProjectionLifecycle(
        chunk({
          content_revision: 5,
          desired_index_revision: 5,
          indexed_revision: 4,
          index_status: "pending",
        }),
      ),
    ).toMatchObject({ state: "pending", label: "投影待处理" });
    expect(chunkWarnings(chunk({ text: "", char_count: 0, parent_chunk_id: "missing" }), new Set(), new Set(["missing"])).map((item) => item.code)).toEqual(
      expect.arrayContaining(["empty", "parent-missing"]),
    );
  });

  it("infers page and section outline from authoritative chunk facts", () => {
    const outline = buildChunkOutline([
      chunk({ chunk_id: "a", seq: 0, page: 1, heading: "Introduction" }),
      chunk({ chunk_id: "b", seq: 1, page: 1, heading: "Policy" }),
      chunk({ chunk_id: "c", seq: 2, page: 2, heading: null }),
    ]);
    expect(outline).toEqual([
      { key: "page:1", page: "1", label: "第 1 页", headings: ["Introduction", "Policy"], chunkIds: ["a", "b"] },
      { key: "page:2", page: "2", label: "第 2 页", headings: [], chunkIds: ["c"] },
    ]);
  });

  it("builds a compact line diff", () => {
    expect(buildDiff("same\nold line", "same\nnew line")).toEqual([
      { kind: "same", text: "same" },
      { kind: "remove", text: "old line" },
      { kind: "add", text: "new line" },
    ]);
  });
});
