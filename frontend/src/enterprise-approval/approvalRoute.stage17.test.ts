// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  approvalRequestIdFromLocation,
  approvalRequestClearNavigationUrl,
  approvalRequestNavigationUrl,
  clearApprovalRequestNavigation,
  navigateToApprovalRequest,
} from "./approvalRoute";

afterEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("Stage17 approval request deep link navigation", () => {
  it("builds and parses direct approval request links", () => {
    expect(
      approvalRequestNavigationUrl(
        { pathname: "/enterprise/workspaces", search: "", hash: "" },
        "request/workspace 17",
      ),
    ).toBe("/enterprise/approvals?request=request%2Fworkspace%2017");
    expect(
      approvalRequestIdFromLocation({
        pathname: "/enterprise/approvals",
        search: "?request=request%2Fworkspace%2017",
        hash: "",
      }),
    ).toBe("request/workspace 17");
  });

  it("preserves hash routing and pushes only the request id", () => {
    expect(
      approvalRequestNavigationUrl(
        { pathname: "/", search: "", hash: "#/enterprise/workspaces?workspace=workspace-a" },
        "request-17",
      ),
    ).toBe("#/enterprise/approvals?request=request-17");

    window.history.replaceState(null, "", "/#/enterprise/workspaces");
    const pushState = vi.spyOn(window.history, "pushState");
    navigateToApprovalRequest("request-17");
    expect(pushState).toHaveBeenCalledWith({}, "", "#/enterprise/approvals?request=request-17");
    pushState.mockRestore();
  });

  it("ignores empty, sibling and oversized request ids", () => {
    expect(
      approvalRequestIdFromLocation({
        pathname: "/enterprise/workspaces",
        search: "?request=request-17",
        hash: "",
      }),
    ).toBeNull();
    expect(
      approvalRequestIdFromLocation({
        pathname: "/",
        search: "",
        hash: `#/enterprise/approvals?request=${"x".repeat(257)}`,
      }),
    ).toBeNull();
  });

  it("clears only the request query while preserving direct and hash route modes", () => {
    expect(
      approvalRequestClearNavigationUrl({
        pathname: "/enterprise/approvals",
        search: "?status=pending&request=request-17",
        hash: "",
      }),
    ).toBe("/enterprise/approvals?status=pending");
    expect(
      approvalRequestClearNavigationUrl({
        pathname: "/",
        search: "",
        hash: "#/enterprise/approvals?status=pending&request=request-17",
      }),
    ).toBe("#/enterprise/approvals?status=pending");
  });

  it("parses the request id from a hash query without duplicating hash parsing", () => {
    expect(
      approvalRequestIdFromLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/approvals?status=pending&request=request-17",
      }),
    ).toBe("request-17");
  });

  it("clears the request with replaceState, preserving history state and emitting one popstate", () => {
    const historyState = { source: "approval-host" };
    window.history.replaceState(
      historyState,
      "",
      "/enterprise/approvals?status=pending&request=request-17",
    );
    const replaceState = vi.spyOn(window.history, "replaceState");
    const dispatchEvent = vi.spyOn(window, "dispatchEvent");

    clearApprovalRequestNavigation();

    expect(replaceState).toHaveBeenCalledWith(
      historyState,
      "",
      "/enterprise/approvals?status=pending",
    );
    expect(dispatchEvent.mock.calls.filter(([event]) => event.type === "popstate")).toHaveLength(1);
    replaceState.mockRestore();
    dispatchEvent.mockRestore();
  });
});
