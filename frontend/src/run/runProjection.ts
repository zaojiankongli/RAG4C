import type {
  BackendRunEvent,
  BackendRunTopology,
  QueryResponse,
  RecentQuery,
  StreamPhase,
} from "../types/rag";
import type { RunExecutionSource } from "./runMonitorStore";

export type FlowNodeStatus =
  "pending" | "active" | "completed" | "skipped" | "degraded" | "failed" | "cancelled";

export type FlowRunStatus = "running" | "completed" | "degraded" | "failed" | "cancelled";

export type FlowTopologySource =
  "coarse-phase" | "trace-inferred" | "typed-events" | "server-history";

export type FlowGroup =
  "input" | "understand" | "retrieve" | "generate" | "verify" | "output" | "extension";

export interface FlowNode extends Record<string, unknown> {
  id: string;
  label: string;
  description: string;
  group: FlowGroup;
  status: FlowNodeStatus;
  durationMs?: number;
  attempt?: number;
  details: string[];
}
export type FlowEdgeKind =
  "dependency" | "conditional" | "failure" | "route" | "retry" | "inferred";

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  kind: FlowEdgeKind;
  status: FlowNodeStatus;
  label?: string;
}

export interface FlowRunProjection {
  id: string;
  query: string;
  route?: string;
  status: FlowRunStatus;
  topologySource: FlowTopologySource;
  topologyId?: string;
  topologyRevision?: string;
  executionSource?: RunExecutionSource;
  durationMs: number;
  startedAt?: string;
  nodes: FlowNode[];
  edges: FlowEdge[];
}

export const FLOW_GROUP_LABELS: Record<FlowGroup, string> = {
  input: "入口",
  understand: "问题理解",
  retrieve: "资料检索",
  generate: "回答生成",
  verify: "依据核验",
  output: "输出",
  extension: "扩展步骤",
};

export const FLOW_TOPOLOGY_SOURCE_META: Record<
  FlowTopologySource,
  { label: string; tooltip: string; color: string }
