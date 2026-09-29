// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("theme mode registry source contract", () => {
  it("keeps browser metadata and mode transitions out of the hook", () => {
    const source = readFileSync(new URL("./useThemeMode.ts", import.meta.url), "utf8");

    expect(source).not.toContain("BROWSER_THEME_COLOR");
    expect(source).not.toMatch(/previous\s*===\s*["']dark["']/);
    expect(source).toContain("getThemeModeSpec");
    expect(source).toContain("nextThemeMode");
    expect(source).toContain("parseStoredThemeMode");
  });
});
