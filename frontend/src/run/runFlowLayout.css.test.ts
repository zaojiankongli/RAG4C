// Vitest executes this contract test in Node; the browser bundle intentionally omits Node types.
// @ts-expect-error -- test-only Node builtin without adding @types/node to the app.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

function escapeRegExp(value: string): string {
  const special = new Set(["\\", "^", "$", ".", "*", "+", "?", "(", ")", "[", "]", "{", "}", "|"]);
  return Array.from(value, (character) =>
    special.has(character) ? "\\" + character : character,
  ).join("");
}

function rule(source: string, selector: string): string {
  const pattern = escapeRegExp(selector) + "\\s*\\{([^}]*)\\}";
  const match = source.match(new RegExp(pattern));
  if (!match) throw new Error("missing CSS rule: " + selector);
  return match[1];
}

function declarations(body: string): Record<string, string> {
  return Object.fromEntries(
    body
      .split(";")
      .map((item) => item.trim())
      .filter(Boolean)
      .map((item) => {
        const index = item.indexOf(":");
        return [item.slice(0, index).trim(), item.slice(index + 1).trim()];
      }),
  );
}

describe("desktop run flow height contract", () => {
  it("propagates a definite flex height from workspace through the active pane to canvas", () => {
    expect(declarations(rule(css, ".run-workspace"))).toMatchObject({
      display: "flex",
      "flex-direction": "column",
      "min-height": "0",
      overflow: "hidden",
    });
    expect(declarations(rule(css, ".run-workspace > .run-view-tabs"))).toMatchObject({
      flex: "1",
      "min-height": "0",
    });
    expect(
      declarations(rule(css, ".run-workspace .run-view-tabs .t-tab-panel.t-is-active")),
    ).toMatchObject({ display: "flex", "flex-direction": "column" });
    expect(declarations(rule(css, ".run-stage"))).toMatchObject({
      height: "100%",
      flex: "1",
      "min-height": "0",
    });
    expect(declarations(rule(css, ".run-flow-canvas"))).toMatchObject({
      flex: "1",
      "min-height": "0",
    });
    expect(declarations(rule(css, ".run-flow-canvas > .react-flow"))).toMatchObject({
      width: "100%",
      height: "100%",
      "min-height": "100%",
    });
  });

  it("keeps tab scrolling and the mobile positive-height override", () => {
    expect(
      declarations(rule(css, ".run-workspace .run-view-tabs .t-tab-panel")),
    ).toMatchObject({ "min-height": "0", overflow: "auto" });
    expect(declarations(rule(css, ".run-timeline-stage"))).toMatchObject({ overflow: "auto" });
    const mobile = css.slice(css.indexOf("@media (max-width: 760px) {", css.indexOf(".run-ops-layout")));
    expect(declarations(rule(mobile, ".run-stage"))).toMatchObject({
      height: "auto",
      "min-height": "480px",
    });
    expect(declarations(rule(mobile, ".run-flow-canvas"))).toMatchObject({
      height: "430px",
      "min-height": "430px",
      flex: "0 0 430px",
    });
  });

  it("keeps the event ledger wide and keyboard-scrollable at 1366px", () => {
    expect(declarations(rule(css, ".run-event-table-wrap"))).toMatchObject({
      "max-width": "100%",
      "overflow-x": "auto",
    });
    expect(declarations(rule(css, ".run-event-table-wrap:focus-visible"))).toHaveProperty("outline");
    expect(declarations(rule(css, ".run-event-table"))).toMatchObject({
      "min-width": "1312px",
      "table-layout": "fixed",
    });
    expect(declarations(rule(css, ".run-event-col-time"))).toMatchObject({
      width: "230px",
      "white-space": "nowrap",
    });
    expect(declarations(rule(css, ".run-event-col-node"))).toMatchObject({
      width: "210px",
      "white-space": "nowrap",
    });
  });

});
