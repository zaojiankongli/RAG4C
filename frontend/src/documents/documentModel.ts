export const IN_FLIGHT_DOCUMENT_STATUSES = new Set(["waiting", "parsing", "splitting", "indexing"]);

export function formatDuration(valueMs: number | null | undefined): string {
  if (valueMs == null || !Number.isFinite(valueMs) || valueMs < 0) return "—";
  if (valueMs < 1) return "<1ms";
  if (valueMs < 10) return `${valueMs.toFixed(1)}ms`;
  if (valueMs < 1000) return `${Math.round(valueMs)}ms`;
  if (valueMs < 60_000) return `${(valueMs / 1000).toFixed(valueMs < 10_000 ? 2 : 1)}s`;
  const minutes = Math.floor(valueMs / 60_000);
  const seconds = Math.round((valueMs % 60_000) / 1000);
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

export function isDocumentSelectable(document: { status: string }): boolean {
  return !IN_FLIGHT_DOCUMENT_STATUSES.has(document.status);
}

export const STAGE_LABELS: Record<string, string> = {
  parse: "解析", clean: "清洗", split: "切分", contextual: "上下文增强",
  embed: "向量计算", insert: "向量写入", graph: "知识图谱",
};
