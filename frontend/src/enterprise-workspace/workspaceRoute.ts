export interface EnterpriseWorkspaceLocationLike {
  pathname: string;
  search?: string;
  hash?: string;
}

const WORKSPACE_PATH = "/enterprise/workspaces";

function normalized(value: string): string {
  const path = value.replace(/^#/, "").split("?", 1)[0]?.replace(/\/+$/, "") ?? "";
  return path || "/";
}

export function enterpriseWorkspaceRouteFromLocation(
  location: EnterpriseWorkspaceLocationLike,
): boolean {
  return (
    normalized(location.pathname) === WORKSPACE_PATH ||
    normalized(location.hash ?? "") === WORKSPACE_PATH
  );
}

function queryForWorkspaceRoute(location: EnterpriseWorkspaceLocationLike): string {
  if (normalized(location.pathname) === WORKSPACE_PATH) return location.search ?? "";
  if (normalized(location.hash ?? "") !== WORKSPACE_PATH) return "";
  const hash = (location.hash ?? "").replace(/^#/, "");
  return hash.includes("?") ? hash.slice(hash.indexOf("?")) : "";
}

export function enterpriseWorkspaceIdFromLocation(
  location: EnterpriseWorkspaceLocationLike,
): string | null {
  const value = new URLSearchParams(queryForWorkspaceRoute(location)).get("workspace")?.trim();
  return value || null;
}

export function enterpriseWorkspaceNavigationUrl(
  location: EnterpriseWorkspaceLocationLike,
  workspaceId?: string | null,
): { mode: "history" | "hash"; url: string } {
  const suffix = workspaceId?.trim() ? `?workspace=${encodeURIComponent(workspaceId.trim())}` : "";
  return normalized(location.pathname).startsWith("/enterprise")
    ? { mode: "history", url: `${WORKSPACE_PATH}${suffix}` }
    : { mode: "hash", url: `${WORKSPACE_PATH}${suffix}` };
}

export { WORKSPACE_PATH as ENTERPRISE_WORKSPACE_PATH };
