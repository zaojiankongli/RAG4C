import type { DocumentChunkItem } from "../../types/rag";

export interface ParseScope {
  tenantId: string;
  datasetId: string;
  actorToken: string;
  docId: string;
}

export type ChunkLifecycle = "enabled" | "tombstone" | "unknown";
export type ProjectionState = "current" | "pending" | "failed" | "unknown";
export interface ProjectionLifecycle {
  state: ProjectionState;
  label: string;
  detail: string;
}
export interface ChunkWarning {
  code: "empty" | "parent-missing" | "projection-failed" | "revision-gap";
  label: string;
}
export interface OutlineNode {
  key: string;
  page: string;
  label: string;
  headings: string[];
  chunkIds: string[];
}
export interface SafeMetadataFact {
  key: string;
  label: string;
  value: string;
}
export interface DiffLine {
  kind: "same" | "add" | "remove";
  text: string;
}

const METADATA_LABELS: Record<string, string> = {
  heading: "标题",
  title: "标题",
  section: "章节",
  page: "页码",
  page_number: "页码",
  language: "语言",
  mime_type: "内容类型",
  source: "来源",
  seq: "顺序",
};
const CREDENTIAL_KEY = /(token|secret|password|credential|api[_-]?key|authorization|cookie)/i;
const CJK = /[\u3400-\u9fff\uf900-\ufaff]/g;
const ASCII_WORD = /[A-Za-z0-9]+(?:[._/-][A-Za-z0-9]+)*/g;

export function requireParseScope(input: ParseScope): ParseScope | null {
  const scope = {
    tenantId: input.tenantId.trim(),
    datasetId: input.datasetId.trim(),
    actorToken: input.actorToken.trim(),
    docId: input.docId.trim(),
  };
  return scope.tenantId && scope.datasetId && scope.actorToken && scope.docId ? scope : null;
}

export function sanitizeSourceFact(value: unknown): string {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return "未记录";
  const normalized = raw.replace(/\\/g, "/");
  if (/^[A-Za-z]:\//.test(normalized)) return normalized.split("/").filter(Boolean).pop()?.slice(0, 160) || "受保护的来源引用";
  try {
    const url = new URL(normalized);
    if (url.protocol === "file:") return url.pathname.split("/").filter(Boolean).pop()?.slice(0, 160) || "受保护的来源引用";
    if (!url.hostname) return "受保护的来源引用";
    url.username = ""; url.password = ""; url.search = ""; url.hash = "";
    return url.toString().replace(/\/$/, url.pathname === "/" ? "/" : "");
  } catch {
    if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(normalized)) return "受保护的来源引用";
    if (normalized.includes("/")) return normalized.split("/").filter(Boolean).pop()?.slice(0, 160) || "受保护的来源引用";
    return normalized.slice(0, 160);
  }
}

function metadataValue(key: string, value: unknown): string | null {
  if (["heading", "title", "section", "language", "mime_type", "source"].includes(key)) {
    if (typeof value !== "string") return null;
    const text = value.trim();
    return text ? text.slice(0, 240) : null;
  }
  if (["page", "page_number"].includes(key)) {
    if (typeof value === "string") return value.trim().slice(0, 64) || null;
    if (typeof value === "number" && Number.isInteger(value)) return String(value);
    return null;
  }
  if (key === "seq") {
    return typeof value === "number" && Number.isInteger(value) && value >= 0 ? String(value) : null;
  }
  return null;
}

export function safeMetadataFacts(metadata: Record<string, unknown> = {}): SafeMetadataFact[] {
  const facts: SafeMetadataFact[] = [];
  const seen = new Set<string>();
  for (const [key, label] of Object.entries(METADATA_LABELS)) {
    if (CREDENTIAL_KEY.test(key) || seen.has(label)) continue;
    const value = metadataValue(key, metadata[key]);
    if (!value) continue;
    facts.push({ key, label, value: key === "source" ? sanitizeSourceFact(value) : value });
    seen.add(label);
  }
  return facts;
}


