import { describe, expect, it } from "vitest";
import { phaseToStep } from "./phaseModel";

describe("phaseToStep", () => {
  it("shows retrieving_again as retrieval rather than answer generation", () => {
    expect(phaseToStep("retrieving_again")).toBe(0);
  });
});
