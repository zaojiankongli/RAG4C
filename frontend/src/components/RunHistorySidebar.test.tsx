import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";
import type { RunAttention, RunStatus, RunSummaryDto } from "../types/runs";
import RunHistorySidebar from "./RunHistorySidebar";

function summary(
  runId: string,
  status: RunStatus = "completed",
  attention: RunAttention[] = [],
): RunSummaryDto {
  return {
    schema_version: 1,
    run_id: runId,
    status,
    outcome: "answered",
    started_at: "2026-08-23T08:00:00.000Z",
    updated_at: "2026-08-23T08:00:01.000Z",
    finished_at: status === "running" ? null : "2026-08-23T08:00:01.000Z",
    elapsed_ms: 1000,
    boot_id: "boot",
    worker_id: "worker",
    topology_id: "rag.query",
    topology_revision: "sha256:test",
    executor: "sequential_stream",
    last_seq: 17,
    event_count: 17,
    earliest_available_seq: 1,
    current_node_ids: status === "running" ? ["generation"] : [],
    failed_node_ids: status === "failed" ? ["generation"] : [],
    route: "hybrid",
    degraded_count: 0,
    retry_count: 1,
    attention,
    event_integrity: "complete",
    persistence_status: "durable",
    interruption_reason: null,
    query_fingerprint: "SENTINEL_QUERY",
  };
}

function text(renderer: ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}

describe("RunHistorySidebar", () => {
  it("renders server summaries without query, answer, or fingerprint previews", () => {
    const renderer = create(
      <RunHistorySidebar
        items={[summary("run-123456789")]}
        selectedRunId="run-123456789"
        view="recent"
        loading={false}
        hasMore={false}
        onSelect={() => undefined}
        onViewChange={() => undefined}
        onLoadMore={() => undefined}
      />,
    );
    expect(text(renderer)).toContain("123456789".slice(-8));
    expect(text(renderer)).not.toMatch(/SENTINEL_QUERY|question|answer/i);
  });

  it("spells out every status and attention state instead of relying on color", () => {
    const renderer = create(
      <RunHistorySidebar
        items={[
          summary("running", "running", ["stuck"]),
          summary("completed", "completed", ["slow"]),
          summary("failed", "failed", ["error"]),
          summary("cancelled", "cancelled", ["cancelled"]),
          summary("interrupted", "interrupted", ["interrupted"]),
        ]}
        selectedRunId={null}
        view="recent"
        loading={false}
        hasMore={false}
        onSelect={() => undefined}
        onViewChange={() => undefined}
        onLoadMore={() => undefined}
      />,
    );
    expect(text(renderer)).toMatch(/执行中.*卡住/s);
    expect(text(renderer)).toMatch(/已完成.*慢运行/s);
    expect(text(renderer)).toMatch(/失败.*错误/s);
    expect(text(renderer)).toMatch(/已取消.*取消关注/s);
    expect(text(renderer)).toMatch(/已中断.*中断关注/s);
  });

  it("pins the current live run, changes filters, and paginates", () => {
    const onSelect = vi.fn(),
      onViewChange = vi.fn(),
      onLoadMore = vi.fn();
    const renderer = create(
      <RunHistorySidebar
        items={[summary("older"), summary("live", "running")]}
        selectedRunId="live"
        liveRunId="live"
        view="active"
        loading={false}
        hasMore
        retentionDays={14}
        sourceLabel="memory + sqlite"
        onSelect={onSelect}
        onViewChange={onViewChange}
        onLoadMore={onLoadMore}
      />,
    );
    const rows = renderer.root.findAll(
      (node) => node.type === "button" && typeof node.props["data-run-id"] === "string",
    );
    expect(rows.map((row) => row.props["data-run-id"])).toEqual(["live", "older"]);
    expect(rows[0].props["aria-current"]).toBe("true");
    act(() => rows[1].props.onClick());
    expect(onSelect).toHaveBeenCalledWith("older");
    const filter = renderer.root.findByProps({ "aria-label": "筛选运行记录" });
    act(() => filter.props.onChange({ currentTarget: { value: "errors" } }));
    expect(onViewChange).toHaveBeenCalledWith("errors");
    act(() => renderer.root.findByProps({ "aria-label": "加载更多运行记录" }).props.onClick());
    expect(onLoadMore).toHaveBeenCalledOnce();
    expect(text(renderer)).toMatch(/memory \+ sqlite/);
    expect(text(renderer)).toMatch(/保留.*14.*天/s);
  });
});
