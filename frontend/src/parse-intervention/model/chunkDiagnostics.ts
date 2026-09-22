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

export const ENGINE_LABELS: Record<string, string> = {
  fast: "快速通道（文字层直取）",
  vision: "视觉解析（OCR）",
  router: "自动分流",
};

export const PDF_TYPE_LABELS: Record<string, string> = {
  text_based: "有文字层",
  scanned: "扫描件",
  mixed: "混合型",
  image_based: "整页图像",
  other: "非 PDF",
  routing_disabled: "路由已关闭（按配置直接定引擎）",
  classification_failed: "分类失败（走了退路）",
};

export interface EngineDecisionView {
  engine: string | null;
  engineLabel: string;
  provider: string | null;
  pdfType: string | null;
  pdfTypeLabel: string | null;
  routeReason: string | null;
  confidence: number | null;
  /** 分类失败后退到备用引擎的原因 —— 有它就是"这次解析是退路"。 */
  fallbackReason: string | null;
  degraded: boolean;
}

function asText(value: string | undefined): string | null {
  return value && value.trim() ? value.trim() : null;
}

/**
 * 解析引擎这一步的决定：走了哪条路、谁在跑、为什么、是不是退路。
 *
 * 与切分决策是两件事：一次入库先由路由决定用哪个解析引擎，再由切分路由决定怎么切片段。
 * 两者都各有一句"为什么"，混成一行"决策理由"会让操作员以为看到的是同一件事。
 */
export function formatEngineDecision(
  meta:
    | {
        engine?: string;
        provider?: string;
        pdf_type?: string;
        route_reason?: string;
        fallback_reason?: string;
        confidence?: number;
      }
    | null
    | undefined,
): EngineDecisionView {
  const record = meta ?? {};
  const engine = asText(record.engine);
  const provider = asText(record.provider);
  const pdfType = asText(record.pdf_type);
  const routeReason = asText(record.route_reason);
  const fallbackReason = asText(record.fallback_reason);
  const confidence =
    typeof record.confidence === "number" && Number.isFinite(record.confidence)
      ? record.confidence
      : null;
  return {
    engine,
    engineLabel: engine ? (ENGINE_LABELS[engine] ?? engine) : "未记录",
    provider,
    pdfType,
    pdfTypeLabel: pdfType ? (PDF_TYPE_LABELS[pdfType] ?? pdfType) : null,
    routeReason,
    confidence,
    fallbackReason,
    degraded: fallbackReason !== null,
  };
}
