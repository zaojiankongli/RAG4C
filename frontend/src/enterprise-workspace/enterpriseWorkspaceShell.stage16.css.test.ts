// Vitest runs this shared-shell CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

describe("Stage16 narrow Workspace shell", () => {
  it("keeps the shared demo banner horizontal and readable at 280px", () => {
    expect(css).toMatch(
      /@media \(max-width: 375px\)[\s\S]*\.mode-banner\s*\{[^}]*flex-wrap:\s*wrap/s,
    );
    expect(css).toMatch(
      /\.mode-banner \.mode-tag,[\s\S]*\.mode-banner \.t-button\s*\{[^}]*flex:\s*0 0 auto/s,
    );
    expect(css).toMatch(/\.mode-banner > \.t-typography\s*\{[^}]*overflow-wrap:\s*anywhere/s);
  });
});
