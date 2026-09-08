// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import * as api from "../api/retrievalQualityApi";
import type { ExperimentDetail, RetrievalScope } from "../model/contracts";
import { useJudgmentMutation, type JudgmentDraft } from "./useJudgmentMutation";

vi.mock("../api/retrievalQualityApi");
const scope: RetrievalScope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "token", actorId: "judge-a" };
const draft: JudgmentDraft = { relevanceLabel: "relevant", score: 3, note: "strong" };
function detail(): ExperimentDetail { return { sequence: 1, id: "exp-1", tenant_id: "tenant-a", dataset_id: "dataset-a", query: "Q", query_hash: "a".repeat(64), strategy_snapshot: {}, result_snapshot: { results: [{ rank: 1, document_id: "doc-1", chunk_id: "chunk-1" }, { rank: 2, document_id: "doc-2", chunk_id: "chunk-2" }] }, evidence_lineage: { citations: [] }, latency_ms: 1, status: "completed", created_by: "runner", created_at: "2026-08-25T00:00:00Z", run_id: "run-1", judgments: [{ id: "owned", tenant_id: "tenant-a", dataset_id: "dataset-a", experiment_id: "exp-1", result_rank: 2, document_id: "doc-2", chunk_id: "chunk-2", relevance_label: "partial", score: 1, note: "old", revision: 4, created_by: "judge-a", created_at: "2026-08-25T00:00:00Z" }, { id: "foreign", tenant_id: "tenant-a", dataset_id: "dataset-a", experiment_id: "exp-1", result_rank: 1, document_id: "doc-1", chunk_id: "chunk-1", relevance_label: "irrelevant", score: 0, note: "foreign", revision: 2, created_by: "judge-b", created_at: "2026-08-25T00:00:00Z" }] }; }

beforeEach(() => vi.clearAllMocks());

describe("useJudgmentMutation", () => {
  it("creates when actor owns no rank judgment and patches owned changed fields with CAS", async () => {
    const refresh = vi.fn(async () => true);
    vi.mocked(api.createJudgment).mockResolvedValue({} as never); vi.mocked(api.patchJudgment).mockResolvedValue({} as never);
    const { result } = renderHook(() => useJudgmentMutation(scope, "judge-a", detail(), refresh));
    await act(() => result.current.save(1, draft));
    expect(api.createJudgment).toHaveBeenCalledWith(scope, "exp-1", { result_rank: 1, document_id: "doc-1", chunk_id: "chunk-1", relevance_label: "relevant", score: 3, note: "strong" }, expect.anything());
    await act(() => result.current.save(2, { relevanceLabel: "partial", score: 2, note: "updated" }));
    expect(api.patchJudgment).toHaveBeenCalledWith(scope, "exp-1", "owned", { expected_revision: 4, score: 2, note: "updated" }, expect.anything());
  });

  it("preserves the draft and refreshes authority after a CAS conflict", async () => {
    const refresh = vi.fn(async () => true);
    vi.mocked(api.patchJudgment).mockRejectedValue(new ApiError("conflict", "http", 409));
    const { result } = renderHook(() => useJudgmentMutation(scope, "judge-a", detail(), refresh));
    const next = { relevanceLabel: "relevant" as const, score: 3, note: "my draft" };
    await act(() => result.current.save(2, next));
    expect(result.current.conflictRanks).toContain(2);
    expect(result.current.draftFor(2)).toEqual(next);
    expect(refresh).toHaveBeenCalled();
  });
});
