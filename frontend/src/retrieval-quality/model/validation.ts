import type { ComposerDraft, RetrievalVariant, RouteTarget, RunRetrievalRequest, SourceDiversity } from "./contracts";

const ROUTES = new Set<RouteTarget>(["auto", "hybrid", "vector_graph_rag", "full"]);
const DIVERSITY = new Set<SourceDiversity>(["off", "group_only", "group_mmr"]);
function isSafeAcl(value: string): boolean { return !/["'\\]/.test(value) && !Array.from(value).some((character) => { const code = character.charCodeAt(0); return code < 32 || code === 127; }); }

export interface VariantErrors { name?: string; route_target?: string; top_k?: string; hybrid_search_on?: string; rerank_on?: string; graph_retrieval_on?: string; sentence_window_on?: string; source_diversity?: string; }
export interface ComposerErrors { query?: string; acl?: Record<number, string>; variantsRoot?: string; variants?: Record<number, VariantErrors>; }
export type ComposerValidation = { ok: true; value: RunRetrievalRequest } | { ok: false; errors: ComposerErrors };

export function normalizeRunQuery(value: string): string { return String(value ?? "").trim(); }
export function normalizeHistoryQuery(value: string): string {
  return String(value ?? "").normalize("NFKC").replace(/\s+/g, " ").trim();
}
export function normalizeVariantName(value: string): string { return String(value ?? "").trim(); }

function variantErrors(input: ComposerDraft["variants"][number]): VariantErrors {
  const errors: VariantErrors = {};
  const name = normalizeVariantName(input.name);
  if (!name) errors.name = "请输入策略名称";
  else if (String(input.name).length > 64) errors.name = "策略名称不能超过 64 个字符";
  if (!ROUTES.has(input.route_target)) errors.route_target = "请选择有效路由目标";
  if (typeof input.top_k !== "number" || !Number.isInteger(input.top_k) || input.top_k < 1 || input.top_k > 50) errors.top_k = "Top K 必须是 1 到 50 的整数";
  for (const key of ["hybrid_search_on", "rerank_on", "graph_retrieval_on", "sentence_window_on"] as const) {
    if (typeof input[key] !== "boolean") errors[key] = "开关值无效";
  }
  if (!DIVERSITY.has(input.source_diversity)) errors.source_diversity = "请选择有效来源多样性策略";
  return errors;
}

export function validateComposer(input: ComposerDraft): ComposerValidation {
  const errors: ComposerErrors = {};
  const query = normalizeRunQuery(input.query);
  if (!query) errors.query = "请输入检索问题";
  else if (String(input.query).length > 20_000) errors.query = "检索问题不能超过 20,000 个字符";

  const acl: string[] = [];
  if (!Array.isArray(input.acl) || input.acl.length > 100) errors.acl = { 0: "ACL 最多包含 100 项" };
  else input.acl.forEach((raw, index) => {
    const value = typeof raw === "string" ? raw.trim() : "";
    if (!value || value.length > 256 || !isSafeAcl(value)) {
      (errors.acl ??= {})[index] = "ACL 必须是 1 到 256 个字符的安全字符串";
    } else acl.push(value);
  });

  if (!Array.isArray(input.variants) || input.variants.length < 1 || input.variants.length > 4) errors.variantsRoot = "请配置 1 到 4 个检索策略";
  const names = new Map<string, number>();
  const variants: RetrievalVariant[] = [];
  input.variants?.forEach((draft, index) => {
    const current = variantErrors(draft);
    const name = normalizeVariantName(draft.name);
    const folded = name.toLocaleLowerCase().replace(/ß/g, "ss").replace(/ς/g, "σ");
    if (name) {
      const previous = names.get(folded);
      if (previous !== undefined) current.name = "策略名称不能重复";
      else names.set(folded, index);
    }
    if (Object.keys(current).length) (errors.variants ??= {})[index] = current;
    variants.push({
      name,
      route_target: draft.route_target,
      top_k: draft.top_k,
      hybrid_search_on: draft.hybrid_search_on,
      rerank_on: draft.rerank_on,
      graph_retrieval_on: draft.graph_retrieval_on,
      sentence_window_on: draft.sentence_window_on,
      source_diversity: draft.source_diversity,
    });
  });

  if (Object.keys(errors).length) return { ok: false, errors };
  return { ok: true, value: { query, acl, variants } };
}
