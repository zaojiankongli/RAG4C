import { OIDC_CALLBACK_PATH, type OidcCallbackInput } from "./enterpriseIdentityModel";

export interface OidcRuntimeLocationLike {
  pathname: string;
  search?: string;
  hash?: string;
  origin?: string;
}
function values(search: string) {
  const query = new URLSearchParams(search.startsWith("?") ? search : `?${search}`);
  const code = query.get("code")?.trim() ?? "";
  const state = query.get("state")?.trim() ?? "";
  return code && state ? { code, state } : null;
}
function normalized(pathname: string) {
  return pathname.replace(/\/+$/, "") || "/";
}
export function buildOidcCallbackUrl(
  location: Pick<OidcRuntimeLocationLike, "origin" | "pathname" | "hash">,
): string {
  const origin = (
    location.origin ?? (typeof window !== "undefined" ? window.location.origin : "")
  ).replace(/\/$/, "");
  return `${origin}${OIDC_CALLBACK_PATH}`;
}
export function oidcCallbackFromLocation(
  location: OidcRuntimeLocationLike,
): OidcCallbackInput | null {
  if (normalized(location.pathname) === OIDC_CALLBACK_PATH) {
    return values(location.search ?? "");
  }
  const hash = (location.hash ?? "").replace(/^#/, "");
  const [hashPath, hashQuery = ""] = hash.split("?", 2);
  if (normalized(hashPath) !== OIDC_CALLBACK_PATH) return null;
  return values(hashQuery);
}
export function clearOidcCallbackLocation(): void {
  if (typeof window === "undefined") return;
  const usesHash = window.location.hash.includes(OIDC_CALLBACK_PATH);
  const target = usesHash
    ? `${window.location.pathname}#/enterprise/identity`
    : "/enterprise/identity";
  window.history.replaceState(null, "", target);
}
