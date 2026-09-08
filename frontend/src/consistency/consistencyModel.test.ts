import { describe, expect, it } from "vitest";
import {
  projectConsistencySummary,
  type ConsistencySummaryResponse,
} from "./consistencyModel";

const summary: ConsistencySummaryResponse = {
  mode: "report-only",
  best_effort: true,
  counts: {
    documents_scanned: 6,
    authoritative_heads: 8,
    projection_chunks: 13,
    missing_chunks: 3,
    stale_chunks: 2,
    orphaned_chunks: 4,
    blocked_documents: 1,
  },
  drift_categories: {
    missing: 3,
    stale: 2,
    orphaned: 4,
    blocked: 1,
    stale_reasons: { revision_mismatch: 2 },
  },
  manifest_ref: "ref-manifest",
  complete: false,
  confirmable: false,
  snapshot_guarantee: "catalog_only",
  has_drift: true,
};

describe("consistency summary projection", () => {
  it("projects desired, observed projection, and drift without upgrading catalog-only authority", () => {
    expect(projectConsistencySummary(summary)).toEqual({
      desired: 8,
      projection: 13,
      drift: 10,
      categories: [
        { key: "missing", label: "缺失", count: 3 },
        { key: "stale", label: "过期", count: 2 },
        { key: "orphaned", label: "孤儿", count: 4 },
        { key: "blocked", label: "阻塞", count: 1 },
      ],
      bestEffort: true,
      catalogOnly: true,
      complete: false,
      confirmable: false,
    });
  });
  it("stays defensive when an older fixture omits best_effort", () => {
    const legacy = { ...summary, best_effort: undefined } as unknown as ConsistencySummaryResponse;
    expect(projectConsistencySummary(legacy).bestEffort).toBe(false);
  });

});
