// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MonitorAttentionGroups } from "../monitor/monitorProjection";
import type { DocumentMetricsResponse, MetricsSnapshot } from "../types/rag";
import MonitorPage, { MonitorAttentionBoard } from "./MonitorPage";

const api = vi.hoisted(() => ({
  fetchMetrics: vi.fn(),
  fetchMetricsHistory: vi.fn(),
  fetchDocumentMetrics: vi.fn(),
  fetchRunHealth: vi.fn(),
  fetchRuns: vi.fn(),
}));
const connection = vi.hoisted(() => ({ online: true, refresh: vi.fn() }));
vi.mock("../api/client", () => ({
  fetchMetrics: api.fetchMetrics,
  fetchMetricsHistory: api.fetchMetricsHistory,
  fetchDocumentMetrics: api.fetchDocumentMetrics,
}));
vi.mock("../api/runs", () => ({ fetchRunHealth: api.fetchRunHealth, fetchRuns: api.fetchRuns }));
vi.mock("../context/ConnectionContext", () => ({
  useConnection: () => ({ online: connection.online, refresh: connection.refresh }),
}));
vi.mock("../charts/EChart", () => ({
  default: ({ ariaLabel }: { ariaLabel: string }) => <div role="img" aria-label={ariaLabel} />,
}));
vi.mock("../charts/chartTheme", () => ({
  CHART_FONT: {},
  useChartPalette: () => ({
    axis: "#667085",
    splitLine: "#eaecf0",
    primary: "#315efb",
    graph: "#7a5af8",
    warning: "#dc6803",
    tooltipBg: "#fff",
    tooltipBorder: "#d0d5dd",
    text: "#101828",
  }),
}));

const emptyAttention = (): MonitorAttentionGroups => ({
  active: [],
  stuck: [],
  slow: [],
  errors: [],
  cancelled: [],
});
const metric = { count: 12, sum: 1098, mean: 91.5, min: 20, max: 210, p50: 80, p95: 180, p99: 205 };
const snapshot: MetricsSnapshot = {
  ts: "2026-08-24T08:30:00Z",
  metrics: { "query.total": metric, "span.search|route=hybrid": metric },
  recent_queries: [
    {
      query: "差旅怎么报销？",
      route: "hybrid",
      abstained: false,
      duration_ms: 1234,
      citations: 3,
      traces: [],
      ts: "2026-08-24T08:29:00Z",
    },
  ],
  circuits: {},
};
const documentMetrics: DocumentMetricsResponse = {
  summary: { total: 0, completed: 0, failed: 0, processing: 0, chunks: 0 },
  latency_ms: {
    parse: { ...metric, count: 0, p95: 0 },
    total: { ...metric, count: 0, p95: 0 },
    stages: {},
  },
  engine_distribution: [],
  type_distribution: [],
  slow_documents: [],
  recent_failures: [],
  legacy_metadata_count: 0,
};

