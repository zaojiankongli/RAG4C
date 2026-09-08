// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import * as api from "./enterpriseAccessApi";

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "signed.actor.token",
};

const grant = {
  id: "grant-1",
  dataset_id: "dataset-1",
  subject_type: "account",
  subject_id: "account-2",
  subject_name: "审计成员",
  role: "viewer",
  status: "active",
  revision: 2,
};

const summary = {
  dataset_id: "dataset-1",
  owner_id: "account-owner",
  visibility: "private",
  enforcement_mode: "dataset_acl",
  actor_role: "admin",
  dataset_role: "manager",
  matched_grants: [],
  effective_permissions: ["knowledge.manage"],
  dataset_acl_supported: true,
  group_grants_supported: true,
  organization_inheritance_supported: true,
  warnings: [],
  acl_mode: "dataset_acl",
  acl_revision: 7,
  acl_enabled_at: "2026-08-26T08:00:00Z",
  acl_enabled_by: "account-owner",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requestHeaders(callIndex = 0): Record<string, string> {
  const [, init] = vi.mocked(fetch).mock.calls[callIndex];
  return init?.headers as Record<string, string>;
}

describe("stage 7 enterpriseAccessApi idempotency and ACL control", () => {
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

  it("generates an Idempotency-Key for every ACL grant mutation", async () => {
    await api.createDatasetAccessGrant(scope, {
      subject_type: "account",
      subject_id: "account-2",
      role: "viewer",
      reason: "为审计成员提供只读访问",
    });
    await api.updateDatasetAccessGrantRole(scope, "grant-1", {
      role: "editor",
      reason: "调整职责",
      revision: 2,
    });
    await api.revokeDatasetAccessGrant(scope, "grant-1", { reason: "撤销", revision: 2 });
    await api.resumeDatasetAccessGrant(scope, "grant-1", { reason: "恢复", revision: 3 });

    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(4);
    const keys = vi
      .mocked(fetch)
      .mock.calls.map((_, index) => requestHeaders(index)["Idempotency-Key"]);
    expect(keys.every((key) => typeof key === "string" && key.length > 0)).toBe(true);
    expect(new Set(keys).size).toBe(4);
  });

  it("forwards the caller's stable key unchanged so a retry can replay the same operation", async () => {
    const stableKey = "acl-op-20260826-0001";
    await api.revokeDatasetAccessGrant(scope, "grant-1", { reason: "网络重试", revision: 2 }, {
      idempotencyKey: stableKey,
    } as never);

    expect(requestHeaders()["Idempotency-Key"]).toBe(stableKey);
  });

  it("accepts a 128-character Idempotency-Key and rejects a 129-character key before network", async () => {
    const acceptedKey = "a".repeat(128);
    const rejectedKey = "b".repeat(129);

    await api.revokeDatasetAccessGrant(scope, "grant-1", { reason: "边界长度验证", revision: 2 }, {
      idempotencyKey: acceptedKey,
    } as never);
    expect(requestHeaders()["Idempotency-Key"]).toBe(acceptedKey);

    await expect(
      api.revokeDatasetAccessGrant(scope, "grant-1", { reason: "超出边界", revision: 2 }, {
        idempotencyKey: rejectedKey,
      } as never),
    ).rejects.toThrow(/128/);
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });

  it("uses the access-control disable route and sends the ACL revision and reason", async () => {
    const disableDatasetAcl = (
      api as unknown as {
        disableDatasetAcl: (
          scope: EnterpriseScope,
          payload: { expected_acl_revision: number; reason: string },
          options?: unknown,
        ) => Promise<unknown>;
      }
    ).disableDatasetAcl;
    expect(typeof disableDatasetAcl).toBe("function");

    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(summary));
    await disableDatasetAcl(
      scope,
      { expected_acl_revision: 7, reason: "完成治理切换" },
      { idempotencyKey: "acl-disable-0001" },
    );

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("http://enterprise.test/api/knowledge-bases/dataset-1/access-control/disable");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      expected_acl_revision: 7,
      reason: "完成治理切换",
    });
    expect(requestHeaders()["Idempotency-Key"]).toBe("acl-disable-0001");
  });
});
