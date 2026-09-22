// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DocumentChunkItem, DocumentDetail } from "../../types/rag";
import type { ParseScope } from "../model/parseInterventionModel";
const api = vi.hoisted(() => ({ fetchParseChunkPage: vi.fn(), fetchParseChunkDetail: vi.fn(), patchParseChunk: vi.fn(), tombstoneParseChunk: vi.fn(), setParseChunkEnabled: vi.fn(), fetchParseChunkRevisions: vi.fn(), revertParseChunk: vi.fn() }));
vi.mock("../api/parseInterventionApi", () => api);
import { useParseIntervention } from "./useParseIntervention";
const scopeA: ParseScope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token-a", docId: "doc-a" };
const document = { id:"doc-a", name:"Doc", tenant_id:"tenant-a", dataset_id:"dataset-a", status:"completed", status_detail:"", progress:1, chunk_count:2, doc_type:"pdf", error_message:"", file_path:"secret", file_hash:"hash", updated_at:null } as DocumentDetail;
function chunk(id:string, rev=1, overrides:Partial<DocumentChunkItem>={}):DocumentChunkItem{return {chunk_id:id,doc_id:"doc-a",tenant_id:"tenant-a",dataset_id:"dataset-a",text:`body ${id}`,text_hash:"h",content_revision:rev,document_revision:3,enabled:true,chunk_role:"flat",seq:Number(id.split("-").pop()||0),context:"",char_count:5,parent_relation:"none",metadata:{},...overrides};}
function page(items:DocumentChunkItem[], total=items.length){return {authority_mode:"active" as const,items,total,offset:0,limit:100,known_parent_ids:[],missing_parent_ids:[]};}
function revisionRow(revision:number){return {revision,content:`v${revision}`,content_hash:`h${revision}`,enabled:true,editor_id:"system:document-workbench",edit_source:"user",edited_at:"2026-09-22T03:04:05.000Z"};}

type Gate = { resolve:(v:unknown)=>void; reject:(e:Error)=>void; signal:AbortSignal };
const gates = new Map<string, Gate>();

beforeEach(()=>{
  vi.clearAllMocks();
  gates.clear();
  api.fetchParseChunkPage.mockResolvedValue(page([chunk("chunk-0",3), chunk("chunk-1",3)],2));
  api.fetchParseChunkRevisions.mockImplementation((_scope:ParseScope, chunkId:string, signal:AbortSignal) =>
    new Promise((resolve, reject) => {
      gates.set(chunkId, {
        resolve,
        reject: (e:Error) => reject(e),
        signal,
      });
    }));
});
afterEach(cleanup);

async function openHistoryOnChunk1() {
  const { result } = renderHook(() => useParseIntervention(scopeA, true, document));
  await waitFor(() => expect(result.current.status).toBe("ready"));
  act(() => result.current.selectChunk("chunk-0"));
  void act(() => { void result.current.loadRevisions(); }); // A：chunk-0 的历史挂在飞
  await waitFor(() => expect(gates.has("chunk-0")).toBe(true));
  act(() => result.current.selectChunk("chunk-1"));
  void act(() => { void result.current.loadRevisions(); }); // B：chunk-1 的历史取代 A
  await waitFor(() => expect(gates.has("chunk-1")).toBe(true));
  return { result };
}

describe("切片历史请求互相取代", () => {
  it("被取代的那次请求真的中止了，不是留在飞", async () => {
    const { result } = await openHistoryOnChunk1();
    expect(result.current.revisionsLoading).toBe(true);
    // 旧代码只在"稍后落地时"判新旧，HTTP 请求本身从不取消：一次点击切换切片
    // 就把上一个请求继续占着连接。
    expect(gates.get("chunk-0")!.signal.aborted).toBe(true);
    expect(gates.get("chunk-1")!.signal.aborted).toBe(false);
    await act(async () => { gates.get("chunk-1")!.resolve({ chunk_id: "chunk-1", items: [revisionRow(3)] }); });
  });

  it("旧切片迟到的历史不许把当前切片的面板清成空", async () => {
    const { result } = await openHistoryOnChunk1();
    await act(async () => { gates.get("chunk-1")!.resolve({ chunk_id: "chunk-1", items: [revisionRow(3)] }); });
    expect(result.current.revisions.map((r) => r.revision)).toEqual([3]);
    // A 晚到：不中止它就会把 revisionsFor 改回 chunk-0，面板按当前切片一比对，
    // 结果是操作员眼前的历史凭空消失。
    await act(async () => { gates.get("chunk-0")!.resolve({ chunk_id: "chunk-0", items: [revisionRow(3), revisionRow(2)] }); });
    expect(result.current.revisions.map((r) => r.revision)).toEqual([3]);
    expect(result.current.revisionsError).toBe("");
  });

  it("旧切片历史的失败不给当前切片挂错误横幅", async () => {
    const { result } = await openHistoryOnChunk1();
    await act(async () => { gates.get("chunk-1")!.resolve({ chunk_id: "chunk-1", items: [revisionRow(3), revisionRow(2)] }); });
    expect(result.current.revisions).toHaveLength(2);
    await act(async () => { gates.get("chunk-0")!.reject(new Error("chunk-0 历史载入失败")); });
    // 操作员从没在 chunk-0 上看历史，横幅却写着它的失败，且会盖住 chunk-1 的正常结果。
    expect(result.current.revisionsError).toBe("");
    expect(result.current.revisions.map((r) => r.revision)).toEqual([3, 2]);
  });

  it("同一个切片的重复点击不重发请求", async () => {
    const { result } = renderHook(() => useParseIntervention(scopeA, true, document));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    await act(async () => { void result.current.loadRevisions(); });
    expect(gates.has("chunk-0")).toBe(true);
    await act(async () => { gates.get("chunk-0")!.resolve({ chunk_id: "chunk-0", items: [revisionRow(3)] }); });
    expect(api.fetchParseChunkRevisions).toHaveBeenCalledTimes(1);
    await act(async () => { expect(await result.current.ensureRevisions()).toBe(false); });
    expect(api.fetchParseChunkRevisions).toHaveBeenCalledTimes(1);
    expect(result.current.revisions.map((r) => r.revision)).toEqual([3]);
  });
});