> = {
  "server-history": {
    label: "后端权威历史",
    tooltip:
      "流程与事件来自服务端保存的 canonical seq ledger，并通过实时运行使用的同一 reducer 重放。",
    color: "geekblue",
  },
  "typed-events": {
    label: "后端拓扑",
    tooltip: "节点与连线来自后端在运行开始时发布的权威拓扑快照，状态与耗时由类型化运行事件更新。",
    color: "geekblue",
  },
  "coarse-phase": {
    label: "阶段视图",
    tooltip: "当前接口只提供高层阶段，节点位置会在本次运行内保持不变。",
    color: "blue",
  },
  "trace-inferred": {
    label: "追踪推断",
    tooltip: "旧接口只提供完成后的追踪记录，连线按照记录顺序推断，不代表真实并行依赖。",
    color: "default",
  },
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function topologyFromStartedEvent(event: BackendRunEvent): BackendRunTopology | null {
  const topology = event.attributes.topology;
  if (!isRecord(topology) || !Array.isArray(topology.nodes) || !Array.isArray(topology.edges)) {
    return null;
  }
  if (
    topology.id !== event.topology_id ||
    topology.revision !== event.topology_revision ||
    typeof topology.executor !== "string"
  ) {
    return null;
  }
  const nodes = topology.nodes;
  const edges = topology.edges;
  const nodeIds = new Set<string>();
  for (const node of nodes) {
    if (
      !isRecord(node) ||
      typeof node.id !== "string" ||
      typeof node.label !== "string" ||
      typeof node.description !== "string" ||
      !(
        node.group === "input" ||
        node.group === "understand" ||
        node.group === "retrieve" ||
        node.group === "generate" ||
        node.group === "verify" ||
        node.group === "output" ||
        node.group === "extension"
      ) ||
      nodeIds.has(node.id)
    ) {
      return null;
    }
    nodeIds.add(node.id);
  }
  const edgeIds = new Set<string>();
  for (const edge of edges) {
    if (
      !isRecord(edge) ||
      typeof edge.id !== "string" ||
      typeof edge.source !== "string" ||
      typeof edge.target !== "string" ||
      !nodeIds.has(edge.source) ||
      !nodeIds.has(edge.target) ||
      edgeIds.has(edge.id)
    ) {
      return null;
    }
    edgeIds.add(edge.id);
  }
  return topology as unknown as BackendRunTopology;
}

function attributeNumber(attributes: Record<string, unknown>, key: string): number | undefined {
  const value = attributes[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

const NORMALIZED_REASON_CODE = /^[a-z][a-z0-9_]{0,63}$/;

function eventDetails(event: BackendRunEvent): string[] {
  const details: string[] = [];
  const route = event.attributes.route;
  const reason = event.attributes.reason;
  if (typeof route === "string") details.push(`route: ${route}`);
  if (typeof reason === "string" && NORMALIZED_REASON_CODE.test(reason)) {
    details.push(`reason: ${reason}`);
  }
  if (event.error) {
    details.push(`error category: ${event.error.type}`);
    if (event.error.code) details.push(`error code: ${event.error.code}`);
    details.push(`recoverable: ${event.error.recoverable ? "yes" : "no"}`);
  }
  return details;
}

function updateIncomingEdges(
  edges: FlowEdge[],
  nodeId: string,
  status: FlowNodeStatus,
): FlowEdge[] {
  return edges.map((edge) =>
    edge.target === nodeId && edge.kind !== "retry" ? { ...edge, status } : edge,
  );
}

function settleTerminalNodes(
  nodes: FlowNode[],
  terminal: "completed" | "failed" | "cancelled",
): FlowNode[] {
  return nodes.map((node) => {
    if (node.status === "pending") return { ...node, status: "skipped" };
    if (node.status !== "active") return node;
    return { ...node, status: terminal };
  });
}

function settleTerminalEdges(
  edges: FlowEdge[],
  terminal: "completed" | "failed" | "cancelled",
): FlowEdge[] {
  return edges.map((edge) => {
    if (edge.status === "pending") return { ...edge, status: "skipped" };
    if (edge.status !== "active") return edge;
    return { ...edge, status: terminal };
  });
}

export function projectTypedRunStarted(
  query: string,
  event: BackendRunEvent,
  executionSource: RunExecutionSource,
): FlowRunProjection | null {
  if (event.type !== "run.started" || event.seq !== 1) return null;
  const topology = topologyFromStartedEvent(event);
  if (!topology) return null;
  return {
    id: event.run_id,
    query,
    status: "running",
    topologySource: "typed-events",
    topologyId: event.topology_id,
    topologyRevision: event.topology_revision,
    executionSource,
    durationMs: event.elapsed_ms,
    startedAt: event.occurred_at,
    nodes: topology.nodes.map((node) => ({
      id: node.id,
      label: node.label,
      description: node.description,
      group: node.group,
      status: node.available ? "pending" : "skipped",
      details: [],
    })),
    edges: topology.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      kind: edge.kind,
      status: "pending",
      label: edge.label,
    })),
  };
}

