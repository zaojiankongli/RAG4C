import { describe, expect, it, vi } from "vitest";
import type { MetricStat, RecentQuery } from "../types/rag";
import type { RunListDto, RunListFilters } from "../types/runs";
import {
  ATTENTION_REQUESTS,
  abstentionPercent,
  cacheHitPercent,
  classifyRunsHealth,
  legacyRecentQueryIdentity,
  loadMonitorAttention,
  monitorAttentionHref,
  projectProtectionSignals,
} from "./monitorProjection";

function stat(count: number): MetricStat {
  return { count, sum: 0, mean: 0, min: 0, max: 0, p50: 0, p95: 0, p99: 0 };
}

function recentQuery(abstained: boolean): RecentQuery {
  return {
    query: "测试问题",
    route: "hybrid",
    abstained,
    duration_ms: 100,
    citations: 0,
    traces: [],
    ts: "2026-08-23T00:00:00Z",
  };
}

describe("monitor projection", () => {
  it("projects RAG4C quality and degradation counters", () => {
    const signals = projectProtectionSignals({
      "query.abstained": stat(8),
      "query.qa_retrieval.hit": stat(5),
      "query.qa_retrieval.no_match": stat(2),
      "query.qa_retrieval.catalog_error": stat(1),
      "retrieval.rerank.degraded": stat(3),
      "graph.fallback": stat(2),
      "query.round2.skipped": stat(1),
    });

    expect(signals.map((signal) => [signal.key, signal.count])).toEqual([
      ["query.abstained", 8],
      ["query.qa_retrieval.hit", 5],
      ["query.qa_retrieval.no_match", 2],
      ["query.qa_retrieval.catalog_error", 1],
      ["retrieval.rerank.degraded", 3],
      ["graph.fallback", 2],
      ["query.round2.skipped", 1],
    ]);
  });

  it("uses explicit cache hit rate and safely derives it when absent", () => {
    expect(cacheHitPercent({ hit_rate: 0.83 })).toBe(83);
    expect(cacheHitPercent({ hits: 7, misses: 3 })).toBe(70);
    expect(cacheHitPercent({ hits: 0, misses: 0 })).toBe(0);
  });

  it("uses delivered completions so four cached abstentions out of five are 80 percent", () => {
    expect(
      abstentionPercent(
        {
          "query.completed": stat(5),
          "query.total": stat(1),
          "query.abstained": stat(4),
        },
        [],
      ),
    ).toBe(80);
  });

  it("caps inconsistent abstention counters at 100 percent", () => {
    expect(
      abstentionPercent(
        {
          "query.completed": stat(2),
          "query.abstained": stat(4),
        },
        [],
      ),
    ).toBe(100);
  });

  it("keeps an explicit zero completed count instead of falling back to recent queries", () => {
    expect(
      abstentionPercent(
        {
          "query.completed": stat(0),
          "query.abstained": stat(0),
        },
        [recentQuery(true)],
      ),
    ).toBe(0);
  });

  it("uses the legacy total counter when completed is unavailable", () => {
    expect(
      abstentionPercent(
        {
          "query.total": stat(8),
          "query.abstained": stat(2),
        },
        [],
      ),
    ).toBe(25);
  });

  it("uses the recent query window when completion counters are unavailable", () => {
    expect(abstentionPercent({}, [recentQuery(true), recentQuery(false)])).toBe(50);
  });

  it("starts all attention requests together and keeps cancelled separate", async () => {
    const resolvers = new Map<string, (value: RunListDto) => void>();
    const calls: string[] = [];
    const fetchRuns = vi.fn((filters: RunListFilters) => {
      const key = filters.status?.[0] === "cancelled" ? "cancelled" : filters.view;
      if (!key) throw new Error("attention request key missing");
      calls.push(key);
      return new Promise<RunListDto>((resolve) => resolvers.set(key, resolve));
    });
    const pending = loadMonitorAttention(fetchRuns as Parameters<typeof loadMonitorAttention>[0]);
    expect(calls).toEqual(ATTENTION_REQUESTS.map(({ key }) => key));
    for (const { key } of ATTENTION_REQUESTS) {
      resolvers.get(key)?.({
        schema_version: 1,
        items: [{
          run_id: `run-${key}`,
          status: key === "cancelled" ? "cancelled" : key === "errors" ? "failed" : "running",
          elapsed_ms: 10,
          updated_at: "2026-08-23T00:00:00Z",
          current_node_ids: [],
          failed_node_ids: [],
          attention: [key === "errors" ? "error" : key],
        }],
      } as unknown as RunListDto);
    }
    const result = await pending;
    expect(result.groups.cancelled[0]?.runId).toBe("run-cancelled");
    expect(result.groups.errors[0]?.runId).toBe("run-errors");
    expect(result.groups.cancelled).not.toEqual(result.groups.errors);
  });

  it("builds stable run-id drill-through links and never uses an array index identity", () => {
    expect(monitorAttentionHref("run-abcdef12", "errors")).toBe(
      "/visualize?run=run-abcdef12&tab=events&view=errors",
    );
    expect(monitorAttentionHref("run-cancelled", "cancelled")).toBe(
      "/visualize?run=run-cancelled&tab=events&view=recent",
    );
    const first = recentQuery(false);
    const second = { ...first, query: "另一个问题" };
    expect(legacyRecentQueryIdentity(first)).toBe(`${first.ts}:${first.query}`);
    expect(legacyRecentQueryIdentity(second)).not.toBe(legacyRecentQueryIdentity(first));
  });

  it("keeps disabled, permission, legacy, and unavailable health states distinct", () => {
    expect(classifyRunsHealth({ enabled: true, status: "ok" })).toBe("available");
    expect(classifyRunsHealth({ enabled: false, status: "disabled" })).toBe("disabled");
    expect(classifyRunsHealth(undefined, { status: 404 })).toBe("legacy");
    expect(classifyRunsHealth(undefined, { status: 401 })).toBe("unauthorized");
    expect(classifyRunsHealth(undefined, { status: 403 })).toBe("unauthorized");
    expect(classifyRunsHealth(undefined, { status: 503 })).toBe("unavailable");
    expect(classifyRunsHealth(undefined, new Error("network"))).toBe("unavailable");
  });
});
