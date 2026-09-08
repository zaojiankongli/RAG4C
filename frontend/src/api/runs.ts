import type { BackendRunEvent } from "../types/rag";
import type {
  RunAttention,
  RunDetailDto,
  RunEventsDto,
  RunHealthDto,
  RunHistoryState,
  RunListDto,
  RunListFilters,
  RunOutcome,
  RunPersistenceStatus,
  RunStatus,
  RunTopologyDto,
} from "../types/runs";
import { ApiError, request, type RequestOptions } from "./client";

import { parseBackendRunEvent } from "./runEventValidation";
const STATUSES = new Set<RunStatus>(["running", "completed", "failed", "cancelled", "interrupted"]);
const OUTCOMES = new Set<RunOutcome>(["answered", "abstained", "unknown"]);
const ATTENTION = new Set<RunAttention>(["error", "interrupted", "cancelled", "slow", "stuck"]);
const INTEGRITY = new Set(["complete", "partial", "unknown"] as const);
const PERSISTENCE = new Set<RunPersistenceStatus>([
  "pending",
  "durable",
  "partial",
  "memory_only",
  "unavailable",
]);
const HISTORY = new Set<RunHistoryState>(["complete", "partial", "expired"]);
const GROUPS = new Set([
  "input",
  "understand",
  "retrieve",
  "generate",
  "verify",
  "output",
  "extension",
] as const);
const EDGE_KINDS = new Set(["dependency", "conditional", "retry", "failure"] as const);
const EXECUTORS = new Set([
  "sequential_stream",
  "sequential",
  "langgraph",
  "cache_replay",
] as const);
const FORBIDDEN_NESTED_KEYS = [
  "query",
  "question",
  "answer",
  "response",
  "text",
  "prompt",
  "acl",
  "tenant",
  "dataset",
  "document",
  "chunk",
  "embedding",
  "vector",
  "score",
  "scores",
  "api_key",
  "authorization",
  "bearer",
  "cookie",
  "token",
  "message",
  "traceback",
  "stack",
  "path",
] as const;

function invalid(path: string): never {
  throw new Error(`Invalid Runs API response at ${path}`);
}
function record(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) invalid(path);
  return value as Record<string, unknown>;
}
function requiredString(value: unknown, path: string): string {
  if (typeof value !== "string" || !value) invalid(path);
  return value;
}
function nullableString(value: unknown, path: string): string | null {
  return value === null ? null : requiredString(value, path);
}
function boolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") invalid(path);
  return value;
}
function finite(value: unknown, path: string, min = 0): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min) invalid(path);
  return value;
}
function integer(value: unknown, path: string, min = 0): number {
  const parsed = finite(value, path, min);
  if (!Number.isInteger(parsed)) invalid(path);
  return parsed;
}
function literal<T extends string>(value: unknown, values: ReadonlySet<T>, path: string): T {
  if (typeof value !== "string" || !values.has(value as T)) invalid(path);
  return value as T;
}
function stringArray(value: unknown, path: string): string[] {
  if (!Array.isArray(value)) invalid(path);
  return value.map((item, index) => requiredString(item, `${path}[${index}]`));
}
function dateTime(value: unknown, path: string): string {
  const text = requiredString(value, path);
  if (!/[zZ]|[+-]\d\d:\d\d$/.test(text) || !Number.isFinite(Date.parse(text))) invalid(path);
  return text;
}
function nullableDateTime(value: unknown, path: string): string | null {
  return value === null ? null : dateTime(value, path);
}
function schema(value: unknown, path: string): void {
  if (record(value, path).schema_version !== 1) invalid(`${path}.schema_version`);
}
function normalizedKeyForms(key: string): [string, string] {
  const normalized = key.normalize("NFKC");
  let separated = "";
  let previousWasLowerOrDigit = false;
  for (const character of normalized) {
    if (/^[\p{L}\p{N}]$/u.test(character)) {
      if (/^[A-Z]$/.test(character) && previousWasLowerOrDigit && !separated.endsWith("_"))
        separated += "_";
      separated += character.toLowerCase();
      previousWasLowerOrDigit = /^[a-z0-9]$/.test(character);
    } else {
      if (separated && !separated.endsWith("_")) separated += "_";
      previousWasLowerOrDigit = false;
    }
  }
  separated = separated.replace(/^_+|_+$/g, "");
  return [separated, separated.replace(/[^\p{L}\p{N}]/gu, "")];
}
function forbiddenNestedKey(key: string): boolean {
  const [separated, compact] = normalizedKeyForms(key);
  return FORBIDDEN_NESTED_KEYS.some((forbidden) => {
    const [forbiddenSeparated, forbiddenCompact] = normalizedKeyForms(forbidden);
    return separated.includes(forbiddenSeparated) || compact.includes(forbiddenCompact);
  });
}
function jsonRecord(
  value: unknown,
  path: string,
  options: { allowedTopLevel?: ReadonlySet<string>; rejectTopLevelKeys?: boolean } = {},
): Record<string, unknown> {
  const root = record(value, path);
  const visit = (current: unknown, currentPath: string, rejectKeys: boolean): void => {
    if (current === null || typeof current === "string" || typeof current === "boolean") return;
    if (typeof current === "number") {
      if (!Number.isFinite(current)) invalid(currentPath);
      return;
    }
    if (Array.isArray(current)) {
      current.forEach((child, index) => visit(child, `${currentPath}[${index}]`, true));
      return;
    }
    if (typeof current === "object") {
      for (const [key, child] of Object.entries(current as Record<string, unknown>)) {
        if (rejectKeys && forbiddenNestedKey(key)) invalid(`${currentPath}.${key}`);
        visit(child, `${currentPath}.${key}`, true);
      }
      return;
    }
    invalid(currentPath);
  };
  for (const [key, child] of Object.entries(root)) {
    if (options.allowedTopLevel && !options.allowedTopLevel.has(key)) invalid(`${path}.${key}`);
    if (options.rejectTopLevelKeys && forbiddenNestedKey(key)) invalid(`${path}.${key}`);
    visit(child, `${path}.${key}`, true);
  }
  return root;
}

