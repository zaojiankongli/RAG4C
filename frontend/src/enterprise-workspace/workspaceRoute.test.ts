import { describe, expect, it } from "vitest";
import {
  enterpriseWorkspaceIdFromLocation,
  enterpriseWorkspaceNavigationUrl,
  enterpriseWorkspaceRouteFromLocation,
} from "./workspaceRoute";

describe("enterprise workspace route", () => {
  it("accepts direct and hash workspace center routes only", () => {
    expect(
      enterpriseWorkspaceRouteFromLocation({
        pathname: "/enterprise/workspaces",
        search: "",
        hash: "",
      }),
    ).toBe(true);
    expect(
      enterpriseWorkspaceRouteFromLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/workspaces?workspace=workspace-a",
      }),
    ).toBe(true);
    expect(
      enterpriseWorkspaceRouteFromLocation({
        pathname: "/enterprise/identity",
        search: "",
        hash: "",
      }),
    ).toBe(false);
  });

  it("reads workspace deep links and clears them while preserving direct/hash routing mode", () => {
    expect(
      enterpriseWorkspaceIdFromLocation({
        pathname: "/enterprise/workspaces",
        search: "?workspace=workspace%2Fprod",
        hash: "",
      }),
    ).toBe("workspace/prod");
    expect(
      enterpriseWorkspaceIdFromLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/workspaces?workspace=workspace-a",
      }),
    ).toBe("workspace-a");
    expect(
      enterpriseWorkspaceNavigationUrl(
        { pathname: "/", search: "", hash: "#/enterprise/workspaces?workspace=workspace-a" },
        null,
      ),
    ).toEqual({ mode: "hash", url: "/enterprise/workspaces" });
  });
});
