// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
const source = readFileSync(new URL("./ExperimentDetailDrawer.tsx", import.meta.url), "utf8");
describe("ExperimentDetailDrawer kept-alive safety", () => {
  it("uses the app Drawer boundary to avoid the native portal render loop", () => {
    expect(source).toContain('import { Drawer } from "../../ui"');
    expect(source).not.toMatch(/import \{[^}]*Drawer[^}]*\} from "tdesign-react"/);
  });
});
