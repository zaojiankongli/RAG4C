export interface KnowledgeBaseLocationLike {
  pathname: string;
  search?: string;
  hash?: string;
}

export type KnowledgeBaseTab =
  "overview" | "workspace" | "applications" | "dependencies" | "operations";
export type KnowledgeBaseRouteIntent = { mode: "history" | "hash"; url: string };
export type DatasetDeepLinkPage = "documents" | "taxonomy" | "sources" | "governance";

const KNOWLEDGE_BASE_PATH = "/enterprise/knowledge-bases";
const TABS: readonly KnowledgeBaseTab[] = [
  "overview",
  "workspace",
  "applications",
  "dependencies",
  "operations",
];

function normalizedPath(value: string): string {
  const path = value.replace(/^#/, "").split("?", 1)[0] ?? "/";
  return path.replace(/\/+$/, "") || "/";
}

function usesDirectRoute(location: KnowledgeBaseLocationLike): boolean {
  return normalizedPath(location.pathname) === KNOWLEDGE_BASE_PATH;
}

function queryForRoute(location: KnowledgeBaseLocationLike): string {
  if (usesDirectRoute(location)) return location.search ?? "";
  const hash = location.hash ?? "";
  if (normalizedPath(hash) !== KNOWLEDGE_BASE_PATH) return "";
  const queryIndex = hash.indexOf("?");
  return queryIndex >= 0 ? hash.slice(queryIndex) : "";
}

export function knowledgeBaseRouteFromLocation(location: KnowledgeBaseLocationLike): boolean {
  return (
    normalizedPath(location.pathname) === KNOWLEDGE_BASE_PATH ||
    normalizedPath(location.hash ?? "") === KNOWLEDGE_BASE_PATH
  );
}

export function knowledgeBaseIdFromLocation(location: KnowledgeBaseLocationLike): string | null {
  const datasetId = new URLSearchParams(queryForRoute(location)).get("dataset")?.trim();
  return datasetId || null;
}

export function knowledgeBaseTabFromLocation(
  location: KnowledgeBaseLocationLike,
): KnowledgeBaseTab {
  const rawTab = new URLSearchParams(queryForRoute(location)).get("tab")?.trim() ?? "";
  if (rawTab === "application-references") return "applications";
  return (TABS as readonly string[]).includes(rawTab) ? (rawTab as KnowledgeBaseTab) : "overview";
}

function routeMode(location: KnowledgeBaseLocationLike): "history" | "hash" {
  return location.pathname.startsWith("/enterprise") ? "history" : "hash";
}

export function knowledgeBaseNavigationUrl(
  location: KnowledgeBaseLocationLike,
  input: { datasetId?: string | null; tab?: KnowledgeBaseTab | null } = {},
): KnowledgeBaseRouteIntent {
  const params = new URLSearchParams();
  const datasetId = input.datasetId?.trim();
  if (datasetId) params.set("dataset", datasetId);
  if (datasetId && input.tab && input.tab !== "overview") params.set("tab", input.tab);
  const query = params.toString();
  return { mode: routeMode(location), url: `${KNOWLEDGE_BASE_PATH}${query ? `?${query}` : ""}` };
}

export function datasetDeepLink(
  location: KnowledgeBaseLocationLike,
  page: DatasetDeepLinkPage,
  datasetId: string,
): KnowledgeBaseRouteIntent {
  const normalizedDatasetId = datasetId.trim();
  if (!normalizedDatasetId) throw new Error("datasetId is required for Dataset deep links");
  const url = `/${page}?dataset=${encodeURIComponent(normalizedDatasetId)}`;
  return { mode: routeMode(location), url };
}

export function knowledgeBaseTabLabel(tab: KnowledgeBaseTab): string {
  return tab === "applications" ? "Application references" : tab[0].toUpperCase() + tab.slice(1);
}

export { KNOWLEDGE_BASE_PATH as ENTERPRISE_KNOWLEDGE_BASE_PATH };
