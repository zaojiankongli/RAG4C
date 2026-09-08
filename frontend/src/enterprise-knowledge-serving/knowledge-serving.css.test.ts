// @vitest-environment node
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./knowledge-serving.css", import.meta.url), "utf8") as string;

describe("Stage26 serving visual contract", () => {
  it("defines the signature rail, governance strip, dense table and constrained mobile surfaces", () => {
    expect(css).toContain(".knowledge-serving__rail");
    expect(css).toContain(".knowledge-serving__evidence-strip");
    expect(css).toContain(".knowledge-serving__desktop-table");
    expect(css).toContain("@media (max-width: 375px)");
    expect(css).toContain("@media (max-width: 280px)");
    expect(css).toContain("prefers-reduced-motion");
    expect(css).toContain('[data-theme="dark"]');
  });
});
