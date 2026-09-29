// Vitest runs this source contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { uiRendererComponentNames } from "./rendererPolicy";

const source = readFileSync(new URL("./index.tsx", import.meta.url), "utf8");

describe("UI renderer Adapter adoption", () => {
  it("removes the parameterless global renderer branch", () => {
    expect(source).toContain('import { uiRendererAdapter } from "./rendererPolicy";');
    expect(source).not.toContain("const canRenderTDesign = () => false");
    expect(source).not.toMatch(/canRenderTDesign\(\)/);
  });

  it("keys every conditional renderer decision to a declared registry component", () => {
    const callKeys = [
      ...source.matchAll(/uiRendererAdapter\.useTDesign\("([^"]+)"\)/g),
    ].map((match) => match[1]);

    expect(new Set(callKeys)).toEqual(new Set(uiRendererComponentNames));
    expect(callKeys.length).toBeGreaterThanOrEqual(uiRendererComponentNames.length);
  });

  it("keeps each facade component paired with its own renderer key", () => {
    const expectedPairs = [
      ["export const Button", 'uiRendererAdapter.useTDesign("button")'],
      ["export const Card", 'uiRendererAdapter.useTDesign("card")'],
      ["export const Alert", 'uiRendererAdapter.useTDesign("alert")'],
      ["export const Empty", 'uiRendererAdapter.useTDesign("empty")'],
      ["export const Checkbox", 'uiRendererAdapter.useTDesign("checkbox")'],
      ["export const Tooltip", 'uiRendererAdapter.useTDesign("tooltip")'],
      ["export const Popover", 'uiRendererAdapter.useTDesign("popover")'],
      ["function CompatPopconfirm", 'uiRendererAdapter.useTDesign("popconfirm")'],
      ["const CompatTextArea", 'uiRendererAdapter.useTDesign("input-text-area")'],
      ["const CompatSearch", 'uiRendererAdapter.useTDesign("input-search")'],
      ["export const Input", 'uiRendererAdapter.useTDesign("input")'],
      ["export const Select", 'uiRendererAdapter.useTDesign("select")'],
      ["export const InputNumber", 'uiRendererAdapter.useTDesign("input-number")'],
      ["export const Switch", 'uiRendererAdapter.useTDesign("switch")'],
      ["export const Spin", 'uiRendererAdapter.useTDesign("spin")'],
      ["export const Skeleton", 'uiRendererAdapter.useTDesign("skeleton")'],
      ["export const Progress", 'uiRendererAdapter.useTDesign("progress")'],
      ["export const Tag", 'uiRendererAdapter.useTDesign("tag")'],
      ["const Text", 'uiRendererAdapter.useTDesign("text")'],
      ["const Title", 'uiRendererAdapter.useTDesign("title")'],
      ["const Paragraph", 'uiRendererAdapter.useTDesign("paragraph")'],
      ["export const Space", 'uiRendererAdapter.useTDesign("space")'],
      ["export const Col", 'uiRendererAdapter.useTDesign("col")'],
      ["export const Row", 'uiRendererAdapter.useTDesign("row")'],
      ["const CompatStatistic", 'uiRendererAdapter.useTDesign("statistic")'],
      ["const CompatRadioButton", 'uiRendererAdapter.useTDesign("radio-button")'],
      ["const CompatRadioGroup", 'uiRendererAdapter.useTDesign("radio-group")'],
      ["export const Segmented", 'uiRendererAdapter.useTDesign("segmented")'],
      ["export const Collapse", 'uiRendererAdapter.useTDesign("collapse")'],
      ["export const Tabs", 'uiRendererAdapter.useTDesign("tabs")'],
      ["export const Drawer", 'uiRendererAdapter.useTDesign("drawer")'],
      ["export const Dialog", 'uiRendererAdapter.useTDesign("dialog")'],
      ["export const Modal", 'uiRendererAdapter.useTDesign("modal")'],
    ] as const;

    for (let index = 0; index < expectedPairs.length; index += 1) {
      const [declaration, rendererCall] = expectedPairs[index];
      const start = source.indexOf(declaration);
      const tail = source.slice(start + declaration.length);
      const nextDeclaration = tail.search(/\n(?:export )?(?:const|function) [A-Za-z]/);
      const section = source.slice(
        start,
        nextDeclaration === -1
          ? source.length
          : start + declaration.length + nextDeclaration,
      );
      expect(start, `missing declaration: ${declaration}`).toBeGreaterThanOrEqual(0);
      expect(section, `wrong renderer key for ${declaration}`).toContain(rendererCall);
    }
  });
});
