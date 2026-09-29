// Vitest reads source contracts in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./TaskMutationDialog.tsx", import.meta.url), "utf8");
const styleSource = readFileSync(new URL("../task-operations.css", import.meta.url), "utf8");

describe("TaskMutationDialog facade boundary", () => {
  it("uses the shared UI facade instead of importing TDesign controls", () => {
    expect(source).not.toContain('from "tdesign-react"');
    expect(source).toContain('from "../../ui"');
  });

  it("keeps the destructive confirmation boundary on shared controls", () => {
    expect(source).toContain("<Dialog");
    expect(source).toContain("<Checkbox");
    expect(source).toContain("<Tag");
  });

  it("keeps body-attached dialog content on the task surface style boundary", () => {
    expect(styleSource).toContain(".task-operations__mutation-dialog .rag-dialog-body");
  });
});
