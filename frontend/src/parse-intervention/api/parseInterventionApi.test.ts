import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ParseScope } from "../model/parseInterventionModel";
const client = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../../api/client", () => client);
import { fetchParseChunkDetail, fetchParseChunkPage, patchParseChunk, tombstoneParseChunk } from "./parseInterventionApi";
const scope: ParseScope = { tenantId: "tenant-a", datasetId: "dataset/a", actorToken: "signed-token", docId: "doc/1" };
beforeEach(() => { vi.clearAllMocks(); client.request.mockResolvedValue({}); });
describe("authenticated parse intervention API", () => {
  it("sends Bearer and tenant headers with page/search params", async () => {
    const signal = new AbortController().signal;
    await fetchParseChunkPage(scope, { offset: 200, limit: 100, query: "policy", includeDisabled: true }, signal);
    expect(client.request).toHaveBeenCalledWith(
      "/api/knowledge-bases/dataset%2Fa/documents/doc%2F1/chunks?offset=200&limit=100&query=policy&include_disabled=true",
      expect.objectContaining({ headers: { Authorization: "Bearer signed-token", "X-RAG4C-Tenant": "tenant-a" }, signal }),
    );
  });
  it("uses scoped detail and exact CAS mutations", async () => {
    const signal = new AbortController().signal;
    await fetchParseChunkDetail(scope, "chunk/1", signal);
    await patchParseChunk(scope, "chunk/1", "edited", 4, signal);
    await tombstoneParseChunk(scope, "chunk/1", 5, signal);
    const calls = client.request.mock.calls;
    expect(calls[0][0]).toContain("/chunks/chunk%2F1");
    expect(JSON.parse(String(calls[1][1].body))).toEqual({ text: "edited", expected_revision: 4 });
    expect(calls[2][0]).toContain("expected_revision=5");
  });
});
