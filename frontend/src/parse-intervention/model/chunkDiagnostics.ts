/** Chunking route diagnostics helpers (parser_meta). */

export const CHUNK_REASON_CODE_LABELS: Record<string, string> = {
  explicit_mode: "配置强制模式",
  table_doc_type: "表格类文档",
  simple_short_no_layout: "短文本且无版面",
  complex_or_structured: "长文或含版面结构",
};

export interface ChunkDecisionFacts {
  doc_type?: string;
  text_chars?: number;
  layout_blocks?: number;
  simple_max_chars?: number;
  configured_mode?: string;
}

export interface ChunkingDecisionView {
  mode: string | null;
  modeLabel: string;
  reason: string | null;
  reasonCode: string | null;
  reasonCodeLabel: string | null;
  facts: ChunkDecisionFacts | null;
  factsSummary: string | null;
}

export const CHUNK_MODE_LABELS: Record<string, string> = {
  auto: "自动路由",
  recursive: "按固定长度",
  parent_child: "按章节结构",
  qa: "表格逐行问答",
};

export function chunkModeLabel(mode: string | null | undefined): string {
  const key = String(mode ?? "").trim();
  if (!key) return "待记录";
  return CHUNK_MODE_LABELS[key] ?? key;
}

function asInt(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return Math.floor(value);
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) {
    return Math.floor(Number(value));
  }
  return null;
}

export function formatChunkingDecision(
  meta:
    | {
        chunking_mode?: string;
        chunking_reason?: string;
        chunking_reason_code?: string;
        chunking_decision?: ChunkDecisionFacts;
      }
    | null
    | undefined,
): ChunkingDecisionView {
  const record = meta ?? {};
  const mode = typeof record.chunking_mode === "string" && record.chunking_mode.trim()
    ? record.chunking_mode.trim()
    : null;
  const reason =
    typeof record.chunking_reason === "string" && record.chunking_reason.trim()
      ? record.chunking_reason.trim()
      : null;
  const reasonCode =
    typeof record.chunking_reason_code === "string" && record.chunking_reason_code.trim()
      ? record.chunking_reason_code.trim()
      : null;
  const rawFacts = record.chunking_decision;
  let facts: ChunkDecisionFacts | null = null;
  if (rawFacts && typeof rawFacts === "object") {
    const f = rawFacts as Record<string, unknown>;
    facts = {
      doc_type: typeof f.doc_type === "string" ? f.doc_type : undefined,
      text_chars: asInt(f.text_chars) ?? undefined,
      layout_blocks: asInt(f.layout_blocks) ?? undefined,
      simple_max_chars: asInt(f.simple_max_chars) ?? undefined,
      configured_mode: typeof f.configured_mode === "string" ? f.configured_mode : undefined,
    };
  }
  let factsSummary: string | null = null;
  if (facts) {
    const parts: string[] = [];
    if (facts.doc_type) parts.push(`类型 ${facts.doc_type}`);
    if (facts.text_chars != null) parts.push(`${facts.text_chars} 字`);
    if (facts.layout_blocks != null) parts.push(`${facts.layout_blocks} 版面块`);
    if (facts.simple_max_chars != null) parts.push(`阈值 ${facts.simple_max_chars}`);
    if (parts.length) factsSummary = parts.join(" · ");
  }
  return {
    mode,
    modeLabel: chunkModeLabel(mode),
    reason,
    reasonCode,
    reasonCodeLabel: reasonCode ? (CHUNK_REASON_CODE_LABELS[reasonCode] ?? reasonCode) : null,
    facts,
    factsSummary,
  };
}

export function chunkingDecisionAria(view: ChunkingDecisionView): string {
  const bits = [view.modeLabel];
  if (view.reasonCodeLabel) bits.push(view.reasonCodeLabel);
  if (view.reason) bits.push(view.reason);
  if (view.factsSummary) bits.push(view.factsSummary);
  return bits.join("；");
}
