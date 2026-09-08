// @vitest-environment jsdom
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api/retrievalQualityApi";
import type { Agreement, ExperimentDetail, RetrievalScope } from "../model/contracts";
import { useExperimentDetail } from "./useExperimentDetail";

vi.mock("../api/retrievalQualityApi");
const scope: RetrievalScope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token", actorId: "judge-a" };
function detail(id: string): ExperimentDetail { return { sequence: 1, id, tenant_id: "tenant-a", dataset_id: "dataset-a", query: "Q", query_hash: "a".repeat(64), strategy_snapshot: {}, result_snapshot: { results: [] }, evidence_lineage: { citations: [] }, latency_ms: 1, status: "completed", created_by: "judge-a", created_at: "2026-08-25T00:00:00Z", run_id: "run-1", judgments: [] }; }
const agreement: Agreement = { experiment_id: "exp-b", judged_results: 0, judgment_count: 0, multi_judged_results: 0, unanimous_results: 0, conflicting_results: 0, exact_agreement_rate: null, label_counts: {}, mean_score: null };
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }

beforeEach(() => vi.clearAllMocks());

describe("useExperimentDetail", () => {
  it("ignores experiment A after selection changes to B", async () => {
    const a = deferred<ExperimentDetail>(); const b = deferred<ExperimentDetail>();
    vi.mocked(api.fetchExperiment).mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
    vi.mocked(api.fetchAgreement).mockResolvedValue(agreement);
    const { result, rerender } = renderHook(({ id }) => useExperimentDetail(scope, id), { initialProps: { id: "exp-a" as string | null } });
    rerender({ id: "exp-b" });
    b.resolve(detail("exp-b"));
    await waitFor(() => expect(result.current.detail?.id).toBe("exp-b"));
    a.resolve(detail("exp-a")); await a.promise; await Promise.resolve();
    expect(result.current.detail?.id).toBe("exp-b");
  });

  it("starts detail and agreement together", async () => {
    const d = deferred<ExperimentDetail>(); const a = deferred<Agreement>();
    vi.mocked(api.fetchExperiment).mockReturnValue(d.promise); vi.mocked(api.fetchAgreement).mockReturnValue(a.promise);
    renderHook(() => useExperimentDetail(scope, "exp-b"));
    expect(api.fetchExperiment).toHaveBeenCalled(); expect(api.fetchAgreement).toHaveBeenCalled();
    d.resolve(detail("exp-b")); a.resolve(agreement);
  });
});
