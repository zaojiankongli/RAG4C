import type { FlowEdge, FlowNode } from "./runProjection";

export type FlowGraphMode = "path" | "full";

export interface FlowGraphView {
  nodes: FlowNode[];
  edges: FlowEdge[];
  hiddenNodeCount: number;
  retryNodeIds: ReadonlySet<string>;
}

const EXECUTED_STATUSES = new Set<FlowNode["status"]>([
  "active",
  "completed",
  "degraded",
  "failed",
  "cancelled",
]);

function isExecutedStatus(status: FlowNode["status"]): boolean {
  return EXECUTED_STATUSES.has(status);
}

export function buildFlowGraphView(
  nodes: FlowNode[],
  edges: FlowEdge[],
  mode: FlowGraphMode,
): FlowGraphView {
  const retryNodeIds = new Set(
    edges.filter((edge) => edge.kind === "retry").map((edge) => edge.target),
  );

  if (mode === "full") {
    return { nodes, edges, hiddenNodeCount: 0, retryNodeIds };
  }

  const visibleNodes = nodes.filter((node) => isExecutedStatus(node.status));
  const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));
  const visibleEdges = edges.filter(
    (edge) =>
      edge.kind !== "retry" &&
      isExecutedStatus(edge.status) &&
      visibleNodeIds.has(edge.source) &&
      visibleNodeIds.has(edge.target),
  );

  return {
    nodes: visibleNodes,
    edges: visibleEdges,
    hiddenNodeCount: nodes.length - visibleNodes.length,
    retryNodeIds,
  };
}

export function relatedExecutedNodeIds(
  selectedNodeId: string,
  nodes: FlowNode[],
  edges: FlowEdge[],
): ReadonlySet<string> {
  const related = new Set([selectedNodeId]);
  const executedNodeIds = new Set(
    nodes.filter((node) => isExecutedStatus(node.status)).map((node) => node.id),
  );
  if (!executedNodeIds.has(selectedNodeId)) return related;

  const neighbours = new Map<string, string[]>();
  for (const edge of edges) {
    if (
      edge.kind === "retry" ||
      !isExecutedStatus(edge.status) ||
      !executedNodeIds.has(edge.source) ||
      !executedNodeIds.has(edge.target)
    ) {
      continue;
    }
    neighbours.set(edge.source, [...(neighbours.get(edge.source) ?? []), edge.target]);
    neighbours.set(edge.target, [...(neighbours.get(edge.target) ?? []), edge.source]);
  }

  const queue = [selectedNodeId];
  for (let index = 0; index < queue.length; index += 1) {
    const current = queue[index];
    for (const neighbour of neighbours.get(current) ?? []) {
      if (related.has(neighbour)) continue;
      related.add(neighbour);
      queue.push(neighbour);
    }
  }
  return related;
}
