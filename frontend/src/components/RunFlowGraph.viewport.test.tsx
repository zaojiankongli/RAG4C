// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { FlowRunProjection } from "../run/runProjection";

const capture = vi.hoisted(() => ({ props: null as Record<string, unknown> | null }));
vi.mock("@xyflow/react", () => ({
  ReactFlow: (props: Record<string, unknown>) => {
    capture.props = props;
    return <div>{props.children as React.ReactNode}</div>;
  },
  Background: () => null,
  Controls: () => null,
  Handle: () => null,
  BackgroundVariant: { Dots: "dots" },
  MarkerType: { ArrowClosed: "arrow" },
  Position: { Left: "left", Right: "right" },
}));

import RunFlowGraph from "./RunFlowGraph";

afterEach(cleanup);

const projection: FlowRunProjection = {
  id: "run-nine",
  query: "",
  status: "running",
  topologySource: "server-history",
  durationMs: 10_000,
  nodes: Array.from({ length: 9 }, (_, index) => ({
    id: "node-" + index,
    label: "Readable node " + index,
    description: "Readable status",
    group: index < 3 ? "retrieve" : index < 6 ? "verify" : "generate",
    status:
      index === 1 ? "active" : index === 7 ? "skipped" : index === 8 ? "pending" : "completed",
    details: [],
    attempt: 1,
  })),
  edges: [
    ...Array.from({ length: 8 }, (_, index) => ({
      id: "edge-" + index,
      source: "node-" + index,
      target: "node-" + (index + 1),
      kind: "dependency" as const,
      status: index >= 6 ? ("skipped" as const) : ("completed" as const),
    })),
    {
      id: "retry-edge",
      source: "node-6",
      target: "node-2",
      kind: "retry",
      status: "completed",
      label: "retry retrieval",
    },
  ],
};

describe("RunFlowGraph readable initial viewport", () => {
  it("allows wide swimlanes to fit while keeping drag panning and manual zoom", () => {
    render(<RunFlowGraph projection={projection} onSelectNode={vi.fn()} />);

    expect(capture.props?.fitViewOptions).toEqual(
      expect.objectContaining({ minZoom: expect.any(Number) }),
    );
    expect((capture.props?.fitViewOptions as { minZoom: number }).minZoom).toBeLessThanOrEqual(0.5);
    expect(capture.props?.minZoom).toBeLessThanOrEqual(0.4);
    expect(capture.props?.panOnDrag).toBe(true);
  });

  it("defaults to the executed path and lets the user reveal the full topology", () => {
    render(<RunFlowGraph projection={projection} onSelectNode={vi.fn()} />);

    const toggle = screen.getByRole("button", { name: "显示完整拓扑" });
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByText("已隐藏 2 个未执行节点")).toBeTruthy();
    expect((capture.props?.nodes as unknown[]).length).toBe(7);
    expect(capture.props?.edges as Array<{ id: string; type: string }>).toEqual(
      expect.arrayContaining([expect.objectContaining({ type: "smoothstep" })]),
    );
    expect(
      (capture.props?.edges as Array<{ id: string }>).some(({ id }) => id === "retry-edge"),
    ).toBe(false);

    fireEvent.click(toggle);

    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "只看本次路径" })).toBeTruthy();
    expect((capture.props?.nodes as unknown[]).length).toBe(9);
    expect(
      (capture.props?.edges as Array<{ id: string }>).some(({ id }) => id === "retry-edge"),
    ).toBe(true);
  });
});
