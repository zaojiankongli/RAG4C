import { describe, expect, it } from "vitest";
import { commitNavigationIntent, type NavigationCommitEnvironment } from "./navigationAdapter";

function environment(): NavigationCommitEnvironment & {
  historyUrls: string[];
  replacedHistoryUrls: string[];
  historyStates: unknown[];
  replacedHistoryStates: unknown[];
  hashUrls: string[];
  popStateEvents: number[];
} {
  const state = {
    historyUrls: [] as string[],
    replacedHistoryUrls: [] as string[],
    historyStates: [] as unknown[],
    replacedHistoryStates: [] as unknown[],
    hashUrls: [] as string[],
    popStateEvents: [] as number[],
  };
  return {
    historyUrls: state.historyUrls,
    replacedHistoryUrls: state.replacedHistoryUrls,
    historyStates: state.historyStates,
    replacedHistoryStates: state.replacedHistoryStates,
    hashUrls: state.hashUrls,
    popStateEvents: state.popStateEvents,
    pushHistory: (url, historyState) => {
      state.historyUrls.push(url);
      state.historyStates.push(historyState);
    },
    replaceHistory: (url, historyState) => {
      state.replacedHistoryUrls.push(url);
      state.replacedHistoryStates.push(historyState);
    },
    setHash: (url) => state.hashUrls.push(url),
    dispatchPopState: () => state.popStateEvents.push(1),
  };
}

describe("navigation commit adapter", () => {
  it("commits history intents with one push and one popstate", () => {
    const target = environment();

    commitNavigationIntent(
      { mode: "history", url: "/enterprise/tasks?task=task-1" },
      {},
      target,
    );

    expect(target.historyUrls).toEqual(["/enterprise/tasks?task=task-1"]);
    expect(target.replacedHistoryUrls).toEqual([]);
    expect(target.historyStates).toEqual([undefined]);
    expect(target.hashUrls).toEqual([]);
    expect(target.popStateEvents).toHaveLength(1);
  });

  it("commits hash intents without inventing a popstate", () => {
    const target = environment();

    commitNavigationIntent({ mode: "hash", url: "/documents?document=doc-1" }, {}, target);

    expect(target.historyUrls).toEqual([]);
    expect(target.replacedHistoryUrls).toEqual([]);
    expect(target.historyStates).toEqual([]);
    expect(target.hashUrls).toEqual(["/documents?document=doc-1"]);
    expect(target.popStateEvents).toHaveLength(0);
  });

  it("can preserve a legacy page contract that emits popstate after hash commits", () => {
    const target = environment();

    commitNavigationIntent(
      { mode: "hash", url: "/documents?document=doc-1" },
      { dispatchPopStateAfterHash: true },
      target,
    );

    expect(target.hashUrls).toEqual(["/documents?document=doc-1"]);
    expect(target.popStateEvents).toHaveLength(1);
  });

  it.each([
    [{ mode: "history", url: "/consistency" } as const, "/consistency"],
    [{ mode: "hash", url: "/consistency" } as const, "/consistency"],
  ])("preserves one operator handoff popstate in %s mode", (intent, url) => {
    const target = environment();

    commitNavigationIntent(intent, { dispatchPopStateAfterHash: true }, target);

    expect(target.popStateEvents).toHaveLength(1);
    if (intent.mode === "history") expect(target.historyUrls).toEqual([url]);
    else expect(target.hashUrls).toEqual([url]);
  });

  it("commits replace history intents without adding a push entry", () => {
    const target = environment();

    commitNavigationIntent(
      { mode: "history", url: "/enterprise/workspaces" },
      { historyAction: "replace" },
      target,
    );

    expect(target.historyUrls).toEqual([]);
    expect(target.replacedHistoryUrls).toEqual(["/enterprise/workspaces"]);
    expect(target.replacedHistoryStates).toEqual([undefined]);
    expect(target.popStateEvents).toHaveLength(1);
  });

  it("forwards an explicit history state without changing the strategy", () => {
    const target = environment();
    const historyState = { source: "approval" };

    commitNavigationIntent(
      { mode: "history", url: "/enterprise/approvals" },
      { historyState },
      target,
    );

    expect(target.historyStates).toEqual([historyState]);
    expect(target.popStateEvents).toHaveLength(1);
  });
});
