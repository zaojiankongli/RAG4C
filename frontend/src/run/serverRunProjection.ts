import type { BackendRunEvent } from "../types/rag";
import type { RunDetailDto, RunEventsDto, RunNodeRollupDto } from "../types/runs";
import {
  projectTypedRunEvent,
  projectTypedRunStarted,
  type FlowNodeStatus,
  type FlowRunProjection,
} from "./runProjection";

export interface RunTimelineInterval {
  id: string;
  nodeId: string;
  attempt: number;
  startMs?: number;
  endMs?: number;
  durationMs?: number;
  status: FlowNodeStatus | "open";
  partial: boolean;
  retry: boolean;
  startSeq?: number;
  endSeq?: number;
  wave?: number;
}
export interface RunTimelineWave {
  id: number;
  startMs: number;
  endMs: number;
  wallTimeMs: number;
  intervalCount: number;
  maxConcurrency: number;
  intervalIds: string[];
}
export interface RunIdleGap {
  startMs: number;
  endMs: number;
  durationMs: number;
}
export type RunKnowledgeComponentState = "enabled" | "skipped" | "degraded";
export interface RunKnowledgeComponentFact {
  state: RunKnowledgeComponentState;
  reason?: string;
  attempt?: number;
  durationMs?: number;
}
export interface RunKnowledgeVerificationLayerFact {
  status?: string;
  attempt?: number;
  durationMs?: number;
  count?: number;
  reason?: string;
}
export interface RunKnowledgeVerificationFact {
  missingEvidence?: boolean;
  entailmentEvaluated?: boolean;
  supported?: boolean;
  attempt?: number;
  durationMs?: number;
  reason?: string;
}
export interface RunKnowledgeProjection {
  route?: string;
  effectiveRoute?: string;
  candidateCount?: number;
  chunkCount?: number;
  sourceCount?: number;
  citationCount?: number;
  verification?: RunKnowledgeVerificationFact;
  verificationLayers?: {
    l1?: RunKnowledgeVerificationLayerFact;
    l2?: RunKnowledgeVerificationLayerFact;
    l3?: RunKnowledgeVerificationLayerFact;
  };
  components: {
    hyde?: RunKnowledgeComponentFact;
    subqueries?: RunKnowledgeComponentFact;
    stepback?: RunKnowledgeComponentFact;
    graph?: RunKnowledgeComponentFact;
    rerank?: RunKnowledgeComponentFact;
    sentenceWindow?: RunKnowledgeComponentFact;
  };
  degradedReason?: string;
  fallbackReason?: string;
}
export interface ServerRunProjection {
  flow: FlowRunProjection;
  nodeRollup: RunNodeRollupDto[];
  intervals: RunTimelineInterval[];
  waves: RunTimelineWave[];
  idleGaps: RunIdleGap[];
  knowledge: RunKnowledgeProjection;
  events: BackendRunEvent[];
}

const TERMINALS = new Map<BackendRunEvent["type"], FlowNodeStatus>([
  ["node.completed", "completed"],
  ["node.failed", "failed"],
  ["node.skipped", "skipped"],
  ["node.cancelled", "cancelled"],
]);
function eventKey(event: BackendRunEvent): string | undefined {
  return event.node_id && event.attempt ? `${event.node_id}:${event.attempt}` : undefined;
}
function safeNumber(attributes: Record<string, unknown>, ...keys: string[]): number | undefined {
  for (const key of keys) {
    const value = attributes[key];
    if (typeof value === "number" && Number.isFinite(value) && value >= 0) return value;
  }
  return undefined;
}
function safeString(attributes: Record<string, unknown>, ...keys: string[]): string | undefined {
  for (const key of keys) {
    const value = attributes[key];
    if (typeof value === "string" && value) return value;
  }
  return undefined;
}
function safeBoolean(attributes: Record<string, unknown>, key: string): boolean | undefined {
  const value = attributes[key];
  return typeof value === "boolean" ? value : undefined;
}

