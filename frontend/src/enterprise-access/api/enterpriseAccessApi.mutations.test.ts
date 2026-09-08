// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  createDatasetAccessGrant,
  fetchDatasetAccessSummary,
  resumeDatasetAccessGrant,
  revokeDatasetAccessGrant,
  updateDatasetAccessGrantRole,
} from "./enterpriseAccessApi";

const scope: EnterpriseScope = {
  tenantId: "tenant 企业/一",
  datasetId: "dataset/制度库",
  actorToken: "signed.actor.token",
};

const grant = {
  id: "grant/1",
  dataset_id: "dataset/制度库",
  subject_type: "group",
  subject_id: "group/研发",
  subject_name: "研发协作组",
  role: "editor",
  status: "active",
  revision: 8,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("enterpriseAccessApi dataset ACL mutations", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse(grant))),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("refreshes the access summary with the same tenant and Bearer headers", async () => {
    const summary = {
      dataset_id: "dataset/制度库",
      owner_id: "account-1",
      visibility: "private",
      enforcement_mode: "dataset_acl",
      actor_role: "admin",
      effective_permissions: ["knowledge.manage"],
      dataset_acl_supported: true,
      group_grants_supported: true,
      organization_inheritance_supported: true,
      warnings: [],
    };
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(summary));

    const result = await fetchDatasetAccessSummary(scope);

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(
      "http://enterprise.test/api/knowledge-bases/dataset%2F%E5%88%B6%E5%BA%A6%E5%BA%93/access-summary",
    );
    expect(init?.method).toBe("GET");
    expect(init?.headers).toMatchObject({
      "X-RAG4C-Tenant": "tenant 企业/一",
      Authorization: "Bearer signed.actor.token",
    });
    expect(result.enforcement_mode).toBe("dataset_acl");
  });

  it("creates a grant with reason but never sends a revision", async () => {
    const result = await createDatasetAccessGrant(scope, {
      subject_type: "group",
      subject_id: "group/研发",
      role: "editor",
      reason: "  为研发协作开放制度库  ",
    });

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(
      "http://enterprise.test/api/knowledge-bases/dataset%2F%E5%88%B6%E5%BA%A6%E5%BA%93/access-grants",
    );
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      subject_type: "group",
      subject_id: "group/研发",
      role: "editor",
      reason: "为研发协作开放制度库",
    });
    expect(JSON.parse(String(init?.body))).not.toHaveProperty("revision");
    expect(init?.headers).toMatchObject({
      "X-RAG4C-Tenant": "tenant 企业/一",
      Authorization: "Bearer signed.actor.token",
    });
    expect(result.id).toBe("grant/1");
  });

  it.each([
    ["role", updateDatasetAccessGrantRole, "PATCH", "/grant%2F1", { role: "manager" }],
    ["revoke", revokeDatasetAccessGrant, "POST", "/grant%2F1/revoke", {}],
    ["resume", resumeDatasetAccessGrant, "POST", "/grant%2F1/resume", {}],
  ] as const)(
    "sends reason and revision for %s",
    async (_name, mutation, method, suffix, extra) => {
      await mutation(scope, "grant/1", { ...extra, reason: "权限边界调整", revision: 8 } as never);

      const [url, init] = vi.mocked(fetch).mock.calls[0];
      expect(url).toContain(
        `/api/knowledge-bases/dataset%2F%E5%88%B6%E5%BA%A6%E5%BA%93/access-grants${suffix}`,
      );
      expect(init?.method).toBe(method);
      expect(JSON.parse(String(init?.body))).toEqual({
        ...extra,
        reason: "权限边界调整",
        revision: 8,
      });
      expect(init?.headers).toMatchObject({
        "X-RAG4C-Tenant": "tenant 企业/一",
        Authorization: "Bearer signed.actor.token",
      });
    },
  );

  it("fails closed before fetch for blank reason, invalid revision, or empty identity", async () => {
    await expect(
      createDatasetAccessGrant(scope, {
        subject_type: "group",
        subject_id: "group-1",
        role: "viewer",
        reason: "   ",
      }),
    ).rejects.toThrow("reason is required");
    await expect(
      updateDatasetAccessGrantRole(scope, "grant-1", {
        role: "viewer",
        reason: "修正",
        revision: 0,
      }),
    ).rejects.toThrow("revision");
    await expect(
      revokeDatasetAccessGrant({ ...scope, actorToken: "" }, "grant-1", {
        reason: "修正",
        revision: 2,
      }),
    ).rejects.toThrow("actorToken is required");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("preserves structured 409 and migration-required 503 errors from the shared client", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        jsonResponse({ detail: { code: "dataset_access_grant_revision_conflict" } }, 409),
      )
      .mockResolvedValueOnce(
        jsonResponse({ detail: { code: "enterprise_access_graph_migration_required" } }, 503),
      );

    await expect(
      revokeDatasetAccessGrant(scope, "grant-1", { reason: "撤销", revision: 2 }),
    ).rejects.toMatchObject({ status: 409 });
    await expect(
      resumeDatasetAccessGrant(scope, "grant-1", { reason: "恢复", revision: 2 }),
    ).rejects.toMatchObject({ status: 503 });
  });
});