export function projectTypedRunEvent(
  projection: FlowRunProjection,
  event: BackendRunEvent,
): FlowRunProjection | null {
  if (
    projection.topologySource !== "typed-events" ||
    event.run_id !== projection.id ||
    event.topology_id !== projection.topologyId ||
    event.topology_revision !== projection.topologyRevision ||
    event.type === "run.started"
  ) {
    return null;
  }
  const base = { ...projection, durationMs: event.elapsed_ms };
  if (event.type.startsWith("run.")) {
    const terminal =
      event.type === "run.completed"
        ? "completed"
        : event.type === "run.failed"
          ? "failed"
          : "cancelled";
    const settledNodes = settleTerminalNodes(projection.nodes, terminal);
    const edges = settleTerminalEdges(projection.edges, terminal);
    const abstained = terminal === "completed" && event.attributes.outcome === "abstained";
    const degraded =
      terminal === "completed" &&
      (abstained || settledNodes.some((node) => node.status === "degraded"));
    const terminalDetails = eventDetails(event);
    if (abstained && !terminalDetails.some((detail) => detail.startsWith("reason: "))) {
      const priorReason = projection.nodes
        .flatMap((node) => node.details)
        .reverse()
        .find((detail) => detail.startsWith("reason: "));
      if (priorReason) terminalDetails.push(priorReason);
    }
    const activeNodeIds = new Set(
      projection.nodes.filter((node) => node.status === "active").map((node) => node.id),
    );
    const failedNodeIds = new Set(
      projection.nodes.filter((node) => node.status === "failed").map((node) => node.id),
    );
    const nodes = settledNodes.map((node): FlowNode => {
      const isAbstainedOutput = abstained && node.group === "output";
      const isFailureTarget =
        terminal === "failed" &&
        (activeNodeIds.size > 0
          ? activeNodeIds.has(node.id)
          : failedNodeIds.size > 0
            ? failedNodeIds.has(node.id)
            : node.group === "output");
      if (!isAbstainedOutput && !isFailureTarget) return node;
      return {
        ...node,
        status: isAbstainedOutput ? "degraded" : node.status,
        details: [
          ...node.details,
          ...terminalDetails.filter((detail) => !node.details.includes(detail)),
        ],
      };
    });
    return {
      ...base,
      status: degraded ? "degraded" : terminal,
      nodes,
      edges,
    };
  }

  const nodeId = event.node_id;
  if (!nodeId || !projection.nodes.some((node) => node.id === nodeId)) return null;
  if (event.type.startsWith("retry.")) {
    const retryStatus: FlowNodeStatus =
      event.type === "retry.started"
        ? "active"
        : event.type === "retry.completed"
          ? "completed"
          : event.type === "retry.failed"
            ? "failed"
            : "skipped";
    const targetAttempt =
      attributeNumber(event.attributes, "target_attempt") ?? (event.attempt ?? 1) + 1;
    return {
      ...base,
      nodes: projection.nodes.map((node) =>
        node.id === nodeId
          ? {
              ...node,
              attempt: Math.max(node.attempt ?? 1, targetAttempt),
              details: [...node.details, ...eventDetails(event)],
            }
          : node,
      ),
      edges: projection.edges.map((edge) =>
        edge.kind === "retry" && edge.target === nodeId
          ? {
              ...edge,
              status: retryStatus,
              label: edge.label ?? `Retry attempt ${targetAttempt}`,
            }
          : edge,
      ),
    };
  }
  if (event.type === "route.selected") {
    const route = event.attributes.route;
    return {
      ...base,
      route: typeof route === "string" ? route : projection.route,
      nodes: projection.nodes.map((node) =>
        node.id === nodeId ? { ...node, details: [...node.details, ...eventDetails(event)] } : node,
      ),
    };
  }

  const nextStatus: FlowNodeStatus =
    event.type === "node.started"
      ? "active"
      : event.type === "node.completed"
        ? "completed"
        : event.type === "node.failed"
          ? "failed"
          : event.type === "node.cancelled"
            ? "cancelled"
            : event.type === "node.skipped"
              ? "skipped"
              : "degraded";
  const nodes = projection.nodes.map((node): FlowNode => {
    if (node.id !== nodeId) return node;
    const status =
      nextStatus === "completed" && node.status === "degraded" ? "degraded" : nextStatus;
    return {
      ...node,
      status,
      attempt: event.attempt ?? node.attempt,
      durationMs: event.duration_ms ?? node.durationMs,
      details: [...node.details, ...eventDetails(event)],
    };
  });
  return {
    ...base,
    nodes,
    edges: updateIncomingEdges(projection.edges, nodeId, nextStatus),
  };
}

export function projectTypedLocalTerminal(
  projection: FlowRunProjection,
  status: "completed" | "degraded" | "cancelled",
  durationMs: number,
  executionSource: RunExecutionSource,
): FlowRunProjection {
  const terminal = status === "cancelled" ? "cancelled" : "completed";
  const settledNodes = settleTerminalNodes(projection.nodes, terminal);
  return {
    ...projection,
    status,
    executionSource,
    durationMs,
    nodes:
      status === "degraded"
        ? settledNodes.map((node): FlowNode =>
            node.group === "output" ? { ...node, status: "degraded" } : node,
          )
        : settledNodes,
    edges: settleTerminalEdges(projection.edges, terminal),
  };
}

interface StageMeta {
  label: string;
  description: string;
  group: FlowGroup;
}

const STAGE_META: Record<string, StageMeta> = {
  gate: { label: "复杂度判断", description: "判断问题需要经过哪些处理", group: "understand" },
  rewrite: { label: "问题改写", description: "把问题转换成更适合检索的表达", group: "understand" },
  route: { label: "意图路由", description: "选择本次问答需要的资料路径", group: "understand" },
  hyde: { label: "假设文档", description: "生成辅助检索的假设内容", group: "retrieve" },
  embed: { label: "查询向量化", description: "提取问题的语义表示", group: "retrieve" },
  search: { label: "混合检索", description: "从知识库查找候选资料", group: "retrieve" },
  subqueries: { label: "子问题检索", description: "拆分问题并并行查找资料", group: "retrieve" },
  stepback: { label: "后退式提问", description: "补充更高层次的检索视角", group: "retrieve" },
  diversity: { label: "来源多样性", description: "减少重复来源并补充不同资料", group: "retrieve" },
  graph: { label: "图谱检索", description: "沿实体和关系补充关联资料", group: "retrieve" },
  rerank: { label: "结果精排", description: "保留与问题最相关的候选资料", group: "retrieve" },
  sentence_window: {
    label: "上下文回取",
    description: "补齐命中片段附近的上下文",
    group: "retrieve",
  },
  generate: { label: "生成回答", description: "根据候选资料组织回答", group: "generate" },
  verify: { label: "引用核验", description: "确认答案内容有资料支持", group: "verify" },
  abstain: { label: "充分性判断", description: "资料不足时停止生成不可靠回答", group: "verify" },
};

