import { describe, expect, it } from "vitest";
import { navigationIntent, parsePageLocation } from "./appRoute";

describe("Stage18 enterprise Knowledge Base route", () => {
  it("mounts the registry page from direct and hash routes", () => {
    expect(
      parsePageLocation({
        pathname: "/enterprise/knowledge-bases",
        search: "?dataset=dataset-prod&tab=applications",
        hash: "",
      }),
    ).toBe("knowledge-bases");
    expect(
      parsePageLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/knowledge-bases?dataset=dataset-prod&tab=applications",
      }),
    ).toBe("knowledge-bases");
  });

  it("uses the current deployment routing mode for registry navigation", () => {
    expect(
      navigationIntent(
        { pathname: "/enterprise/knowledge-bases", search: "", hash: "" },
        "knowledge-bases",
      ),
    ).toEqual({ mode: "history", url: "/enterprise/knowledge-bases" });
    expect(
      navigationIntent(
        { pathname: "/", search: "", hash: "#/enterprise/knowledge-bases" },
        "knowledge-bases",
      ),
    ).toEqual({ mode: "hash", url: "/enterprise/knowledge-bases" });
  });
});
