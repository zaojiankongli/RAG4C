export type PageKey =
  | "overview"
  | "query"
  | "documents"
  | "taxonomy"
  | "governance"
  | "sources"
  | "retrieval-lab"
  | "visualize"
  | "eval"
  | "monitor"
  | "consistency"
  | "enterprise"
  | "notifications"
  | "recycle-bin"
  | "tasks"
  | "automations"
  | "knowledge-bases"
  | "knowledge-base-workspace"
  | "parse-intervention"
  | "config";

export const PAGE_KEYS: readonly PageKey[] = [
  "overview",
  "query",
  "documents",
  "taxonomy",
  "governance",
  "sources",
  "retrieval-lab",
  "visualize",
  "eval",
  "monitor",
  "consistency",
  "enterprise",
  "notifications",
  "recycle-bin",
  "tasks",
  "automations",
  "knowledge-bases",
  "knowledge-base-workspace",
  "parse-intervention",
  "config",
];

export interface AppLocationLike {
  pathname: string;
  search: string;
  hash: string;
}

const ENTERPRISE_NOTIFICATIONS_PATH = "/enterprise/notifications";
const ENTERPRISE_RECYCLE_BIN_PATH = "/enterprise/recycle-bin";
const ENTERPRISE_TASKS_PATH = "/enterprise/tasks";
const ENTERPRISE_AUTOMATIONS_PATH = "/enterprise/automations";
const ENTERPRISE_KNOWLEDGE_BASES_PATH = "/enterprise/knowledge-bases";
const ENTERPRISE_KNOWLEDGE_BASE_WORKSPACE_PATH = "/enterprise/knowledge-base";
/**
 * 解析干预工作区的权威路由；`/documents/parse?doc=` 继续作为老深链别名解析（见
 * `parseChunkWorkbenchLocation`），由文档页就地接管渲染同一个工作区组件。
 */
export const PARSE_INTERVENTION_PATH = "/parse-intervention";
export const LEGACY_PARSE_INTERVENTION_PATH = "/documents/parse";
/** 治理面 shell 路由（`PAGE_KEYS.governance`）；QA 权威深链 `#/governance?qa=…` 挂在这条路径上。 */
export const GOVERNANCE_PATH = "/governance";

/** 治理面 QA 深链解析结果：`qaId` 已 trim，缺省为空串（调用方据此判定"无深链"）。 */
export interface ParsedGovernanceQaDeepLink {
  qaId: string;
}

/**
 * 解析一致性/证据面板点入治理面时携带的 QA 权威深链 `#/governance?qa=<id>`
 * （history 与 #hash 两种形态都解析，与切片工作区深链同一惯例）。只取 `qa`：
 * `dataset` 半段由 `workspaceScope.readKnowledgeDatasetIdFromLocation` 负责预筛选，
 * 这里不重复认领，避免同一参数两处口径。落在非治理路径或无 `qa` 时返回空串。
 */
export function parseGovernanceQaDeepLink(
  location: AppLocationLike,
): ParsedGovernanceQaDeepLink {
  const params = workbenchParamsAt(location, GOVERNANCE_PATH);
  return { qaId: params?.get("qa")?.trim() ?? "" };
}

export interface ChunkWorkbenchParams {
  docId: string;
  chunkId?: string;
  datasetId?: string;
}

/** 解析结果：三个字段都已归一化为字符串，调用方不必再判 undefined。 */
export interface ParsedChunkWorkbenchLocation {
  docId: string;
  chunkId: string;
  datasetId: string;
}

