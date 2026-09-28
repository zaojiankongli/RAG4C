// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Citation, EvidenceChunk } from "../types/rag";
import { parseChunkWorkbenchLocation } from "../run/appRoute";
import RetrievalTrace from "./RetrievalTrace";

afterEach(cleanup);

const citation = (chunk_id: string, status: Citation["status"]): Citation => ({
  claim: "结论",
  chunk_id,
  status,
  reason: "",
});
const evidence = (
  chunk_id: string,
  doc_id: string,
  dataset_id: string | null = "dataset-a",
): EvidenceChunk => ({
  chunk_id,
  doc_id,
  dataset_id,
  text: "body",
  score: 0.9,
  rank: 1,
  source: null,
});

describe("RetrievalTrace stale → 切片直达", () => {
  it("links each stale citation into the chunk workbench with a resolvable deep link", () => {
    render(
      <RetrievalTrace
        traces={[]}
        citations={[citation("chunk-9", "stale"), citation("chunk-1", "ok")]}
        evidence={[evidence("chunk-9", "doc-1"), evidence("chunk-1", "doc-1")]}
      />,
    );
    const links = screen.getAllByLabelText(/^在解析干预中查看已变更的切片/);
    expect(links).toHaveLength(1);
    const href = (links[0] as HTMLAnchorElement).getAttribute("href") ?? "";
    expect(links[0].textContent).toContain("在解析干预中核对");
    expect(parseChunkWorkbenchLocation({ pathname: "/", search: "", hash: href })).toEqual({
      docId: "doc-1",
      chunkId: "chunk-9",
      datasetId: "dataset-a",
    });
  });

  it("says 无法直达 instead of inventing a link when document or dataset ownership is missing", () => {
    render(<RetrievalTrace traces={[]} citations={[citation("chunk-9", "stale")]} evidence={[]} />);
    expect(screen.queryByLabelText(/^在解析干预中查看已变更的切片/)).toBeNull();
    expect(screen.getByText(/缺少文档或知识库归属，无法直达/)).toBeTruthy();

    cleanup();
    render(
      <RetrievalTrace
        traces={[]}
        citations={[citation("chunk-9", "stale")]}
        evidence={[evidence("chunk-9", "doc-1", null)]}
      />,
    );
    expect(screen.queryByLabelText(/^在解析干预中查看已变更的切片/)).toBeNull();
    expect(screen.getByText(/缺少文档或知识库归属，无法直达/)).toBeTruthy();
  });

  it("keeps non-stale verification rows link-free", () => {
    render(
      <RetrievalTrace
        traces={[]}
        citations={[citation("chunk-3", "unsupported")]}
        evidence={[evidence("chunk-3", "doc-1")]}
      />,
    );
    expect(screen.queryByText(/在解析干预中核对/)).toBeNull();
  });

  // 实机读数里刷了 363 次 React 警告：两条 stale 引用命中同一切片时，
  // `key={link.chunkId}` 就撞了。重复引用是合法数据（同一条证据可以支撑多个结论），
  // 撞的是 key。此前单测每条 stale 都取自不同 chunkId，所以一直没红。
  it("renders one row per stale citation without a duplicate-key warning", () => {
    const errors: unknown[] = [];
    const spy = vi.spyOn(console, "error").mockImplementation((...args) => {
      errors.push(args.join(" "));
    });
    try {
      render(
        <RetrievalTrace
          traces={[]}
          citations={[citation("chunk-9", "stale"), citation("chunk-9", "stale")]}
          evidence={[evidence("chunk-9", "doc-1")]}
        />,
      );
    } finally {
      spy.mockRestore();
    }
    expect(errors.filter((line) => /same key/i.test(String(line)))).toEqual([]);
    expect(screen.getAllByText(/在解析干预中核对/)).toHaveLength(2);
  });
});
