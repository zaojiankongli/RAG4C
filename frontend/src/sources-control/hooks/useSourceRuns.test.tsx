// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api/sourcesApi";
import type { SourceRecord, SourceRun, SourceScope } from "../model/sourceModels";
import { useSourceRuns } from "./useSourceRuns";

vi.mock("../api/sourcesApi");
const scope: SourceScope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "signed" };
const source: SourceRecord = { id: "source-a", tenant_id: "tenant-a", dataset_id: "dataset-a", name: "Docs", kind: "local_dir", config: { path: "C:/docs" }, metadata: {}, status: "active", generation: 2, last_cursor: {}, last_result: {}, last_error: "", last_sync_at: null, created_at: "2026-08-25T00:00:00Z", updated_at: "2026-08-25T00:00:00Z" };
const run: SourceRun = { id: "run-a", source_id: "source-a", tenant_id: "tenant-a", dataset_id: "dataset-a", status: "failed", trigger: "manual", force_full: false, dry_run: false, source_generation: 2, dataset_generation: 9, retry_of_run_id: null, execution_state: "completed", execution_attempts: 2, execution_last_error: "", execution_finished_at: "2026-08-25T00:00:04Z", cursor_before: {}, cursor_after: {}, counts: { fetched: 1, ingested: 0, skipped: 0, removed: 0, pending_deletes: 0, chunks: 0, failed: 1 }, fetch_error: "safe failure", duration_ms: 4000, started_at: "2026-08-25T00:00:00Z", finished_at: "2026-08-25T00:00:04Z" };

afterEach(() => { vi.useRealTimers(); Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); });

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.fetchRuns).mockResolvedValue({ items: [run], count: 1, next_cursor: "next_cursor_123456" });
  vi.mocked(api.fetchRun).mockResolvedValue(run);
  vi.mocked(api.fetchRunItems).mockResolvedValue({ items: [], count: 0, next_cursor: null });
});

