// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  approvalCenterNavigationUrl,
  navigateToApprovalCenter,
} from "./memberRoleApprovalNavigation";

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("member role approval center navigation", () => {
  it("uses a direct path from history-routed enterprise pages", () => {
    expect(approvalCenterNavigationUrl({ pathname: "/enterprise/admin", hash: "" })).toBe(
      "/enterprise/approvals",
    );
  });

  it("preserves hash routing when the member directory is hash-routed", () => {
    expect(approvalCenterNavigationUrl({ pathname: "/", hash: "#/enterprise/admin" })).toBe(
      "#/enterprise/approvals",
    );
  });

  it.each([
    ["/enterprise/admin", "", "/enterprise/approvals"],
    ["/", "#/enterprise/admin", "#/enterprise/approvals"],
  ])("pushes the %s navigation form without losing the route mode", (pathname, hash, expected) => {
    window.history.replaceState(null, "", pathname + hash);
    const pushState = vi.spyOn(window.history, "pushState");

    navigateToApprovalCenter();

    expect(pushState).toHaveBeenCalledWith({}, "", expected);
    pushState.mockRestore();
  });
});