function parseTopology(value: unknown, path: string): RunTopologyDto {
  const item = record(value, path);
  const id = requiredString(item.id, `${path}.id`);
  const revision = requiredString(item.revision, `${path}.revision`);
  const executor = literal(item.executor, EXECUTORS, `${path}.executor`);
  if (!Array.isArray(item.nodes) || !Array.isArray(item.edges)) invalid(path);
  const nodeIds = new Set<string>();
  const nodes = item.nodes.map((raw, index) => {
    const node = record(raw, `${path}.nodes[${index}]`);
    const nodeId = requiredString(node.id, `${path}.nodes[${index}].id`);
    if (nodeIds.has(nodeId)) invalid(`${path}.nodes`);
    nodeIds.add(nodeId);
    return {
      id: nodeId,
      label: requiredString(node.label, `${path}.node.label`),
      group: literal(node.group, GROUPS, `${path}.node.group`),
      description: requiredString(node.description, `${path}.node.description`),
      optional: boolean(node.optional, `${path}.node.optional`),
      repeatable: boolean(node.repeatable, `${path}.node.repeatable`),
      available: boolean(node.available, `${path}.node.available`),
      ...(node.plugin === undefined || node.plugin === null
        ? {}
        : { plugin: requiredString(node.plugin, `${path}.node.plugin`) }),
      attributes: jsonRecord(node.attributes, `${path}.node.attributes`, {
        rejectTopLevelKeys: true,
      }),
    };
  });
  const edgeIds = new Set<string>();
  const edges = item.edges.map((raw) => {
    const edge = record(raw, `${path}.edge`);
    const edgeId = requiredString(edge.id, `${path}.edge.id`);
    const source = requiredString(edge.source, `${path}.edge.source`);
    const target = requiredString(edge.target, `${path}.edge.target`);
    if (edgeIds.has(edgeId) || !nodeIds.has(source) || !nodeIds.has(target))
      invalid(`${path}.edges`);
    edgeIds.add(edgeId);
    return {
      id: edgeId,
      source,
      target,
      kind: literal(edge.kind, EDGE_KINDS, `${path}.edge.kind`),
      ...(edge.label === undefined || edge.label === null
        ? {}
        : { label: requiredString(edge.label, `${path}.edge.label`) }),
    };
  });
  return { id, revision, executor, nodes, edges };
}

export function parseEvent(value: unknown, path: string): BackendRunEvent {
  return parseBackendRunEvent(value, path);
}

