// Vitest runs this CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

describe("compatibility control visual contracts", () => {
  it("gives fallback tooltips a positioned readable surface", () => {
    expect(css).toContain(".rag-tooltip-trigger");
    expect(css).toContain(".rag-tooltip-content");
    expect(css).toMatch(/\.rag-tooltip-content\s*\{[^}]*position:\s*absolute/s);
  });

  it("renders fallback tag states with semantic colors", () => {
    expect(css).toContain(".rag-tag.is-success");
    expect(css).toContain(".rag-tag.is-danger");
    expect(css).toContain(".rag-tag.is-warning");
    expect(css).toContain(".rag-tag.is-primary");
  });

  it("distinguishes successful and failed fallback progress bars", () => {
    expect(css).toContain(".rag-progress.is-success > span");
    expect(css).toContain(".rag-progress.is-error > span");
  });
});
