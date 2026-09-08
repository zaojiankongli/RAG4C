// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  CROSS_CONTRACT_CREATED_MUTATION_RESPONSE,
  CROSS_CONTRACT_DEPENDENCIES_RESPONSE,
  CROSS_CONTRACT_DETAIL_RESPONSE,
  CROSS_CONTRACT_LIST_RESPONSE,
  CROSS_CONTRACT_REMOVED_MUTATION_RESPONSE,
  CROSS_CONTRACT_TRANSFERRED_MUTATION_RESPONSE,
} from "../crossContractFixtures";
import {
  createApplicationReference,
  createKnowledgeBaseIdempotencyKey,
  fetchEnterpriseKnowledgeBaseDependencies,
  fetchEnterpriseKnowledgeBaseDetail,
  fetchEnterpriseKnowledgeBases,
  removeApplicationReference,
  transferKnowledgeBaseOwnership,
} from "./enterpriseKnowledgeBaseApi";

const scope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "actor-token" };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  localStorage.setItem("rag4c.base_url", "https://enterprise.test");
  vi.stubGlobal(
    "fetch",
    vi.fn((_input: RequestInfo | URL) =>
      Promise.resolve(
        jsonResponse({
          id: "dataset-a",
          name: "客服知识库",
          status: "active",
          profile_revision: 3,
        }),
      ),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Stage18 Knowledge Base Registry API", () => {
  it("projects list/detail/dependency reads with tenant authentication", async () => {
    vi.mocked(fetch).mockImplementation((input) => {
      const url = String(input);
      if (url.endsWith("/dependencies")) {
        return Promise.resolve(jsonResponse(CROSS_CONTRACT_DEPENDENCIES_RESPONSE));
      }
      if (url.endsWith("/api/enterprise/knowledge-bases/dataset-prod")) {
        return Promise.resolve(jsonResponse(CROSS_CONTRACT_DETAIL_RESPONSE));
      }
      return Promise.resolve(jsonResponse(CROSS_CONTRACT_LIST_RESPONSE));
    });

    const page = await fetchEnterpriseKnowledgeBases(scope, {
      workspaceId: "workspace-prod",
      status: "active",
      keyword: "客服",
      limit: 20,
    });
    expect(page.items[0]?.id).toBe("dataset-prod");
    expect(String(vi.mocked(fetch).mock.calls[0]?.[0])).toContain(
      "/api/enterprise/knowledge-bases?workspace_id=workspace-prod&status=active&q=%E5%AE%A2%E6%9C%8D&limit=20",
    );
    expect(vi.mocked(fetch).mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-RAG4C-Tenant": "tenant-a",
          Authorization: "Bearer actor-token",
        }),
      }),
    );

    await fetchEnterpriseKnowledgeBaseDetail(scope, "dataset-prod");
    await fetchEnterpriseKnowledgeBaseDependencies(scope, "dataset-prod");
    expect(vi.mocked(fetch).mock.calls.map(([input]) => String(input))).toEqual([
      "https://enterprise.test/api/enterprise/knowledge-bases?workspace_id=workspace-prod&status=active&q=%E5%AE%A2%E6%9C%8D&limit=20",
      "https://enterprise.test/api/enterprise/knowledge-bases/dataset-prod",
      "https://enterprise.test/api/enterprise/knowledge-bases/dataset-prod/dependencies",
    ]);
  });

  it("sends stable idempotency keys and revision-fenced mutation payloads", async () => {
    const calls: Array<{ input: string; init?: RequestInit }> = [];
    vi.mocked(fetch).mockImplementation((input, init) => {
      calls.push({ input: String(input), init });
      const url = String(input);
      if (url.includes("workspace-transfer")) {
        return Promise.resolve(jsonResponse(CROSS_CONTRACT_TRANSFERRED_MUTATION_RESPONSE));
      }
      if (init?.method === "DELETE") {
        return Promise.resolve(jsonResponse(CROSS_CONTRACT_REMOVED_MUTATION_RESPONSE));
      }
      return Promise.resolve(jsonResponse(CROSS_CONTRACT_CREATED_MUTATION_RESPONSE));
    });

    expect(createKnowledgeBaseIdempotencyKey()).toMatch(/^rag4c-knowledge-base-/);
    await createApplicationReference(
      scope,
      "app-support",
      "dataset-a",
      { reason: "  建立客服引用  " },
      { idempotencyKey: "stable-reference-key" },
    );
    await removeApplicationReference(
      scope,
      "app-support",
      "dataset-a",
      { expectedRevision: 4, reason: "移除旧引用" },
      { idempotencyKey: "stable-reference-key-2" },
    );
    await transferKnowledgeBaseOwnership(
      scope,
      "dataset-a",
      {
        workspaceId: "workspace-test",
        expectedProfileRevision: 3,
        expectedOwnershipRevision: 7,
        expectedSourceWorkspaceRevision: 7,
        expectedTargetWorkspaceRevision: 4,
        reason: "职责转移",
      },
      { idempotencyKey: "stable-transfer-key" },
    );

    expect(calls[0]?.init?.headers).toEqual(
      expect.objectContaining({ "Idempotency-Key": "stable-reference-key" }),
    );
    expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({ reason: "建立客服引用" });
    expect(calls[1]?.input).toBe(
      "https://enterprise.test/api/enterprise/apps/app-support/knowledge-bases/dataset-a",
    );
    expect(calls[1]?.init?.method).toBe("DELETE");
    expect(JSON.parse(String(calls[1]?.init?.body))).toEqual({
      expected_revision: 4,
      reason: "移除旧引用",
    });
    expect(JSON.parse(String(calls[2]?.init?.body))).toEqual({
      target_workspace_id: "workspace-test",
      expected_dataset_profile_revision: 3,
      expected_ownership_revision: 7,
      expected_source_workspace_revision: 7,
      expected_target_workspace_revision: 4,
      reason: "职责转移",
    });
  });
});