function parseSummary(value: unknown, path: string) {
  const item = record(value, path);
  schema(item, path);
  if (!Array.isArray(item.attention)) invalid(`${path}.attention`);
  return {
    schema_version: 1 as const,
    run_id: requiredString(item.run_id, `${path}.run_id`),
    status: literal(item.status, STATUSES, `${path}.status`),
    outcome: literal(item.outcome, OUTCOMES, `${path}.outcome`),
    started_at: dateTime(item.started_at, `${path}.started_at`),
    updated_at: dateTime(item.updated_at, `${path}.updated_at`),
    finished_at: nullableDateTime(item.finished_at, `${path}.finished_at`),
    elapsed_ms: finite(item.elapsed_ms, `${path}.elapsed_ms`),
    boot_id: requiredString(item.boot_id, `${path}.boot_id`),
    worker_id: requiredString(item.worker_id, `${path}.worker_id`),
    topology_id: requiredString(item.topology_id, `${path}.topology_id`),
    topology_revision: requiredString(item.topology_revision, `${path}.topology_revision`),
    executor: literal(item.executor, EXECUTORS, `${path}.executor`),
    last_seq: integer(item.last_seq, `${path}.last_seq`),
    event_count: integer(item.event_count, `${path}.event_count`),
    earliest_available_seq: integer(
      item.earliest_available_seq,
      `${path}.earliest_available_seq`,
      1,
    ),
    current_node_ids: stringArray(item.current_node_ids, `${path}.current_node_ids`),
    failed_node_ids: stringArray(item.failed_node_ids, `${path}.failed_node_ids`),
    route: nullableString(item.route, `${path}.route`),
    degraded_count: integer(item.degraded_count, `${path}.degraded_count`),
    retry_count: integer(item.retry_count, `${path}.retry_count`),
    attention: item.attention.map((entry, index) =>
      literal(entry, ATTENTION, `${path}.attention[${index}]`),
    ),
    event_integrity: literal(item.event_integrity, INTEGRITY, `${path}.event_integrity`),
    persistence_status: literal(item.persistence_status, PERSISTENCE, `${path}.persistence_status`),
    interruption_reason: nullableString(item.interruption_reason, `${path}.interruption_reason`),
    ...(item.query_fingerprint === undefined
      ? {}
      : { query_fingerprint: requiredString(item.query_fingerprint, `${path}.query_fingerprint`) }),
  };
}

