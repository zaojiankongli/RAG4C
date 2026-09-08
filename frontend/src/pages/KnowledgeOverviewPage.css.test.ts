// Vitest runs this CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./knowledge-overview.css", import.meta.url), "utf8");

describe("Knowledge Overview enterprise visual contracts", () => {
  it("uses a dedicated command-center surface instead of the legacy marketing hero", () => {
    expect(css).toContain(".knowledge-command-center");
    expect(css).toContain(".knowledge-command-header");
    expect(css).not.toContain(".knowledge-hero");
  });

  it("defines compact metric, lifeline, attention, asset and action layouts", () => {
    expect(css).toContain(".knowledge-metric-strip");
    expect(css).toContain(".knowledge-lifeline");
    expect(css).toContain(".knowledge-attention-list");
    expect(css).toContain(".knowledge-recent-list");
    expect(css).toContain(".knowledge-quick-actions");
    expect(css).toContain(".knowledge-data-quality");
  });

  it("visually distinguishes pending, warning and error lifeline evidence states", () => {
    expect(css).toContain(".knowledge-lifeline li.is-pending::before");
    expect(css).toContain(".knowledge-lifeline li.is-warning::before");
    expect(css).toContain(".knowledge-lifeline li.is-error::before");
  });

  it("has explicit responsive behavior for the enterprise workbench", () => {
    expect(css).toMatch(/@media\s*\(max-width:\s*900px\)/);
    expect(css).toMatch(/@media\s*\(max-width:\s*640px\)/);
  });

  it("preserves visible keyboard focus on interactive quick actions", () => {
    expect(css).toMatch(/\.knowledge-quick-action:focus-visible\s*\{[^}]*outline:/s);
  });
});
