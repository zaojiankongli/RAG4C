// @vitest-environment node
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../quality-operations.css", import.meta.url), "utf8");

describe("Stage21 Quality Operations visual contract", () => {
  it("defines the Horizon Rail, enterprise evidence colors and dense work surface", () => {
    expect(css).toContain(".quality-operations__rail");
    expect(css).toContain("grid-template-columns: repeat(6");
    expect(css).toContain("--qo-blue: #0052d9");
    expect(css).toContain("--qo-red: #d54941");
    expect(css).toContain("--qo-green: #2ba471");
    expect(css).not.toContain("linear-gradient");
    expect(css).not.toContain("metric-card-wall");
  });

  it("provides real 768/320 mobile layouts, dark tokens and reduced motion", () => {
    expect(css).toContain("@media (max-width: 768px)");
    expect(css).toContain("@media (max-width: 320px)");
    expect(css).toContain('[data-theme="dark"] .quality-operations');
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
    expect(css).toContain("grid-template-columns: 1fr");
  });
});
