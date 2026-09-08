import { describe, expect, it } from "vitest";
import {
  datasetDeepLink,
  knowledgeBaseIdFromLocation,
  knowledgeBaseNavigationUrl,
  knowledgeBaseRouteFromLocation,
  knowledgeBaseTabFromLocation,
} from "./knowledgeBaseRoute";

describe("Stage18 Knowledge Base route", () => {
  it("accepts direct and hash registry routes and reads dataset/tab deep links", () => {
    expect(
      knowledgeBaseRouteFromLocation({
        pathname: "/enterprise/knowledge-bases",
        search: "?dataset=dataset-prod&tab=applications",
        hash: "",
      }),
    ).toBe(true);
    expect(
      knowledgeBaseRouteFromLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/knowledge-bases?dataset=dataset-prod&tab=applications",
      }),
    ).toBe(true);
    expect(
      knowledgeBaseIdFromLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/knowledge-bases?dataset=dataset-prod&tab=applications",
      }),
    ).toBe("dataset-prod");
    expect(
      knowledgeBaseTabFromLocation({
        pathname: "/enterprise/knowledge-bases",
        search: "?tab=applications",
        hash: "",
      }),
    ).toBe("applications");
  });

  it("uses a safe overview fallback for unknown tabs", () => {
    expect(
      knowledgeBaseTabFromLocation({
        pathname: "/enterprise/knowledge-bases",
        search: "?tab=secrets",
        hash: "",
      }),
    ).toBe("overview");
  });

  it("preserves direct/hash deployment mode and creates Dataset-scoped deep links", () => {
    expect(
      knowledgeBaseNavigationUrl(
        { pathname: "/enterprise/knowledge-bases", search: "", hash: "" },
        { datasetId: "dataset/a", tab: "dependencies" },
      ),
    ).toEqual({
      mode: "history",
      url: "/enterprise/knowledge-bases?dataset=dataset%2Fa&tab=dependencies",
    });
    expect(
      knowledgeBaseNavigationUrl(
        { pathname: "/", search: "", hash: "#/enterprise/knowledge-bases" },
        { datasetId: null, tab: "overview" },
      ),
    ).toEqual({ mode: "hash", url: "/enterprise/knowledge-bases" });
    expect(
      datasetDeepLink(
        { pathname: "/", search: "", hash: "#/enterprise/knowledge-bases" },
        "documents",
        "dataset-prod",
      ),
    ).toEqual({ mode: "hash", url: "/documents?dataset=dataset-prod" });
  });
});
