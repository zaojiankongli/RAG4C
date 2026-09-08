export interface InvitationRouteLocationLike {
  pathname: string;
  search?: string;
  hash?: string;
  origin?: string;
}

const ACCEPT_PATH = "/enterprise/invitations/accept";

function tokenFromSearch(search: string): string | null {
  const value = new URLSearchParams(search.startsWith("?") ? search : `?${search}`).get("token");
  return value?.trim() || null;
}

export function invitationAcceptTokenFromLocation(
  location: InvitationRouteLocationLike,
): string | null {
  const pathname = location.pathname.replace(/\/+$/, "") || "/";
  if (pathname === ACCEPT_PATH) return tokenFromSearch(location.search ?? "");
  const hash = (location.hash ?? "").replace(/^#/, "");
  const [hashPath, hashQuery = ""] = hash.split("?", 2);
  return hashPath.replace(/\/+$/, "") === ACCEPT_PATH ? tokenFromSearch(hashQuery) : null;
}

export function buildInvitationAcceptUrl(
  token: string,
  location: Pick<InvitationRouteLocationLike, "origin" | "pathname">,
): string {
  const normalizedToken = token.trim();
  if (!normalizedToken) throw new Error("invitation token is required");
  const origin = location.origin?.replace(/\/$/, "") ?? "";
  const query = `token=${encodeURIComponent(normalizedToken)}`;
  return location.pathname === "/"
    ? `${origin}/#${ACCEPT_PATH}?${query}`
    : `${origin}${ACCEPT_PATH}?${query}`;
}

export function clearInvitationAcceptLocation(): void {
  if (typeof window === "undefined") return;
  const useHash = window.location.hash.includes(ACCEPT_PATH);
  const target = useHash ? `${window.location.pathname}#/enterprise` : "/enterprise";
  window.history.replaceState(null, "", target);
}
