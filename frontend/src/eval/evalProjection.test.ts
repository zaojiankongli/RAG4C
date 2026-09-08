import { describe, expect, it } from "vitest";
import type { CaseResult } from "../types/rag";
import {
  classifyEvalCase,
  filterEvalCases,
  summarizeEvalCases,
} from "./evalProjection";

function item(overrides: Partial<CaseResult>): CaseResult {
  return {
    id: "case",
    question: "问题",
    unanswerable: false,
    abstained: false,
    answered: true,
    groundedness: 0.9,
    relevance: 0.8,
    citations_ok: true,
    notes: "",
    ...overrides,
  };
}

const cases = [
  item({ id: "correct" }),
  item({ id: "over", abstained: true, answered: false, groundedness: null, relevance: null }),
  item({ id: "safe", unanswerable: true, abstained: true, answered: false, groundedness: null, relevance: null }),
  item({ id: "unsafe", unanswerable: true, answered: true, citations_ok: false, groundedness: 0.4, relevance: null }),
  item({ id: "incomplete", answered: false, abstained: false, groundedness: null, relevance: null }),
];

describe("evaluation projection", () => {
  it("classifies the answerability and abstention decision matrix", () => {
    expect(cases.map(classifyEvalCase)).toEqual([
      "correct_answer",
      "over_refusal",
      "safe_refusal",
      "unsafe_answer",
      "incomplete",
    ]);
  });

  it("summarizes decision counts and judge coverage without treating null as zero", () => {
    const summary = summarizeEvalCases(cases);

    expect(summary.matrix).toEqual({
      correctAnswer: 1,
      overRefusal: 1,
      safeRefusal: 1,
      unsafeAnswer: 1,
      incomplete: 1,
    });
    expect(summary.groundedness).toEqual({ scored: 2, eligible: 2, missing: 0, average: 0.65 });
    expect(summary.relevance).toEqual({ scored: 1, eligible: 2, missing: 1, average: 0.8 });
    expect(summary.citationCases).toEqual({ passed: 1, failed: 1, eligible: 2 });
  });

  it("filters cases by actionable failure category", () => {
    expect(filterEvalCases(cases, "unsafe_answer").map((entry) => entry.id)).toEqual(["unsafe"]);
    expect(filterEvalCases(cases, "judge_missing").map((entry) => entry.id)).toEqual(["unsafe"]);
    expect(filterEvalCases(cases, "citation_failed").map((entry) => entry.id)).toEqual(["unsafe"]);
  });
});
