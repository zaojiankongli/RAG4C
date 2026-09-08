import type { RunDetailTab, RunListView } from "../types/runs";

export type RunViewTab = RunDetailTab;
export interface RunViewState {
  runId?: string;
  nodeId?: string;
  tab: RunDetailTab;
  view: RunListView;
  follow: boolean;
}
const TABS = new Set<RunDetailTab>(["process", "timeline", "events", "knowledge"]);
const VIEWS = new Set<RunListView>(["recent", "active", "slow", "errors", "stuck"]);
export function parseRunViewState(search: string): RunViewState {
  const params = new URLSearchParams(search);
  const tab = params.get("tab");
  const view = params.get("view");
  return {
    runId: params.get("run") || undefined,
    nodeId: params.get("node") || undefined,
    tab: TABS.has(tab as RunDetailTab) ? (tab as RunDetailTab) : "process",
    view: VIEWS.has(view as RunListView) ? (view as RunListView) : "recent",
    follow: params.get("follow") !== "0",
  };
}
export function updateRunViewSearch(search: string, state: RunViewState): string {
  const params = new URLSearchParams(search);
  if (state.runId) params.set("run", state.runId);
  else params.delete("run");
  if (state.nodeId) params.set("node", state.nodeId);
  else params.delete("node");
  params.set("tab", state.tab);
  params.set("view", state.view);
  params.set("follow", state.follow ? "1" : "0");
  const next = params.toString();
  return next ? "?" + next : "";
}
export interface RunLocationLike {
  pathname: string;
  search: string;
  hash: string;
}
function hashSearch(hash: string): string {
  const index = hash.indexOf("?");
  return index >= 0 ? hash.slice(index) : "";
}
export function parseRunLocation(location: RunLocationLike): RunViewState {
  return parseRunViewState(
    location.pathname === "/visualize" ? location.search : hashSearch(location.hash),
  );
}
export function runViewUrl(location: RunLocationLike, state: RunViewState): string {
  const search = updateRunViewSearch(
    location.pathname === "/visualize" ? location.search : hashSearch(location.hash),
    state,
  );
  return location.pathname === "/visualize" ? `/visualize${search}` : `/#/visualize${search}`;
}
