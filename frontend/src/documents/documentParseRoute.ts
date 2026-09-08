export interface DocumentRouteLocationLike {
  pathname: string;
  search: string;
  hash: string;
}
export type DocumentRouteIntent = { mode: "history" | "hash"; url: string };

function nestedPath(location: DocumentRouteLocationLike): { pathname: string; search: string } {
  if (location.pathname.startsWith("/documents")) return { pathname: location.pathname, search: location.search };
  const raw = location.hash.replace(/^#/, "");
  const [pathname, query = ""] = raw.split("?", 2);
  return { pathname, search: query ? `?${query}` : "" };
}

export function parseDocumentWorkspaceLocation(location: DocumentRouteLocationLike): { docId: string } | null {
  const nested = nestedPath(location);
  if (nested.pathname.replace(/\/+$/, "") !== "/documents/parse") return null;
  const docId = new URLSearchParams(nested.search).get("doc")?.trim() ?? "";
  return docId ? { docId } : null;
}

function deploymentMode(location: DocumentRouteLocationLike): "history" | "hash" {
  return location.pathname.startsWith("/documents") ? "history" : "hash";
}

export function documentWorkspaceNavigationIntent(location: DocumentRouteLocationLike, docId: string): DocumentRouteIntent {
  return { mode: deploymentMode(location), url: `/documents/parse?doc=${encodeURIComponent(docId)}` };
}

export function documentsReturnIntent(location: DocumentRouteLocationLike): DocumentRouteIntent {
  return { mode: deploymentMode(location), url: "/documents" };
}
