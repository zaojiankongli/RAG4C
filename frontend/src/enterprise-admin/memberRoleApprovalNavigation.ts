export interface ApprovalCenterLocationLike {
  pathname: string;
  hash?: string;
}

const APPROVAL_CENTER_PATH = "/enterprise/approvals";

function isHashRoute(location: ApprovalCenterLocationLike): boolean {
  const hash = (location.hash ?? "").trim();
  if (!hash.startsWith("#/")) return false;
  const path = hash.slice(1).split("?", 1)[0] ?? "";
  return path.startsWith("/");
}

export function approvalCenterNavigationUrl(location: ApprovalCenterLocationLike): string {
  return isHashRoute(location) ? `#${APPROVAL_CENTER_PATH}` : APPROVAL_CENTER_PATH;
}

export function navigateToApprovalCenter(): void {
  if (typeof window === "undefined") return;
  window.history.pushState({}, "", approvalCenterNavigationUrl(window.location));
  window.dispatchEvent(new PopStateEvent("popstate"));
}
