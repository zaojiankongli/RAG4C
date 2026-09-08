import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import {
  CheckCircleFilled,
  CloseCircleFilled,
  LoadingOutlined,
  MinusCircleFilled,
  PauseCircleFilled,
  WarningFilled,
} from "../ui/icons";
import "@xyflow/react/dist/style.css";
import { FLOW_GROUP_LABELS, type FlowNode, type FlowRunProjection } from "../run/runProjection";
import { layoutFlowGraph, type FlowPosition } from "../run/flowLayout";
import {
  buildFlowGraphView,
  relatedExecutedNodeIds,
  type FlowGraphMode,
} from "../run/flowGraphView";

interface Props {
  projection: FlowRunProjection;
  selectedNodeId?: string;
  onSelectNode: (nodeId: string) => void;
  sequenceByNode?: Readonly<Record<string, string>>;
}

type RunNodeData = FlowNode & {
  onActivate: (nodeId: string) => void;
  sequenceLabel?: string;
  hasRetry: boolean;
  dimmed: boolean;
};
type RunGraphNode = Node<RunNodeData, "runStep">;

const STATUS_LABEL: Record<FlowNode["status"], string> = {
  pending: "等待中",
  active: "执行中",
  completed: "已完成",
  skipped: "已跳过",
  degraded: "已降级",
  failed: "失败",
  cancelled: "已取消",
};

const layoutCache = new Map<string, Record<string, FlowPosition>>();

function topologyKey(nodes: FlowNode[], edges: FlowRunProjection["edges"]): string {
  const nodeKey = nodes.map((node) => `${node.id}:${node.group}`).join("|");
  const edgeKey = edges
    .filter((edge) => edge.kind !== "retry")
    .map((edge) => `${edge.source}>${edge.target}:${edge.kind}`)
    .sort()
    .join("|");
  return `${nodeKey}::${edgeKey}`;
}

function cachedLayout(
  nodes: FlowNode[],
  edges: FlowRunProjection["edges"],
): Record<string, FlowPosition> {
  const key = topologyKey(nodes, edges);
  const cached = layoutCache.get(key);
  if (cached) return cached;
  const positions = layoutFlowGraph(nodes, edges);
  layoutCache.set(key, positions);
  if (layoutCache.size > 48) {
    const oldest = layoutCache.keys().next().value;
    if (oldest) layoutCache.delete(oldest);
  }
  return positions;
}

function StatusIcon({ status }: { status: FlowNode["status"] }) {
  switch (status) {
    case "active":
      return <LoadingOutlined spin />;
    case "completed":
      return <CheckCircleFilled />;
    case "degraded":
      return <WarningFilled />;
    case "failed":
      return <CloseCircleFilled />;
    case "cancelled":
      return <PauseCircleFilled />;
    case "skipped":
      return <MinusCircleFilled />;
    case "pending":
      return <span className="run-node-pending-dot" aria-hidden="true" />;
  }
}

function RunStepNode({ data, selected }: NodeProps<RunGraphNode>) {
  const stateClasses = [
    "run-flow-node",
    `is-${data.status}`,
    selected ? "is-selected" : "",
    data.dimmed ? "is-dimmed" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      type="button"
      className={stateClasses}
      onClick={() => data.onActivate(data.id)}
      data-testid={`flow-node-${data.id}`}
      aria-current={selected ? "true" : undefined}
      aria-label={`${FLOW_GROUP_LABELS[data.group]}，${data.label}，${STATUS_LABEL[data.status]}${data.hasRetry ? "，发生过重试" : ""}`}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <div className="run-flow-node-head">
        <span className="run-flow-node-group">{FLOW_GROUP_LABELS[data.group]}</span>
        <span className="run-flow-node-status">
          <StatusIcon status={data.status} />
          {STATUS_LABEL[data.status]}
        </span>
      </div>
      <div className="run-flow-node-title-row">
        <div className="run-flow-node-title">{data.label}</div>
        {data.hasRetry ? (
          <span className="run-flow-node-retry" title="此步骤发生过重试">
            <span aria-hidden="true">↻</span> 重试
          </span>
        ) : null}
      </div>
      <div className="run-flow-node-desc">{data.description}</div>
      <div className="run-flow-node-meta">
        <span>{data.sequenceLabel ?? "seq —"}</span>
        <span>attempt {data.attempt ?? 1}</span>
        {data.durationMs !== undefined ? (
          <span className="tabular-nums">{(data.durationMs / 1000).toFixed(2)}s</span>
        ) : null}
      </div>
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </button>
  );
}

const NODE_TYPES = { runStep: RunStepNode };

function edgeColor(status: FlowNode["status"]): string {
  if (status === "active") return "var(--color-primary)";
  if (status === "completed") return "var(--color-success)";
  if (status === "degraded") return "var(--color-warning)";
  if (status === "failed" || status === "cancelled") return "var(--color-danger)";
  return "var(--color-border-strong)";
}

function hasImportantEdgeStatus(status: FlowNode["status"]): boolean {
  return (
    status === "active" || status === "degraded" || status === "failed" || status === "cancelled"
  );
}

