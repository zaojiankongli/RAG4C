import { create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it } from "vitest";
import type { RunHealthDto } from "../types/runs";
import RunOpsBanners from "./RunOpsBanners";

function health(overrides: Partial<RunHealthDto> = {}): RunHealthDto {
  return {
    schema_version: 1,
    status: "ok",
    enabled: true,
    boot_id: "boot",
    worker_id: "worker",
    memory: { active_runs: 1, recent_runs: 2, event_count: 20, dropped_runs: 0, dropped_events: 0 },
    persistence: {
      enabled: true,
      state: "ready",
      database: "run-history.sqlite3",
      wal: true,
      writer_queue_depth: 0,
      last_commit_at: null,
      commit_lag_ms: 0,
      dropped_mutations: 0,
      quick_check: "ok",
    },
    heartbeat: { interval_s: 5, stale_after_s: 20, last_heartbeat_at: null },
    retention: { days: 14, max_runs: 10000, memory_terminal_ttl_s: 900 },
    scope_stability: "installation",
    ...overrides,
  };
}
function output(renderer: ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}

describe("RunOpsBanners", () => {
  it("keeps capability, reconnect, transport, integrity, and persistence facts distinct", () => {
    const renderer = create(
      <RunOpsBanners
        capability="available"
        health={health({ status: "degraded" })}
        liveTransportDesync
        eventIntegrity="partial"
        persistenceStatus="memory_only"
        reconnecting
        expired={false}
      />,
    );
    const value = output(renderer);
    expect(value).toMatch(/Registry 服务降级/);
    expect(value).toMatch(/正在重新连接/);
    expect(value).toMatch(/实时传输序列不同步/);
    expect(value).toMatch(/事件历史不完整/);
    expect(value).toMatch(/仅保存在内存/);
    expect(renderer.root.findAllByProps({ role: "status" }).length).toBeGreaterThanOrEqual(4);
  });

  it.each([
    ["disabled", "运行历史未启用"],
    ["unauthorized", "无权查看运行历史"],
    ["legacy", "后端不支持运行历史"],
    ["unavailable", "运行历史暂不可用"],
  ] as const)(
    "renders the %s capability without pretending demo authority",
    (capability, label) => {
      const renderer = create(
        <RunOpsBanners
          capability={capability}
          health={null}
          liveTransportDesync={false}
          eventIntegrity="unknown"
          persistenceStatus={null}
          reconnecting={false}
          expired={false}
        />,
      );
      expect(output(renderer)).toContain(label);
      expect(output(renderer)).not.toMatch(/演示|demo/i);
    },
  );

  it("gives an expired run its own alert", () => {
    const renderer = create(
      <RunOpsBanners
        capability="available"
        health={health()}
        liveTransportDesync={false}
        eventIntegrity="complete"
        persistenceStatus="durable"
        reconnecting={false}
        expired
      />,
    );
    expect(output(renderer)).toMatch(/运行记录已过期/);
  });

  it.each([
    [
      "disabled persistence",
      {
        persistence: {
          ...health().persistence,
          enabled: false,
          state: "disabled" as const,
          wal: false,
        },
        scope_stability: "boot" as const,
      },
    ],
    [
      "memory-only persistence",
      {
        persistence: {
          ...health().persistence,
          state: "memory_only" as const,
          wal: false,
        },
      },
    ],
    ["boot-scoped identity", { scope_stability: "boot" as const }],
  ])("shows one explicit memory-only fact for available %s", (_label, overrides) => {
    const renderer = create(
      <RunOpsBanners
        capability="available"
        health={health(overrides)}
        liveTransportDesync={false}
        eventIntegrity="complete"
        persistenceStatus="durable"
        reconnecting={false}
        expired={false}
      />,
    );
    const value = output(renderer);
    expect(value).toMatch(/运行仅保存在内存/);
    expect(value).toMatch(/重启.*不可恢复/);
    expect(value.match(/运行仅保存在内存/g)).toHaveLength(1);
  });


  it("deduplicates service and selected-run memory-only evidence", () => {
    const renderer = create(
      <RunOpsBanners
        capability="available"
        health={health({
          persistence: {
            ...health().persistence,
            enabled: false,
            state: "disabled",
            wal: false,
          },
          scope_stability: "boot",
        })}
        liveTransportDesync={false}
        eventIntegrity="complete"
        persistenceStatus="memory_only"
        reconnecting={false}
        expired={false}
      />,
    );
    expect(output(renderer).match(/运行仅保存在内存/g)).toHaveLength(1);
  });

  it("does not duplicate memory-only service facts when Registry capability is disabled", () => {
    const renderer = create(
      <RunOpsBanners
        capability="disabled"
        health={health({
          enabled: false,
          persistence: {
            ...health().persistence,
            enabled: false,
            state: "disabled",
            wal: false,
          },
          scope_stability: "boot",
        })}
        liveTransportDesync={false}
        eventIntegrity="unknown"
        persistenceStatus={null}
        reconnecting={false}
        expired={false}
      />,
    );
    const value = output(renderer);
    expect(value).toMatch(/运行历史未启用/);
    expect(value).not.toMatch(/运行仅保存在内存/);
  });

});
