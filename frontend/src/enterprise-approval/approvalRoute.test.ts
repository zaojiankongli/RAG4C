import { describe, expect, it } from "vitest";
import { enterpriseApprovalRouteFromLocation } from "./approvalRoute";

describe("Stage 13 nested approval route", () => {
  it("recognizes direct and hash approval paths with query strings", () => {
    expect(
      enterpriseApprovalRouteFromLocation({
        pathname: "/enterprise/approvals",
        search: "?status=pending",
        hash: "",
      }),
    ).toBe(true);
    expect(
      enterpriseApprovalRouteFromLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/approvals?tab=rules",
      }),
    ).toBe(true);
  });

  it("does not claim sibling enterprise surfaces or partial paths", () => {
    expect(
      enterpriseApprovalRouteFromLocation({ pathname: "/enterprise", search: "", hash: "" }),
    ).toBe(false);
    expect(
      enterpriseApprovalRouteFromLocation({
        pathname: "/enterprise/approval",
        search: "",
        hash: "",
      }),
    ).toBe(false);
    expect(
      enterpriseApprovalRouteFromLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/identity",
      }),
    ).toBe(false);
  });
});