function installViewport(width: number) {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("max-width") ? width <= 768 : false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

beforeEach(() => {
  installViewport(1440);
  api.fetchMetrics.mockReset().mockResolvedValue(snapshot);
  api.fetchMetricsHistory.mockReset().mockResolvedValue({
    items: [
      { ts: "2026-08-24T08:28:00Z", metrics: { "query.total": { ...metric, count: 8 } } },
      { ts: "2026-08-24T08:29:00Z", metrics: { "query.total": { ...metric, count: 10 } } },
      { ts: "2026-08-24T08:30:00Z", metrics: { "query.total": metric } },
    ],
    count: 3,
  });
  api.fetchDocumentMetrics.mockReset().mockResolvedValue(documentMetrics);
  api.fetchRunHealth.mockReset().mockRejectedValue({ status: 404 });
  api.fetchRuns.mockReset().mockResolvedValue({ items: [] });
  connection.online = true;
  connection.refresh.mockReset();
});
afterEach(() => {
  cleanup();
  vi.clearAllTimers();
});

describe("MonitorAttentionBoard", () => {
  it("keeps the app-owned attention grouping and native TDesign card shell", () => {
    const groups = emptyAttention();
    groups.errors = [
      {
        runId: "run-abcdef12",
        group: "errors",
        status: "failed",
        elapsedMs: 1234.567,
        updatedAt: "2026-08-24T08:00:00Z",
        nodeIds: [],
        href: "/visualize?run=run-abcdef12&tab=events&view=errors",
      },
    ];
    render(
      <MonitorAttentionBoard
        groups={groups}
        capability="available"
        reconnecting={false}
        loading={false}
      />,
    );
    const region = screen.getByRole("region", { name: "需要关注的运行" });
    expect(region.querySelector(".t-card")).not.toBeNull();
    expect(within(region).getByRole("heading", { name: "错误" })).toBeTruthy();
    expect(within(region).getByRole("link", { name: "run-abcdef12 · 失败 · 1.23s" })).toBeTruthy();
  });

  it("renders an actionable retry in the unavailable alert", () => {
    const onRetry = vi.fn();
    render(
      <MonitorAttentionBoard
        groups={emptyAttention()}
        capability="unavailable"
        reconnecting
        loading={false}
        onRetry={onRetry}
      />,
    );
    const alert = screen.getByRole("alert");
    const retry = within(alert).getByRole("button", { name: "重试运行状态" });
    expect(alert.classList.contains("t-alert")).toBe(true);
    fireEvent.click(retry);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("MonitorPage native TDesign migration", () => {
  it.each([375, 1440])(
    "keeps native controls and named scroll regions accessible at %ipx",
    async (width) => {
      installViewport(width);
      render(<MonitorPage active />);
      const refresh = await screen.findByRole("button", { name: "刷新" });
      expect(refresh.classList.contains("t-button")).toBe(true);
      expect(
        screen.getByRole("button", { name: "自动更新：开" }).classList.contains("t-button"),
      ).toBe(true);
      expect(
        screen.getByRole("textbox", { name: "选择趋势指标" }).closest(".t-select"),
      ).not.toBeNull();
      expect(
        screen.getByRole("region", { name: "技术指标明细，可横向滚动" }).querySelector(".t-table"),
      ).not.toBeNull();
      expect(
        screen.getByRole("region", { name: "最近提问记录，可横向滚动" }).querySelector(".t-table"),
      ).not.toBeNull();
    },
  );

  it("refreshes connection and force-fetches a stale snapshot while offline", async () => {
    api.fetchRunHealth.mockResolvedValue({ enabled: false, status: "disabled" });
    api.fetchMetrics.mockResolvedValue(snapshot);
    const view = render(<MonitorPage active />);
    await screen.findByText("技术明细");
    expect(api.fetchMetrics).toHaveBeenCalledTimes(1);

    connection.online = false;
    view.rerender(<MonitorPage active />);
    const alert = await screen.findByRole("alert", { name: /监控数据已停止更新/ });
    const retry = within(alert).getByRole("button", { name: "重试监控数据" });
    fireEvent.click(retry);

    await waitFor(() => expect(connection.refresh).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(api.fetchMetrics).toHaveBeenCalledTimes(2));
  });

  it("shows an actionable stale-data alert and retries the metrics request", async () => {
    api.fetchRunHealth.mockResolvedValue({ enabled: false, status: "disabled" });
    api.fetchMetrics
      .mockResolvedValueOnce(snapshot)
      .mockRejectedValueOnce(new Error("gateway timeout"))
      .mockResolvedValueOnce(snapshot);
    render(<MonitorPage active />);
    await screen.findByText("技术明细");
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    const alert = await screen.findByRole("alert", { name: /监控数据已停止更新/ });
    const retry = within(alert).getByRole("button", { name: "重试监控数据" });
    expect(alert.classList.contains("t-alert")).toBe(true);
    fireEvent.click(retry);
    await waitFor(() => expect(api.fetchMetrics).toHaveBeenCalledTimes(3));
  });
});
