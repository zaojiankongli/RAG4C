import type { MetricStat, RecentQuery } from "../types/rag";
import type { RunListDto, RunListFilters, RunSummaryDto } from "../types/runs";

export interface ProtectionSignal {
  key: string;
  label: string;
  hint: string;
  count: number;
  tone: "warning" | "danger" | "neutral";
}

const PROTECTION_DEFS: Array<Omit<ProtectionSignal, "count">> = [
  {
    key: "query.abstained",
    label: "系统选择弃权",
    hint: "资料或核验不足时停止输出不可靠回答的累计次数。",
    tone: "warning",
  },
  {
    key: "query.qa_retrieval.hit",
    label: "QA 权威命中",
    hint: "Catalog 已审核 FAQ 进入检索证据的累计次数。",
    tone: "neutral",
  },
  {
    key: "query.qa_retrieval.no_match",
    label: "QA 无命中",
    hint: "存在有效 FAQ 包但当前查询未命中权威问答的累计次数。",
    tone: "neutral",
  },
  {
    key: "query.qa_retrieval.catalog_error",
    label: "QA 目录读取失败",
    hint: "FAQ 检索读 Catalog 失败的累计次数（不阻断问答，但 QA 权威未参与）。",
    tone: "warning",
  },
  {
    key: "retrieval.rerank.degraded",
    label: "重排降级",
    hint: "重排不可用时退回召回顺序的累计次数。",
    tone: "warning",
  },
  {
    key: "graph.fallback",
    label: "图编排回退",
    hint: "图编排不可用后退回 RAG4C 顺序管线的累计次数。",
    tone: "danger",
  },
  {
    key: "query.round2.skipped",
    label: "跳过无效二轮检索",
    hint: "查询增强全部关闭时，为避免重复相同检索而跳过二轮补救的累计次数。",
    tone: "neutral",
  },
];

export function projectProtectionSignals(
  metrics: Record<string, MetricStat>,
): ProtectionSignal[] {
  return PROTECTION_DEFS.map((definition) => ({
    ...definition,
    count: metrics[definition.key]?.count ?? 0,
  }));
}

function boundedPercent(numerator: number, denominator: number): number {
  if (denominator <= 0) return 0;
  return Math.min(100, Math.max(0, (numerator / denominator) * 100));
}

export function abstentionPercent(
  metrics: Record<string, MetricStat>,
  recent: RecentQuery[],
): number {
  const abstained = metrics["query.abstained"]?.count ?? 0;
  const completed = metrics["query.completed"];
  if (completed) return boundedPercent(abstained, completed.count);

  const legacyTotal = metrics["query.total"];
  if (legacyTotal) return boundedPercent(abstained, legacyTotal.count);

  const recentAbstentions = recent.filter((query) => query.abstained).length;
  return boundedPercent(recentAbstentions, recent.length);
}

export interface CacheLayerLike {
  hit_rate?: number;
  hits?: number;
  misses?: number;
}

export function cacheHitPercent(cache: CacheLayerLike | undefined): number {
  if (!cache) return 0;
  if (cache.hit_rate !== undefined) return cache.hit_rate * 100;
  const hits = cache.hits ?? 0;
  const misses = cache.misses ?? 0;
  return hits + misses > 0 ? (hits / (hits + misses)) * 100 : 0;
}


export type MonitorAttentionGroup = "active" | "stuck" | "slow" | "errors" | "cancelled";
export interface MonitorAttentionItem {
  runId: string;
  group: MonitorAttentionGroup;
  status: RunSummaryDto["status"];
  elapsedMs: number;
  updatedAt: string;
  nodeIds: readonly string[];
  href: string;
}
export const ATTENTION_REQUESTS: ReadonlyArray<{ key: MonitorAttentionGroup; filters: RunListFilters }> = [
  { key: "active", filters: { view: "active", limit: 12 } },
  { key: "stuck", filters: { view: "stuck", limit: 12 } },
  { key: "slow", filters: { view: "slow", limit: 12 } },
  { key: "errors", filters: { view: "errors", limit: 12 } },
  { key: "cancelled", filters: { status: ["cancelled"], limit: 12 } },
];
export type MonitorAttentionGroups = Record<MonitorAttentionGroup, MonitorAttentionItem[]>;
export interface MonitorAttentionResult {
  groups: MonitorAttentionGroups;
  failedGroups: MonitorAttentionGroup[];
}
export function monitorAttentionHref(runId: string, group: MonitorAttentionGroup): string {
  const tab = group === "active" || group === "stuck" || group === "slow" ? "timeline" : "events";
  const view = group === "cancelled" ? "recent" : group;
  const query = new URLSearchParams({ run: runId, tab, view });
  return `/visualize?${query.toString()}`;
}
function attentionItem(summary: RunSummaryDto, group: MonitorAttentionGroup): MonitorAttentionItem {
  return {
    runId: summary.run_id,
    group,
    status: summary.status,
    elapsedMs: summary.elapsed_ms,
    updatedAt: summary.updated_at,
    nodeIds: summary.failed_node_ids.length ? summary.failed_node_ids : summary.current_node_ids,
    href: monitorAttentionHref(summary.run_id, group),
  };
}
export async function loadMonitorAttention(
  fetcher: (filters: RunListFilters) => Promise<RunListDto>,
): Promise<MonitorAttentionResult> {
  const settled = await Promise.allSettled(ATTENTION_REQUESTS.map(({ filters }) => fetcher(filters)));
  const groups: MonitorAttentionGroups = { active: [], stuck: [], slow: [], errors: [], cancelled: [] };
  const failedGroups: MonitorAttentionGroup[] = [];
  settled.forEach((result, index) => {
    const group = ATTENTION_REQUESTS[index].key;
    if (result.status === "fulfilled") groups[group] = result.value.items.map((item) => attentionItem(item, group));
    else failedGroups.push(group);
  });
  return { groups, failedGroups };
}
export function legacyRecentQueryIdentity(query: RecentQuery): string {
  return `${query.ts}:${query.query}`;
}


export type MonitorRunsCapability = "loading" | "available" | "disabled" | "unauthorized" | "legacy" | "unavailable";
export function classifyRunsHealth(
  health?: { enabled: boolean; status: string },
  error?: unknown,
): MonitorRunsCapability {
  if (health) return health.enabled && health.status !== "disabled" ? "available" : "disabled";
  const status = typeof error === "object" && error !== null && "status" in error
    ? Number((error as { status?: unknown }).status)
    : undefined;
  if (status === 404) return "legacy";
  if (status === 401 || status === 403) return "unauthorized";
  return "unavailable";
}