function buildIntervals(events: BackendRunEvent[]): RunTimelineInterval[] {
  const intervals = new Map<string, RunTimelineInterval>();
  const retryTargets = new Set<string>();
  for (const event of events) {
    if (event.type === "retry.started" && event.node_id) {
      const target = safeNumber(event.attributes, "target_attempt") ?? (event.attempt ?? 1) + 1;
      retryTargets.add(`${event.node_id}:${target}`);
      continue;
    }
    const key = eventKey(event);
    if (!key || (!event.type.startsWith("node.") && event.type !== "degraded")) continue;
    const existing = intervals.get(key) ?? {
      id: key,
      nodeId: event.node_id!,
      attempt: event.attempt!,
      status: "open" as const,
      partial: true,
      retry: event.attempt! > 1 || retryTargets.has(key),
    };
    if (event.type === "node.started") {
      intervals.set(key, {
        ...existing,
        startMs: event.elapsed_ms,
        startSeq: event.seq,
        endMs: existing.endMs,
        status: "active",
        partial: existing.endMs === undefined,
      });
      continue;
    }
    if (event.type === "degraded") {
      intervals.set(key, {
        ...existing,
        status: "degraded",
        endSeq: Math.max(existing.endSeq ?? 0, event.seq),
      });
      continue;
    }
    const terminal = TERMINALS.get(event.type);
    if (terminal) {
      const duration = event.duration_ms;
      const inferredStart =
        existing.startMs ??
        (duration === undefined ? undefined : Math.max(0, event.elapsed_ms - duration));
      intervals.set(key, {
        ...existing,
        startMs: inferredStart,
        endMs: event.elapsed_ms,
        durationMs:
          duration ?? (inferredStart === undefined ? undefined : event.elapsed_ms - inferredStart),
        endSeq: event.seq,
        status: terminal,
        partial: existing.startMs === undefined,
        retry: existing.retry || retryTargets.has(key),
      });
    }
  }
  return [...intervals.values()].sort(
    (left, right) =>
      (left.startMs ?? Number.POSITIVE_INFINITY) - (right.startMs ?? Number.POSITIVE_INFINITY) ||
      left.nodeId.localeCompare(right.nodeId) ||
      left.attempt - right.attempt,
  );
}

