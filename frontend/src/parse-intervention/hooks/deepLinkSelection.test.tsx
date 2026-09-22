// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentChunkItem, DocumentDetail } from "../../types/rag";
import type { ParseScope } from "../model/parseInterventionModel";
const api = vi.hoisted(() => ({ fetchParseChunkPage: vi.fn(), fetchParseChunkDetail: vi.fn(), patchParseChunk: vi.fn(), tombstoneParseChunk: vi.fn(), setParseChunkEnabled: vi.fn(), fetchParseChunkRevisions: vi.fn(), revertParseChunk: vi.fn() }));
vi.mock("../api/parseInterventionApi", () => api);
import { useParseIntervention } from "./useParseIntervention";

const scopeA: ParseScope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token-a", docId: "doc-a" };
const document = { id:"doc-a", name:"Doc", tenant_id:"tenant-a", dataset_id:"dataset-a", status:"completed", status_detail:"", progress:1, chunk_count:250, doc_type:"pdf", error_message:"", file_path:"secret", file_hash:"hash", updated_at:null } as DocumentDetail;
function chunk(id:string, rev=1, overrides:Partial<DocumentChunkItem>={}):DocumentChunkItem{return {chunk_id:id,doc_id:"doc-a",tenant_id:"tenant-a",dataset_id:"dataset-a",text:`body ${id}`,text_hash:"h",content_revision:rev,document_revision:3,enabled:true,chunk_role:"flat",seq:0,context:"",char_count:5,parent_relation:"none",metadata:{},...overrides};}
function page(items:DocumentChunkItem[], total:number){return {authority_mode:"active" as const,items,total,offset:0,limit:100,known_parent_ids:[],missing_parent_ids:[]};}

beforeEach(()=>{vi.clearAllMocks();api.fetchParseChunkPage.mockResolvedValue(page([chunk("chunk-0"),chunk("chunk-1")],250));});
afterEach(cleanup);

describe("答案↔切片深链落在首页之外", () => {
  it("不在第一页时按详情取回并选中那一条，而不是回落到第一条", async () => {
    api.fetchParseChunkDetail.mockResolvedValue(chunk("chunk-173", 4));
    const { result } = renderHook(() => useParseIntervention(scopeA, true, document, "chunk-173"));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    await waitFor(() => expect(result.current.selected?.chunk_id).toBe("chunk-173"));
    expect(api.fetchParseChunkDetail).toHaveBeenCalledWith(scopeA, "chunk-173", expect.any(AbortSignal));
    expect(result.current.draft).toBe("body chunk-173");
    expect(result.current.expectedRevision).toBe(4);
    expect(result.current.error).toBe("");
  });

  it("深链目标已不存在时明确报错，不静默展示另一个切片的内容当作它", async () => {
    api.fetchParseChunkDetail.mockRejectedValue(Object.assign(new Error("切片不存在或无权访问"), { status: 404 }));
    const { result } = renderHook(() => useParseIntervention(scopeA, true, document, "chunk-gone"));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    await waitFor(() => expect(result.current.error).toContain("切片不存在或无权访问"));
    expect(result.current.selected?.chunk_id).not.toBe("chunk-gone");
  });

  it("命中第一页时不额外请求详情", async () => {
    const { result } = renderHook(() => useParseIntervention(scopeA, true, document, "chunk-1"));
    await waitFor(() => expect(result.current.selected?.chunk_id).toBe("chunk-1"));
    expect(api.fetchParseChunkDetail).not.toHaveBeenCalled();
  });

  it("同一切片只补取一次，切换 scope 后允许重新解析", async () => {
    api.fetchParseChunkDetail.mockResolvedValue(chunk("chunk-173", 4));
    const { rerender, result } = renderHook(({ docId, chunkId }) => useParseIntervention(
      { ...scopeA, docId, actorToken: "token-a", tenantId: "tenant-a", datasetId: "dataset-a" },
      true,
      document,
      chunkId,
    ), { initialProps: { docId: "doc-a", chunkId: "chunk-173" } });
    await waitFor(() => expect(result.current.selected?.chunk_id).toBe("chunk-173"));
    act(() => rerender({ docId: "doc-a", chunkId: "chunk-173" }));
    expect(api.fetchParseChunkDetail).toHaveBeenCalledTimes(1);
  });
});
