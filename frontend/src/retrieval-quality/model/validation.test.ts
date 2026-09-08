import { describe, expect, it } from "vitest";
import type { RetrievalVariantDraft } from "./contracts";
import {
  normalizeHistoryQuery,
  validateComposer,
} from "./validation";

function variant(overrides: Partial<RetrievalVariantDraft> = {}): RetrievalVariantDraft {
  return {
    clientId: "variant-1",
    name: "Strategy A",
    route_target: "auto",
    top_k: 8,
    hybrid_search_on: true,
    rerank_on: true,
    graph_retrieval_on: false,
    sentence_window_on: false,
    source_diversity: "off",
    ...overrides,
  };
}

describe("retrieval composer validation", () => {
  it("matches the strict run contract and strips UI-only fields", () => {
    const result = validateComposer({
      query: "  报销\n  流程  ",
      acl: [" legal ", "legal"],
      variants: [variant()],
    });

    expect(result).toEqual({
      ok: true,
      value: {
        query: "报销\n  流程",
        acl: ["legal", "legal"],
        variants: [{
          name: "Strategy A",
          route_target: "auto",
          top_k: 8,
          hybrid_search_on: true,
          rerank_on: true,
          graph_retrieval_on: false,
          sentence_window_on: false,
          source_diversity: "off",
        }],
      },
    });
  });

  it("normalizes history query exactly like the repository query hash", () => {
    expect(normalizeHistoryQuery("  ＲＡＧ\n  quality  ")).toBe("RAG quality");
  });

  it("rejects duplicate trimmed case-folded names", () => {
    const result = validateComposer({
      query: "Q",
      acl: [],
      variants: [variant({ clientId: "a", name: " A " }), variant({ clientId: "b", name: "a" })],
    });
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errors.variants?.[1]?.name).toContain("不能重复");
  });

  it.each([0, 5])("requires one through four variants, received %s", (count) => {
    const result = validateComposer({
      query: "Q",
      acl: [],
      variants: Array.from({ length: count }, (_, index) => variant({ clientId: String(index), name: `S${index}` })),
    });
    expect(result.ok).toBe(false);
  });

  it("enforces exact field bounds and safe ACL characters", () => {
    const result = validateComposer({
      query: " ",
      acl: ["legal'admin"],
      variants: [variant({ name: "x".repeat(65), top_k: 51 })],
    });
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.errors.query).toBeTruthy();
      expect(result.errors.acl?.[0]).toBeTruthy();
      expect(result.errors.variants?.[0]?.name).toBeTruthy();
      expect(result.errors.variants?.[0]?.top_k).toBeTruthy();
    }
  });
});
