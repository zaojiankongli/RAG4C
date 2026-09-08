import { describe, expect, it } from "vitest";
import type { FlowEdge, FlowNode } from "./runProjection";
import { buildFlowGraphView, relatedExecutedNodeIds } from "./flowGraphView";

function node(id: string, status: FlowNode["status"]): FlowNode {
  return {
    id,
    label: id,
    description: id,
    group: "extension",
    status,
    details: [],
  };
}

function edge(
  source: string,
  target: string,
  status: FlowEdge["status"],
  kind: FlowEdge["kind"] = "dependency",
): FlowEdge {
  return { id: `${source}->${target}:${kind}`, source, target, status, kind };
}

describe("buildFlowGraphView", () => {
  it("shows only the executed path and turns retry edges into target-node markers", () => {
    const nodes = [
      node("input", "completed"),
      node("route", "completed"),
      node("search", "skipped"),
      node("answer", "active"),
      node("output", "pending"),
    ];
    const edges = [
      edge("input", "route", "completed"),
      edge("route", "search", "skipped", "route"),
      edge("route", "answer", "active", "route"),
      edge("answer", "route", "completed", "retry"),
      edge("answer", "output", "pending"),
    ];

    const view = buildFlowGraphView(nodes, edges, "path");

    expect(view.nodes.map(({ id }) => id)).toEqual(["input", "route", "answer"]);
    expect(view.edges.map(({ id }) => id)).toEqual([
      "input->route:dependency",
      "route->answer:route",
    ]);
    expect(view.hiddenNodeCount).toBe(2);
    expect([...view.retryNodeIds]).toEqual(["route"]);
  });

  it("restores every node and edge in full-topology mode", () => {
    const nodes = [node("input", "completed"), node("unused", "skipped")];
    const edges = [edge("input", "unused", "skipped", "conditional")];

    const view = buildFlowGraphView(nodes, edges, "full");

    expect(view.nodes).toEqual(nodes);
    expect(view.edges).toEqual(edges);
    expect(view.hiddenNodeCount).toBe(0);
  });
});

describe("relatedExecutedNodeIds", () => {
  it("keeps the selected executed branch while excluding skipped branches", () => {
    const nodes = [
      node("input", "completed"),
      node("route", "completed"),
      node("search", "skipped"),
      node("answer", "active"),
      node("output", "pending"),
    ];
    const edges = [
      edge("input", "route", "completed"),
      edge("route", "search", "skipped", "route"),
      edge("route", "answer", "active", "route"),
      edge("answer", "output", "pending"),
    ];

    expect([...relatedExecutedNodeIds("route", nodes, edges)].sort()).toEqual([
      "answer",
      "input",
      "route",
    ]);
  });
});
