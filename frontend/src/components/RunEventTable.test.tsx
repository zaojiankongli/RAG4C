// @vitest-environment jsdom
import React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
afterEach(cleanup);
import type { BackendRunEvent, BackendRunTopology } from "../types/rag";
import RunEventTable, { type RunEventFilters } from "./RunEventTable";

const topology: BackendRunTopology = {
  id: "rag4c.query.v1",
  revision: "rev-1",
  executor: "sequential_stream",
  nodes: [{
    id: "search", label: "Search", group: "retrieve", description: "", optional: false,
    repeatable: true, available: true, attributes: {},
  }],
  edges: [],
};

function event(seq: number, overrides: Partial<BackendRunEvent> = {}): BackendRunEvent {
  return {
    schema_version: 1,
    run_id: "run-1",
    seq,
    occurred_at: `2026-08-23T00:00:${String(seq % 60).padStart(2, "0")}Z`,
    elapsed_ms: seq * 10,
    topology_id: topology.id,
    topology_revision: topology.revision,
    type: "node.completed",
    node_id: "search",
    attempt: 1,
    duration_ms: 10,
    attributes: { candidate_count: seq },
    ...overrides,
  };
}

function Harness({ events, writeText, pageSize }: {
  events: BackendRunEvent[];
  writeText?: (text: string) => Promise<void>;
  pageSize?: number;
}) {
  const [filters, setFilters] = React.useState<RunEventFilters>({});
  return (
    <RunEventTable
      events={events}
      topology={topology}
      pageSize={pageSize}
      filters={filters}
      onFiltersChange={setFilters}
      writeText={writeText}
    />
  );
}

describe("RunEventTable", () => {
  it("filters a semantic event table and copies only sanitized JSON", async () => {
    const user = userEvent.setup();
    const failed = event(7, {
      type: "node.failed",
      error: { type: "SearchError", code: "search_failed", recoverable: true },
      attributes: { reason: "search_failed" },
    });
    const writeText = vi.fn().mockResolvedValue(undefined);
    render(<Harness events={[event(6), failed]} writeText={writeText} />);

    await user.selectOptions(screen.getByLabelText("事件类型"), "node.failed");
    expect(screen.getAllByRole("row")).toHaveLength(2);
    const scrollRegion = screen.getByRole("region", { name: "事件账本，可横向滚动" });
    expect(scrollRegion.getAttribute("tabindex")).toBe("0");
    const table = screen.getByRole("table", { name: "脱敏后的 canonical 运行事件" });
    expect([...table.querySelectorAll("col")].map((column) => (column as HTMLElement).style.width)).toEqual([
      "72px", "230px", "160px", "210px", "90px", "110px", "220px", "220px",
    ]);
    expect(within(table).getByRole("columnheader", { name: "Seq" })).not.toBeNull();
    expect(within(table).getByRole("columnheader", { name: "详情" })).not.toBeNull();
    const failedRow = screen.getAllByRole("row")[1];
    const cells = failedRow.querySelectorAll("td");
    expect(cells[1]?.textContent).toBe("70ms · 2026-08-23T00:00:07Z");
    expect(cells[3]?.textContent).toBe("Search · search");

    await user.click(screen.getByText("查看事件 7 JSON"));
    expect(document.querySelector("pre")?.textContent).toBe(JSON.stringify(failed, null, 2));
    await user.click(screen.getByRole("button", { name: "复制事件 7" }));
    expect(writeText).toHaveBeenCalledWith(JSON.stringify(failed, null, 2));
    expect(screen.getByRole("status").textContent).toContain("已复制事件 7");
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("supports node, attempt, failed, degraded, and retry filters", async () => {
    const user = userEvent.setup();
    render(<Harness events={[
      event(1, { type: "node.failed", attempt: 1 }),
      event(2, { type: "degraded", attempt: 2, attributes: { reason: "fallback" } }),
      event(3, { type: "retry.started", attempt: 1, attributes: { target_attempt: 2 } }),
    ]} />);
    await user.selectOptions(screen.getByLabelText("节点"), "search");
    await user.selectOptions(screen.getByLabelText("Attempt"), "2");
    expect(screen.getAllByText("degraded").length).toBeGreaterThan(0);
    await user.selectOptions(screen.getByLabelText("Attempt"), "");
    await user.click(screen.getByLabelText("仅失败"));
    expect(screen.getAllByText("node.failed").length).toBeGreaterThan(0);
    await user.click(screen.getByLabelText("仅失败"));
    await user.click(screen.getByLabelText("仅降级"));
    expect(screen.getAllByText("degraded").length).toBeGreaterThan(0);
    await user.click(screen.getByLabelText("仅降级"));
    await user.click(screen.getByLabelText("仅重试"));
    expect(screen.getAllByText("retry.started").length).toBeGreaterThan(0);
  });

  it("keeps only the current 200-row page mounted for a 1000-event ledger", async () => {
    const user = userEvent.setup();
    render(<Harness events={Array.from({ length: 1000 }, (_, index) => event(index + 1))} />);
    expect(screen.getAllByRole("row")).toHaveLength(201);
    expect(screen.getByText("第 1 / 5 页")).not.toBeNull();
    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByText("第 2 / 5 页")).not.toBeNull();
    expect(screen.getByText("201")).not.toBeNull();
  });

  it("announces copy failure politely and keeps native controls keyboard operable", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    render(<Harness events={[event(7)]} writeText={writeText} />);
    await user.tab();
    expect(screen.getByLabelText("事件类型")).toBe(document.activeElement);
    await user.selectOptions(screen.getByLabelText("事件类型"), "node.completed");
    await user.click(screen.getByText("查看事件 7 JSON"));
    await user.click(screen.getByRole("button", { name: "复制事件 7" }));
    expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
    expect(screen.getByRole("status").textContent).toContain("复制事件 7 失败");
  });

  it("uses Tab/Arrow/Enter/Space and remounts the announcement for repeated copy", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    render(<Harness events={[event(6), event(7, { type: "node.failed" })]} writeText={writeText} />);
    await user.tab();
    expect(document.activeElement).toBe(screen.getByLabelText("事件类型"));
    await user.selectOptions(screen.getByLabelText("事件类型"), "node.failed");
    expect((screen.getByLabelText("事件类型") as HTMLSelectElement).value).toBe("node.failed");
    const summary = screen.getByText("查看事件 7 JSON");
    for (let index = 0; index < 12 && document.activeElement !== summary; index += 1) await user.tab();
    expect(document.activeElement).toBe(summary);
    await user.keyboard("{Enter}");
    expect(summary.closest("details")?.open).toBe(true);
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "复制事件 7" }));
    await user.keyboard(" ");
    const firstToken = screen.getByRole("status").querySelector("[data-announcement-token]")?.getAttribute("data-announcement-token");
    await user.keyboard(" ");
    const secondToken = screen.getByRole("status").querySelector("[data-announcement-token]")?.getAttribute("data-announcement-token");
    expect(writeText).toHaveBeenCalledTimes(2);
    expect(firstToken).toBeTruthy();
    expect(secondToken).not.toBe(firstToken);
  });
});
