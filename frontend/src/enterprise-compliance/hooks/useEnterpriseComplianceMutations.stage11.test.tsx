// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import * as api from "../api/enterpriseComplianceApi";
import { useEnterpriseComplianceMutations } from "./useEnterpriseComplianceMutations";

vi.mock("../api/enterpriseComplianceApi");

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};
const context: EnterpriseContext = {
  tenant: {
    id: "tenant-1",
    name: "星海科技",
    plan: "enterprise",
    status: "active",
    quota_documents: 1,
    quota_chunks: 1,
    doc_count: 0,
    chunk_count: 0,
  },
  actor: { id: "owner", name: "Owner", email: "owner@example.com", role: "owner" },
  member_count: 1,
  dataset_count: 1,
  effective_permissions: ["enterprise.manage"],
  role_permissions: {},
  capabilities: { audit_compliance: { state: "ready", label: "审计合规", reason: null } },
};

describe("Stage 11 compliance mutations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.createComplianceIdempotencyKey).mockReturnValue("stable-compliance-key");
  });

  it("reuses the same key when a retention execute network result is unknown", async () => {
    vi.mocked(api.executeRetention)
      .mockRejectedValueOnce(new ApiError("network", "network"))
      .mockResolvedValueOnce({
        deleted_count: 8,
        protected_count: 2,
        policy_revision: 7,
        executed_at: "2026-08-26T08:00:00Z",
      });
    const reload = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useEnterpriseComplianceMutations({ scope, context, reload }),
    );

    await act(async () => {
      await result.current.execute({
        policy_revision: 7,
        preview_fingerprint: "sha256:preview",
        reason: "approved",
        confirmation: "EXECUTE RETENTION",
      });
    });
    expect(result.current.error?.retryAvailable).toBe(true);
    await act(async () => {
      await result.current.retry();
    });
    expect(vi.mocked(api.executeRetention).mock.calls[0][2]).toEqual({
      idempotencyKey: "stable-compliance-key",
    });
    expect(vi.mocked(api.executeRetention).mock.calls[1][2]).toEqual({
      idempotencyKey: "stable-compliance-key",
    });
  });

  it("maps revision conflicts to refresh-required without inventing an automatic retry", async () => {
    vi.mocked(api.releaseLegalHold).mockRejectedValueOnce(
      new ApiError("conflict", "http", 409, {}),
    );
    const { result } = renderHook(() =>
      useEnterpriseComplianceMutations({
        scope,
        context,
        reload: vi.fn().mockResolvedValue(undefined),
      }),
    );
    await act(async () => {
      await result.current.releaseHold(
        { id: "hold-1", revision: 2 },
        { revision: 2, reason: "case closed" },
      );
    });
    expect(result.current.error).toMatchObject({
      needsRefresh: true,
      retryAvailable: false,
      status: 409,
    });
  });
});
