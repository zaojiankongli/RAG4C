// Vitest runs this source contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./RetrievalComposer.tsx", import.meta.url), "utf8");

describe("RetrievalComposer facade adoption contracts", () => {
  it("uses the shared facade and nested TextArea adapter", () => {
    expect(source).toContain('from "../../ui"');
    expect(source).not.toContain('from "tdesign-react"');
    expect(source).toContain("Input.TextArea");
    expect(source).not.toContain("<Textarea");
  });

  it("uses event-shaped input values and facade button vocabulary", () => {
    expect(source).toContain("target?.value");
    expect(source).toContain('type="text"');
    expect(source).toContain('type="primary"');
    expect(source).not.toContain('variant="outline"');
    expect(source).not.toContain('variant="text"');
    expect(source).not.toContain('theme="primary"');
    expect(source).toContain("maxLength={20000}");
    expect(source).toContain("autoSize={{ minRows: 3, maxRows: 8 }}");
  });
});