const SPAN_RE = /^([A-Za-z_][A-Za-z0-9_.-]*):([\d.]+)ms$/;

const LEGACY_RULES: Array<{ key: string; pattern: RegExp }> = [
  { key: "gate", pattern: /\[gate\]|复杂度门控/i },
  { key: "rewrite", pattern: /\[rewrite\]|查询改写/i },
  { key: "route", pattern: /\[(?:router|route)\]|路由决策/i },
  { key: "hyde", pattern: /\[hyde\]|假设文档/i },
  { key: "subqueries", pattern: /\[subqueries\]|子查询/i },
  { key: "stepback", pattern: /\[stepback\]|后退式/i },
  { key: "embed", pattern: /\[embed\]|向量化/i },
  { key: "diversity", pattern: /\[diversity\]|来源多样性/i },
  { key: "graph", pattern: /\[graph\]|图谱|实体检索|子图扩展|关系检索/i },
  { key: "rerank", pattern: /\[rerank\]|精排/i },
  { key: "sentence_window", pattern: /\[sentence_window\]|父块回取/i },
  { key: "search", pattern: /\[(?:retrieval|search)\]|混合检索/i },
  { key: "generate", pattern: /\[(?:generation|generate)\]|生成答案/i },
  { key: "verify", pattern: /\[verify(?:\/[^\]]+)?\]|引用.*校验|蕴含判定/i },
  { key: "abstain", pattern: /\[abstention\]|弃权|拒绝作答/i },
];

function genericMeta(key: string): StageMeta {
  return {
    label: key.replace(/[_.-]+/g, " "),
    description: "由当前流程动态注册的执行步骤",
    group: "extension",
  };
}

function stageMeta(key: string): StageMeta {
  return STAGE_META[key] ?? genericMeta(key);
}

function makeEdge(
  source: FlowNode,
  target: FlowNode,
  status: FlowNodeStatus,
  kind: FlowEdgeKind = "dependency",
): FlowEdge {
  return { id: `${source.id}->${target.id}`, source: source.id, target: target.id, status, kind };
}

function connect(
  nodes: FlowNode[],
  status: FlowNodeStatus,
  kind: FlowEdgeKind = "dependency",
): FlowEdge[] {
  const edges: FlowEdge[] = [];
  for (let index = 1; index < nodes.length; index += 1) {
    edges.push(makeEdge(nodes[index - 1], nodes[index], status, kind));
  }
  return edges;
}

export function projectRecentQuery(query: RecentQuery): FlowRunProjection {
  const uniqueTraces = Array.from(new Set(query.traces ?? []));
  const stages = new Map<string, FlowNode>();

  for (const trace of uniqueTraces) {
    const line = trace.trim();
    const span = SPAN_RE.exec(line);
    const key = span?.[1] ?? LEGACY_RULES.find((rule) => rule.pattern.test(line))?.key;
    if (!key || key === "done" || key === "verify_l2" || key === "verify_l3") continue;

    const normalizedKey = key.startsWith("verify_") ? "verify" : key;
    const existing = stages.get(normalizedKey);
    if (existing) {
      if (span) existing.durationMs = (existing.durationMs ?? 0) + Number(span[2]);
      if (!span) existing.details.push(trace);
      continue;
    }

    const meta = stageMeta(normalizedKey);
    stages.set(normalizedKey, {
      id: normalizedKey,
      label: meta.label,
      description: meta.description,
      group: meta.group,
      status: "completed",
      durationMs: span ? Number(span[2]) : undefined,
      details: span ? [] : [trace],
    });
  }

  const input: FlowNode = {
    id: "run-input",
    label: "接收问题",
    description: "锁定本次运行使用的配置与流程",
    group: "input",
    status: "completed",
    details: [query.query],
  };
  const output: FlowNode = {
    id: "run-output",
    label: query.abstained ? "资料不足，未生成回答" : "返回回答",
    description: query.abstained ? "系统没有找到足够可靠的依据" : "回答和引用已返回问答页面",
    group: "output",
    status: query.abstained ? "degraded" : "completed",
    details: [`${query.citations} 条引用`],
  };
  const nodes = [input, ...stages.values(), output];
  const edges = connect(nodes, "completed", "inferred");
  if (query.abstained && edges.length > 0) edges[edges.length - 1].status = "degraded";

  return {
    id: `history-${query.ts}-${query.query}`,
    query: query.query,
    route: query.route,
    status: query.abstained ? "degraded" : "completed",
    topologySource: "trace-inferred",
    durationMs: query.duration_ms,
    startedAt: query.ts,
    nodes,
    edges,
  };
}

