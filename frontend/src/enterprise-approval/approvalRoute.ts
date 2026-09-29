import { commitNavigationIntent } from "../run/navigationAdapter";

export interface EnterpriseApprovalLocationLike {
  pathname: string;
  search?: string;
  hash?: string;
}

const APPROVAL_PATH = "/enterprise/approvals";
const APPROVAL_REQUEST_QUERY = "request";

interface ApprovalRouteLocation {
  mode: "direct" | "hash";
  query: string;
}

function splitPathAndQuery(value: string): { path: string; query: string } {
  const withoutHash = value.replace(/^#/, "");
  const index = withoutHash.indexOf("?");
  return {
    path: index >= 0 ? withoutHash.slice(0, index) : withoutHash,
    query: index >= 0 ? withoutHash.slice(index) : "",
  };
}

function normalizePath(value: string): string {
  const normalized = value.replace(/\/+$/, "");
  return normalized || "/";
}

function joinQueries(...queries: string[]): string {
  const parts = queries.map((query) => query.replace(/^\?/, "").trim()).filter(Boolean);
  return parts.length ? `?${parts.join("&")}` : "";
}

function routeFromLocation(location: EnterpriseApprovalLocationLike): ApprovalRouteLocation | null {
  const direct = splitPathAndQuery(location.pathname);
  if (normalizePath(direct.path) === APPROVAL_PATH) {
    return {
      mode: "direct",
      query: joinQueries(direct.query, location.search ?? ""),
    };
  }

  const hash = splitPathAndQuery(location.hash ?? "");
  if (normalizePath(hash.path) === APPROVAL_PATH) {
    return { mode: "hash", query: hash.query };
  }

  return null;
}

function navigationModeFromLocation(location: EnterpriseApprovalLocationLike): "direct" | "hash" {
  const directPath = normalizePath(splitPathAndQuery(location.pathname).path);
  return directPath === "/" && (location.hash ?? "").trim().startsWith("#/") ? "hash" : "direct";
}

function removeRequestQuery(query: string): string {
  const params = new URLSearchParams(query);
  params.delete(APPROVAL_REQUEST_QUERY);
  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}

export function enterpriseApprovalRouteFromLocation(
  location: EnterpriseApprovalLocationLike,
): boolean {
  return routeFromLocation(location) !== null;
}

export function approvalRequestIdFromLocation(
  location: EnterpriseApprovalLocationLike,
): string | null {
  const route = routeFromLocation(location);
  if (!route) return null;
  const value = new URLSearchParams(route.query).get(APPROVAL_REQUEST_QUERY)?.trim() ?? "";
  return value && value.length <= 256 ? value : null;
}

export function approvalRequestClearNavigationUrl(
  location: EnterpriseApprovalLocationLike,
): string {
  const route = routeFromLocation(location);
  if (!route) return navigationModeFromLocation(location) === "hash" ? "#/" : "/";
  const query = removeRequestQuery(route.query);
  return route.mode === "hash" ? `#${APPROVAL_PATH}${query}` : `${APPROVAL_PATH}${query}`;
}

export function approvalRequestNavigationUrl(
  location: EnterpriseApprovalLocationLike,
  requestId: string,
): string {
  const normalizedRequestId = requestId.trim();
  if (!normalizedRequestId) throw new Error("requestId is required for approval navigation");
  if (normalizedRequestId.length > 256) throw new Error("requestId must be at most 256 characters");
  const url = `${APPROVAL_PATH}?${APPROVAL_REQUEST_QUERY}=${encodeURIComponent(normalizedRequestId)}`;
  return navigationModeFromLocation(location) === "hash" ? `#${url}` : url;
}

export function navigateToApprovalRequest(requestId: string): void {
  if (typeof window === "undefined") return;
  commitNavigationIntent(
    { mode: "history", url: approvalRequestNavigationUrl(window.location, requestId) },
    { historyState: {} },
  );
}

export function clearApprovalRequestNavigation(): void {
  if (typeof window === "undefined") return;
  const url = approvalRequestClearNavigationUrl(window.location);
  if (!enterpriseApprovalRouteFromLocation(window.location)) return;
  commitNavigationIntent({ mode: "history", url }, { historyAction: "replace" });
}

export { APPROVAL_PATH as ENTERPRISE_APPROVAL_PATH };
