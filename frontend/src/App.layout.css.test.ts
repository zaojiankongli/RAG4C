// Vitest runs this CSS contract in Node; the browser app intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./styles.css", import.meta.url), "utf8") as string;

function declarations(selector: string): Record<string, string> {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = css.match(new RegExp(escaped + "\\s*\\{([^}]*)\\}"));
  if (!match) throw new Error("missing CSS rule: " + selector);
  return Object.fromEntries(
    match[1]
      .split(";")
      .map((item: string) => item.trim())
      .filter(Boolean)
      .map((item: string) => {
        const index = item.indexOf(":");
        return [item.slice(0, index).trim(), item.slice(index + 1).trim()];
      }),
  );
}

describe("App content flex layout", () => {
  it("restores the TDesign Content flex growth while retaining the semantic div", () => {
    expect(declarations(".app-content")).toMatchObject({
      display: "flex",
      flex: "1 1 auto",
      "min-width": "0",
      "flex-direction": "column",
      overflow: "hidden",
    });
    expect(declarations(".page-slot")).toMatchObject({ flex: "1", "min-height": "0" });
  });
});
