// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { RecentQuery } from "../types/rag";
import MonitorMetricsTable, { LegacyRecentQueriesTable } from "./MonitorMetricsTable";

afterEach(cleanup);

const metricRows = [
  {
    key: "span.search|route=hybrid",
    name: "span.search",
    tags: { route: "hybrid" },
    stat: { count: 12, sum: 1098, mean: 91.5, min: 20, max: 210, p50: 80, p95: 180, p99: 205 },
  },
];

describe("MonitorMetricsTable", () => {
  it("renders nested metric statistics through the real TDesign table DOM", () => {
    render(<MonitorMetricsTable rows={metricRows} />);

    const region = screen.getByRole("region", { name: "技术指标明细，可横向滚动" });
    expect(region.querySelector(".t-table")).not.toBeNull();
    for (const value of ["12", "91.5", "80", "180", "205"]) {
      expect(within(region).getByText(value)).toBeTruthy();
    }
    expect(within(region).getByText("route=hybrid").closest(".t-tag")).not.toBeNull();
  });

  it("renders an actionable empty state instead of a blank table", () => {
    render(<MonitorMetricsTable rows={[]} />);
    expect(screen.getByRole("status").textContent).toContain("完成一次问答后");
  });
});

describe("LegacyRecentQueriesTable", () => {
  const rows: RecentQuery[] = [
    {
      query: "如何申请差旅报销？",
      route: "hybrid",
      abstained: false,
      duration_ms: 1234,
      citations: 3,
      traces: [],
      ts: "2026-08-24T08:30:00Z",
    },
  ];

  it("keeps question, route, duration, sources and status readable", () => {
    render(<LegacyRecentQueriesTable rows={rows} />);
    const table = screen.getByRole("region", { name: "最近提问记录，可横向滚动" });
    expect(table.querySelector(".t-table")).not.toBeNull();
    expect(within(table).getByText("如何申请差旅报销？")).toBeTruthy();
    expect(within(table).getByText("1.23s")).toBeTruthy();
    expect(within(table).getByText("3")).toBeTruthy();
    expect(within(table).getByText("已完成").closest(".t-tag")).not.toBeNull();
  });

  it("renders a named empty state", () => {
    render(<LegacyRecentQueriesTable rows={[]} />);
    expect(screen.getByRole("status").textContent).toContain("暂无最近提问");
  });
});
