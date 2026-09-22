import { ApiError } from "../../api/client";
import { chunkWorkbenchDeepLink as appChunkWorkbenchDeepLink } from "../../run/appRoute";

/** outcome_code — answered | abstained | cancelled | failed | cached（开放字符串） */
export type AnswerOutcomeCode =
  | "answered"
  | "abstained"
  | "cancelled"
  | "failed"
  | "cached"
  | (string & {});

/** route_code — rag | cache | fallback | abstain 等（开放字符串） */
export type AnswerRouteCode = "rag" | "cache" | "fallback" | "abstain" | (string & {});

/** citation_status — ok | exists_only | stale | unsupported（开放字符串） */
export type CitationStatusCode =
  | "ok"
  | "exists_only"
  | "stale"
  | "unsupported"
  | (string & {});

/** 证据引用：只含身份与状态，不含 chunk 正文 */
export interface AnswerEvidenceRef {
  id: string;
  seq: number;
  chunk_id: string | null;
  chunk_revision_id: string | null;
  document_id: string | null;
  citation_status: CitationStatusCode;
  evidence_digest?: string | null;
  /** qa | document — API 可选；缺省时由 chunk_id 前缀推断 */
  source_kind?: "qa" | "document" | (string & {});
  qa_id?: string | null;
  qa_revision?: number | null;
}

/** chunk_id 约定：qa::{qa_id}（见 retrieval.qa_matcher.qa_chunk_id） */
export function parseQaChunkId(chunkId: string | null | undefined): string | null {
  const raw = (chunkId ?? "").trim();
  if (!raw.startsWith("qa::")) return null;
  const id = raw.slice(4).trim();
  return id || null;
}

export function evidenceSourceKind(
  ref: Partial<Pick<AnswerEvidenceRef, "source_kind" | "chunk_id" | "document_id">> | null | undefined,
): "qa" | "document" {
  if (!ref) return "document";
  const explicit = String(ref.source_kind || "").trim().toLowerCase();
  if (explicit === "qa") return "qa";
  if (explicit === "document") return "document";
  if (parseQaChunkId(ref.chunk_id)) return "qa";
  if (parseQaChunkId(ref.document_id)) return "qa";
  return "document";
}

export function evidenceQaId(
  ref:
    | Partial<Pick<AnswerEvidenceRef, "qa_id" | "chunk_id" | "document_id" | "source_kind">>
    | null
    | undefined,
): string | null {
  if (!ref) return null;
  const explicit = (ref.qa_id ?? "").trim();
  if (explicit) return explicit;
  return parseQaChunkId(ref.chunk_id) || parseQaChunkId(ref.document_id);
}

/** QA 深链：治理页 PAGE key 为 governance（shell 路由 /governance） */
export function qaDeepLink(qaId: string | null | undefined, datasetId?: string | null): string | null {
  const id = (qaId ?? "").trim();
  if (!id) return null;
  const params = new URLSearchParams({ qa: id });
  const dataset = datasetId?.trim();
  if (dataset) params.set("dataset", dataset);
  return `#/governance?${params.toString()}`;
}

/** 仅对真实文档 id 生成 Documents 深链；qa:: 伪 id 返回 null */
export function documentDeepLink(
  documentId: string | null | undefined,
  datasetId?: string | null,
): string | null {
  const id = documentId?.trim();
  if (!id) return null;
  if (parseQaChunkId(id)) return null;
  const params = new URLSearchParams({ document: id });
  const dataset = datasetId?.trim();
  if (dataset) params.set("dataset", dataset);
  return `#/documents?${params.toString()}`;
}

/**
 * 切片级深链：直达解析干预工作区并选中那个 chunk（`…?doc=<id>&chunk=<id>`）。
 * QA 权威条目不是 ChunkHead，返回 null 而不是给出一个打不开的工作区链接。
 */
export function chunkWorkbenchDeepLink(
  documentId: string | null | undefined,
  chunkId: string | null | undefined,
  datasetId?: string | null,
): string | null {
  const doc = documentId?.trim();
  const chunk = chunkId?.trim();
  if (!doc || !chunk) return null;
  if (parseQaChunkId(doc) || parseQaChunkId(chunk)) return null;
  return appChunkWorkbenchDeepLink(doc, chunk, datasetId);
}

export function countQaEvidence(refs: readonly AnswerEvidenceRef[]): number {
  return refs.filter((ref) => evidenceSourceKind(ref) === "qa").length;
}

