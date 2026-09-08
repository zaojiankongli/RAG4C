import type { BackendRunEvent, BackendRunEventType } from "../types/rag";

const EVENT_TYPES = new Set<BackendRunEventType>([
  "run.started",
  "node.started",
  "node.completed",
  "node.failed",
  "node.skipped",
  "node.cancelled",
  "route.selected",
  "retry.started",
  "retry.completed",
  "retry.failed",
  "retry.skipped",
  "degraded",
  "run.completed",
  "run.failed",
  "run.cancelled",
]);
const EVENT_ATTRIBUTE_KEYS = new Set([
  "outcome",
  "reason",
  "request_path",
  "cache_level",
  "mode",
  "executor",
  "executor_requested",
  "executor_used",
  "retry_enabled",
  "delivery",
  "singleflight",
  "queue_admitted",
  "flight_released",
  "cache_write",
  "route",
  "effective",
  "effective_route",
  "fallback_route",
  "configured_route",
  "component",
  "source",
  "enabled",
  "available",
  "skipped",
  "abstained",
  "recoverable",
  "degraded",
  "changed",
  "rewrite_required",
  "output_present",
  "scoped",
  "supported",
  "missing_evidence",
  "entailment_evaluated",
  "target_attempt",
  "last_attempt",
  "attempt_count",
  "top_k",
  "chunk_count",
  "candidate_count",
  "requested_count",
  "input_count",
  "output_count",
  "source_count",
  "citation_count",
  "valid_count",
  "invalid_count",
  "subquery_count",
  "successful_count",
  "failure_count",
  "added_count",
  "added_chunk_count",
  "removed_count",
  "output_chars",
  "token_count",
  "queue_wait_ms",
  "slot_wait_ms",
  "confidence",
  "score_bucket",
  "topology",
]);
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
const GROUPS = new Set([
  "input",
  "understand",
  "retrieve",
  "generate",
  "verify",
  "output",
  "extension",
]);
const EDGE_KINDS = new Set(["dependency", "conditional", "retry", "failure"]);

