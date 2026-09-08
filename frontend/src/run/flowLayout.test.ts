import { describe, expect, it } from "vitest";
import type { FlowEdge, FlowNode } from "./runProjection";
import { layoutFlowGraph } from "./flowLayout";

function node(id: string, group: FlowNode["group"] = "extension"): FlowNode {
  return {
    id,
    label: id,
    description: id,
    group,
    status: "completed",
    details: [],
  };
}

function edge(source: string, target: string): FlowEdge {
  return { id: `${source}->${target}`, source, target, status: "completed", kind: "dependency" };
}

describe("layoutFlowGraph", () => {
  it("places fan-out peers in one rank and the joined node after them", () => {
    const positions = layoutFlowGraph(
      [node("start"), node("vector"), node("graph"), node("merge")],
      [
        edge("start", "vector"),
        edge("start", "graph"),
        edge("vector", "merge"),
        edge("graph", "merge"),
      ],
    );

    expect(positions.start.x).toBeLessThan(positions.vector.x);
    expect(positions.start.x).toBeLessThan(positions.graph.x);
    expect(positions.vector.x).toBeLessThan(positions.merge.x);
    expect(positions.graph.x).toBeLessThan(positions.merge.x);
    expect(positions.vector.x).toBe(positions.graph.x);
    expect(positions.vector.y).not.toBe(positions.graph.y);
  });

  it("ignores retry back-edges for layout so completed nodes do not move backwards", () => {
    const positions = layoutFlowGraph(
      [node("search"), node("generate"), node("verify")],
      [
        edge("search", "generate"),
        edge("generate", "verify"),
        { ...edge("generate", "search"), kind: "retry" },
      ],
    );

    expect(positions.search.x).toBeLessThan(positions.generate.x);
    expect(positions.generate.x).toBeLessThan(positions.verify.x);
  });

  it("uses four ordered business swimlanes for larger composable topologies", () => {
    const nodes = [
      node("receive", "input"),
      node("complexity", "understand"),
      node("rewrite", "understand"),
      node("route", "understand"),
      node("source-diversity", "extension"),
      node("embed", "retrieve"),
      node("search", "retrieve"),
      node("generate", "generate"),
      node("verify", "verify"),
      node("output", "output"),
    ];
    const edges = nodes.slice(1).map((item, index) => edge(nodes[index].id, item.id));

    const positions = layoutFlowGraph(nodes, edges);

    expect(positions.receive.x).toBe(positions.complexity.x);
    expect(positions.complexity.x).toBe(positions.rewrite.x);
    expect(positions.rewrite.x).toBe(positions.route.x);
    expect(positions["source-diversity"].x).toBe(positions.embed.x);
    expect(positions.embed.x).toBe(positions.search.x);
    expect(positions.verify.x).toBe(positions.output.x);
    expect(positions.receive.x).toBeLessThan(positions.search.x);
    expect(positions.search.x).toBeLessThan(positions.generate.x);
    expect(positions.generate.x).toBeLessThan(positions.verify.x);
    expect(
      new Set([positions.receive.y, positions.complexity.y, positions.rewrite.y, positions.route.y])
        .size,
    ).toBe(4);
  });
});
