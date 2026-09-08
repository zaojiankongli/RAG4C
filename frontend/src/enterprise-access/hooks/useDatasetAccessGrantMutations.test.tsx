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
  subject_type: "group",
  subject_id: "group-1",
  subject_name: "研发协作组",
  role: "editor",
  status: "active",
  revision: 9,
};

const baseContext: EnterpriseContext = {
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
  actor: { id: "account-1", name: "林澈", email: "lin@example.com", role: "admin" },
  member_count: 1,
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

describe("useDatasetAccessGrantMutations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.createDatasetAccessGrant).mockResolvedValue(grant);
    vi.mocked(api.updateDatasetAccessGrantRole).mockResolvedValue({ ...grant, role: "manager" });
    vi.mocked(api.revokeDatasetAccessGrant).mockResolvedValue({ ...grant, status: "revoked" });
    vi.mocked(api.resumeDatasetAccessGrant).mockResolvedValue(grant);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  const gateCases: Array<[string, EnterpriseContext, EnterpriseScope]> = [
    [
      "missing knowledge.manage",
      { ...baseContext, effective_permissions: ["knowledge.read"] },
      scope,
    ],
    [
      "unavailable dataset ACL",
      {
        ...baseContext,
        capabilities: {
          ...baseContext.capabilities,
          dataset_acl: { state: "unavailable", label: "知识库 ACL", reason: "ACL 迁移未完成" },
        },
      },
      scope,
    ],
    ["missing dataset", baseContext, { ...scope, datasetId: undefined }],
  ];

  it.each(gateCases)(
    "fails closed for %s before calling the mutation API",
    async (_name, context, gatedScope) => {
      const target = resource();
      const { result } = renderHook(() =>
        useDatasetAccessGrantMutations({ scope: gatedScope, context, accessGrants: target }),
      );

      await act(async () => {
        await result.current.create({
          subject_type: "group",
          subject_id: "group-1",
          role: "editor",
          reason: "授权研发组",
        });
      });

      expect(api.createDatasetAccessGrant).not.toHaveBeenCalled();
      expect(result.current.error?.message).toBeTruthy();
      expect(target.upsert).not.toHaveBeenCalled();
    },
  );

  it("creates a grant, performs local upsert, and refreshes both grant list and access summary", async () => {
    const target = resource();
    const onAccessSummaryRefresh = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useDatasetAccessGrantMutations({
        scope,
        context: baseContext,
        accessGrants: target,
        onAccessSummaryRefresh,
      }),
    );

    await act(async () => {
      await result.current.create({
        subject_type: "group",
        subject_id: "group-1",
        role: "editor",
        reason: "  授权研发组  ",
      });
    });

    expect(api.createDatasetAccessGrant).toHaveBeenCalledWith(
      scope,
      {
        subject_type: "group",
        subject_id: "group-1",
        role: "editor",
        reason: "授权研发组",
      },
      expect.anything(),
    );
    expect(target.upsert).toHaveBeenCalledWith(grant);
    expect(target.reload).toHaveBeenCalledTimes(1);
    expect(onAccessSummaryRefresh).toHaveBeenCalledTimes(1);
    expect(result.current.success).toBe("授权已创建");
    expect(result.current.saving).toBe(false);
  });

  const mutationCases: Array<
    [
      string,
      "updateRole" | "revoke" | "resume",
      { role: "manager"; reason: string; revision: number } | { reason: string; revision: number },
    ]
  > = [
    ["role", "updateRole", { role: "manager", reason: "角色调整", revision: 9 }],
    ["revoke", "revoke", { reason: "撤销授权", revision: 9 }],
    ["resume", "resume", { reason: "恢复授权", revision: 9 }],
  ];

  it.each(mutationCases)(
    "executes the %s mutation with revision fencing and refreshes facts",
    async (_name, method, input) => {
      const target = resource();
      const { result } = renderHook(() =>
        useDatasetAccessGrantMutations({ scope, context: baseContext, accessGrants: target }),
      );

      await act(async () => {
        if (method === "updateRole") await result.current.updateRole(grant, input as never);
        if (method === "revoke") await result.current.revoke(grant, input);
        if (method === "resume") await result.current.resume(grant, input);
      });

      const mutation =
        method === "updateRole"
          ? api.updateDatasetAccessGrantRole
          : method === "revoke"
            ? api.revokeDatasetAccessGrant
            : api.resumeDatasetAccessGrant;
      expect(mutation).toHaveBeenCalledWith(scope, "grant-1", input, expect.anything());
      expect(target.upsert).toHaveBeenCalledTimes(1);
      expect(target.reload).toHaveBeenCalledTimes(1);
      expect(result.current.success).toBe(
        method === "updateRole"
          ? "授权角色已更新"
          : method === "revoke"
            ? "授权已撤销"
            : "授权已恢复",
      );
    },
  );

  it("surfaces 409 as refresh-needed without clearing the loaded grant list", async () => {
    const target = resource();
    vi.mocked(api.revokeDatasetAccessGrant).mockRejectedValue(
      new ApiError("conflict", "http", 409, {
        detail: { code: "dataset_access_grant_revision_conflict" },
      }),
    );
    const { result } = renderHook(() =>
      useDatasetAccessGrantMutations({ scope, context: baseContext, accessGrants: target }),
    );

    await act(async () => {
      await result.current.revoke(grant, { reason: "撤销", revision: 9 });
    });

    expect(result.current.error).toMatchObject({ needsRefresh: true });
    expect(result.current.error?.message).toContain("刷新");
    expect(target.upsert).not.toHaveBeenCalled();
    expect(target.reload).not.toHaveBeenCalled();
  });

  it("surfaces migration-required 503 as unavailable and never guesses from grant rows", async () => {
    const target = resource();
    vi.mocked(api.createDatasetAccessGrant).mockRejectedValue(
      new ApiError("migration required", "http", 503, {
        detail: { code: "enterprise_access_graph_migration_required" },
      }),
    );
    const { result } = renderHook(() =>
      useDatasetAccessGrantMutations({ scope, context: baseContext, accessGrants: target }),
    );

    await act(async () => {
      await result.current.create({
        subject_type: "account",
        subject_id: "account-2",
        role: "viewer",
        reason: "授权",
      });
    });

    expect(result.current.error).toMatchObject({ migrationRequired: true });
    expect(result.current.error?.message).toContain("迁移");
    expect(target.upsert).not.toHaveBeenCalled();
  });
});
