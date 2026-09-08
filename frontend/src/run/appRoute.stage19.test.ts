import { describe, expect, it } from "vitest";
import { mainNavigationKey, navigationIntent, parsePageLocation } from "./appRoute";

describe("Stage19 hidden Knowledge Base workspace PageKey", () => {
  it("parses direct and hash resource-workspace routes", () => {
    expect(
      parsePageLocation({
        pathname: "/enterprise/knowledge-base",
        search: "?dataset=dataset-prod&section=overview",
        hash: "",
      }),
    ).toBe("knowledge-base-workspace");
    expect(
      parsePageLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/knowledge-base?dataset=dataset-prod&section=releases",
      }),
    ).toBe("knowledge-base-workspace");
  });

  it("keeps the hidden workspace out of the main sider while selecting the registry domain", () => {
    expect(mainNavigationKey("knowledge-base-workspace")).toBe("knowledge-bases");
    expect(mainNavigationKey("documents")).toBe("documents");
    expect(
      navigationIntent(
        { pathname: "/enterprise/knowledge-base", search: "", hash: "" },
        "knowledge-base-workspace",
      ),
    ).toEqual({ mode: "history", url: "/enterprise/knowledge-base" });
  });
});
