import { describe, expect, it } from "vitest";
import {
  KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY,
  KNOWLEDGE_DATASET_STORAGE_KEY,
  KNOWLEDGE_TENANT_STORAGE_KEY,
  readKnowledgeDatasetIdFromLocation,
  resolveKnowledgeWorkspaceScope,
} from "./workspaceScope";

function actorToken(tenant: string): string {
  const payload = btoa(JSON.stringify({ sub: "account-a", tenant, iat: 1, exp: 2, jti: "token-a" }))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return `${payload}.signature`;
}

function storage(values: Record<string, string>) {
  return { getItem: (key: string) => values[key] ?? null };
}

describe("knowledge workspace scope resolver", () => {
  it("derives tenant from the authenticated actor token and dataset from workspace storage", () => {
    const scope = resolveKnowledgeWorkspaceScope({
      storage: storage({
        [KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY]: actorToken("tenant-token"),
        [KNOWLEDGE_TENANT_STORAGE_KEY]: "tenant-stale",
        [KNOWLEDGE_DATASET_STORAGE_KEY]: "dataset-stored",
      }),
    });

    expect(scope).toEqual({ tenantId: "tenant-token", datasetId: "dataset-stored" });
  });

  it("reads Dataset-scoped deep links from direct and hash knowledge routes", () => {
    expect(
      readKnowledgeDatasetIdFromLocation({
        pathname: "/documents",
        search: "?dataset=dataset-docs",
        hash: "",
      }),
    ).toBe("dataset-docs");
    expect(
      readKnowledgeDatasetIdFromLocation({
        pathname: "/",
        search: "",
        hash: "#/taxonomy?dataset=dataset-taxonomy",
      }),
    ).toBe("dataset-taxonomy");
    expect(
      readKnowledgeDatasetIdFromLocation({
        pathname: "/enterprise/knowledge-bases",
        search: "?dataset=registry-dataset",
        hash: "",
      }),
    ).toBeNull();
  });

  it("prefers explicit provider scope and falls back centrally for malformed sessions", () => {
    expect(
      resolveKnowledgeWorkspaceScope({
        tenantId: "tenant-explicit",
        datasetId: "dataset-explicit",
        storage: storage({
          [KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY]: actorToken("tenant-token"),
        }),
      }),
    ).toEqual({ tenantId: "tenant-explicit", datasetId: "dataset-explicit" });

    expect(
      resolveKnowledgeWorkspaceScope({
        storage: storage({
          [KNOWLEDGE_ACTOR_TOKEN_STORAGE_KEY]: "malformed",
          [KNOWLEDGE_TENANT_STORAGE_KEY]: "tenant-configured",
        }),
      }),
    ).toEqual({ tenantId: "tenant-configured", datasetId: "default" });
  });
});
