import type { CaseResult } from "../types/rag";

export type EvalCaseCategory =
  | "correct_answer"
  | "over_refusal"
  | "safe_refusal"
  | "unsafe_answer"
  | "incomplete";

export type EvalCaseFilter =
  | "all"
  | EvalCaseCategory
  | "judge_missing"
  | "citation_failed";

export interface CoverageSummary {
  scored: number;
  eligible: number;
  missing: number;
  average: number | null;
}

export interface EvalCaseSummary {
  total: number;
  answerable: number;
  unanswerable: number;
  answered: number;
  abstained: number;
  matrix: {
    correctAnswer: number;
    overRefusal: number;
    safeRefusal: number;
    unsafeAnswer: number;
    incomplete: number;
  };
  groundedness: CoverageSummary;
  relevance: CoverageSummary;
  citationCases: { passed: number; failed: number; eligible: number };
}

export function classifyEvalCase(entry: CaseResult): EvalCaseCategory {
  if (entry.unanswerable && entry.answered) return "unsafe_answer";
  if (entry.unanswerable && entry.abstained) return "safe_refusal";
  if (!entry.unanswerable && entry.abstained) return "over_refusal";
  if (!entry.unanswerable && entry.answered) return "correct_answer";
  return "incomplete";
}

function coverage(cases: CaseResult[], field: "groundedness" | "relevance"): CoverageSummary {
  const eligible = cases.filter((entry) => entry.answered && !entry.abstained);
  const scores = eligible
    .map((entry) => entry[field])
    .filter((value): value is number => value !== null);
  return {
    scored: scores.length,
    eligible: eligible.length,
    missing: eligible.length - scores.length,
    average: scores.length ? scores.reduce((sum, value) => sum + value, 0) / scores.length : null,
  };
}

export function summarizeEvalCases(cases: CaseResult[]): EvalCaseSummary {
  const categories = cases.map(classifyEvalCase);
  const eligibleCitations = cases.filter((entry) => entry.answered && !entry.abstained);
  return {
    total: cases.length,
    answerable: cases.filter((entry) => !entry.unanswerable).length,
    unanswerable: cases.filter((entry) => entry.unanswerable).length,
    answered: cases.filter((entry) => entry.answered).length,
    abstained: cases.filter((entry) => entry.abstained).length,
    matrix: {
      correctAnswer: categories.filter((category) => category === "correct_answer").length,
      overRefusal: categories.filter((category) => category === "over_refusal").length,
      safeRefusal: categories.filter((category) => category === "safe_refusal").length,
      unsafeAnswer: categories.filter((category) => category === "unsafe_answer").length,
      incomplete: categories.filter((category) => category === "incomplete").length,
    },
    groundedness: coverage(cases, "groundedness"),
    relevance: coverage(cases, "relevance"),
    citationCases: {
      passed: eligibleCitations.filter((entry) => entry.citations_ok).length,
      failed: eligibleCitations.filter((entry) => !entry.citations_ok).length,
      eligible: eligibleCitations.length,
    },
  };
}

export function filterEvalCases(cases: CaseResult[], filter: EvalCaseFilter): CaseResult[] {
  if (filter === "all") return cases;
  if (filter === "judge_missing") {
    return cases.filter(
      (entry) =>
        entry.answered &&
        !entry.abstained &&
        (entry.groundedness === null || entry.relevance === null),
    );
  }
  if (filter === "citation_failed") {
    return cases.filter((entry) => entry.answered && !entry.abstained && !entry.citations_ok);
  }
  return cases.filter((entry) => classifyEvalCase(entry) === filter);
}
