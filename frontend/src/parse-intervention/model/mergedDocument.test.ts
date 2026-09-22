import { describe, expect, it } from "vitest";
import type { DocumentChunkItem } from "../../types/rag";
import { buildMergedDocument } from "./mergedDocument";

function head(id: string, overrides: Partial<DocumentChunkItem> = {}): DocumentChunkItem {
  return {
    chunk_id: id,
    doc_id: "doc-a",
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    text: `text of ${id}`,
    text_hash: "h",
    content_revision: 1,
    document_revision: 3,
    enabled: true,
    chunk_role: "flat",
    seq: 0,
    context: "",
    char_count: 0,
    parent_relation: "none",
    metadata: {},
    ...overrides,
  };
}

describe("整篇合并视图模型", () => {
  it("按 (seq, chunkId) 排，排序键与服务端 order_by(chunk_index, id) 同源", () => {
    // 服务端 SQL 已是 order_by(chunk_index, id)；前端再排一次是漂移源
    const merged = buildMergedDocument(
      [head("b", { seq: 5 }), head("a", { seq: 1 }), head("c", { seq: 9 })],
      3,
    );
    // 排序键与服务端 order_by(chunk_index, id) 同源：seq 就是 chunk_index。在客户端再排
    // 一次不是"另起一套次序"，而是因为已载集合可能被钉进一条深链取回的头部（逻辑上在中
    // 间、物理上在末尾），信任数组顺序会把整篇读序打乱 —— 评审实测到过。
    expect(merged.segments.map((s) => s.chunkId)).toEqual(["a", "b", "c"]);
    expect(merged.bodyText).toBe("text of a\n\ntext of b\n\ntext of c");
  });

  it("seq 相同时按 chunkId 定序，与 SQL 的 tie-break 一致", () => {
    const merged = buildMergedDocument(
      [head("chunk-z", { seq: 4 }), head("chunk-a", { seq: 4 })],
      2,
    );
    expect(merged.segments.map((s) => s.chunkId)).toEqual(["chunk-a", "chunk-z"]);
  });

  it("父块一律排除，父子同拼会重复正文", () => {
    const merged = buildMergedDocument(
      [
        head("p1", { chunk_role: "parent", text: "父块全文" }),
        head("c1", { chunk_role: "child", text: "子块一" }),
        head("c2", { chunk_role: "child", text: "子块二" }),
      ],
      3,
    );
    expect(merged.segments.map((s) => s.chunkId)).toEqual(["c1", "c2"]);
    expect(merged.bodyText).not.toContain("父块全文");
    expect(merged.excludedParentCount).toBe(1);
  });

  it("墓碑以占位形式留在原位，不进正文也不静默消失", () => {
    const merged = buildMergedDocument(
      [head("k1"), head("k2", { enabled: false }), head("k3")],
      3,
    );
    expect(merged.segments.map((s) => [s.chunkId, s.kind])).toEqual([
      ["k1", "body"],
      ["k2", "tombstone"],
      ["k3", "body"],
    ]);
    expect(merged.tombstoneCount).toBe(1);
    expect(merged.bodyText).toBe("text of k1\n\ntext of k3");
  });

  it("未载全时明确报告不完整，绝不把部分显示成整篇", () => {
    const merged = buildMergedDocument([head("k1"), head("k2")], 250);
    expect(merged.complete).toBe(false);
    expect(merged.loadedCount).toBe(2);
    expect(merged.totalCount).toBe(250);
    expect(merged.coverageLabel).toContain("2");
    expect(merged.coverageLabel).toContain("250");
  });

  it("载全后 coverageLabel 不再声称部分", () => {
    const merged = buildMergedDocument([head("k1"), head("k2")], 2);
    expect(merged.complete).toBe(true);
    expect(merged.coverageLabel).not.toMatch(/仅|部分/);
  });

  it("content_revision 大于 1 才标为人工改过，并带上来源", () => {
    const merged = buildMergedDocument(
      [
        head("k1"),
        head("k2", { content_revision: 4, edit_source: "revert" }),
        head("k3", { content_revision: 2 }),
      ],
      3,
    );
    expect(merged.segments.map((s) => s.editedManually)).toEqual([false, true, true]);
    expect(merged.segments[1]).toMatchObject({ revision: 4, editSource: "revert" });
    expect(merged.manuallyEditedCount).toBe(2);
  });

  it("空列表与全墓碑都不会伪造出正文", () => {
    expect(buildMergedDocument([], 0)).toMatchObject({
      bodyText: "",
      complete: true,
      segmentCount: 0,
    });
    const allTomb = buildMergedDocument([head("k1", { enabled: false })], 1);
    expect(allTomb.bodyText).toBe("");
    expect(allTomb.complete).toBe(true);
    expect(allTomb.tombstoneCount).toBe(1);
  });

  it("总数缺失（未知）时不猜测完整性", () => {
    const merged = buildMergedDocument([head("k1")], null);
    expect(merged.complete).toBe(false);
    expect(merged.totalCount).toBeNull();
    expect(merged.coverageLabel).toContain("未知");
  });
});