describe("useSourceRuns", () => {
  it("uses ten-row keyset pages and keeps previous cursors locally", async () => {
    const view = renderHook(() => useSourceRuns(scope, source, true));
    await waitFor(() => expect(view.result.current.status).toBe("ready"));
    expect(api.fetchRuns).toHaveBeenCalledWith(scope, "source-a", expect.objectContaining({ limit: 10, cursor: null }), expect.anything());
    await act(() => view.result.current.nextPage());
    expect(api.fetchRuns).toHaveBeenLastCalledWith(scope, "source-a", expect.objectContaining({ cursor: "next_cursor_123456", limit: 10 }), expect.anything());
    await act(() => view.result.current.previousPage());
    expect(api.fetchRuns).toHaveBeenLastCalledWith(scope, "source-a", expect.objectContaining({ cursor: null, limit: 10 }), expect.anything());
  });

  it("loads detail/items and retries one current-generation failed run with replay truth", async () => {
    vi.mocked(api.retryRun).mockResolvedValue({ run_id: "retry-a", source_id: "source-a", status: "running", trigger: "retry", retry_of_run_id: "run-a", execution_state: "pending", replayed: true });
    const view = renderHook(() => useSourceRuns(scope, source, true));
    await waitFor(() => expect(view.result.current.status).toBe("ready"));
    await act(() => view.result.current.selectRun(run));
    expect(api.fetchRunItems).toHaveBeenCalledWith(scope, "source-a", "run-a", expect.objectContaining({ limit: 10 }), expect.anything());
    await act(() => view.result.current.retry(run));
    expect(api.retryRun).toHaveBeenCalledTimes(1);
    expect(view.result.current.lastAccepted?.replayed).toBe(true);
  });

  it("does not poll a hidden or inactive kept-alive page", async () => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    const activeRun = { ...run, status: "running" as const, execution_state: "pending" as const };
    vi.mocked(api.fetchRuns).mockResolvedValue({ items: [activeRun], count: 1, next_cursor: null });
    const hidden = renderHook(() => useSourceRuns(scope, source, true));
    await waitFor(() => expect(hidden.result.current.status).toBe("ready"));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.fetchRuns).toHaveBeenCalledTimes(1);
    hidden.unmount();

    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    vi.mocked(api.fetchRuns).mockClear();
    const inactive = renderHook(() => useSourceRuns(scope, source, false));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.fetchRuns).not.toHaveBeenCalled();
    inactive.unmount();
  });
  it("gates old runs and open detail on the first source-scope change and aborts requests", async () => {
    const view = renderHook(({ currentScope, currentSource }) => useSourceRuns(currentScope, currentSource, true), { initialProps: { currentScope: scope, currentSource: source } });
    await waitFor(() => expect(view.result.current.status).toBe("ready"));
    await act(() => view.result.current.selectRun(run));
    expect(view.result.current.selectedRun?.id).toBe("run-a");
    let resolveDetail!: (value: SourceRun) => void;
    let resolveItems!: (value: { items: []; count: number; next_cursor: null }) => void;
    vi.mocked(api.fetchRun).mockReturnValueOnce(new Promise((resolve) => { resolveDetail = resolve; }));
    vi.mocked(api.fetchRunItems).mockReturnValueOnce(new Promise((resolve) => { resolveItems = resolve; }));
    void view.result.current.selectRun(run);
    await waitFor(() => expect(api.fetchRun).toHaveBeenCalledTimes(2));
    const oldDetailSignal = vi.mocked(api.fetchRun).mock.calls[1][3]?.signal as AbortSignal;
    const nextSource = { ...source, id: "source-b", name: "Other", generation: 1 };
    view.rerender({ currentScope: { tenantId: "tenant-b", datasetId: "dataset-b", actorToken: "signed-b" }, currentSource: nextSource });
    expect(view.result.current.runs).toEqual([]);
    expect(view.result.current.selectedRun).toBeNull();
    expect(view.result.current.items).toEqual([]);
    await waitFor(() => expect(oldDetailSignal.aborted).toBe(true));
    resolveDetail(run); resolveItems({ items: [], count: 0, next_cursor: null });
  });
  it("loads items with the newly selected filters instead of stale closure values", async () => {
    const view = renderHook(() => useSourceRuns(scope, source, true));
    await waitFor(() => expect(view.result.current.status).toBe("ready"));
    await act(() => view.result.current.selectRun(run));
    await act(async () => { view.result.current.setItemFilters({ result: "failed", action: "delete" }); });
    await waitFor(() => expect(api.fetchRunItems).toHaveBeenLastCalledWith(scope, "source-a", "run-a", expect.objectContaining({ result: "failed", action: "delete", cursor: null, limit: 10 }), expect.anything()));
  });

  it("commits cursor history only after success and locks rapid navigation", async () => {
    const view = renderHook(() => useSourceRuns(scope, source, true));
    await waitFor(() => expect(view.result.current.status).toBe("ready"));
    let reject!: (reason: unknown) => void;
    vi.mocked(api.fetchRuns).mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail; }));
    let first!: Promise<boolean>; let second!: Promise<boolean>;
    act(() => { first = view.result.current.nextPage(); second = view.result.current.nextPage(); });
    expect(api.fetchRuns).toHaveBeenCalledTimes(2);
    reject(new Error("page failed"));
    await act(async () => { await Promise.all([first, second]); });
    expect(view.result.current.hasPrevious).toBe(false);
    expect(view.result.current.paging).toBe(false);
  });

  it("blocks retry for disabled sources and stale source generations", async () => {
    const disabled = { ...source, status: "disabled" as const };
    const disabledView = renderHook(() => useSourceRuns(scope, disabled, true));
    await waitFor(() => expect(disabledView.result.current.status).toBe("ready"));
    expect(await disabledView.result.current.retry(run)).toBe(false);
    const currentView = renderHook(() => useSourceRuns(scope, source, true));
    await waitFor(() => expect(currentView.result.current.status).toBe("ready"));
    expect(await currentView.result.current.retry({ ...run, source_generation: 1 })).toBe(false);
    expect(api.retryRun).not.toHaveBeenCalled();
  });

  it("polls active runs, reconciles an open detail and reloads its items", async () => {
    vi.useFakeTimers();
    const activeRun = { ...run, status: "running" as const, execution_state: "pending" as const };
    const completedRun = { ...activeRun, status: "completed" as const, execution_state: "completed" as const };
    vi.mocked(api.fetchRuns).mockResolvedValueOnce({ items: [activeRun], count: 1, next_cursor: null }).mockResolvedValueOnce({ items: [completedRun], count: 1, next_cursor: null });
    vi.mocked(api.fetchRun).mockResolvedValueOnce(activeRun).mockResolvedValueOnce(completedRun);
    const view = renderHook(() => useSourceRuns(scope, source, true));
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(view.result.current.runs[0]?.execution_state).toBe("pending");
    await act(async () => { await view.result.current.selectRun(activeRun); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); await Promise.resolve(); });
    expect(api.fetchRun).toHaveBeenCalledTimes(2);
    expect(api.fetchRunItems).toHaveBeenCalledTimes(2);
    expect(view.result.current.selectedRun?.execution_state).toBe("completed");
  });
});
