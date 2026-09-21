import { describe, expect, it } from "vitest";
import {
  normalizeQaAuthority,
  projectConsistencySummary,
  type ConsistencySummaryResponse,
} from "./consistencyModel";

const summary: ConsistencySummaryResponse = {
  mode: "report-only",
  best_effort: true,
  counts: {
    documents_scanned: 12,
    authoritative_heads: 40,
    projection_chunks: 35,
    missing_chunks: 3,
    stale_chunks: 2,
    orphaned_chunks: 1,
    blocked_documents: 4,
  },
  drift_categories: {
    missing: 3,
    stale: 2,
    orphaned: 1,
    blocked: 4,
    stale_reasons: { revision_lag: 2 },
  },
  manifest_ref: "ref-abc",
  complete: false,
  confirmable: false,
  snapshot_guarantee: "catalog_only",
  has_drift: true,
  qa_authority: {
    total: 9,
    effective_retrieval: 4,
    pending_review: 2,
    rejected: 1,
    expired: 1,
    retrieval_disabled: 1,
    note: "QA 权威来自 MySQL Catalog",
  },
};

describe("consistency summary projection", () => {
  it("projects document drift categories and desired/projection counts", () => {
    expect(projectConsistencySummary(summary)).toMatchObject({
      desired: 40,
      projection: 35,
      drift: 10,
      bestEffort: true,
      catalogOnly: true,
      complete: false,
      confirmable: false,
      categories: [
        { key: "missing", label: "缺失", count: 3 },
        { key: "stale", label: "过期", count: 2 },
        { key: "orphaned", label: "孤儿", count: 1 },
        { key: "blocked", label: "阻塞", count: 4 },
      ],
    });
  });

  it("projects qa authority when present", () => {
    const projection = projectConsistencySummary(summary);
    expect(projection.qaAuthority).toMatchObject({
      total: 9,
      effective_retrieval: 4,
      pending_review: 2,
    });
  });

  it("returns null qa authority when missing or invalid (no fake zeros)", () => {
    const legacy = { ...summary, qa_authority: undefined } as ConsistencySummaryResponse;
    expect(projectConsistencySummary(legacy).qaAuthority).toBeNull();
    expect(normalizeQaAuthority({ note: "x" })).toBeNull();
    expect(normalizeQaAuthority({ total: 3, effective_retrieval: 1 })).toBeNull();
  });

  it("tolerates legacy payloads without best_effort", () => {
    const legacy = { ...summary, best_effort: undefined } as unknown as ConsistencySummaryResponse;
    expect(projectConsistencySummary(legacy).bestEffort).toBe(false);
  });
});