function parseRunList(value: unknown): RunListDto {
  const item = record(value, "list");
  schema(item, "list");
  if (!Array.isArray(item.items)) invalid("list.items");
  const retention = record(item.retention, "list.retention");
  return {
    schema_version: 1,
    items: item.items.map((entry, index) => parseSummary(entry, `list.items[${index}]`)),
    next_cursor: nullableString(item.next_cursor, "list.next_cursor"),
    as_of: dateTime(item.as_of, "list.as_of"),
    source: literal(
      item.source,
      new Set(["memory", "sqlite", "memory+sqlite"] as const),
      "list.source",
    ),
    history_state: literal(item.history_state, HISTORY, "list.history_state"),
    retention: {
      days: integer(retention.days, "list.retention.days", 1),
      max_runs: integer(retention.max_runs, "list.retention.max_runs", 1),
    },
  };
}
function parseRunDetail(value: unknown, requestedRunId: string): RunDetailDto {
  const item = record(value, "detail");
  schema(item, "detail");
  if (!Array.isArray(item.node_rollup)) invalid("detail.node_rollup");
  const summary = parseSummary(item.summary, "detail.summary");
  const topology = parseTopology(item.topology, "detail.topology");
  const history = record(item.history, "detail.history");
  const parsedHistory = {
    event_integrity: literal(history.event_integrity, INTEGRITY, "detail.history.event_integrity"),
    earliest_available_seq: integer(history.earliest_available_seq, "detail.history.earliest", 1),
    last_seq: integer(history.last_seq, "detail.history.last_seq"),
    persistence_status: literal(
      history.persistence_status,
      PERSISTENCE,
      "detail.history.persistence",
    ),
  };
  if (summary.run_id !== requestedRunId) invalid("detail.summary.run_id");
  if (summary.topology_id !== topology.id) invalid("detail.summary.topology_id");
  if (summary.topology_revision !== topology.revision) invalid("detail.summary.topology_revision");
  if (summary.executor !== topology.executor) invalid("detail.summary.executor");
  if (summary.last_seq !== parsedHistory.last_seq) invalid("detail.history.last_seq");
  if (summary.earliest_available_seq !== parsedHistory.earliest_available_seq)
    invalid("detail.history.earliest_available_seq");
  return {
    schema_version: 1,
    summary,
    topology,
    node_rollup: item.node_rollup.map((raw, index) => {
      const rollup = record(raw, `detail.node_rollup[${index}]`);
      const optionalNumber = (candidate: unknown, path: string) =>
        candidate === null ? null : finite(candidate, path);
      return {
        node_id: requiredString(rollup.node_id, "rollup.node_id"),
        attempt: integer(rollup.attempt, "rollup.attempt", 1),
        status: requiredString(rollup.status, "rollup.status"),
        started_elapsed_ms: optionalNumber(rollup.started_elapsed_ms, "rollup.started"),
        finished_elapsed_ms: optionalNumber(rollup.finished_elapsed_ms, "rollup.finished"),
        duration_ms: optionalNumber(rollup.duration_ms, "rollup.duration"),
        degraded_reason: nullableString(rollup.degraded_reason, "rollup.degraded"),
        retry_reason: nullableString(rollup.retry_reason, "rollup.retry"),
        error_type: nullableString(rollup.error_type, "rollup.error_type"),
        error_code: nullableString(rollup.error_code, "rollup.error_code"),
      };
    }),
    history: parsedHistory,
  };
}
function parseRunEvents(
  value: unknown,
  requestedRunId: string,
  requestedAfterSeq: number,
): RunEventsDto {
  const item = record(value, "events");
  schema(item, "events");
  if (!Array.isArray(item.events)) invalid("events.events");
  const runId = requiredString(item.run_id, "events.run_id");
  const afterSeq = integer(item.after_seq, "events.after_seq");
  const latestSeq = integer(item.latest_seq, "events.latest_seq");
  const parsedEvents = item.events.map((entry, index) =>
    parseBackendRunEvent(entry, `events.events[${index}]`),
  );
  if (runId !== requestedRunId) invalid("events.run_id");
  let expectedSeq = requestedAfterSeq + 1;
  let topologyId: string | undefined;
  let topologyRevision: string | undefined;
  for (const event of parsedEvents) {
    if (event.run_id !== runId || event.run_id !== requestedRunId) invalid("events.events.run_id");
    if (event.seq !== expectedSeq) invalid("events.events.seq");
    if (event.seq > latestSeq) invalid("events.events.latest_seq");
    topologyId ??= event.topology_id;
    topologyRevision ??= event.topology_revision;
    if (event.topology_id !== topologyId || event.topology_revision !== topologyRevision)
      invalid("events.events.topology");
    expectedSeq += 1;
  }
  const expectedAfterSeq = parsedEvents.length
    ? parsedEvents[parsedEvents.length - 1].seq
    : requestedAfterSeq;
  if (afterSeq !== expectedAfterSeq || latestSeq < afterSeq) invalid("events.after_seq");
  return {
    schema_version: 1,
    run_id: runId,
    events: parsedEvents,
    after_seq: afterSeq,
    latest_seq: latestSeq,
    terminal: boolean(item.terminal, "events.terminal"),
    timed_out: boolean(item.timed_out, "events.timed_out"),
    history_state: literal(item.history_state, HISTORY, "events.history_state"),
    earliest_available_seq: integer(item.earliest_available_seq, "events.earliest", 1),
    persistence_status: literal(item.persistence_status, PERSISTENCE, "events.persistence"),
    retry_after_ms: integer(item.retry_after_ms, "events.retry_after_ms"),
  };
}
function parseRunHealth(value: unknown): RunHealthDto {
  const item = record(value, "health");
  schema(item, "health");
  const memory = record(item.memory, "health.memory");
  const persistence = record(item.persistence, "health.persistence");
  const heartbeat = record(item.heartbeat, "health.heartbeat");
  const retention = record(item.retention, "health.retention");
  return {
    schema_version: 1,
    status: literal(item.status, new Set(["ok", "degraded", "disabled"] as const), "health.status"),
    enabled: boolean(item.enabled, "health.enabled"),
    boot_id: requiredString(item.boot_id, "health.boot_id"),
    worker_id: requiredString(item.worker_id, "health.worker_id"),
    memory: {
      active_runs: integer(memory.active_runs, "memory.active"),
      recent_runs: integer(memory.recent_runs, "memory.recent"),
      event_count: integer(memory.event_count, "memory.events"),
      dropped_runs: integer(memory.dropped_runs, "memory.dropped_runs"),
      dropped_events: integer(memory.dropped_events, "memory.dropped_events"),
    },
    persistence: {
      enabled: boolean(persistence.enabled, "persistence.enabled"),
      state: literal(
        persistence.state,
        new Set(["ready", "read_only", "memory_only", "disabled"] as const),
        "persistence.state",
      ),
      database: requiredString(persistence.database, "persistence.database"),
      wal: boolean(persistence.wal, "persistence.wal"),
      writer_queue_depth: integer(persistence.writer_queue_depth, "persistence.queue"),
      last_commit_at: nullableDateTime(persistence.last_commit_at, "persistence.commit"),
      commit_lag_ms:
        persistence.commit_lag_ms === null
          ? null
          : finite(persistence.commit_lag_ms, "persistence.lag"),
      dropped_mutations: integer(persistence.dropped_mutations, "persistence.dropped"),
      quick_check: literal(
        persistence.quick_check,
        new Set(["ok", "failed", "not_run"] as const),
        "persistence.quick",
      ),
    },
    heartbeat: {
      interval_s: integer(heartbeat.interval_s, "heartbeat.interval", 1),
      stale_after_s: integer(heartbeat.stale_after_s, "heartbeat.stale", 1),
      last_heartbeat_at: nullableDateTime(heartbeat.last_heartbeat_at, "heartbeat.last"),
    },
    retention: {
      days: integer(retention.days, "retention.days", 1),
      max_runs: integer(retention.max_runs, "retention.max", 1),
      memory_terminal_ttl_s: integer(retention.memory_terminal_ttl_s, "retention.ttl"),
    },
    scope_stability: literal(
      item.scope_stability,
      new Set(["installation", "boot"] as const),
      "health.scope_stability",
    ),
  };
}

