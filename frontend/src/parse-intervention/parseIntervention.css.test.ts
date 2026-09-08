// @vitest-environment node
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./parse-intervention.css", import.meta.url), "utf8");

describe("parse intervention CSS contract", () => {
  it("contains desktop grid and horizontal containment", () => {
    expect(css).toMatch(/grid-template-columns:\s*minmax\(260px/);
    expect(css).toMatch(/overflow-x:\s*hidden/);
    expect(css).toMatch(/min-width:\s*0/);
  });
  it("contains honest mobile tabs, focus, and reduced motion rules", () => {
    expect(css).toMatch(/@media\s*\(max-width:\s*720px\)/);
    expect(css).toMatch(/:focus-visible/);
    expect(css).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/);
    expect(css).toMatch(/\.parse-editor-pane \.t-alert--info \.t-alert__description/);
  });
});
