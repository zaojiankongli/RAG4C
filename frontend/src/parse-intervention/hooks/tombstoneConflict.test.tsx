// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentChunkItem, DocumentDetail } from "../../types/rag";
import type { ParseScope } from "../model/parseInterventionModel";
const api = vi.hoisted(() => ({ fetchParseChunkPage: vi.fn(), fetchParseChunkDetail: vi.fn(), patchParseChunk: vi.fn(), tombstoneParseChunk: vi.fn(), setParseChunkEnabled: vi.fn(), fetchParseChunkRevisions: vi.fn(), revertParseChunk: vi.fn() }));
vi.mock("../api/parseInterventionApi", () => api);
import { useParseIntervention } from "./useParseIntervention";
const scopeA: ParseScope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token-a", docId: "doc-a" };
const document = { id:"doc-a", name:"Doc", tenant_id:"tenant-a", dataset_id:"dataset-a", status:"completed", status_detail:"", progress:1, chunk_count:1, doc_type:"pdf", error_message:"", file_path:"secret", file_hash:"hash", updated_at:null } as DocumentDetail;
function chunk(id:string, rev=1, overrides:Partial<DocumentChunkItem>={}):DocumentChunkItem{return {chunk_id:id,doc_id:"doc-a",tenant_id:"tenant-a",dataset_id:"dataset-a",text:`body ${id}`,text_hash:"h",content_revision:rev,document_revision:3,enabled:true,chunk_role:"flat",seq:0,context:"",char_count:5,parent_relation:"none",metadata:{},...overrides};}
function page(items:DocumentChunkItem[], total=items.length){return {authority_mode:"active" as const,items,total,offset:0,limit:100,known_parent_ids:[],missing_parent_ids:[]};}
beforeEach(()=>{vi.clearAllMocks();api.fetchParseChunkPage.mockResolvedValue(page([chunk("chunk-0",3)],1));}); afterEach(cleanup);

describe("墓碑写入的并发冲突恢复", () => {
  it("删除撞 409 时进入孤稿重放，而不是只留一条错误", async () => {
    api.tombstoneParseChunk.mockRejectedValue(Object.assign(new Error("conflict"), { status: 409 }));
    api.fetchParseChunkDetail.mockResolvedValue(chunk("chunk-0", 7));
    const { result } = renderHook(() => useParseIntervention(scopeA, true, document));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    act(() => { result.current.setReason("内容已过时"); });
    await act(async () => { expect(await result.current.removeSelected()).toBe(false); });
    expect(result.current.orphanDraft).toMatchObject({ chunkId: "chunk-0", baseRevision: 3, resolution: "conflict", reason: "内容已过时" });
    expect(result.current.error).toBe("");
    expect(result.current.receipt).toBeNull();
  });

  it("删除撞 409 且服务端已是墓碑时，重放路径判定为 tombstone", async () => {
    api.tombstoneParseChunk.mockRejectedValue(Object.assign(new Error("conflict"), { status: 409 }));
    api.fetchParseChunkDetail.mockResolvedValue(chunk("chunk-0", 7, { enabled: false }));
    const { result } = renderHook(() => useParseIntervention(scopeA, true, document));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    await act(async () => { await result.current.removeSelected(); });
    expect(result.current.orphanDraft?.resolution).toBe("tombstone");
    expect(result.current.canRebaseOrphan).toBe(false);
  });

  it("非 409 的删除失败保持可见错误，不伪造重放状态", async () => {
    api.tombstoneParseChunk.mockRejectedValue(Object.assign(new Error("boom"), { status: 500 }));
    const { result } = renderHook(() => useParseIntervention(scopeA, true, document));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    await act(async () => { expect(await result.current.removeSelected()).toBe(false); });
    expect(result.current.orphanDraft).toBeNull();
    expect(result.current.error).toBe("boom");
  });
});