export default function RunFlowGraph({
  projection,
  selectedNodeId,
  onSelectNode,
  sequenceByNode,
}: Props) {
  const [mode, setMode] = useState<FlowGraphMode>("path");
  const flowInstanceRef = useRef<ReactFlowInstance<RunGraphNode, Edge> | null>(null);

  useEffect(() => setMode("path"), [projection.id]);

  const view = useMemo(
    () => buildFlowGraphView(projection.nodes, projection.edges, mode),
    [mode, projection.edges, projection.nodes],
  );
  const positions = useMemo(() => cachedLayout(view.nodes, view.edges), [view.edges, view.nodes]);
  const visibleNodeIds = useMemo(() => new Set(view.nodes.map((node) => node.id)), [view.nodes]);
  const relatedNodeIds = useMemo(
    () =>
      selectedNodeId
        ? relatedExecutedNodeIds(selectedNodeId, projection.nodes, projection.edges)
        : null,
    [projection.edges, projection.nodes, selectedNodeId],
  );

  const centerNode = useCallback(
    (nodeId: string, duration = 220) => {
      if (!visibleNodeIds.has(nodeId)) return;
      void flowInstanceRef.current?.fitView({
        nodes: [{ id: nodeId }],
        padding: 1.3,
        minZoom: 0.72,
        maxZoom: 1.08,
        duration,
      });
    },
    [visibleNodeIds],
  );

  const handleActivate = useCallback(
    (nodeId: string) => {
      onSelectNode(nodeId);
      centerNode(nodeId);
    },
    [centerNode, onSelectNode],
  );

  const handleInit = useCallback(
    (instance: ReactFlowInstance<RunGraphNode, Edge>) => {
      flowInstanceRef.current = instance;
      if (selectedNodeId && visibleNodeIds.has(selectedNodeId)) centerNode(selectedNodeId, 0);
    },
    [centerNode, selectedNodeId, visibleNodeIds],
  );

  useEffect(() => {
    if (selectedNodeId && visibleNodeIds.has(selectedNodeId)) {
      centerNode(selectedNodeId, 180);
      return;
    }
    void flowInstanceRef.current?.fitView({
      padding: 0.08,
      minZoom: 0.42,
      maxZoom: 1,
      duration: 180,
    });
  }, [centerNode, mode, selectedNodeId, visibleNodeIds]);

  const nodes = useMemo<RunGraphNode[]>(
    () =>
      view.nodes.map((node) => ({
        id: node.id,
        type: "runStep",
        position: positions[node.id],
        selected: node.id === selectedNodeId,
        focusable: true,
        ariaLabel: `${FLOW_GROUP_LABELS[node.group]}，${node.label}，${STATUS_LABEL[node.status]}`,
        data: {
          ...node,
          onActivate: handleActivate,
          sequenceLabel: sequenceByNode?.[node.id],
          hasRetry: view.retryNodeIds.has(node.id),
          dimmed: relatedNodeIds !== null && !relatedNodeIds.has(node.id),
        },
      })),
    [handleActivate, positions, relatedNodeIds, selectedNodeId, sequenceByNode, view],
  );

  const edges = useMemo<Edge[]>(
    () =>
      view.edges.map((edge) => {
        const dimmed =
          relatedNodeIds !== null &&
          (!relatedNodeIds.has(edge.source) || !relatedNodeIds.has(edge.target));
        return {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          type: "smoothstep",
          label: mode === "full" || hasImportantEdgeStatus(edge.status) ? edge.label : undefined,
          animated: edge.status === "active",
          className: `run-flow-edge is-${edge.status} is-${edge.kind}${dimmed ? " is-dimmed" : ""}`,
          style: {
            stroke: edgeColor(edge.status),
            strokeWidth: edge.status === "active" ? 2.4 : 1.6,
          },
          labelStyle: { fill: "var(--color-text-secondary)", fontSize: 11 },
          labelBgStyle: { fill: "var(--color-bg-elevated)", fillOpacity: 0.92 },
          markerEnd: { type: MarkerType.ArrowClosed, color: edgeColor(edge.status) },
        };
      }),
    [mode, relatedNodeIds, view.edges],
  );

  const activeLabels = projection.nodes
    .filter((node) => node.status === "active")
    .map((node) => node.label)
    .join("、");

  return (
    <div
      className="run-flow-canvas"
      role="region"
      aria-label={`问答执行流程，当前显示 ${nodes.length} 个节点`}
    >
      <div className="run-flow-toolbar" aria-label="流程图显示选项">
        {mode === "path" && view.hiddenNodeCount > 0 ? (
          <span className="run-flow-hidden-count">已隐藏 {view.hiddenNodeCount} 个未执行节点</span>
        ) : null}
        <button
          type="button"
          className="run-flow-mode-toggle"
          aria-pressed={mode === "full"}
          onClick={() => setMode((current) => (current === "path" ? "full" : "path"))}
        >
          {mode === "path" ? "显示完整拓扑" : "只看本次路径"}
        </button>
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.08, minZoom: 0.42, maxZoom: 1 }}
        minZoom={0.35}
        panOnDrag
        maxZoom={1.5}
        nodesDraggable={false}
        nodesConnectable={false}
        elevateNodesOnSelect={false}
        onInit={handleInit}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
        <Controls showInteractive={false} position="bottom-left" />
      </ReactFlow>
      <span className="sr-only" role="status" aria-live="polite">
        {activeLabels ? `当前执行：${activeLabels}` : "当前没有正在执行的节点"}
      </span>
      <ol className="sr-only">
        {view.nodes.map((node) => (
          <li key={node.id}>
            {FLOW_GROUP_LABELS[node.group]}，{node.label}，{STATUS_LABEL[node.status]}
          </li>
        ))}
      </ol>
    </div>
  );
}