function normalizedPath(value: string): string {
  const path = value.replace(/^#/, "").split(/[?#]/, 1)[0] ?? "/";
  return path.replace(/\/+$/, "") || "/";
}

function keyOf(value: string): PageKey | null {
  const normalized = normalizedPath(value);
  if (normalized === ENTERPRISE_NOTIFICATIONS_PATH) return "notifications";
  if (normalized === ENTERPRISE_RECYCLE_BIN_PATH) return "recycle-bin";
  if (normalized === ENTERPRISE_TASKS_PATH) return "tasks";
  if (normalized === ENTERPRISE_AUTOMATIONS_PATH) return "automations";
  if (normalized === ENTERPRISE_KNOWLEDGE_BASES_PATH) return "knowledge-bases";
  if (normalized === ENTERPRISE_KNOWLEDGE_BASE_WORKSPACE_PATH) return "knowledge-base-workspace";
  if (normalized === PARSE_INTERVENTION_PATH) return "parse-intervention";
  const key = normalized.replace(/^\/+/, "").split("/", 1)[0] ?? "";
  return (PAGE_KEYS as readonly string[]).includes(key) ? (key as PageKey) : null;
}

export function parsePageLocation(location: AppLocationLike): PageKey | null {
  return keyOf(location.pathname) ?? keyOf(location.hash);
}

export type NavigationIntent = { mode: "history" | "hash"; url: string };

export function navigationIntent(location: AppLocationLike, key: PageKey): NavigationIntent {
  const url =
    key === "notifications"
      ? ENTERPRISE_NOTIFICATIONS_PATH
      : key === "recycle-bin"
        ? ENTERPRISE_RECYCLE_BIN_PATH
        : key === "tasks"
          ? ENTERPRISE_TASKS_PATH
          : key === "automations"
            ? ENTERPRISE_AUTOMATIONS_PATH
            : key === "knowledge-bases"
              ? ENTERPRISE_KNOWLEDGE_BASES_PATH
              : key === "knowledge-base-workspace"
                ? ENTERPRISE_KNOWLEDGE_BASE_WORKSPACE_PATH
                : `/${key}`;
  return keyOf(location.pathname) ? { mode: "history", url } : { mode: "hash", url };
}

/** 侧栏该高亮哪一项：只有"主导航里没有自己那一项"的子页面才折进父项。 */
export function mainNavigationKey(page: PageKey): PageKey {
  if (page === "knowledge-base-workspace") return "knowledge-bases";
  return page;
}

/** history 还是 #hash 部署：与工作区其余深链同一判定，避免同一应用出现两种链接形态。 */
export function deepLinkMode(location: AppLocationLike): "history" | "hash" {
  return keyOf(location.pathname) ? "history" : "hash";
}

/**
 * 只读取当前部署形态那一份 URL：history 模式看 pathname+search，hash 模式看 hash。
 * 与 `navigationIntent` 的优先级保持一致，不会让过期 hash 压过真实 pathname。
 */
function workbenchParamsAt(location: AppLocationLike, path: string): URLSearchParams | null {
  const raw =
    deepLinkMode(location) === "history"
      ? `${location.pathname ?? ""}${location.search ?? ""}`
      : (location.hash ?? "").replace(/^#/, "");
  if (normalizedPath(raw) !== path) return null;
  const queryIndex = raw.indexOf("?");
  return new URLSearchParams(queryIndex >= 0 ? raw.slice(queryIndex + 1) : "");
}

/**
 * 解析干预深链参数 `/parse-intervention?doc=<id>&chunk=<id>`（history 与 #hash 两种形态都解析）。
 *
 * `paths` 用于收窄认领范围：App 用 keep-alive 保留已访问页面（隐藏 ≠ 卸载），两条入口各渲染
 * 一份工作区，所以一条路径只能被一个入口认领 —— 默认只含规范路径，老别名
 * `/documents/parse` 由 `documents/documentParseRoute.ts` 用自己的那一份解析。
 */
export function parseChunkWorkbenchLocation(
  location: AppLocationLike,
  paths: readonly string[] = [PARSE_INTERVENTION_PATH],
): ParsedChunkWorkbenchLocation | null {
  let params: URLSearchParams | null = null;
  for (const path of paths) {
    params = workbenchParamsAt(location, path);
    if (params) break;
  }
  if (!params) return null;
  const docId = params.get("doc")?.trim() ?? "";
  if (!docId) return null;
  return {
    docId,
    chunkId: params.get("chunk")?.trim() ?? "",
    datasetId: params.get("dataset")?.trim() ?? "",
  };
}

export function chunkWorkbenchQuery(params: ChunkWorkbenchParams): string {
  const query = new URLSearchParams({ doc: params.docId });
  const chunkId = params.chunkId?.trim();
  const datasetId = params.datasetId?.trim();
  if (chunkId) query.set("chunk", chunkId);
  if (datasetId) query.set("dataset", datasetId);
  return query.toString();
}

export function chunkWorkbenchNavigationIntent(
  location: AppLocationLike,
  params: ChunkWorkbenchParams,
): NavigationIntent {
  return {
    mode: deepLinkMode(location),
    url: `${PARSE_INTERVENTION_PATH}?${chunkWorkbenchQuery(params)}`,
  };
}

/** 证据/追踪面板用的纯字符串深链（与 `documentDeepLink` 同一 hash 惯例）。 */
export function chunkWorkbenchDeepLink(
  docId: string,
  chunkId?: string | null,
  datasetId?: string | null,
): string {
  return `#${PARSE_INTERVENTION_PATH}?${chunkWorkbenchQuery({
    docId,
    chunkId: chunkId ?? "",
    datasetId: datasetId ?? "",
  })}`;
}