function invalid(path: string): never {
  throw new Error(`Invalid Runs API response at ${path}`);
}
function record(value: unknown, path: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) invalid(path);
  return value as Record<string, unknown>;
}
function string(value: unknown, path: string): string {
  if (typeof value !== "string" || !value) invalid(path);
  return value;
}
function finite(value: unknown, path: string, min = 0): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min) invalid(path);
  return value;
}
function dateTime(value: unknown, path: string): string {
  const result = string(value, path);
  if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(result) || !Number.isFinite(Date.parse(result))) {
    invalid(path);
  }
  return result;
}
function integer(value: unknown, path: string, min = 0): number {
  const result = finite(value, path, min);
  if (!Number.isInteger(result)) invalid(path);
  return result;
}
function boolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") invalid(path);
  return value;
}
function literal<T extends string>(value: unknown, allowed: ReadonlySet<T>, path: string): T {
  if (typeof value !== "string" || !allowed.has(value as T)) invalid(path);
  return value as T;
}
function normalizedKeyForms(key: string): [string, string] {
  let separated = "";
  let previousWasLowerOrDigit = false;
  for (const character of key.normalize("NFKC")) {
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
function forbiddenKey(key: string): boolean {
  const [separated, compact] = normalizedKeyForms(key);
  return FORBIDDEN_NESTED_KEYS.some((forbidden) => {
    const [forbiddenSeparated, forbiddenCompact] = normalizedKeyForms(forbidden);
    return separated.includes(forbiddenSeparated) || compact.includes(forbiddenCompact);
  });
}
function jsonRecord(
  value: unknown,
  path: string,
  allowedTopLevel?: ReadonlySet<string>,
): Record<string, unknown> {
  const root = record(value, path);
  const visit = (current: unknown, currentPath: string): void => {
    if (current === null || typeof current === "string" || typeof current === "boolean") return;
    if (typeof current === "number") {
      finite(current, currentPath, Number.NEGATIVE_INFINITY);
      return;
    }
    if (Array.isArray(current)) {
      current.forEach((item, index) => visit(item, `${currentPath}[${index}]`));
      return;
    }
    if (typeof current !== "object") invalid(currentPath);
    for (const [key, child] of Object.entries(current as Record<string, unknown>)) {
      if (forbiddenKey(key)) invalid(`${currentPath}.${key}`);
      visit(child, `${currentPath}.${key}`);
    }
  };
  for (const [key, child] of Object.entries(root)) {
    if (allowedTopLevel && !allowedTopLevel.has(key)) invalid(`${path}.${key}`);
    visit(child, `${path}.${key}`);
  }
  return root;
}
function topology(value: unknown, path: string): Record<string, unknown> {
  const item = record(value, path);
  string(item.id, `${path}.id`);
  string(item.revision, `${path}.revision`);
  string(item.executor, `${path}.executor`);
  if (!Array.isArray(item.nodes) || !Array.isArray(item.edges)) invalid(path);
  const nodeIds = new Set<string>();
  for (const [index, raw] of item.nodes.entries()) {
    const node = record(raw, `${path}.nodes[${index}]`);
    const id = string(node.id, `${path}.nodes[${index}].id`);
    if (nodeIds.has(id)) invalid(`${path}.nodes`);
    nodeIds.add(id);
    string(node.label, `${path}.nodes[${index}].label`);
    literal(node.group, GROUPS, `${path}.nodes[${index}].group`);
    string(node.description, `${path}.nodes[${index}].description`);
    boolean(node.optional, `${path}.nodes[${index}].optional`);
    boolean(node.repeatable, `${path}.nodes[${index}].repeatable`);
    boolean(node.available, `${path}.nodes[${index}].available`);
    jsonRecord(node.attributes, `${path}.nodes[${index}].attributes`);
  }
  for (const [index, raw] of item.edges.entries()) {
    const edge = record(raw, `${path}.edges[${index}]`);
    string(edge.id, `${path}.edges[${index}].id`);
    const source = string(edge.source, `${path}.edges[${index}].source`);
    const target = string(edge.target, `${path}.edges[${index}].target`);
    if (!nodeIds.has(source) || !nodeIds.has(target)) invalid(`${path}.edges`);
    literal(edge.kind, EDGE_KINDS, `${path}.edges[${index}].kind`);
  }
  return item;
}

export function parseBackendRunEvent(value: unknown, path = "run_event"): BackendRunEvent {
  const item = record(value, path);
  if (item.schema_version !== 1) invalid(`${path}.schema_version`);
  const type = literal(item.type, EVENT_TYPES, `${path}.type`);
  const nodeId = item.node_id == null ? undefined : string(item.node_id, `${path}.node_id`);
  const attempt = item.attempt == null ? undefined : integer(item.attempt, `${path}.attempt`, 1);
  if ((type.startsWith("node.") || type.startsWith("retry.")) && (!nodeId || attempt === undefined))
    invalid(`${path}.lifecycle`);
  if (type.startsWith("run.") && (nodeId !== undefined || attempt !== undefined))
    invalid(`${path}.lifecycle`);
  const attributes = jsonRecord(item.attributes, `${path}.attributes`, EVENT_ATTRIBUTE_KEYS);
  if (type === "run.started" && attributes.topology !== undefined)
    topology(attributes.topology, `${path}.attributes.topology`);
  const error =
    item.error == null
      ? undefined
      : (() => {
          const errorItem = record(item.error, `${path}.error`);
          return {
            type: string(errorItem.type, `${path}.error.type`),
            ...(errorItem.code == null
              ? {}
              : { code: string(errorItem.code, `${path}.error.code`) }),
            recoverable: boolean(errorItem.recoverable, `${path}.error.recoverable`),
          };
        })();
  return {
    schema_version: 1,
    run_id: string(item.run_id, `${path}.run_id`),
    seq: integer(item.seq, `${path}.seq`, 1),
    occurred_at: dateTime(item.occurred_at, `${path}.occurred_at`),
    elapsed_ms: finite(item.elapsed_ms, `${path}.elapsed_ms`),
    topology_id: string(item.topology_id, `${path}.topology_id`),
    topology_revision: string(item.topology_revision, `${path}.topology_revision`),
    type,
    ...(nodeId ? { node_id: nodeId } : {}),
    ...(attempt === undefined ? {} : { attempt }),
    ...(item.duration_ms == null
      ? {}
      : { duration_ms: finite(item.duration_ms, `${path}.duration_ms`) }),
    attributes,
    ...(error ? { error } : {}),
  };
}