/**
 * 答案事实（privacy-safe）。MySQL Catalog 是权威；
 * 不含原始问题/答案正文，只含 digest 与可选 redacted preview。
 */
export interface AnswerFact {
  id: string;
  tenant_id: string;
  dataset_id: string;
  run_id: string | null;
  outcome_code: AnswerOutcomeCode;
  route_code: AnswerRouteCode;
  citation_count: number;
  evidence_count: number;
  safe_query_preview: string | null;
  observed_at: string | null;
  request_id_digest?: string | null;
  query_digest?: string | null;
  answer_digest?: string | null;
  evidence_chain_digest?: string | null;
  fact_digest?: string | null;
  evidence_refs: AnswerEvidenceRef[];
}

export interface AnswerFactListResponse {
  items: AnswerFact[];
  count: number;
}

export type AnswerEvidenceErrorKind =
  | "offline"
  | "forbidden"
  | "not-found"
  | "invalid"
  | "unavailable";

export interface AnswerEvidenceErrorView {
  kind: AnswerEvidenceErrorKind;
  title: string;
  description: string;
  canRetry: boolean;
}

export const OUTCOME_LABELS: Record<string, string> = {
  answered: "已回答",
  abstained: "已弃权",
  cancelled: "已取消",
  failed: "失败",
  cached: "缓存命中",
};

export const OUTCOME_COLORS: Record<string, string> = {
  answered: "success",
  abstained: "default",
  cancelled: "default",
  failed: "error",
  cached: "warning",
};

export const ROUTE_LABELS: Record<string, string> = {
  rag: "RAG",
  cache: "缓存",
  fallback: "回退",
  abstain: "弃权",
};

export const CITATION_STATUS_LABELS: Record<string, string> = {
  ok: "引用有效",
  exists_only: "仅存在",
  stale: "过期",
  unsupported: "无支撑",
};

export const CITATION_STATUS_COLORS: Record<string, string> = {
  ok: "success",
  exists_only: "warning",
  stale: "warning",
  unsupported: "error",
};

export const SOURCE_KIND_LABELS: Record<string, string> = {
  qa: "QA 权威",
  document: "文档投影",
};

export function outcomeLabel(code: AnswerOutcomeCode | null | undefined): string {
  if (!code) return "—";
  return OUTCOME_LABELS[code] ?? String(code);
}

export function routeLabel(code: AnswerRouteCode | null | undefined): string {
  if (!code) return "—";
  return ROUTE_LABELS[code] ?? String(code);
}

export function citationStatusLabel(code: CitationStatusCode | null | undefined): string {
  if (!code) return "—";
  return CITATION_STATUS_LABELS[code] ?? String(code);
}

export function citationStatusColor(code: CitationStatusCode | null | undefined): string {
  if (!code) return "default";
  return CITATION_STATUS_COLORS[code] ?? "default";
}

function asStringOrNull(value: unknown): string | null {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? trimmed : null;
  }
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return null;
}

function asNonNegativeInt(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value) && value >= 0) {
    return Math.floor(value);
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed >= 0) return Math.floor(parsed);
  }
  return fallback;
}

export function normalizeAnswerEvidenceRef(payload: unknown): AnswerEvidenceRef | null {
  if (!payload || typeof payload !== "object") return null;
  const record = payload as Record<string, unknown>;
  const id = asStringOrNull(record.id);
  if (!id) return null;
  const chunk_id = asStringOrNull(record.chunk_id);
  const document_id = asStringOrNull(record.document_id);
  const source_kind_raw = asStringOrNull(record.source_kind);
  const qa_id =
    asStringOrNull(record.qa_id) || parseQaChunkId(chunk_id) || parseQaChunkId(document_id);
  const kind: AnswerEvidenceRef["source_kind"] =
    source_kind_raw === "qa" || source_kind_raw === "document"
      ? source_kind_raw
      : qa_id
        ? "qa"
        : "document";
  const qaRevisionRaw = record.qa_revision;
  const qa_revision =
    typeof qaRevisionRaw === "number" && Number.isFinite(qaRevisionRaw)
      ? Math.floor(qaRevisionRaw)
      : typeof qaRevisionRaw === "string" && Number.isFinite(Number(qaRevisionRaw))
        ? Math.floor(Number(qaRevisionRaw))
        : null;
  return {
    id,
    seq: asNonNegativeInt(record.seq),
    chunk_id,
    chunk_revision_id: asStringOrNull(record.chunk_revision_id),
    document_id,
    citation_status: asStringOrNull(record.citation_status) ?? "ok",
    evidence_digest: asStringOrNull(record.evidence_digest),
    source_kind: kind,
    qa_id: qa_id || null,
    qa_revision: kind === "qa" ? qa_revision : null,
  };
}

