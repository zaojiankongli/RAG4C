// @vitest-environment node
// @ts-expect-error -- test-only Node builtin without adding @types/node.

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./enterprise-approval.css", import.meta.url), "utf8");

describe("Stage 15 approval execution responsive CSS", () => {
  it("provides a generic change-facts grid for approval execution evidence", () => {
    expect(css).toContain("approval-execution-facts");
    expect(css).toContain("approval-execution-scope");
    expect(css).toMatch(/\.approval-execution-facts[^{]*\{[^}]*display:\s*grid/s);
    expect(css).not.toContain("data-ticket");
  });

  it("keeps generic execution facts usable at both 375px and 280px", () => {
    expect(css).toMatch(
      /@media\s*\(max-width:\s*375px\)[\s\S]*\.approval-execution-facts[^{]*\{[^}]*grid-template-columns:\s*1fr/s,
    );
    expect(css).toMatch(
      /@media\s*\(max-width:\s*280px\)[\s\S]*\.approval-execution-facts[^{]*\{[^}]*gap:/s,
    );
  });
});
