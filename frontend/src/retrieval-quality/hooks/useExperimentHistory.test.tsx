// @vitest-environment jsdom
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api/retrievalQualityApi";
import type { Experiment, RetrievalScope } from "../model/contracts";
import { useExperimentHistory } from "./useExperimentHistory";

vi.mock("../api/retrievalQualityApi");
const scope: RetrievalScope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token", actorId: "judge-a" };
function experiment(sequence: number): Experiment { return { sequence, id: `exp-${sequence}`, tenant_id: "tenant-a", dataset_id: "dataset-a", query: "Q", query_hash: "a".repeat(64), strategy_snapshot: {}, result_snapshot: { results: [] }, evidence_lineage: { citations: [] }, latency_ms: 1, status: "completed", created_by: "judge-a", created_at: "2026-08-25T00:00:00Z", run_id: "run-1" }; }

beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.hashNormalizedQuery).mockResolvedValue("h".repeat(64)); });
afterEach(() => vi.useRealTimers());

describe("useExperimentHistory", () => {
  it("uses next_before_sequence and a local previous cursor stack", async () => {
    const first = [experiment(12), experiment(11)]; const second = [experiment(10)];
    vi.mocked(api.fetchExperiments).mockResolvedValueOnce({ items: first, next_before_sequence: 11 }).mockResolvedValueOnce({ items: second, next_before_sequence: null }).mockResolvedValueOnce({ items: first, next_before_sequence: 11 });
    const { result } = renderHook(() => useExperimentHistory(scope, true, false));
    await waitFor(() => expect(result.current.items).toEqual(first));
    await act(() => result.current.nextPage());
    expect(api.fetchExperiments).toHaveBeenLastCalledWith(scope, expect.objectContaining({ beforeSequence: 11 }), expect.anything());
    await act(() => result.current.previousPage());
    expect(api.fetchExperiments).toHaveBeenLastCalledWith(scope, expect.not.objectContaining({ beforeSequence: 11 }), expect.anything());
  });

  it("resets cursors and hashes exact query filters", async () => {
    vi.mocked(api.fetchExperiments).mockResolvedValue({ items: [], next_before_sequence: null });
    const { result } = renderHook(() => useExperimentHistory(scope, true, false));
    await waitFor(() => expect(api.fetchExperiments).toHaveBeenCalled());
    await act(() => result.current.applyFilters({ status: "failed", runId: "run-2", query: " ＲＡＧ quality " }));
    expect(api.hashNormalizedQuery).toHaveBeenCalledWith(" ＲＡＧ quality ");
    expect(api.fetchExperiments).toHaveBeenLastCalledWith(scope, { status: "failed", runId: "run-2", query: " ＲＡＧ quality ", queryHash: "h".repeat(64), beforeSequence: undefined }, expect.anything());
    expect(result.current.hasPrevious).toBe(false);
  });

  it("polls only while a run request is pending", async () => {
    vi.useFakeTimers();
    vi.mocked(api.fetchExperiments).mockResolvedValue({ items: [], next_before_sequence: null });
    const { rerender } = renderHook(({ pending }) => useExperimentHistory(scope, true, pending), { initialProps: { pending: false } });
    await act(async () => { await Promise.resolve(); });
    const initial = vi.mocked(api.fetchExperiments).mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(5_000); await Promise.resolve(); });
    expect(api.fetchExperiments).toHaveBeenCalledTimes(initial);
    rerender({ pending: true });
    await act(async () => { vi.advanceTimersByTime(2_100); await Promise.resolve(); });
    expect(vi.mocked(api.fetchExperiments).mock.calls.length).toBeGreaterThan(initial);
  });
});