export function estimateTokens(text: string): number {
  const cjk = text.match(CJK)?.length ?? 0;
  const words = text.replace(CJK, " ").match(ASCII_WORD)?.length ?? 0;
  return cjk + words;
}

export function projectChunkLifecycle(chunk: DocumentChunkItem): ChunkLifecycle {
  if (chunk.enabled === false) return "tombstone";
  if (chunk.enabled === true) return "enabled";
  return "unknown";
}

export function projectProjectionLifecycle(chunk: DocumentChunkItem): ProjectionLifecycle {
  if (chunk.index_status === "failed") {
    return { state: "failed", label: "投影失败", detail: "请前往一致性控制台检查失败操作" };
  }
  const desired = chunk.desired_index_revision ?? chunk.content_revision;
  const indexed = chunk.indexed_revision;
  if (chunk.projection_pending || chunk.index_status === "pending" || (indexed !== undefined && indexed < desired)) {
    return { state: "pending", label: "投影待处理", detail: `目标 Revision ${desired}，已索引 ${indexed ?? "未知"}` };
  }
  if (indexed !== undefined && indexed >= desired && chunk.index_status === "ready") {
    return { state: "current", label: "投影响应为当前", detail: `索引 Revision ${indexed}` };
  }
  return { state: "unknown", label: "投影状态未知", detail: "当前接口没有可验证的投影状态" };
}

export function chunkWarnings(chunk: DocumentChunkItem, knownParents: ReadonlySet<string> = new Set(), missingParents: ReadonlySet<string> = new Set()): ChunkWarning[] {
  const warnings: ChunkWarning[] = [];
  if (!chunk.text.trim()) warnings.push({ code: "empty", label: "正文为空" });
  if (chunk.parent_chunk_id && missingParents.has(chunk.parent_chunk_id) && !knownParents.has(chunk.parent_chunk_id)) {
    warnings.push({ code: "parent-missing", label: "后端确认父级不存在" });
  }
  if (chunk.index_status === "failed") warnings.push({ code: "projection-failed", label: "投影失败" });
  if (
    chunk.indexed_revision !== undefined &&
    (chunk.desired_index_revision ?? chunk.content_revision) - chunk.indexed_revision > 1
  ) {
    warnings.push({ code: "revision-gap", label: "投影落后多个 Revision" });
  }
  return warnings;
}

export function buildChunkOutline(chunks: DocumentChunkItem[]): OutlineNode[] {
  const pages = new Map<string, OutlineNode>();
  for (const chunk of [...chunks].sort((a, b) => a.seq - b.seq)) {
    const page = chunk.page === null || chunk.page === undefined || chunk.page === "" ? "未标页" : String(chunk.page);
    const key = `page:${page}`;
    const node = pages.get(key) ?? {
      key,
      page,
      label: page === "未标页" ? "未标页切片" : `第 ${page} 页`,
      headings: [],
      chunkIds: [],
    };
    const heading = chunk.heading?.trim();
    if (heading && !node.headings.includes(heading)) node.headings.push(heading);
    node.chunkIds.push(chunk.chunk_id);
    pages.set(key, node);
  }
  return [...pages.values()];
}

export function buildDiff(before: string, after: string): DiffLine[] {
  const left = before.split("\n");
  const right = after.split("\n");
  const result: DiffLine[] = [];
  const count = Math.max(left.length, right.length);
  for (let index = 0; index < count; index += 1) {
    const oldLine = left[index];
    const newLine = right[index];
    if (oldLine === newLine) result.push({ kind: "same", text: oldLine ?? "" });
    else {
      if (oldLine !== undefined) result.push({ kind: "remove", text: oldLine });
      if (newLine !== undefined) result.push({ kind: "add", text: newLine });
    }
  }
  return result;
}
