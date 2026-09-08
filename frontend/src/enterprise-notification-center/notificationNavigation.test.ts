import { describe, expect, it } from "vitest";

import { notificationHandoffTarget } from "./notificationNavigation";

describe("notificationHandoffTarget", () => {
  it("projects a quality alert only to the verified release operations route", () => {
    expect(
      notificationHandoffTarget({
        notificationId: "notification-quality-001",
        sourceKind: "quality_alert",
        route: {
          code: "knowledge_quality_operations",
          path: "/enterprise/knowledge-base",
          query: { dataset: "dataset-a", section: "releases", alert: "alert-a" },
          href: "/enterprise/knowledge-base?dataset=dataset-a&section=releases&alert=alert-a",
        },
      }),
    ).toEqual({
      page: "knowledge-base-workspace",
      url: "/enterprise/knowledge-base?dataset=dataset-a&section=releases&alert=alert-a",
    });
  });

  it("projects a pending approval only to the enterprise approval route", () => {
    expect(
      notificationHandoffTarget({
        notificationId: "notification-approval-001",
        sourceKind: "approval_pending_for_me",
        route: {
          code: "enterprise_approval",
          path: "/enterprise/approvals",
          query: { request: "request-a" },
          href: "/enterprise/approvals?request=request-a",
        },
      }),
    ).toEqual({ page: "enterprise", url: "/enterprise/approvals?request=request-a" });
  });

  it("fails closed on mismatched source kinds, paths, or extra query fields", () => {
    expect(
      notificationHandoffTarget({
        notificationId: "notification-unsafe-001",
        sourceKind: "approval_pending_for_me",
        route: {
          code: "knowledge_quality_operations",
          path: "/enterprise/knowledge-base",
          query: { dataset: "dataset-a", section: "releases" },
          href: "/enterprise/knowledge-base?dataset=dataset-a&section=releases",
        },
      }),
    ).toBeNull();

    expect(
      notificationHandoffTarget({
        notificationId: "notification-unsafe-002",
        sourceKind: "approval_pending_for_me",
        route: {
          code: "enterprise_approval",
          path: "/enterprise/approvals",
          query: { request: "request-a", token: "must-not-pass" },
          href: "/enterprise/approvals?request=request-a&token=must-not-pass",
        },
      }),
    ).toBeNull();
  });
});
