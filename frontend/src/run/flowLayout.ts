import dagre from "@dagrejs/dagre";
import type { FlowEdge, FlowNode } from "./runProjection";

export const FLOW_NODE_WIDTH = 218;
export const FLOW_NODE_HEIGHT = 92;

export interface FlowPosition {
  x: number;
  y: number;
}

const BUSINESS_LANE_BY_GROUP: Record<FlowNode["group"], number> = {
  input: 0,
  understand: 0,
  retrieve: 1,
  extension: 1,
  generate: 2,
  verify: 3,
  output: 3,
};

function layoutBusinessSwimlanes(nodes: FlowNode[]): Record<string, FlowPosition> {
  const laneCounts = [0, 0, 0, 0];
  const laneGap = 72;
  const rowGap = 46;

  return Object.fromEntries(
    nodes.map((node) => {
      const lane = BUSINESS_LANE_BY_GROUP[node.group];
      const row = laneCounts[lane];
      laneCounts[lane] += 1;
      return [
        node.id,
        {
          x: 24 + lane * (FLOW_NODE_WIDTH + laneGap),
          y: 24 + row * (FLOW_NODE_HEIGHT + rowGap),
        },
      ];
    }),
  );
}

export function layoutFlowGraph(
  nodes: FlowNode[],
  edges: FlowEdge[],
): Record<string, FlowPosition> {
  if (nodes.length >= 7) return layoutBusinessSwimlanes(nodes);

  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({ rankdir: "LR", ranksep: 78, nodesep: 34, marginx: 24, marginy: 24 });

  for (const node of nodes) {
    graph.setNode(node.id, { width: FLOW_NODE_WIDTH, height: FLOW_NODE_HEIGHT });
  }
  for (const edge of edges) {
    if (edge.kind === "retry" || !graph.hasNode(edge.source) || !graph.hasNode(edge.target))
      continue;
    graph.setEdge(edge.source, edge.target);
  }

  dagre.layout(graph);
  const positions: Record<string, FlowPosition> = {};
  for (const node of nodes) {
    const point = graph.node(node.id) as { x: number; y: number };
    positions[node.id] = {
      x: point.x - FLOW_NODE_WIDTH / 2,
      y: point.y - FLOW_NODE_HEIGHT / 2,
    };
  }
  return positions;
}
