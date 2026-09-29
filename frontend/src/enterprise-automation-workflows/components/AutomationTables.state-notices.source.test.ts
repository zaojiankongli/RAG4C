// Vitest reads source contracts in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./AutomationTables.tsx", import.meta.url), "utf8");
const tdesignImport = source.match(/import \{[\s\S]*?\} from "tdesign-react";/)?.[0] ?? "";

describe("AutomationTables state notice facade boundary", () => {
  it("does not keep Alert or Loading in the direct TDesign import", () => {
    expect(tdesignImport).not.toMatch(/\bAlert\b/);
    expect(tdesignImport).not.toMatch(/\bLoading\b/);
    expect(source).toContain('from "../../ui"');
    expect(source).toContain("<Spin");
  });
});
