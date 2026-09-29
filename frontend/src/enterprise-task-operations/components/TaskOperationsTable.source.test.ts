// Vitest reads source contracts in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./TaskOperationsTable.tsx", import.meta.url), "utf8");
const styleSource = readFileSync(new URL("../task-operations.css", import.meta.url), "utf8");

describe("TaskOperationsTable facade boundary", () => {
  it("uses the shared UI facade instead of importing TDesign controls", () => {
    expect(source).not.toContain('from "tdesign-react"');
    expect(source).toContain('from "../../ui"');
  });

  it("uses the renderer-independent table contract", () => {
    expect(source).toContain("Table");
    expect(source).not.toContain("PrimaryTable");
    expect(source).not.toContain("PrimaryTableCol");
    expect(source).toContain('size="small"');
    expect(source).toContain('verticalAlign="top"');
  });

  it("maps historical TDesign action props to facade vocabulary", () => {
    expect(source).not.toContain('variant="text"');
    expect(source).not.toContain('variant="outline"');
    expect(source).not.toContain('theme="danger"');
    expect(source).toContain('type="text"');
    expect(source).toContain("danger");
  });

  it("keeps native and TDesign button renderer selectors in the mobile action rule", () => {
    expect(styleSource).toContain(".task-operations__row-actions .t-button");
    expect(styleSource).toContain(".task-operations__row-actions .rag-button");
  });
});
