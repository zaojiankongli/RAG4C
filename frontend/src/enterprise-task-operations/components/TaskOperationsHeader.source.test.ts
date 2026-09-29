// Vitest reads source contracts in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./TaskOperationsHeader.tsx", import.meta.url), "utf8");
const taskUiSource = readFileSync(new URL("./taskOperationsUi.tsx", import.meta.url), "utf8");

describe("TaskOperationsHeader facade boundary", () => {
  it("uses the shared UI facade instead of importing TDesign controls", () => {
    expect(source).not.toContain('from "tdesign-react"');
    expect(source).toContain('from "../../ui"');
    expect(taskUiSource).not.toContain('import { Tag } from "tdesign-react"');
    expect(taskUiSource).toContain('import { Tag } from "../../ui"');
  });

  it("maps historical TDesign button variants to facade vocabulary", () => {
    expect(source).not.toContain('variant="outline"');
    expect(source).not.toContain('variant="text"');
    expect(source).toContain('type="text"');
  });
});