export interface LivePhaseInput {
  query: string;
  phase: StreamPhase | string | null;
  elapsedMs: number;
  phaseElapsedMs?: number;
  phaseDurations?: Record<string, number>;
  route?: string;
  chunks?: number;
  retrievalAttempt?: number;
  startedAt?: string;
}

function liveNode(
  id: string,
  status: FlowNodeStatus,
  options: { attempt?: number; durationMs?: number; details?: string[] } = {},
): FlowNode {
  const meta = stageMeta(id);
  return {
    id,
    label: meta.label,
    description: meta.description,
    group: meta.group,
    status,
    attempt: options.attempt,
    durationMs: options.durationMs,
    details: options.details ?? [],
  };
}

function sumDurations(durations: Record<string, number>, phases: string[]): number | undefined {
  let total = 0;
  let found = false;
  for (const phase of phases) {
    if (durations[phase] !== undefined) {
      total += durations[phase];
      found = true;
    }
  }
  return found ? total : undefined;
}

function addCurrentDuration(
  recorded: number | undefined,
  active: boolean,
  phaseElapsedMs: number | undefined,
): number | undefined {
  if (!active || phaseElapsedMs === undefined) return recorded;
  return (recorded ?? 0) + phaseElapsedMs;
}

export function projectLivePhase(input: LivePhaseInput): FlowRunProjection {
  const phase = input.phase ?? "retrieving";
  const retrying = phase === "retrieving_again";
  const retrievalAttempt = input.retrievalAttempt ?? (retrying ? 2 : 1);
  const retrievalActive = retrying || phase === "retrieving";
  const retrievalDone = phase === "retrieved" || phase === "generating" || phase === "verifying";
  const generationActive = phase === "retrieved" || phase === "generating";
  const generationDone = phase === "verifying" || retrying;
  const durations = input.phaseDurations ?? {};
  const searchDetails = [
    ...(input.route ? [`检索路线：${input.route}`] : []),
    ...(input.chunks !== undefined ? [`命中 ${input.chunks} 个候选片段`] : []),
  ];

  const inputNode: FlowNode = {
    id: "run-input",
    label: "接收问题",
    description: "本次问答已经开始",
    group: "input",
    status: "completed",
    details: [input.query],
  };
  const search = liveNode(
    "search",
    retrievalActive ? "active" : retrievalDone ? "completed" : "pending",
    {
      attempt: retrievalAttempt,
      durationMs: addCurrentDuration(
        sumDurations(durations, ["retrieving", "retrieving_again"]),
        retrievalActive,
        input.phaseElapsedMs,
      ),
      details: searchDetails,
    },
  );
  const generate = liveNode(
    "generate",
    generationDone ? "completed" : generationActive ? "active" : "pending",
    {
      durationMs: addCurrentDuration(
        sumDurations(durations, ["retrieved", "generating"]),
        generationActive,
        input.phaseElapsedMs,
      ),
    },
  );
  const verifyActive = phase === "verifying";
  const verify = liveNode("verify", verifyActive ? "active" : "pending", {
    durationMs: addCurrentDuration(
      sumDurations(durations, ["verifying"]),
      verifyActive,
      input.phaseElapsedMs,
    ),
  });
  const output: FlowNode = {
    id: "run-output",
    label: "返回回答",
    description: "等待回答和引用完成",
    group: "output",
    status: "pending",
    details: [],
  };
  const nodes = [inputNode, search, generate, verify, output];
  const edges = connect(nodes, "pending");

  for (const edge of edges) {
    const target = nodes.find((node) => node.id === edge.target);
    if (target?.status === "completed") edge.status = "completed";
    if (target?.status === "active") edge.status = "active";
  }
  if (retrievalAttempt > 1) {
    edges.push({
      id: "generate->search:retry-2",
      source: "generate",
      target: "search",
      status: retrying ? "active" : "completed",
      kind: "retry",
      label: "第 2 次检索",
    });
  }

  return {
    id: "live-run",
    query: input.query,
    route: input.route,
    status: "running",
    topologySource: "coarse-phase",
    durationMs: input.elapsedMs,
    startedAt: input.startedAt,
    nodes,
    edges,
  };
}

