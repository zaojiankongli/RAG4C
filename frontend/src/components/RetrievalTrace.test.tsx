// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
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
const evidence = (chunk_id: string, doc_id: string): EvidenceChunk => ({
  chunk_id,
  doc_id,
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
      datasetId: "",
    });
  });

  it("says 无法直达 instead of inventing a link when the chunk has no document ownership", () => {
    render(<RetrievalTrace traces={[]} citations={[citation("chunk-9", "stale")]} evidence={[]} />);
    expect(screen.queryByLabelText(/^在解析干预中查看已变更的切片/)).toBeNull();
    expect(screen.getByText(/缺少文档归属，无法直达/)).toBeTruthy();
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
});
