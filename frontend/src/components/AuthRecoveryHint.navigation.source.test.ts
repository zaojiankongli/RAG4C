// Vitest runs this contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./AuthRecoveryHint.tsx", import.meta.url), "utf8");

describe("AuthRecoveryHint navigation seam", () => {
  it("delegates Config navigation to the shared adapter", () => {
    expect(source).toContain('navigationIntent(window.location, "config")');
    expect(source).toContain("commitNavigationIntent");
    expect(source).not.toMatch(/window\.location\.hash\s*=/);
  });
});