function maxConcurrency(intervals: RunTimelineInterval[], fallbackEnd: number): number {
  const points = new Map<number, { starts: number; ends: number }>();
  for (const interval of intervals) {
    if (interval.startMs === undefined) continue;
    const end = interval.endMs ?? fallbackEnd;
    const startPoint = points.get(interval.startMs) ?? { starts: 0, ends: 0 };
    startPoint.starts += 1;
    points.set(interval.startMs, startPoint);
    const endPoint = points.get(end) ?? { starts: 0, ends: 0 };
    endPoint.ends += 1;
    points.set(end, endPoint);
  }
  let active = 0;
  let maximum = 0;
  for (const [, point] of [...points].sort((left, right) => left[0] - right[0])) {
    active = Math.max(0, active - point.ends);
    active += point.starts;
    maximum = Math.max(maximum, active);
  }
  return maximum;
}
function buildWaves(intervals: RunTimelineInterval[], runEnd: number): RunTimelineWave[] {
  const waves: RunTimelineWave[] = [];
  let members: RunTimelineInterval[] = [];
  let start = 0,
    end = 0;
  const flush = () => {
    if (!members.length) return;
    const id = waves.length + 1;
    waves.push({
      id,
      startMs: start,
      endMs: end,
      wallTimeMs: Math.max(0, end - start),
      intervalCount: members.length,
      maxConcurrency: maxConcurrency(members, end),
      intervalIds: members.map((item) => item.id),
    });
    members.forEach((item) => {
      item.wave = id;
    });
    members = [];
  };
  for (const interval of intervals) {
    if (interval.startMs === undefined) continue;
    const intervalEnd = interval.endMs ?? runEnd;
    if (!members.length) {
      members = [interval];
      start = interval.startMs;
      end = intervalEnd;
      continue;
    }
    if (interval.startMs <= end) {
      members.push(interval);
      end = Math.max(end, intervalEnd);
    } else {
      flush();
      members = [interval];
      start = interval.startMs;
      end = intervalEnd;
    }
  }
  flush();
  return waves;
}
function buildIdleGaps(waves: RunTimelineWave[]): RunIdleGap[] {
  const gaps: RunIdleGap[] = [];
  for (let index = 1; index < waves.length; index += 1) {
    const startMs = waves[index - 1].endMs;
    const endMs = waves[index].startMs;
    if (endMs - startMs > 250) gaps.push({ startMs, endMs, durationMs: endMs - startMs });
  }
  return gaps;
}
const KNOWLEDGE_NODES: ReadonlyMap<string, keyof RunKnowledgeProjection["components"]> = new Map([
  ["plugin.hyde.expand", "hyde"],
  ["plugin.subqueries.expand", "subqueries"],
  ["plugin.stepback.expand", "stepback"],
  ["graph.retrieve", "graph"],
  ["rerank", "rerank"],
  ["sentence_window", "sentenceWindow"],
]);
type VerificationLayerKey = keyof NonNullable<RunKnowledgeProjection["verificationLayers"]>;
const VERIFICATION_NODES: ReadonlyMap<string, VerificationLayerKey> = new Map([
  ["verify.l1", "l1"], ["verify_l1", "l1"], ["verify/l1", "l1"],
  ["verify.l2", "l2"], ["verify_l2", "l2"], ["verify/l2", "l2"],
  ["verify.l3", "l3"], ["verify_l3", "l3"], ["verify/l3", "l3"],
]);
function verificationStatus(type: BackendRunEvent["type"]): string | undefined {
  if (type === "node.started" || type === "retry.started") return "running";
  if (type === "node.completed" || type === "retry.completed") return "completed";
  if (type === "node.failed" || type === "retry.failed") return "failed";
  if (type === "node.skipped" || type === "retry.skipped") return "skipped";
  if (type === "node.cancelled") return "cancelled";
  if (type === "degraded") return "degraded";
  return undefined;
}
function layerCount(layer: VerificationLayerKey, attributes: Record<string, unknown>): number | undefined {
  if (layer === "l1") return safeNumber(attributes, "citation_count", "valid_count");
  if (layer === "l2") return safeNumber(attributes, "valid_count", "citation_count");
  return safeNumber(attributes, "successful_count", "valid_count", "citation_count");
}
function rollupLayerFact(rollup: RunNodeRollupDto): RunKnowledgeVerificationLayerFact {
  const reason = rollup.degraded_reason ?? rollup.error_code ?? rollup.retry_reason ?? undefined;
  return {
    status: rollup.status,
    attempt: rollup.attempt,
    ...(rollup.duration_ms === null ? {} : { durationMs: rollup.duration_ms }),
    ...(reason === undefined ? {} : { reason }),
  };
}
function eventLayerFact(event: BackendRunEvent, layer: VerificationLayerKey): RunKnowledgeVerificationLayerFact {
  const status = verificationStatus(event.type);
  const count = layerCount(layer, event.attributes);
  const reason = safeString(event.attributes, "reason") ?? event.error?.code;
  return {
    ...(status === undefined ? {} : { status }),
    ...(event.attempt === undefined ? {} : { attempt: event.attempt }),
    ...(event.duration_ms === undefined ? {} : { durationMs: event.duration_ms }),
    ...(count === undefined ? {} : { count }),
    ...(reason === undefined ? {} : { reason }),
  };
}

