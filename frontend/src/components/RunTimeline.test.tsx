import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";
import type { RunIdleGap, RunTimelineInterval, RunTimelineWave } from "../run/serverRunProjection";
import RunTimeline from "./RunTimeline";

function interval(
  nodeId: string,
  startMs: number | undefined,
  endMs: number | undefined,
  overrides: Partial<RunTimelineInterval> = {},
): RunTimelineInterval {
  return {
    id: `${nodeId}:${overrides.attempt ?? 1}`,
    nodeId,
    attempt: overrides.attempt ?? 1,
    startMs,
    endMs,
    durationMs:
      startMs === undefined || endMs === undefined ? undefined : Math.max(0, endMs - startMs),
    status: "completed",
    partial: false,
    retry: false,
    startSeq: 2,
    endSeq: 3,
    wave: 1,
    ...overrides,
  };
}
function wave(overrides: Partial<RunTimelineWave> = {}): RunTimelineWave {
  return {
    id: 1,
    startMs: 0,
    endMs: 240,
    wallTimeMs: 240,
    intervalCount: 3,
    maxConcurrency: 2,
    intervalIds: ["a:1", "b:1", "c:1"],
    ...overrides,
  };
}
function renderTimeline(
  intervals: RunTimelineInterval[],
  waves: RunTimelineWave[],
  idleGaps: RunIdleGap[] = [],
  selectedNodeId?: string,
  onSelectNode = vi.fn(),
): ReactTestRenderer {
  return create(
    <RunTimeline
      intervals={intervals}
      waves={waves}
      idleGaps={idleGaps}
      selectedNodeId={selectedNodeId}
      onSelectNode={onSelectNode}
    />,
  );
}

describe("RunTimeline", () => {
  it("renders a transitive wave, maximum concurrency, and equivalent semantic rows", () => {
    const renderer = renderTimeline(
      [interval("a", 0, 100), interval("b", 80, 180), interval("c", 170, 240)],
      [wave()],
    );
    const group = renderer.root.findByProps({ role: "group" });
    expect(group.props["aria-label"]).toMatch(/并发波 1.*3 个节点.*最大并发 2/);
    expect(renderer.root.findAllByType("tr")).toHaveLength(4);
  });

  it("keeps native buttons in chronological tab order and links selection by node id", () => {
    const onSelectNode = vi.fn();
    const renderer = renderTimeline(
      [interval("generation", 80, 180), interval("retrieval", 0, 60)],
      [wave({ intervalCount: 2, intervalIds: ["generation:1", "retrieval:1"] })],
      [],
      "generation",
      onSelectNode,
    );
    const bars = renderer.root.findAll(
      (node) => node.type === "button" && node.props.className?.includes("run-timeline-bar"),
    );
    expect(bars.map((bar) => bar.props["data-node-id"])).toEqual(["retrieval", "generation"]);
    expect(bars[1].props.type).toBe("button");
    expect(bars[1].props["aria-label"]).toMatch(/generation.*attempt 1/i);
    expect(bars[1].props["aria-current"]).toBe("true");
    act(() => bars[1].props.onClick());
    expect(onSelectNode).toHaveBeenCalledWith("generation");
  });

  it("marks retry, degraded, open, and partial intervals with text and a six-pixel minimum", () => {
    const renderer = renderTimeline(
      [
        interval("retry-node", 10, 11, {
          attempt: 2,
          id: "retry-node:2",
          retry: true,
          status: "degraded",
        }),
        interval("open-node", 30, undefined, { status: "open", partial: true }),
        interval("partial-node", undefined, 90, { status: "failed", partial: true }),
      ],
      [
        wave({
          intervalIds: ["retry-node:2", "open-node:1", "partial-node:1"],
          intervalCount: 3,
          endMs: 100,
        }),
      ],
    );
    const output = JSON.stringify(renderer.toJSON());
    expect(output).toMatch(/重试.*已降级/s);
    expect(output).toMatch(/进行中.*部分记录/s);
    expect(output).toMatch(/失败.*部分记录/s);
    expect(
      renderer.root.findByProps({ "data-interval-id": "retry-node:2" }).props.style.width,
    ).toMatch(/^max\(6px,/);
  });

  it("shows only idle gaps greater than 250ms and keeps the semantic table available", () => {
    const renderer = renderTimeline(
      [interval("a", 0, 100), interval("b", 400, 500)],
      [
        wave({ intervalCount: 1, intervalIds: ["a:1"], endMs: 100 }),
        wave({ id: 2, startMs: 400, endMs: 500, intervalCount: 1, intervalIds: ["b:1"] }),
      ],
      [
        { startMs: 100, endMs: 350, durationMs: 250 },
        { startMs: 100, endMs: 400, durationMs: 300 },
      ],
    );
    const gaps = renderer.root.findAllByProps({ "data-idle-gap": true });
    expect(gaps).toHaveLength(1);
    expect(gaps[0].props["aria-label"]).toMatch(/空闲 300ms/);
    expect(renderer.root.findByProps({ "aria-label": "运行时间线明细表" })).toBeTruthy();
  });
});
it("interleaves waves and gaps chronologically and gives open bars remaining wall time", () => {
  const renderer = renderTimeline([interval("a", 0, 100), interval("b", 400, undefined, { status: "open", partial: true, wave: 2 })], [wave({ intervalCount: 1, intervalIds: ["a:1"], endMs: 100 }), wave({ id: 2, startMs: 400, endMs: 1000, wallTimeMs: 600, intervalCount: 1, intervalIds: ["b:1"] })], [{ startMs: 100, endMs: 400, durationMs: 300 }]);
  const children = renderer.root.findByProps({ className: "run-timeline-waves" }).children as Array<{ props?: Record<string, unknown> }>;
  expect(children.filter((child) => child.props).map((child) => child.props?.className)).toEqual(["run-timeline-wave", "run-timeline-idle", "run-timeline-wave"]);
  expect(renderer.root.findByProps({ "data-interval-id": "b:1" }).props.style.width).toContain("60%");
});
