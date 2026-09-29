// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
const css = readFileSync(new URL("./retrieval-quality.css", import.meta.url), "utf8");
describe("retrieval quality responsive and accessibility CSS", () => {
  it("defines desktop alignment, mobile tabs, focus, dark mode, and reduced motion", () => {
    expect(css).toContain("--rq-variant-count");
    expect(css).toContain("@media (max-width: 600px)");
    expect(css).toContain(":focus-visible");
    expect(css).toContain('html[data-theme="dark"]');
    expect(css).toContain("prefers-reduced-motion: reduce");
    expect(css).toContain(".rq-composer-actions > .rag-space");
    expect(css).toContain(".rq-composer-actions .rag-button");
  });
});
