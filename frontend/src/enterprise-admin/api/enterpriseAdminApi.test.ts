// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchDatasetAccessSummary,
  fetchEnterpriseContext,
  fetchEnterpriseMembers,
} from "./enterpriseAdminApi";
import type { EnterpriseScope } from "../model/enterpriseAdminModel";

const scope: EnterpriseScope = {
  tenantId: "tenant 企业/一",
  datasetId: "dataset/制度库",
  actorToken: "signed.actor.token",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("enterpriseAdminApi", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("requests the enterprise context with the authoritative tenant and actor headers", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ tenant: {}, actor: {} }));

    await fetchEnterpriseContext(scope);

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("http://enterprise.test/api/enterprise/context");
    expect(init?.method).toBe("GET");
    expect(init?.headers).toMatchObject({
      "X-RAG4C-Tenant": "tenant 企业/一",
      Authorization: "Bearer signed.actor.token",
    });
  });

  it("encodes member pagination and filters without changing the tenant scope", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ items: [], count: 0, next_before_id: null }),
    );

    await fetchEnterpriseMembers(scope, {
      query: "张 三",
      role: "editor",
      beforeId: 42,
      limit: 25,
    });

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(
      "http://enterprise.test/api/enterprise/members?q=%E5%BC%A0+%E4%B8%89&role=editor&before_id=42&limit=25",
    );
    expect(init?.headers).toMatchObject({ "X-RAG4C-Tenant": "tenant 企业/一" });
  });

  it("uses the current dataset for the access summary endpoint", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ dataset_id: scope.datasetId }));

    await fetchDatasetAccessSummary(scope);

    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(
      "http://enterprise.test/api/knowledge-bases/dataset%2F%E5%88%B6%E5%BA%A6%E5%BA%93/access-summary",
    );
  });
});
