// Vitest reads source contracts in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./KnowledgeServingCenter.tsx", import.meta.url), "utf8");
const tdesignImport = source.match(/import \{[\s\S]*?\} from "tdesign-react";/)?.[0] ?? "";

describe("KnowledgeServingCenter state notice facade boundary", () => {
  it("keeps Alert out of the direct TDesign import", () => {
    expect(tdesignImport).not.toMatch(/\bAlert\b/);
    expect(source).toContain('from "../../ui"');
  });
});
