import { describe, expect, it } from "vitest";
import {
  knowledgeBaseEvidenceNavigationUrl,
  knowledgeBaseResourceDatasetIdFromLocation,
  knowledgeBaseResourceNavigationUrl,
  knowledgeBaseResourceRouteFromLocation,
  knowledgeBaseResourceSectionFromLocation,
  knowledgeBaseResourceSectionForPage,
  type KnowledgeBaseResourceSection,
} from "./knowledgeBaseResourceRoute";

describe("Stage19 Knowledge Base resource route", () => {
  it("parses direct and hash resource-workspace routes without losing the Dataset", () => {
    expect(
      knowledgeBaseResourceRouteFromLocation({
        pathname: "/enterprise/knowledge-base",
        search: "?dataset=dataset-prod&section=releases",
        hash: "",
      }),
    ).toBe(true);
    expect(
      knowledgeBaseResourceRouteFromLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/knowledge-base?dataset=dataset-prod&section=documents",
      }),
    ).toBe(true);
    expect(
      knowledgeBaseResourceDatasetIdFromLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/knowledge-base?dataset=dataset%2Fprod&section=documents",
      }),
    ).toBe("dataset/prod");
  });

  it("uses an overview fallback for unknown sections and maps every section to its canonical page", () => {
    expect(
      knowledgeBaseResourceSectionFromLocation({
        pathname: "/enterprise/knowledge-base",
        search: "?dataset=dataset-prod&section=unknown",
        hash: "",
      }),
    ).toBe("overview");
    const sections: KnowledgeBaseResourceSection[] = [
      "overview",
      "documents",
      "taxonomy",
      "sources",
      "governance",
      "releases",
    ];
    expect(sections.map((section) => knowledgeBaseResourceSectionForPage(section))).toEqual(
      sections,
    );
  });

  it("keeps direct/hash mode and uses existing scoped routes for resource sections", () => {
    expect(
      knowledgeBaseResourceNavigationUrl(
        { pathname: "/enterprise/knowledge-base", search: "", hash: "" },
        { datasetId: "dataset/a", section: "documents" },
      ),
    ).toEqual({ mode: "history", url: "/documents?dataset=dataset%2Fa" });
    expect(
      knowledgeBaseResourceNavigationUrl(
        { pathname: "/", search: "", hash: "#/enterprise/knowledge-base" },
        { datasetId: "dataset/a", section: "releases" },
      ),
    ).toEqual({
      mode: "hash",
      url: "/enterprise/knowledge-base?dataset=dataset%2Fa&section=releases",
    });
    expect(
      knowledgeBaseResourceNavigationUrl(
        { pathname: "/documents", search: "?dataset=dataset-prod", hash: "" },
        { datasetId: "dataset-prod", section: "overview" },
      ),
    ).toEqual({
      mode: "history",
      url: "/enterprise/knowledge-base?dataset=dataset-prod&section=overview",
    });
  });
});

describe("Stage26 Knowledge Serving resource route", () => {
  it("parses serving in direct and hash resource routes", () => {
    const serving = "serving" as KnowledgeBaseResourceSection;
    expect(
      knowledgeBaseResourceSectionFromLocation({
        pathname: "/enterprise/knowledge-base",
        search: "?dataset=dataset-serving&section=serving",
        hash: "",
      }),
    ).toBe(serving);
    expect(
      knowledgeBaseResourceSectionFromLocation({
        pathname: "/",
        search: "",
        hash: "#/enterprise/knowledge-base?dataset=dataset-serving&section=serving",
      }),
    ).toBe(serving);
    expect(knowledgeBaseResourceSectionForPage(serving)).toBe(serving);
  });

  it("keeps serving in the Knowledge Base resource shell for direct and hash navigation", () => {
    const serving = "serving" as KnowledgeBaseResourceSection;
    expect(
      knowledgeBaseResourceNavigationUrl(
        { pathname: "/enterprise/knowledge-base", search: "", hash: "" },
        { datasetId: "dataset-serving", section: serving },
      ),
    ).toEqual({
      mode: "history",
      url: "/enterprise/knowledge-base?dataset=dataset-serving&section=serving",
    });
    expect(
      knowledgeBaseResourceNavigationUrl(
        { pathname: "/", search: "", hash: "#/enterprise/knowledge-base" },
        { datasetId: "dataset-serving", section: serving },
      ),
    ).toEqual({
      mode: "hash",
      url: "/enterprise/knowledge-base?dataset=dataset-serving&section=serving",
    });
  });
});

describe("Stage26 canonical serving evidence routes", () => {
  it("keeps every evidence kind on the canonical path/query contract and preserves encoded resources", () => {
    const cases = [
      ["source", "knowledge_sources", "/enterprise/knowledge-base", "source"],
      ["source_sync_run", "knowledge_sources", "/enterprise/knowledge-base", "sync"],
      ["document", "knowledge_documents", "/enterprise/knowledge-base", "document"],
      ["ingest_attempt", "knowledge_documents", "/enterprise/knowledge-base", "document"],
      ["chunk_head", "knowledge_documents", "/enterprise/knowledge-base", "document"],
      ["index_operation", "knowledge_indexing", "/enterprise/tasks", "operation"],
      ["release", "knowledge_base_releases", "/enterprise/knowledge-base", "release"],
      ["certification", "release_quality", "/enterprise/knowledge-base", "certification"],
      ["task", "enterprise_tasks", "/enterprise/tasks", "task"],
    ] as const;

    for (const [evidenceKind, routeCode, path, parameter] of cases) {
      const resourceId = `${evidenceKind}-a&segment=1`;
      expect(
        knowledgeBaseEvidenceNavigationUrl(
          { pathname: "/enterprise/knowledge-base", search: "", hash: "" },
          { evidenceKind, routeCode, resourceId },
        ),
      ).toEqual({
        mode: "history",
        url: `${path}?${parameter}=${encodeURIComponent(resourceId)}`,
      });
    }
  });

  it("keeps canonical evidence routes in hash mode and rejects mismatched route codes", () => {
    expect(
      knowledgeBaseEvidenceNavigationUrl(
        { pathname: "/", search: "", hash: "#/enterprise/knowledge-base" },
        {
          evidenceKind: "certification",
          routeCode: "release_quality",
          resourceId: "cert/a?revision=2",
        },
      ),
    ).toEqual({
      mode: "hash",
      url: "/enterprise/knowledge-base?certification=cert%2Fa%3Frevision%3D2",
    });
    expect(() =>
      knowledgeBaseEvidenceNavigationUrl(
        { pathname: "/enterprise/knowledge-base", search: "", hash: "" },
        {
          evidenceKind: "source",
          routeCode: "knowledge_sources",
          resourceId: "custom://attacker.example/resource",
        },
      ),
    ).toThrow(/resource|invalid/);
    expect(() =>
      knowledgeBaseEvidenceNavigationUrl(
        { pathname: "/enterprise/knowledge-base", search: "", hash: "" },
        {
          evidenceKind: "release",
          routeCode: "enterprise_tasks",
          resourceId: "release-a",
        },
      ),
    ).toThrow(/canonical|route/);
  });
});
