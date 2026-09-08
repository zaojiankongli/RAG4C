import { describe, expect, it } from "vitest";
import {
  parseRunLocation,
  parseRunViewState,
  runViewUrl,
  updateRunViewSearch,
} from "./runViewState";

describe("run view URL state", () => {
  it("parses independent run tab and list view", () => {
    expect(parseRunViewState("?run=history-1&node=verify&tab=events&view=errors&follow=0")).toEqual(
      {
        runId: "history-1",
        nodeId: "verify",
        tab: "events",
        view: "errors",
        follow: false,
      },
    );
  });

  it("uses safe defaults for missing or invalid values", () => {
    expect(parseRunViewState("?tab=unknown&view=unknown")).toEqual({
      runId: undefined,
      nodeId: undefined,
      tab: "process",
      view: "recent",
      follow: true,
    });
  });

  it("updates monitor parameters while preserving unrelated search state", () => {
    expect(
      updateRunViewSearch("?theme=dark", {
        runId: "run-2",
        nodeId: "search",
        tab: "timeline",
        view: "slow",
        follow: true,
      }),
    ).toBe("?theme=dark&run=run-2&node=search&tab=timeline&view=slow&follow=1");
  });

  it("round trips every tab and list view independently", () => {
    const tabs = ["process", "timeline", "events", "knowledge"] as const;
    const views = ["recent", "active", "slow", "errors", "stuck"] as const;
    for (const tab of tabs)
      for (const view of views) {
        const state = { runId: "run-1", nodeId: undefined, tab, view, follow: false };
        expect(parseRunViewState(updateRunViewSearch("", state))).toEqual(state);
      }
  });
});

describe("visualize route location", () => {
  it("reads canonical query from direct and compatible hash routes", () => {
    expect(
      parseRunLocation({ pathname: "/visualize", search: "?run=direct&tab=timeline", hash: "" })
        .runId,
    ).toBe("direct");
    expect(
      parseRunLocation({
        pathname: "/",
        search: "",
        hash: "#/visualize?run=hash&tab=events&view=errors",
      }),
    ).toMatchObject({ runId: "hash", tab: "events", view: "errors" });
  });
  it("writes back to the same canonical query location", () => {
    const state = {
      runId: "r",
      nodeId: undefined,
      tab: "timeline" as const,
      view: "slow" as const,
      follow: true,
    };
    expect(runViewUrl({ pathname: "/visualize", search: "", hash: "" }, state)).toMatch(
      /^\/visualize\?/,
    );
    expect(runViewUrl({ pathname: "/", search: "", hash: "#/visualize" }, state)).toContain(
      "/#/visualize?",
    );
  });
});
