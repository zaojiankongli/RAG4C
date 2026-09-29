// Vitest runs this CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

describe("native Statistic/Skeleton visual contracts", () => {
  it("defines readable metric and skeleton surfaces", () => {
    expect(css).toContain(".rag-statistic");
    expect(css).toContain(".rag-statistic-content");
    expect(css).toContain(".rag-skeleton");
    expect(css).toContain(".rag-skeleton-line");
  });

  it("disables skeleton motion when reduced motion is requested", () => {
    expect(css).toMatch(
      /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*\.rag-skeleton-line[\s\S]*animation:\s*none/s,
    );
  });

  it("disables animation for an explicitly inactive skeleton", () => {
    expect(css).toMatch(
      /\.rag-skeleton:not\(\.is-active\)\s+\.rag-skeleton-line\s*\{[^}]*animation:\s*none/s,
    );
  });
});