interface CompletedLiveRunInput extends Omit<LivePhaseInput, "phase"> {
  response: QueryResponse;
  source: RunExecutionSource;
}

function traceDetails(traces: string[], pattern: RegExp): string[] {
  return Array.from(new Set(traces.filter((trace) => pattern.test(trace))));
}

export function projectCompletedLiveRun(input: CompletedLiveRunInput): FlowRunProjection {
  const response = input.response;
  const route = input.route ?? response.result.route;
  const base = projectLivePhase({ ...input, route, phase: "verifying", phaseElapsedMs: 0 });
  const degraded = response.result.abstained || input.source === "demo" || response.using_mock;
  const nodes = base.nodes.map((node): FlowNode => {
    if (node.id === "run-input") {
      return {
        ...node,
        details: [
          input.query,
          ...traceDetails(response.result.traces, /\[(?:gate|router|route|rewrite)\]/i),
        ],
      };
    }
    if (node.id === "search") {
      return {
        ...node,
        status: "completed",
        details: [
          ...(route ? [`检索路线：${route}`] : []),
          ...(input.chunks !== undefined ? [`命中 ${input.chunks} 个候选片段`] : []),
          ...traceDetails(
            response.result.traces,
            /\[(?:retrieval|search|graph|rerank|hyde|subqueries|stepback|diversity|sentence_window)\]/i,
          ),
        ],
      };
    }
    if (node.id === "generate") {
      return {
        ...node,
        status: "completed",
        details: traceDetails(response.result.traces, /\[(?:generation|generate)\]/i),
      };
    }
    if (node.id === "verify") {
      return {
        ...node,
        status: "completed",
        details: traceDetails(response.result.traces, /\[(?:verify|abstention)/i),
      };
    }
    return {
      ...node,
      label: degraded
        ? input.source === "demo" || response.using_mock
          ? "展示演示回答"
          : "资料不足，未生成回答"
        : "返回回答",
      description: degraded
        ? input.source === "demo" || response.using_mock
          ? "真实服务不可用，本次展示的是演示数据"
          : "系统没有找到足够可靠的依据"
        : "回答和引用已返回问答页面",
      status: degraded ? "degraded" : "completed",
      details: [`${response.result.citations.length} 条引用`],
    };
  });
  const edges = base.edges
    .filter((edge) => edge.kind !== "retry")
    .map((edge, index, all): FlowEdge => ({
      ...edge,
      status: degraded && index === all.length - 1 ? "degraded" : "completed",
    }));

  return {
    ...base,
    route,
    status: degraded ? "degraded" : "completed",
    executionSource: input.source,
    durationMs: response.duration_ms || input.elapsedMs,
    nodes,
    edges,
  };
}

interface TerminalLiveRunInput extends LivePhaseInput {
  status: "failed" | "cancelled";
  error?: string;
}

export function projectTerminalLiveRun(input: TerminalLiveRunInput): FlowRunProjection {
  const projection = projectLivePhase(input);
  const terminalStatus: FlowNodeStatus = input.status;
  const nodes = projection.nodes.map((node): FlowNode => {
    if (node.id === "run-output") {
      return {
        ...node,
        label: input.status === "cancelled" ? "运行已取消" : "运行失败",
        description: input.status === "cancelled" ? "这次问答已由用户停止" : "执行过程中发生错误",
        status: terminalStatus,
        details: input.error ? [input.error] : [],
      };
    }
    if (node.status === "active") return { ...node, status: terminalStatus };
    if (node.status === "pending") return { ...node, status: "skipped" };
    return node;
  });
  const edges = projection.edges.map((edge): FlowEdge => {
    if (edge.status === "active") return { ...edge, status: terminalStatus };
    if (edge.status === "pending") return { ...edge, status: "skipped" };
    return edge;
  });

  return { ...projection, status: input.status, nodes, edges };
}
