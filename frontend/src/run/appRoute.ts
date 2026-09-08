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

export function mainNavigationKey(page: PageKey): PageKey {
  if (page === "knowledge-base-workspace") return "knowledge-bases";
  if (page === "notifications") return "enterprise";
  return page;
}
