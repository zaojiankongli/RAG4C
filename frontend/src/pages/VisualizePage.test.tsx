/* eslint-disable @typescript-eslint/no-explicit-any */
import { act, create } from "react-test-renderer";
import { beforeEach, describe, expect, it, vi } from "vitest";
const s = vi.hoisted(() => ({
  flow: vi.fn(),
  timeline: vi.fn(),
  knowledge: vi.fn(),
  replace: vi.fn(),
  mobile: { matches: false },
  history: {
    capability: "available",
    health: null,
    items: [{ run_id: "run-1", status: "completed", last_seq: 4 }],
    listSource: "sqlite",
    retention: { days: 7, max_runs: 70 },
    selectedRunId: "run-1",
    detail: { summary: { run_id: "run-1", last_seq: 4, elapsed_ms: 100, status: "completed" } },
    events: [{ seq: 1 }],
    loading: false,
    reconnecting: false,
    expired: false,
    historyGap: false,
    legacyMode: false,
    view: "recent",
    hasMore: false,
    maximumContiguousSeq: 4,
    liveTransportDesync: false,
    eventIntegrity: "complete",
    persistenceStatus: "durable",
    selectRun: vi.fn(),
    setView: vi.fn(),
    loadMore: vi.fn(),
    refresh: vi.fn(),
    retryNow: vi.fn(),
  },
}));
vi.mock("../ui", () => ({
  Button: ({ children, onClick, ...p }: any) => (
    <button onClick={onClick} {...p}>
      {children}
    </button>
  ),
  Drawer: ({ open, children }: any) => (open ? <div role="dialog">{children}</div> : null),
  Tabs: ({ items, activeKey, onChange }: any) => (
    <div role="tablist">
      {items.map((i: any) => (
        <button key={i.key} role="tab" data-key={i.key} onClick={() => onChange(i.key)}>
          {i.label}
        </button>
      ))}
      {items.find((i: any) => i.key === activeKey)?.children}
    </div>
  ),
  Tag: ({ children }: any) => <span>{children}</span>,
  Tooltip: ({ children }: any) => children,
  Typography: { Text: ({ children }: any) => <span>{children}</span> },
}));
vi.mock("../ui/icons", () => ({
  AimOutlined: () => null,
  ApartmentOutlined: () => null,
  BranchesOutlined: () => null,
  ClockCircleOutlined: () => null,
  DatabaseOutlined: () => null,
  FieldTimeOutlined: () => null,
  UnorderedListOutlined: () => null,
}));
vi.mock("../components/PageTopbar", () => ({ default: () => <header /> }));
vi.mock("../components/PageState", () => ({ default: ({ title }: any) => <div>{title}</div> }));
vi.mock("../components/RunHistorySidebar", () => ({ default: () => <aside /> }));
vi.mock("../components/RunOpsBanners", () => ({ default: () => null }));
vi.mock("../components/RunEventTable", () => ({ default: () => <div>events</div> }));
vi.mock("../components/RunKnowledgePanel", () => ({ default: () => <div>knowledge</div> }));
vi.mock("../answer-evidence/components/AnswerEvidencePanel", () => ({
  default: ({ runId }: any) => (
    <div data-testid="answer-evidence-panel" data-run-id={runId ?? ""}>
      evidence
    </div>
  ),
}));
vi.mock("../components/RunFlowGraph", () => ({
  default: ({ selectedNodeId, onSelectNode }: any) => (
    <button
      data-testid="graph"
      aria-current={selectedNodeId === "generation" ? "true" : undefined}
      onClick={() => onSelectNode("generation")}
    >
      graph
    </button>
  ),
}));
vi.mock("../components/RunTimeline", () => ({
  default: ({ selectedNodeId, onSelectNode }: any) => (
    <button data-testid="timeline" onClick={() => onSelectNode("generation")}>
      {selectedNodeId}
    </button>
  ),
}));
vi.mock("../run/RunMonitorContext", () => ({ useRunMonitor: () => ({ current: null }) }));
vi.mock("../run/useRunHistory", () => ({ useRunHistory: () => s.history }));
vi.mock("../run/serverRunProjection", () => ({
  projectServerFlow: s.flow,
  projectRunTimeline: s.timeline,
  projectServerRun: s.knowledge,
}));
import VisualizePage from "./VisualizePage";
const flow = {
  id: "run-1",
  query: "",
  status: "completed",
  topologySource: "server-history",
  durationMs: 100,
  nodes: [
    {
      id: "generation",
      label: "Generation",
      description: "",
      group: "generate",
      status: "completed",
      details: [],
      attempt: 1,
    },
  ],
  edges: [],
};
const listeners = new Map<string, () => void>();
function location(search: string, historyState: unknown = null) {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      location: { pathname: "/visualize", search, hash: "" },
      history: { state: historyState, replaceState: s.replace },
      matchMedia: () => s.mobile,
      setInterval: vi.fn(),
      clearInterval: vi.fn(),
      addEventListener: (type: string, listener: () => void) => listeners.set(type, listener),
      removeEventListener: (type: string) => listeners.delete(type),
    },
  });
}
function hashLocation(search: string, historyState: unknown = null) {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      location: { pathname: "/", search: "", hash: `#/visualize${search}` },
      history: { state: historyState, replaceState: s.replace },
      matchMedia: () => s.mobile,
      setInterval: vi.fn(),
      clearInterval: vi.fn(),
      addEventListener: (type: string, listener: () => void) => listeners.set(type, listener),
      removeEventListener: (type: string) => listeners.delete(type),
    },
  });
}
describe("VisualizePage orchestration", () => {
  beforeEach(() => {
    s.flow.mockReset().mockReturnValue(flow);
    s.timeline.mockReset().mockReturnValue({ intervals: [], waves: [], idleGaps: [] });
    s.knowledge.mockReset().mockReturnValue({ knowledge: { components: {} }, nodeRollup: [] });
    s.replace.mockReset();
    s.mobile.matches = false;
    s.history.items = [{ run_id: "run-1", status: "completed", last_seq: 4 }];
    s.history.detail = {
      summary: { run_id: "run-1", last_seq: 4, elapsed_ms: 100, status: "completed" },
    };
    s.history.events = [{ seq: 1 }];
    s.history.loading = false;
    s.history.reconnecting = false;
  });
  it("uses a labelled section for the run workspace instead of nesting a main landmark", () => {
    location("?run=run-1&tab=process&view=recent");
    const renderer = create(<VisualizePage />);

    expect(renderer.root.findAllByType("main")).toHaveLength(0);
    const workspace = renderer.root.findByProps({
      className: "run-workspace",
      "aria-label": "运行详情工作区",
    });
    expect(workspace.type).toBe("section");
  });
  it("keeps the graph wide until a node is selected for inspection", () => {
    location("?run=run-1&tab=process&view=recent");
    const renderer = create(<VisualizePage />);

    expect(renderer.root.findAllByProps({ "aria-label": "节点详情" })).toHaveLength(0);
    expect(
      renderer.root.findByProps({ "data-mobile-order": "filters-selected-tabs-workspace-drawer" })
        .props.className,
    ).toContain("is-inspector-collapsed");

    act(() => renderer.root.findByProps({ "data-testid": "graph" }).props.onClick());

    expect(renderer.root.findAllByProps({ "aria-label": "节点详情" })).toHaveLength(1);
  });
  it("keeps an active run process and timeline visible while the next long-poll is pending", () => {
    s.history.items = [{ run_id: "run-1", status: "running", last_seq: 2 }];
    s.history.detail = {
      summary: { run_id: "run-1", last_seq: 2, elapsed_ms: 8_000, status: "running" },
    };
    s.history.events = [{ seq: 1 }, { seq: 2 }];
    location("?run=run-1&tab=process&view=active");
    const renderer = create(<VisualizePage />);
    expect(renderer.root.findByProps({ "data-testid": "graph" })).toBeTruthy();

    s.flow.mockReturnValue(null);
    s.history.detail = {
      summary: { run_id: "run-1", last_seq: 2, elapsed_ms: 8_500, status: "running" },
    };
    s.history.events = [{ seq: 1 }, { seq: 2 }];
    s.history.loading = true;
    act(() => renderer.update(<VisualizePage />));

    expect(renderer.root.findByProps({ "data-testid": "graph" })).toBeTruthy();
    expect(
      renderer.root.findAll((node) => node.children.includes("正在加载运行流程")),
    ).toHaveLength(0);
    act(() => renderer.root.findByProps({ "data-key": "timeline" }).props.onClick());
    expect(renderer.root.findByProps({ "data-testid": "timeline" })).toBeTruthy();
  });
  it("keeps inactive projections cold then updates canonical timeline URL", () => {
    location("?run=run-1&tab=events&view=recent");
    const r = create(<VisualizePage />);
    expect(s.flow).not.toHaveBeenCalled();
    expect(s.knowledge).not.toHaveBeenCalled();
    act(() => r.root.findByProps({ "data-key": "timeline" }).props.onClick());
    expect(s.flow).toHaveBeenCalled();
    expect(s.timeline).toHaveBeenCalled();
    expect(s.knowledge).not.toHaveBeenCalled();
    expect(s.replace.mock.calls[s.replace.mock.calls.length - 1]?.[2]).toMatch(
      /^\/visualize\?.*tab=timeline/,
    );
    act(() => r.root.findByProps({ "data-key": "knowledge" }).props.onClick());
    expect(s.knowledge).toHaveBeenCalledTimes(1);
  });
  it("preserves history state while silently replacing the direct route", () => {
    const historyState = { source: "visualize-host" };
    location("?run=run-1&tab=process&view=recent", historyState);
    const r = create(<VisualizePage />);
    s.replace.mockReset();

    act(() => r.root.findByProps({ "data-key": "timeline" }).props.onClick());

    expect(s.replace.mock.calls[s.replace.mock.calls.length - 1]?.[0]).toBe(historyState);
    expect(s.replace.mock.calls[s.replace.mock.calls.length - 1]?.[2]).toMatch(
      /^\/visualize\?.*tab=timeline/,
    );
  });
  it("silently replaces the hash route and syncs hashchange state", () => {
    hashLocation("?run=run-1&tab=process&view=recent");
    const renderer = create(<VisualizePage />);
    s.replace.mockReset();

    act(() => renderer.root.findByProps({ "data-key": "timeline" }).props.onClick());

    expect(s.replace.mock.calls[s.replace.mock.calls.length - 1]?.[2]).toMatch(
      /^\/#\/visualize\?.*tab=timeline/,
    );
    window.location.hash = "#/visualize?run=run-2&tab=events&view=errors&follow=0";
    act(() => listeners.get("hashchange")?.());
    expect(s.history.selectRun).toHaveBeenCalledWith("run-2");
    expect(s.history.setView).toHaveBeenCalledWith("errors");
  });
  it("propagates graph selection to timeline and opens mobile drawer", () => {
    s.mobile.matches = true;
    location("?run=run-1&tab=process&view=recent");
    const r = create(<VisualizePage />);
    expect(r.root.findByProps({ role: "group", "aria-label": "移动端运行筛选" })).toBeTruthy();
    act(() => r.root.findByProps({ "data-testid": "graph" }).props.onClick());
    expect(r.root.findByProps({ role: "dialog" })).toBeTruthy();
    act(() => r.root.findByProps({ "data-key": "timeline" }).props.onClick());
    expect(r.root.findByProps({ "data-testid": "timeline" }).children).toContain("generation");
  });
  it("syncs mounted state from popstate without a remount", () => {
    location("?run=run-1&tab=process&view=recent");
    const renderer = create(<VisualizePage />);
    window.location.search = "?run=run-2&tab=events&view=errors&follow=0";
    act(() => listeners.get("popstate")?.());
    expect(s.history.selectRun).toHaveBeenCalledWith("run-2");
    expect(s.history.setView).toHaveBeenCalledWith("errors");
    expect(renderer.root.findByProps({ "data-key": "events" })).toBeTruthy();
  });
  it("propagates timeline selection back to the graph", () => {
    location("?run=run-1&tab=timeline&view=recent");
    const renderer = create(<VisualizePage />);
    act(() => renderer.root.findByProps({ "data-testid": "timeline" }).props.onClick());
    act(() => renderer.root.findByProps({ "data-key": "process" }).props.onClick());
    expect(renderer.root.findByProps({ "data-testid": "graph" }).props["aria-current"]).toBe(
      "true",
    );
  });
  it("does not overwrite Query when hidden Visualize state changes", () => {
    location("?run=run-1&tab=process&view=recent");
    const renderer = create(<VisualizePage />);
    s.replace.mockReset();
    window.location.pathname = "/query";
    window.location.search = "";
    act(() => renderer.root.findByProps({ "data-testid": "graph" }).props.onClick());
    act(() => renderer.root.findByProps({ "data-key": "timeline" }).props.onClick());
    expect(s.replace).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe("/query");
  });
});

