import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ParseScope } from "../model/parseInterventionModel";
const client = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../../api/client", () => client);
import { fetchParseChunkDetail, fetchParseChunkPage, fetchParseChunkRevisions, patchParseChunk, revertParseChunk, setParseChunkEnabled, tombstoneParseChunk } from "./parseInterventionApi";
const scope: ParseScope = { tenantId: "tenant-a", datasetId: "dataset/a", actorToken: "signed-token", docId: "doc/1" };
const auth = { Authorization: "Bearer signed-token", "X-RAG4C-Tenant": "tenant-a" };
beforeEach(() => { vi.clearAllMocks(); client.request.mockResolvedValue({}); });
function body(index: number) { return JSON.parse(String(client.request.mock.calls[index][1].body)); }
describe("authenticated parse intervention API", () => {
  it("sends Bearer and tenant headers with page/search params", async () => {
    const signal = new AbortController().signal;
    await fetchParseChunkPage(scope, { offset: 200, limit: 100, query: "policy", includeDisabled: true }, signal);
    expect(client.request).toHaveBeenCalledWith(
      "/api/knowledge-bases/dataset%2Fa/documents/doc%2F1/chunks?offset=200&limit=100&query=policy&include_disabled=true",
      expect.objectContaining({ headers: auth, signal }),
    );
  });
  it("uses scoped detail and exact CAS mutations", async () => {
    const signal = new AbortController().signal;
    await fetchParseChunkDetail(scope, "chunk/1", signal);
    await patchParseChunk(scope, "chunk/1", "edited", 4, "修正 OCR 错字", signal);
    await tombstoneParseChunk(scope, "chunk/1", 5, signal);
    const calls = client.request.mock.calls;
    expect(calls[0][0]).toContain("/chunks/chunk%2F1");
    expect(calls[1][1].method).toBe("PATCH");
    expect(body(1)).toEqual({ text: "edited", expected_revision: 4, reason: "修正 OCR 错字" });
    expect(calls[2][0]).toContain("expected_revision=5");
  });
  it("persists the reason on the text patch, scoped and CAS fenced", async () => {
    const signal = new AbortController().signal;
    await patchParseChunk(scope, "chunk/1", "新正文", 7, "恢复遗漏条款", signal);
    expect(client.request).toHaveBeenCalledWith(
      "/api/knowledge-bases/dataset%2Fa/documents/doc%2F1/chunks/chunk%2F1",
      expect.objectContaining({ method: "PATCH", headers: auth, signal }),
    );
    expect(body(0)).toEqual({ text: "新正文", expected_revision: 7, reason: "恢复遗漏条款" });
  });
  it("toggles enabled through the same CAS PATCH without text", async () => {
    const signal = new AbortController().signal;
    await setParseChunkEnabled(scope, "chunk/1", true, 8, "误删恢复", signal);
    await setParseChunkEnabled(scope, "chunk/1", false, 9, "内容过期", signal);
    const calls = client.request.mock.calls;
    expect(calls[0][0]).toBe("/api/knowledge-bases/dataset%2Fa/documents/doc%2F1/chunks/chunk%2F1");
    expect(calls[0][1].method).toBe("PATCH");
    expect(JSON.parse(String(calls[0][1].body))).toEqual({ enabled: true, expected_revision: 8, reason: "误删恢复" });
    expect(JSON.parse(String(calls[1][1].body))).toEqual({ enabled: false, expected_revision: 9, reason: "内容过期" });
  });
  it("reads the revision history from the scoped revisions endpoint", async () => {
    const signal = new AbortController().signal;
    await fetchParseChunkRevisions(scope, "chunk/1", signal);
    expect(client.request).toHaveBeenCalledWith(
      "/api/knowledge-bases/dataset%2Fa/documents/doc%2F1/chunks/chunk%2F1/revisions",
      expect.objectContaining({ method: "GET", headers: auth, signal }),
    );
  });
  it("reverts by target revision with CAS and reason, as a POST", async () => {
    const signal = new AbortController().signal;
    await revertParseChunk(scope, "chunk/1", 3, 9, "回到解析器产出", signal);
    expect(client.request).toHaveBeenCalledWith(
      "/api/knowledge-bases/dataset%2Fa/documents/doc%2F1/chunks/chunk%2F1/revert",
      expect.objectContaining({ method: "POST", headers: auth, signal }),
    );
    expect(body(0)).toEqual({ target_revision: 3, expected_revision: 9, reason: "回到解析器产出" });
  });
});
