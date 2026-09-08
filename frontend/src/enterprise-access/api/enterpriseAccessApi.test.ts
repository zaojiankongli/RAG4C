// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  fetchDatasetAccessGrants,
  fetchEnterpriseGroupMembers,
  fetchEnterpriseGroups,
  fetchEnterpriseInvitations,
  fetchOrganizationUnits,
} from "./enterpriseAccessApi";

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

describe("enterpriseAccessApi", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ items: [], count: 0 }))),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("uses the tenant and Bearer headers for organization keyset reads", async () => {
    await fetchOrganizationUnits(scope, { beforeId: "ou/9", limit: 25 });

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(
      "http://enterprise.test/api/enterprise/organization-units?before_id=ou%2F9&limit=25",
    );
    expect(init?.method).toBe("GET");
    expect(init?.headers).toMatchObject({
      "X-RAG4C-Tenant": "tenant 企业/一",
      Authorization: "Bearer signed.actor.token",
    });
  });

  it("encodes group member identities and their next cursor", async () => {
    await fetchEnterpriseGroupMembers(scope, "group/研发", {
      beforeId: "member/8",
      limit: 10,
    });

    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(
      "http://enterprise.test/api/enterprise/groups/group%2F%E7%A0%94%E5%8F%91/members?before_id=member%2F8&limit=10",
    );
  });

  it("uses the exact group and invitation collection endpoints", async () => {
    await fetchEnterpriseGroups(scope, { limit: 50 });
    await fetchEnterpriseInvitations(scope, { limit: 50 });

    const urls = vi.mocked(fetch).mock.calls.map(([input]) => String(input));
    expect(urls).toContain("http://enterprise.test/api/enterprise/groups?limit=50");
    expect(urls).toContain("http://enterprise.test/api/enterprise/invitations?limit=50");
  });

  it("uses the selected knowledge base for ACL grants", async () => {
    await fetchDatasetAccessGrants(scope, { beforeId: "grant-3", limit: 20 });

    const [url] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(
      "http://enterprise.test/api/knowledge-bases/dataset%2F%E5%88%B6%E5%BA%A6%E5%BA%93/access-grants?before_id=grant-3&limit=20",
    );
  });

  it("fails closed before fetch when an actor token or dataset is missing", async () => {
    await expect(fetchOrganizationUnits({ ...scope, actorToken: "" })).rejects.toThrow(
      "actorToken is required",
    );
    await expect(fetchDatasetAccessGrants({ ...scope, datasetId: undefined })).rejects.toThrow(
      "datasetId is required",
    );
    expect(fetch).not.toHaveBeenCalled();
  });
});