function componentFact(event: BackendRunEvent): RunKnowledgeComponentFact | undefined {
  const reason = safeString(event.attributes, "reason");
  const base = {
    ...(reason ? { reason } : {}),
    ...(event.attempt ? { attempt: event.attempt } : {}),
    ...(event.duration_ms === undefined ? {} : { durationMs: event.duration_ms }),
  };
  if (event.type === "node.skipped") return { state: "skipped", ...base };
  if (event.type === "node.failed" || event.type === "degraded")
    return { state: "degraded", ...base };
  if (event.type === "node.started" || event.type === "node.completed")
    return { state: "enabled", ...base };
  return undefined;
}
function rollupFact(rollup: RunNodeRollupDto): RunKnowledgeComponentFact | undefined {
  const reason = rollup.degraded_reason ?? rollup.error_code ?? rollup.retry_reason ?? undefined;
  const base = {
    ...(reason ? { reason } : {}),
    attempt: rollup.attempt,
    ...(rollup.duration_ms === null ? {} : { durationMs: rollup.duration_ms }),
  };
  if (rollup.status === "skipped" || rollup.status === "retry_skipped")
    return { state: "skipped", ...base };
  if (
    rollup.status === "failed" ||
    rollup.status === "degraded" ||
    rollup.status === "retry_failed"
  )
    return { state: "degraded", ...base };
  if (
    rollup.status === "running" ||
    rollup.status === "completed" ||
    rollup.status === "retry_completed"
  )
    return { state: "enabled", ...base };
  return undefined;
}
function provenFallbackReason(event: BackendRunEvent): string | undefined {
  const reason = safeString(event.attributes, "reason");
  if (!reason) return undefined;
  const hasRouteEvidence =
    safeString(event.attributes, "effective_route") !== undefined ||
    safeString(event.attributes, "fallback_route") !== undefined;
  const isFallbackEvent = event.type === "degraded" || event.type === "route.selected";
  const isKnownFallbackCode =
    reason === "graph_retrieval_failed" ||
    reason === "route_fallback" ||
    reason.startsWith("fallback_") ||
    reason.endsWith("_fallback");
  return (isFallbackEvent && hasRouteEvidence) || isKnownFallbackCode ? reason : undefined;
}

function buildKnowledge(detail: RunDetailDto, events: BackendRunEvent[]): RunKnowledgeProjection {
  const components: RunKnowledgeProjection["components"] = {};
  const verificationLayers: NonNullable<RunKnowledgeProjection["verificationLayers"]> = {};
  const result: RunKnowledgeProjection = { components, verificationLayers };
  if (detail.summary.route) result.route = detail.summary.route;
  for (const rollup of detail.node_rollup) {
    const layer = VERIFICATION_NODES.get(rollup.node_id);
    if (layer) verificationLayers[layer] = rollupLayerFact(rollup);
    const key = KNOWLEDGE_NODES.get(rollup.node_id);
    const fact = key ? rollupFact(rollup) : undefined;
    if (key && fact) components[key] = fact;
  }
  for (const event of events) {
    const attributes = event.attributes;
    result.route ??= safeString(attributes, "route");
    result.effectiveRoute ??= safeString(attributes, "effective_route");
    result.candidateCount ??= safeNumber(attributes, "candidate_count");
    result.chunkCount ??= safeNumber(attributes, "chunk_count");
    result.sourceCount ??= safeNumber(attributes, "source_count");
    result.citationCount ??= safeNumber(attributes, "citation_count");
    const verificationLayer = event.node_id ? VERIFICATION_NODES.get(event.node_id) : undefined;
    if (verificationLayer) verificationLayers[verificationLayer] = { ...verificationLayers[verificationLayer], ...eventLayerFact(event, verificationLayer) };
    if (event.node_id === "verify") {
      const missingEvidence = safeBoolean(attributes, "missing_evidence");
      const entailmentEvaluated = safeBoolean(attributes, "entailment_evaluated");
      const supported = safeBoolean(attributes, "supported");
      const reason = safeString(attributes, "reason");
      if (
        missingEvidence !== undefined ||
        entailmentEvaluated !== undefined ||
        supported !== undefined ||
        event.attempt !== undefined ||
        event.duration_ms !== undefined ||
        reason !== undefined
      ) {
        result.verification = {
          ...(missingEvidence === undefined ? {} : { missingEvidence }),
          ...(entailmentEvaluated === undefined ? {} : { entailmentEvaluated }),
          ...(supported === undefined ? {} : { supported }),
          ...(event.attempt === undefined ? {} : { attempt: event.attempt }),
          ...(event.duration_ms === undefined ? {} : { durationMs: event.duration_ms }),
          ...(reason === undefined ? {} : { reason }),
        };
      }
    }
    const key = event.node_id ? KNOWLEDGE_NODES.get(event.node_id) : undefined;
    const fact = key ? componentFact(event) : undefined;
    if (key && fact) {
      const previous = components[key];
      components[key] =
        previous?.state === "degraded" && fact.state === "enabled"
          ? previous
          : { ...previous, ...fact, reason: fact.reason ?? previous?.reason };
    }
    if (event.type === "degraded") result.degradedReason ??= safeString(attributes, "reason");
    result.fallbackReason ??= provenFallbackReason(event);
  }
  return result;
}