export function normalizeAnswerFact(payload: unknown): AnswerFact | null {
  if (!payload || typeof payload !== "object") return null;
  const record = payload as Record<string, unknown>;
  const id = asStringOrNull(record.id);
  if (!id) return null;

  const rawRefs = record.evidence_refs;
  const refs = Array.isArray(rawRefs)
    ? rawRefs
        .map(normalizeAnswerEvidenceRef)
        .filter((item): item is AnswerEvidenceRef => item !== null)
        .sort((a, b) => a.seq - b.seq || a.id.localeCompare(b.id))
    : [];

  return {
    id,
    tenant_id: asStringOrNull(record.tenant_id) ?? "",
    dataset_id: asStringOrNull(record.dataset_id) ?? "",
    run_id: asStringOrNull(record.run_id),
    outcome_code: asStringOrNull(record.outcome_code) ?? "answered",
    route_code: asStringOrNull(record.route_code) ?? "rag",
    citation_count: asNonNegativeInt(record.citation_count),
    evidence_count: asNonNegativeInt(record.evidence_count, refs.length),
    safe_query_preview: asStringOrNull(record.safe_query_preview),
    observed_at: asStringOrNull(record.observed_at),
    request_id_digest: asStringOrNull(record.request_id_digest),
    query_digest: asStringOrNull(record.query_digest),
    answer_digest: asStringOrNull(record.answer_digest),
    evidence_chain_digest: asStringOrNull(record.evidence_chain_digest),
    fact_digest: asStringOrNull(record.fact_digest),
    evidence_refs: refs,
  };
}

/** 宽容解析 list 响应：{items,count} | AnswerFact[] | AnswerFact */
export function normalizeAnswerFactList(payload: unknown): AnswerFactListResponse {
  if (Array.isArray(payload)) {
    const items = payload
      .map(normalizeAnswerFact)
      .filter((item): item is AnswerFact => item !== null);
    return { items, count: items.length };
  }
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    if (Array.isArray(record.items)) {
      const items = record.items
        .map(normalizeAnswerFact)
        .filter((item): item is AnswerFact => item !== null);
      return { items, count: asNonNegativeInt(record.count, items.length) };
    }
    const single = normalizeAnswerFact(payload);
    if (single) return { items: [single], count: 1 };
  }
  return { items: [], count: 0 };
}

/** by-run 可能返回单 fact 或 list — 统一成 list 视图 */
export function normalizeAnswerFactOrList(payload: unknown): AnswerFactListResponse {
  return normalizeAnswerFactList(payload);
}

export function projectAnswerEvidenceError(error: unknown): AnswerEvidenceErrorView {
  if (error instanceof ApiError) {
    if (error.kind === "network" || error.kind === "timeout" || error.kind === "aborted") {
      return {
        kind: "offline",
        title: "无法连接 Catalog",
        description: "当前无法读取答案事实。请检查桥服务后重试。",
        canRetry: true,
      };
    }
    if (error.status === 401 || error.status === 403) {
      return {
        kind: "forbidden",
        title: "没有读取权限",
        description: "当前身份不能读取该知识库的答案证据链。",
        canRetry: false,
      };
    }
    if (error.status === 404) {
      return {
        kind: "not-found",
        title: "尚无答案证据",
        description: "该运行或知识库还没有登记答案事实。",
        canRetry: true,
      };
    }
    if (error.status === 400 || error.status === 422) {
      return {
        kind: "invalid",
        title: "查询参数无效",
        description: error.message || "请检查 dataset / run 参数后重试。",
        canRetry: false,
      };
    }
  }
  return {
    kind: "unavailable",
    title: "答案证据链不可用",
    description: "服务未返回可安全展示的结果。请稍后重试。",
    canRetry: true,
  };
}

export function formatObservedAt(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "Z");
}

export function sortEvidenceRefs(refs: readonly AnswerEvidenceRef[]): AnswerEvidenceRef[] {
  return [...refs].sort((a, b) => a.seq - b.seq || a.id.localeCompare(b.id));
}
