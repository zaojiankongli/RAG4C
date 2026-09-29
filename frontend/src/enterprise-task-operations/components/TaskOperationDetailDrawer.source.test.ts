// Vitest reads source contracts in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./TaskOperationDetailDrawer.tsx", import.meta.url), "utf8");
const styleSource = readFileSync(new URL("../task-operations.css", import.meta.url), "utf8");

describe("TaskOperationDetailDrawer facade boundary", () => {
  it("uses the shared UI facade instead of importing TDesign controls", () => {
    expect(source).not.toContain('from "tdesign-react"');
    expect(source).toContain('from "../../ui"');
  });

  it("maps historical TDesign controls to the shared vocabulary", () => {
    expect(source).not.toContain('variant="text"');
    expect(source).not.toContain('variant="outline"');
    expect(source).toContain('type="text"');
    expect(source).toContain('type="default"');
    expect(source).toContain("danger");
    expect(source).toContain("<Spin");
  });

  it("keeps portal-safe Drawer styling at the component class boundary", () => {
    expect(styleSource).toContain(".task-operations__detail-drawer .rag-drawer-header");
    expect(styleSource).toContain(".task-operations__detail-drawer .rag-drawer-body");
  });
});