function validatedEvents(detail: RunDetailDto, eventPage: RunEventsDto): BackendRunEvent[] {
  if (eventPage.run_id !== detail.summary.run_id)
    throw new Error("Run detail/events run id mismatch");
  if (
    detail.topology.id !== detail.summary.topology_id ||
    detail.topology.revision !== detail.summary.topology_revision
  )
    throw new Error("Run detail topology revision mismatch");
  for (const event of eventPage.events) {
    if (event.run_id !== detail.summary.run_id) throw new Error("Run event run id mismatch");
    if (event.topology_id !== detail.topology.id) throw new Error("Run event topology id mismatch");
    if (event.topology_revision !== detail.topology.revision)
      throw new Error("Run event topology revision mismatch");
  }
  return [...eventPage.events].sort((left, right) => left.seq - right.seq);
}

export function projectServerFlow(
  detail: RunDetailDto,
  eventPage: RunEventsDto,
): FlowRunProjection {
  const events = validatedEvents(detail, eventPage);
  const started = events.find((event) => event.type === "run.started");
  if (!started) throw new Error("Run history is missing run.started");
  const replayStart: BackendRunEvent = {
    ...started,
    attributes: { ...started.attributes, topology: detail.topology },
  };
  let flow = projectTypedRunStarted("", replayStart, "live_stream");
  if (!flow) throw new Error("Run history topology could not be projected");
  for (const event of events) {
    if (event.seq <= started.seq) continue;
    const next = projectTypedRunEvent(flow, event);
    if (next) flow = next;
  }
  return {
    ...flow,
    topologySource: "server-history",
    durationMs: detail.summary.elapsed_ms,
    startedAt: detail.summary.started_at,
  };
}

export function projectRunTimeline(
  events: readonly BackendRunEvent[],
  runEnd: number,
): Pick<ServerRunProjection, "intervals" | "waves" | "idleGaps"> {
  const intervals = buildIntervals([...events].sort((left, right) => left.seq - right.seq));
  const waves = buildWaves(intervals, runEnd);
  return { intervals, waves, idleGaps: buildIdleGaps(waves) };
}

export function projectServerRun(
  detail: RunDetailDto,
  eventPage: RunEventsDto,
): ServerRunProjection {
  const events = validatedEvents(detail, eventPage);
  const flow = projectServerFlow(detail, { ...eventPage, events });
  const timeline = projectRunTimeline(events, detail.summary.elapsed_ms);
  return {
    flow,
    nodeRollup: detail.node_rollup,
    ...timeline,
    knowledge: buildKnowledge(detail, events),
    events,
  };
}
