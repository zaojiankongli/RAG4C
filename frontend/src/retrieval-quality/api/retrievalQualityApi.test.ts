import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../api/client";
import type { RetrievalScope, RunRetrievalRequest } from "../model/contracts";
import {
  createJudgment,
  fetchAgreement,
  fetchExperiment,
  fetchExperiments,
  hashNormalizedQuery,
  patchJudgment,
  runRetrievalComparison,
} from "./retrievalQualityApi";

vi.mock("../../api/client", () => ({ request: vi.fn() }));
const requestMock = vi.mocked(request);
const scope: RetrievalScope = { tenantId: "tenant a", datasetId: "dataset/a", actorToken: "signed-token", actorId: "judge-a" };
const headers = { "X-RAG4C-Tenant": "tenant a", Authorization: "Bearer signed-token" };
const payload: RunRetrievalRequest = {
  query: "Q", acl: ["legal"], variants: [{
    name: "A", route_target: "auto", top_k: 8, hybrid_search_on: true,
    rerank_on: false, graph_retrieval_on: false, sentence_window_on: false,
    source_diversity: "off",
  }],
};

beforeEach(() => requestMock.mockReset());

describe("retrieval quality API", () => {
  it("posts the approved run contract with authenticated scope", async () => {
    const signal = new AbortController().signal;
    requestMock.mockResolvedValue({ run_id: "run-1", dataset_serving_generation: 12, items: [] });
    await runRetrievalComparison(scope, payload, { signal });
    expect(requestMock).toHaveBeenCalledWith(
      "/api/knowledge-bases/dataset%2Fa/retrieval-experiments/run",
      { method: "POST", headers, body: JSON.stringify(payload), signal, timeoutMs: 120_000 },
    );
  });

  it("serializes only supported history filters with limit ten", async () => {
    requestMock.mockResolvedValue({ items: [], next_before_sequence: null });
    await fetchExperiments(scope, { status: "failed", runId: "run/1", queryHash: "a".repeat(64), beforeSequence: 42 });
    expect(requestMock).toHaveBeenCalledWith(
      `/api/knowledge-bases/dataset%2Fa/retrieval-experiments?status=failed&query_hash=${"a".repeat(64)}&run_id=run%2F1&before_sequence=42&limit=10`,
      { method: "GET", headers, signal: undefined },
    );
  });

  it("uses exact detail, judgment CAS, and agreement paths", async () => {
    requestMock.mockResolvedValue({});
    await fetchExperiment(scope, "exp/1");
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/retrieval-experiments/exp%2F1",
      { method: "GET", headers, signal: undefined },
    );
    await createJudgment(scope, "exp/1", { result_rank: 1, relevance_label: "relevant", score: 3, note: "strong" });
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/retrieval-experiments/exp%2F1/judgments",
      { method: "POST", headers, body: JSON.stringify({ result_rank: 1, relevance_label: "relevant", score: 3, note: "strong" }), signal: undefined },
    );
    await patchJudgment(scope, "exp/1", "judgment/1", { expected_revision: 4, note: "updated" });
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/retrieval-experiments/exp%2F1/judgments/judgment%2F1",
      { method: "PATCH", headers, body: JSON.stringify({ expected_revision: 4, note: "updated" }), signal: undefined },
    );
    await fetchAgreement(scope, "exp/1");
    expect(requestMock).toHaveBeenLastCalledWith(
      "/api/knowledge-bases/dataset%2Fa/retrieval-experiments/exp%2F1/agreement",
      { method: "GET", headers, signal: undefined },
    );
  });

  it("hashes the repository-normalized exact query", async () => {
    expect(await hashNormalizedQuery("  ＲＡＧ\n quality ")).toBe("46a4cd302e39ec8b45454bee92823b77c392618dc6252f2059f8f096e6673367");
  });
});

