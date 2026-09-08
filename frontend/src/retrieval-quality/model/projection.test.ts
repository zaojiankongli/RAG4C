import { describe, expect, it } from "vitest";
import type { Experiment, RunItem } from "./contracts";
import { abbreviateHash, projectExperiment, sanitizeDisplayText } from "./projection";

function experiment(overrides: Partial<Experiment> = {}): Experiment {
  return {
    sequence: 1,
    id: "exp-1",
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    query: "Where is the policy?",
    query_hash: "b".repeat(64),
    strategy_snapshot: { variant_name: "Strategy A", route_target: "auto", top_k: 8, dataset_serving_generation: 12 },
    result_snapshot: {
      dataset_serving_generation: 12,
      route: "hybrid",
      reranked: true,
      degraded: false,
      traces: ["search token=raw-secret", "Bearer abc.def"],
      results: [{
        rank: 1,
        document_id: "doc-1",
        chunk_id: "chunk-1",
        document_revision: 4,
        content_revision: 8,
        content_hash: "a".repeat(64),
        score: 0.875,
        branch: "hybrid",
        content: "Read https://user:pass@example.test/manual for details",
        api_token: "must-not-render",
      }],
    },
    evidence_lineage: { citations: [{ rank: 1, document_id: "doc-1", chunk_id: "chunk-1", document_revision: 4, content_revision: 8, content_hash: "a".repeat(64) }] },
    latency_ms: 37,
    status: "completed",
    created_by: "judge-a",
    created_at: "2026-08-25T00:00:00Z",
    run_id: "run-1",
    ...overrides,
  };
}

describe("retrieval quality safe projection", () => {
  it("projects allow-listed evidence, lineage, traces, and abbreviated hashes", () => {
    const view = projectExperiment(experiment() as RunItem);
    expect(view.state).toBe("completed");
    expect(view.evidence[0]).toMatchObject({ rank: 1, score: 0.875, branch: "hybrid", documentRevision: 4, contentRevision: 8, contentHash: "aaaaaaaaaaaa…" });
    expect(view.evidence[0].excerpt).not.toContain("user:pass");
    expect(view.traces.join(" ")).not.toContain("raw-secret");
    expect(view.traces.join(" ")).not.toContain("abc.def");
    expect(JSON.stringify(view)).not.toContain("api_token");
  });

  it("keeps failed and no-hit experiments truthful", () => {
    expect(projectExperiment(experiment({ status: "failed", result_snapshot: { results: [], failure_code: "retrieval_execution_failed" } })).state).toBe("failed");
    expect(projectExperiment(experiment({ result_snapshot: { results: [] } })).state).toBe("no-hit");
  });

  it("bounds display strings and refuses credential references", () => {
    expect(sanitizeDisplayText("vault://retrieval/secret", 100)).toBe("[已隐藏敏感引用]");
    expect(sanitizeDisplayText("x".repeat(30), 10)).toBe("xxxxxxxxxx…");
    expect(abbreviateHash("short")).toBe("short");
  });
});
