// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./RetrievalQualityCenter.tsx", import.meta.url), "utf8");

describe("RetrievalQualityCenter facade boundary", () => {
  it("routes host controls through the shared UI facade", () => {
    expect(source).toContain('import { Button, Tag } from "../ui";');
    expect(source).not.toContain('from "tdesign-react"');
    expect(source).not.toContain('variant={mobileTab === value ? "base" : "outline"}');
    expect(source).not.toContain('theme={mobileTab === value ? "primary" : "default"}');
    expect(source).toContain("openerRef.current = event.currentTarget");
  });
});