const FALLBACK_CODES: Record<number, string> = {
  401: "ops_unauthorized",
  403: "ops_forbidden",
  404: "run_not_found",
  409: "run_event_gap",
  429: "run_poll_capacity",
  503: "run_ops_unavailable",
};
export class RunOpsApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly retryAfterSeconds?: number,
    readonly earliestAvailableSeq?: number,
    readonly latestSeq?: number,
  ) {
    super(message);
    this.name = "RunOpsApiError";
  }
}
async function runRequest<T>(
  path: string,
  parser: (value: unknown) => T,
  init?: RequestOptions,
): Promise<T> {
  try {
    return parser(await request<unknown>(path, init));
  } catch (error) {
    if (error instanceof ApiError && error.kind === "http" && error.status) {
      const body =
        typeof error.body === "object" && error.body !== null
          ? (error.body as Record<string, unknown>)
          : {};
      const detail =
        typeof body.detail === "object" && body.detail !== null
          ? (body.detail as Record<string, unknown>)
          : {};
      const retry = error.retryAfter === undefined ? undefined : Number(error.retryAfter);
      throw new RunOpsApiError(
        typeof detail.message === "string" ? detail.message : error.message,
        error.status,
        typeof detail.code === "string"
          ? detail.code
          : (FALLBACK_CODES[error.status] ?? "run_ops_http_error"),
        Number.isFinite(retry) ? retry : undefined,
        typeof detail.earliest_available_seq === "number"
          ? detail.earliest_available_seq
          : undefined,
        typeof detail.latest_seq === "number" ? detail.latest_seq : undefined,
      );
    }
    throw error;
  }
}
export function fetchRunHealth(signal?: AbortSignal): Promise<RunHealthDto> {
  return runRequest("/api/runs/health", parseRunHealth, {
    method: "GET",
    timeoutMs: 8_000,
    signal,
  });
}
export function fetchRuns(filters: RunListFilters = {}, signal?: AbortSignal): Promise<RunListDto> {
  const query = new URLSearchParams();
  if (filters.view) query.set("view", filters.view);
  for (const status of [...new Set(filters.status ?? [])].sort()) query.append("status", status);
  if (filters.slowMs !== undefined) query.set("slow_ms", String(filters.slowMs));
  if (filters.startedAfter) query.set("started_after", filters.startedAfter);
  if (filters.startedBefore) query.set("started_before", filters.startedBefore);
  if (filters.fingerprint) query.set("fingerprint", filters.fingerprint);
  if (filters.limit !== undefined) query.set("limit", String(filters.limit));
  if (filters.cursor) query.set("cursor", filters.cursor);
  const encoded = query.toString();
  return runRequest("/api/runs" + (encoded ? `?${encoded}` : ""), parseRunList, {
    method: "GET",
    signal,
  });
}
export function fetchRunDetail(runId: string, signal?: AbortSignal): Promise<RunDetailDto> {
  return runRequest(
    "/api/runs/" + encodeURIComponent(runId),
    (value) => parseRunDetail(value, runId),
    {
      method: "GET",
      signal,
    },
  );
}
export function fetchRunEvents(
  runId: string,
  options: { afterSeq: number; limit?: number; waitMs?: number; signal?: AbortSignal },
): Promise<RunEventsDto> {
  const query = new URLSearchParams({
    after_seq: String(options.afterSeq),
    limit: String(options.limit ?? 200),
    wait_ms: String(options.waitMs ?? 0),
  });
  return runRequest(
    "/api/runs/" + encodeURIComponent(runId) + "/events?" + query,
    (value) => parseRunEvents(value, runId, options.afterSeq),
    {
      method: "GET",
      timeoutMs: (options.waitMs ?? 0) + 5_000,
      signal: options.signal,
    },
  );
}
