// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api/governanceApi";
import type { DocumentVersion, GovernanceScope } from "../model/governanceModel";
import { useDocumentVersions } from "./useDocumentVersions";

vi.mock("../api/governanceApi");

const scope: GovernanceScope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token" };
const version = (documentId: string, revision: number): DocumentVersion => ({
  id: `${documentId}-v${revision}`, tenant_id: "tenant-a", dataset_id: "dataset-a", document_id: documentId,
  revision, source_identity: `upload:${documentId}.pdf`, source_hash: "a".repeat(64),
  parser_policy_snapshot: {}, parser_metadata: {}, source_content_ref: "", created_by: "editor-a",
  change_reason: revision === 1 ? "initial" : "updated", created_at: `2026-08-2${revision}T00:00:00Z`,
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => { resolve = next; });
  return { promise, resolve };
}

beforeEach(() => vi.resetAllMocks());

describe("useDocumentVersions", () => {
  it("does not guess a document and orders the inspected immutable timeline newest first", async () => {
    vi.mocked(api.fetchDocumentVersions).mockResolvedValue({ items: [version("doc-a", 1), version("doc-a", 2)], count: 2 });
    const { result } = renderHook(() => useDocumentVersions(scope, true));
    expect(api.fetchDocumentVersions).not.toHaveBeenCalled();

    await act(async () => { await result.current.inspect(" doc-a "); });
    expect(api.fetchDocumentVersions).toHaveBeenCalledWith(scope, "doc-a", 100, expect.anything());
    expect(result.current.versions.map((item) => item.revision)).toEqual([2, 1]);
  });

  it("creates with exact fences and refetches the selected document", async () => {
    vi.mocked(api.fetchDocumentVersions)
      .mockResolvedValueOnce({ items: [version("doc-a", 1)], count: 1 })
      .mockResolvedValueOnce({ items: [version("doc-a", 2), version("doc-a", 1)], count: 2 });
    vi.mocked(api.createDocumentVersion).mockResolvedValue(version("doc-a", 2));
    const { result } = renderHook(() => useDocumentVersions(scope, true));
    await act(async () => { await result.current.inspect("doc-a"); });

    const payload = { expected_current_revision: 1, expected_current_version_id: "doc-a-v1", source_identity: "upload:doc-a.pdf", source_hash: "b".repeat(64) };
    await act(async () => { await result.current.create(payload); });

    expect(api.createDocumentVersion).toHaveBeenCalledWith(scope, "doc-a", payload, expect.anything());
    expect(api.fetchDocumentVersions).toHaveBeenCalledTimes(2);
    expect(result.current.versions[0].revision).toBe(2);
  });

  it("fences a slower previous document request when inspection changes", async () => {
    const first = deferred<{ items: DocumentVersion[]; count: number }>();
    const second = deferred<{ items: DocumentVersion[]; count: number }>();
    vi.mocked(api.fetchDocumentVersions).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const { result } = renderHook(() => useDocumentVersions(scope, true));

    act(() => { void result.current.inspect("doc-a"); });
    act(() => { void result.current.inspect("doc-b"); });
    second.resolve({ items: [version("doc-b", 1)], count: 1 });
    await waitFor(() => expect(result.current.documentId).toBe("doc-b"));
    await waitFor(() => expect(result.current.versions[0]?.document_id).toBe("doc-b"));

    first.resolve({ items: [version("doc-a", 1)], count: 1 });
    await Promise.resolve();
    expect(result.current.versions[0]?.document_id).toBe("doc-b");
  });

  it("marks the 100-version boundary truncated", async () => {
    vi.mocked(api.fetchDocumentVersions).mockResolvedValue({ items: [version("doc-a", 1)], count: 100 });
    const { result } = renderHook(() => useDocumentVersions(scope, true)); await act(async () => { await result.current.inspect("doc-a"); }); expect(result.current.truncated).toBe(true);
  });

  it("clears document A and ignores its create completion after scope B", async () => {
    let resolveCreate!: (value: DocumentVersion) => void; const createPromise = new Promise<DocumentVersion>((resolve) => { resolveCreate = resolve; });
    vi.mocked(api.fetchDocumentVersions).mockImplementation(async (current, doc) => ({ items: [version(`${current.datasetId}-${doc}`, 1)], count: 1 })); vi.mocked(api.createDocumentVersion).mockReturnValue(createPromise);
    const scopeB = { tenantId: "tenant-b", datasetId: "dataset-b", actorToken: "token-b" };
    const { result, rerender } = renderHook(({ current }) => useDocumentVersions(current, true), { initialProps: { current: scope } });
    await act(async () => { await result.current.inspect("doc-a"); }); act(() => { void result.current.create({ expected_current_revision: 1, source_identity: "x", source_hash: "b".repeat(64) }); });
    rerender({ current: scopeB }); expect(result.current.documentId).toBe(""); expect(result.current.versions).toEqual([]); await waitFor(() => expect(result.current.documentId).toBe("")); resolveCreate(version("doc-a", 2)); await createPromise; await Promise.resolve(); expect(result.current.versions).toEqual([]);
  });


  it("auto-refreshes the head after create conflict without false success", async () => {
    vi.mocked(api.fetchDocumentVersions).mockResolvedValueOnce({items:[version("doc-a",1)],count:1}).mockResolvedValueOnce({items:[version("doc-a",2)],count:1});
    vi.mocked(api.createDocumentVersion).mockRejectedValue(new (await import("../../api/client")).ApiError("raw","http",409));
    const { result } = renderHook(() => useDocumentVersions(scope,true)); await act(async()=>{await result.current.inspect("doc-a");});
    let succeeded=true; await act(async()=>{succeeded=await result.current.create({expected_current_revision:1,source_identity:"x",source_hash:"b".repeat(64)});});
    expect(succeeded).toBe(false); expect(result.current.error?.kind).toBe("conflict"); expect(result.current.versions[0].revision).toBe(2);
  });

});
