import type {
  EvidenceView, Experiment, JsonValue, LineageView, RunItem, StrategyView, VariantView,
} from "./contracts";

function stripControls(value: string): string { return Array.from(value).filter((character) => { const code = character.charCodeAt(0); return code === 9 || code === 10 || code === 13 || (code >= 32 && code !== 127); }).join(""); }
const SECRET_REF = /\b(?:secret|vault):\/\/[^\s]+/gi;
const CREDENTIAL_URL = /\b[a-z][a-z0-9+.-]*:\/\/[^\s/@:]+:[^\s/@]+@[^\s]+/gi;
const SECRET_ASSIGNMENT = /\b(password|passwd|pwd|token|secret|api[_-]?key|access[_-]?key|authorization|cookie|credential)\s*([=:])\s*([^\s,;]+)/gi;
const BEARER = /\bbearer\s+[A-Za-z0-9._~+/=-]+/gi;

function object(value: JsonValue | undefined): Record<string, JsonValue> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, JsonValue> : null;
}
function string(value: JsonValue | undefined): string | null { return typeof value === "string" ? value : null; }
function number(value: JsonValue | undefined): number | null { return typeof value === "number" && Number.isFinite(value) ? value : null; }
function integer(value: JsonValue | undefined): number | null { const result = number(value); return result !== null && Number.isInteger(result) ? result : null; }
function boolean(value: JsonValue | undefined): boolean | null { return typeof value === "boolean" ? value : null; }

export function sanitizeDisplayText(value: unknown, maxLength = 800): string {
  if (typeof value !== "string") return "";
  const original = value.trim();
  if (/^(?:secret|vault):\/\//i.test(original)) return "[已隐藏敏感引用]";
  let safe = stripControls(original)
    .replace(CREDENTIAL_URL, "[已隐藏凭据网址]")
    .replace(SECRET_REF, "[已隐藏敏感引用]")
    .replace(SECRET_ASSIGNMENT, (_match, label: string, separator: string) => `${label}${separator}[已隐藏]`)
    .replace(BEARER, "Bearer [已隐藏]")
    .replace(/\s+/g, " ");
  if (safe.length > maxLength) safe = `${safe.slice(0, maxLength)}…`;
  return safe;
}

export function abbreviateHash(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const safe = sanitizeDisplayText(value, 128);
  return safe.length > 12 ? `${safe.slice(0, 12)}…` : safe;
}

function lineage(value: Record<string, JsonValue>): LineageView {
  return {
    documentId: string(value.document_id),
    chunkId: string(value.chunk_id),
    documentRevision: integer(value.document_revision),
    contentRevision: integer(value.content_revision),
    contentHash: abbreviateHash(value.content_hash),
  };
}

function evidenceRows(experiment: Experiment): EvidenceView[] {
  const rawResults = experiment.result_snapshot.results;
  if (!Array.isArray(rawResults)) return [];
  const citations = Array.isArray(experiment.evidence_lineage.citations) ? experiment.evidence_lineage.citations : [];
  const byRank = new Map<number, LineageView>();
  for (const raw of citations) {
    const item = object(raw);
    if (!item) continue;
    const rank = integer(item.rank);
    if (rank !== null && rank > 0) byRank.set(rank, lineage(item));
  }
  return rawResults.flatMap((raw): EvidenceView[] => {
    const item = object(raw);
    if (!item) return [];
    const rank = integer(item.rank);
    if (rank === null || rank < 1) return [];
    const source = object(item.source);
    const excerpt = sanitizeDisplayText(string(item.content) ?? string(item.excerpt) ?? "", 900) || null;
    return [{
      rank,
      score: number(item.score),
      denseCosine: number(item.dense_cosine),
      branch: sanitizeDisplayText(string(item.branch) ?? "", 48) || null,
      ...lineage(item),
      excerpt,
      documentName: sanitizeDisplayText(string(source?.document_name) ?? "", 160) || null,
      sourceType: sanitizeDisplayText(string(source?.source_type) ?? "", 64) || null,
      lineage: byRank.get(rank) ?? null,
    }];
  }).sort((a, b) => a.rank - b.rank);
}

function strategy(snapshot: Record<string, JsonValue>): StrategyView {
  return {
    revision: integer(snapshot.strategy_revision),
    routeTarget: string(snapshot.route_target),
    topK: integer(snapshot.top_k),
    hybridSearchOn: boolean(snapshot.hybrid_search_on),
    rerankOn: boolean(snapshot.rerank_on),
    graphRetrievalOn: boolean(snapshot.graph_retrieval_on),
    sentenceWindowOn: boolean(snapshot.sentence_window_on),
    sourceDiversity: string(snapshot.source_diversity),
  };
}

export function projectExperiment(experiment: Experiment | RunItem): VariantView {
  const evidence = evidenceRows(experiment);
  const runItem = experiment as Partial<RunItem>;
  const snapshot = experiment.result_snapshot;
  const traces = Array.isArray(snapshot.traces)
    ? snapshot.traces.filter((item): item is string => typeof item === "string").slice(0, 24).map((item) => sanitizeDisplayText(item, 320)).filter(Boolean)
    : [];
  const generation = integer(experiment.strategy_snapshot.dataset_serving_generation)
    ?? integer(snapshot.dataset_serving_generation)
    ?? integer(experiment.evidence_lineage.dataset_serving_generation);
  const status = experiment.status;
  const state = status === "failed" ? "failed" : evidence.length === 0 ? "no-hit" : "completed";
  return {
    experimentId: experiment.id,
    sequence: experiment.sequence,
    runId: experiment.run_id,
    name: sanitizeDisplayText(runItem.name ?? string(experiment.strategy_snapshot.variant_name) ?? "未命名策略", 64),
    query: sanitizeDisplayText(experiment.query, 20_000),
    status,
    state,
    route: sanitizeDisplayText(runItem.route ?? string(snapshot.route) ?? "", 64) || null,
    latencyMs: experiment.latency_ms,
    resultCount: typeof runItem.result_count === "number" ? runItem.result_count : evidence.length,
    reranked: typeof runItem.reranked === "boolean" ? runItem.reranked : boolean(snapshot.reranked) ?? false,
    degraded: typeof runItem.degraded === "boolean" ? runItem.degraded : boolean(snapshot.degraded) ?? status === "failed",
    datasetServingGeneration: generation,
    createdBy: sanitizeDisplayText(experiment.created_by, 64),
    createdAt: experiment.created_at,
    failureCode: sanitizeDisplayText(string(snapshot.failure_code) ?? "", 80) || null,
    strategy: strategy(experiment.strategy_snapshot),
    evidence,
    traces,
  };
}
