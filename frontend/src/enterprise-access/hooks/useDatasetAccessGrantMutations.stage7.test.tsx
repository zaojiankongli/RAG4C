// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import * as api from "../api/enterpriseAccessApi";
import type { DatasetAccessGrant } from "../enterpriseAccessModel";
import { useDatasetAccessGrantMutations } from "./useDatasetAccessGrantMutations";

vi.mock("../api/enterpriseAccessApi");

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};

const grant: DatasetAccessGrant = {
  id: "grant-1",
  dataset_id: "dataset-1",
  subject_type: "account",
  subject_id: "account-2",
  subject_name: "审计成员",
  role: "viewer",
  status: "active",
  revision: 2,
};

const context: EnterpriseContext = {
  tenant: {
    id: "tenant-1",
    name: "星海科技",
    plan: "enterprise",
    status: "active",
    quota_documents: 100,
    quota_chunks: 1000,
    doc_count: 1,
    chunk_count: 2,
  },
  actor: { id: "account-admin", name: "管理员", email: "admin@example.com", role: "admin" },
  member_count: 2,
  dataset_count: 1,
  effective_permissions: ["knowledge.read", "knowledge.manage"],
  role_permissions: { admin: ["knowledge.read", "knowledge.manage"] },
  capabilities: {
    dataset_acl: { state: "ready", label: "知识库 ACL", reason: null },
  },
};

function resource() {
  return {
    upsert: vi.fn(),
    reload: vi.fn().mockResolvedValue(undefined),
  };
}

describe("stage 7 ACL mutation idempotency lifecycle", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    let keyIndex = 0;
    vi.mocked(api.createEnterpriseIdempotencyKey).mockImplementation(
      () => `test-acl-key-${++keyIndex}`,
    );
    vi.mocked(api.createDatasetAccessGrant).mockResolvedValue(grant);
    vi.mocked(api.revokeDatasetAccessGrant).mockResolvedValue({ ...grant, status: "revoked" });
  });

  afterEach(() => vi.clearAllMocks());

  it("reuses the same key when a network-failed operation is explicitly retried", async () => {
    const target = resource();
    vi.mocked(api.createDatasetAccessGrant)
      .mockRejectedValueOnce(new ApiError("offline", "network"))
      .mockResolvedValueOnce(grant);
    const { result } = renderHook(() =>
      useDatasetAccessGrantMutations({ scope, context, accessGrants: target }),
    );

    await act(async () => {
      await result.current.create({
        subject_type: "account",
        subject_id: "account-2",
        role: "viewer",
        reason: "网络重试验证",
      });
    });
    expect(result.current.error?.retryAvailable).toBe(true);

    await act(async () => {
      await result.current.retry();
    });

    expect(api.createDatasetAccessGrant).toHaveBeenCalledTimes(2);
    const firstOptions = vi.mocked(api.createDatasetAccessGrant).mock.calls[0][2] as Record<
      string,
      unknown
    >;
    const retryOptions = vi.mocked(api.createDatasetAccessGrant).mock.calls[1][2] as Record<
      string,
      unknown
    >;
    expect(firstOptions.idempotencyKey).toBeTruthy();
    expect(retryOptions.idempotencyKey).toBe(firstOptions.idempotencyKey);
    expect(result.current.success).toBe("授权已创建");
  });

  it("allocates a different key for each independent operator action", async () => {
    const target = resource();
    const { result } = renderHook(() =>
      useDatasetAccessGrantMutations({ scope, context, accessGrants: target }),
    );

    await act(async () => {
      await result.current.create({
        subject_type: "account",
        subject_id: "account-2",
        role: "viewer",
        reason: "首次授权",
      });
      await result.current.revoke(grant, { reason: "独立撤销", revision: 2 });
    });

    const createOptions = vi.mocked(api.createDatasetAccessGrant).mock.calls[0][2] as Record<
      string,
      unknown
    >;
    const revokeOptions = vi.mocked(api.revokeDatasetAccessGrant).mock.calls[0][3] as Record<
      string,
      unknown
    >;
    expect(createOptions.idempotencyKey).toBeTruthy();
    expect(revokeOptions.idempotencyKey).toBeTruthy();
    expect(revokeOptions.idempotencyKey).not.toBe(createOptions.idempotencyKey);
  });

  it("gives a distinct professional message for an idempotency key conflict", async () => {
    const target = resource();
    vi.mocked(api.createDatasetAccessGrant).mockRejectedValueOnce(
      new ApiError("conflict", "http", 409, {
        detail: { code: "idempotency_key_conflict" },
      }),
    );
    const { result } = renderHook(() =>
      useDatasetAccessGrantMutations({ scope, context, accessGrants: target }),
    );

    await act(async () => {
      await result.current.create({
        subject_type: "account",
        subject_id: "account-2",
        role: "viewer",
        reason: "幂等冲突",
      });
    });

    expect(result.current.error?.message).toContain("幂等键");
    expect(result.current.error?.message).toContain("重复写入");
    expect(result.current.error?.retryAvailable).toBe(false);
    expect(result.current.error?.needsRefresh).toBe(false);
  });
});
