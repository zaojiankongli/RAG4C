import type { PageKey } from "../run/appRoute";
import type { NotificationHandoffContext } from "./components/notificationCenterShared";

export interface NotificationHandoffTarget {
  page: Extract<PageKey, "knowledge-base-workspace" | "enterprise">;
  url: string;
}

const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;

function safeId(value: unknown): value is string {
  return typeof value === "string" && SAFE_ID.test(value);
}

function exactKeys(value: Readonly<Record<string, string>>, keys: readonly string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

/**
 * Converts the already-projected Notification route into an App navigation target.
 *
 * This boundary deliberately rebuilds the URL from a small allowlist instead of trusting
 * `route.href`, so an unsafe or stale object injected between projection and navigation fails closed.
 */
export function notificationHandoffTarget(
  context: NotificationHandoffContext,
): NotificationHandoffTarget | null {
  if (!safeId(context.notificationId)) return null;

  const { route, sourceKind } = context;
  if (
    sourceKind === "quality_alert" &&
    route.code === "knowledge_quality_operations" &&
    route.path === "/enterprise/knowledge-base"
  ) {
    const keys =
      route.query.alert === undefined ? ["dataset", "section"] : ["dataset", "section", "alert"];
    if (
      !exactKeys(route.query, keys) ||
      !safeId(route.query.dataset) ||
      route.query.section !== "releases" ||
      (route.query.alert !== undefined && !safeId(route.query.alert))
    ) {
      return null;
    }
    const query = new URLSearchParams({ dataset: route.query.dataset, section: "releases" });
    if (route.query.alert !== undefined) query.set("alert", route.query.alert);
    return { page: "knowledge-base-workspace", url: `${route.path}?${query.toString()}` };
  }

  if (
    sourceKind === "approval_pending_for_me" &&
    route.code === "enterprise_approval" &&
    route.path === "/enterprise/approvals" &&
    exactKeys(route.query, ["request"]) &&
    safeId(route.query.request)
  ) {
    const query = new URLSearchParams({ request: route.query.request });
    return { page: "enterprise", url: `${route.path}?${query.toString()}` };
  }

  return null;
}
