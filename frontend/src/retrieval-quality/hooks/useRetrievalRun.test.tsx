// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api/retrievalQualityApi";
import type { RetrievalScope, RunResponse, RunRetrievalRequest } from "../model/contracts";
import { useRetrievalRun } from "./useRetrievalRun";

vi.mock("../api/retrievalQualityApi");
const scopeA: RetrievalScope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token-a", actorId: "judge-a" };
const scopeB: RetrievalScope = { tenantId: "tenant-b", datasetId: "dataset-b", actorToken: "token-b", actorId: "judge-b" };
const input: RunRetrievalRequest = { query: "Q", acl: [], variants: [{ name: "A", route_target: "auto", top_k: 8, hybrid_search_on: true, rerank_on: true, graph_retrieval_on: false, sentence_window_on: false, source_diversity: "off" }] };
const responseA: RunResponse = { run_id: "run-a", dataset_serving_generation: 1, items: [] };
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }

beforeEach(() => vi.clearAllMocks());

describe("useRetrievalRun", () => {
  it("fails closed without authenticated scope", async () => {
    const { result } = renderHook(() => useRetrievalRun(null));
    expect(result.current.status).toBe("scope-error");
    expect(await result.current.run(input)).toBe(false);
    expect(api.runRetrievalComparison).not.toHaveBeenCalled();
  });

  it("clears scope A synchronously and ignores its late completion after scope B", async () => {
    const first = deferred<RunResponse>();
    vi.mocked(api.runRetrievalComparison).mockReturnValue(first.promise);
    const { result, rerender } = renderHook(({ scope }) => useRetrievalRun(scope), { initialProps: { scope: scopeA as RetrievalScope | null } });
    act(() => { void result.current.run(input); });
    expect(result.current.status).toBe("running");
    rerender({ scope: scopeB });
    expect(result.current.response).toBeNull();
    expect(result.current.status).toBe("idle");
    first.resolve(responseA);
    await first.promise;
    await Promise.resolve();
    expect(result.current.response).toBeNull();
  });

  it("reports pending only while the POST remains active", async () => {
    const pending = deferred<RunResponse>();
    vi.mocked(api.runRetrievalComparison).mockReturnValue(pending.promise);
    const onPendingChange = vi.fn();
    const { result } = renderHook(() => useRetrievalRun(scopeA, onPendingChange));
    act(() => { void result.current.run(input); });
    expect(onPendingChange).toHaveBeenCalledWith(true);
    pending.resolve(responseA);
    await waitFor(() => expect(result.current.status).toBe("success"));
    expect(onPendingChange).toHaveBeenLastCalledWith(false);
  });
});
