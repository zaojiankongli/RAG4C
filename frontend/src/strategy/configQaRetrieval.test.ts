import { describe, expect, it } from "vitest";
import { STRATEGY_PATHS, STRATEGY_STAGES } from "./spec";

describe("config QA retrieval strategy surface", () => {
  it("exposes qa retrieval switch and thresholds in recall stage", () => {
    for (const path of [
      "pipeline.qa_retrieval_on",
      "pipeline.qa_match_min_score",
      "pipeline.qa_match_top_k",
    ]) {
      expect(STRATEGY_PATHS.has(path)).toBe(true);
    }
    const recall = STRATEGY_STAGES.flatMap((s) => s.groups).find((g) => g.key === "recall");
    const paths = recall?.items.map((i) => i.path) ?? [];
    expect(paths).toContain("pipeline.qa_retrieval_on");
  });
});
