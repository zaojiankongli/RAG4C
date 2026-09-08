// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import * as api from "../api/enterpriseIdentityApi";
import { useEnterpriseIdentityMutations } from "./useEnterpriseIdentityMutations";

vi.mock("../api/enterpriseIdentityApi");
const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};
const context = {
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
  effective_permissions: [],
  role_permissions: {},
  capabilities: { identity_federation: { state: "ready", label: "身份联合", reason: null } },
} satisfies EnterpriseContext;
const domain = {
  id: "domain-1",
  domain: "example.com",
  normalized_domain: "example.com",
  status: "pending",
  verification_method: "dns_txt",
  txt_host: "_rag4c-verify.example.com",
  txt_value: "rag4c-verification=challenge",
  revision: 1,
};
const scim = {
  id: "scim-1",
  name: "hr-sync",
  prefix: "r4c_scim_ab12",
  status: "active",
  scopes: ["Users.Read"],
  expires_at: "2026-09-26T08:00:00Z",
  revision: 1,
};
function resource() {
  return { upsert: vi.fn(), reload: vi.fn().mockResolvedValue(undefined) };
}

describe("Stage 9 identity mutation hook", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    let key = 0;
    vi.mocked(api.createIdentityIdempotencyKey).mockImplementation(() => `identity-key-${++key}`);
    vi.mocked(api.createDomain).mockResolvedValue({ domain } as never);
    vi.mocked(api.issueScimToken).mockResolvedValue({
      token: scim,
      delivery: { state: "token_returned_once", scim_token: "raw-once" },
    } as never);
  });

  it("keeps raw SCIM token only in transient state and clears it", async () => {
    const domains = resource();
    const providers = resource();
    const tokens = resource();
    const { result } = renderHook(() =>
      useEnterpriseIdentityMutations({ scope, context, domains, providers, scimTokens: tokens }),
    );
    await act(async () => {
      await result.current.issueScim({
        name: "hr-sync",
        scopes: ["Users.Read"],
        expires_in_days: 30,
        reason: "issue",
      });
    });
    expect(result.current.scimDelivery?.scim_token).toBe("raw-once");
    expect(tokens.upsert.mock.calls[0][0]).not.toHaveProperty("scim_token");
    act(() => result.current.clearScimDelivery());
    expect(result.current.scimDelivery).toBeNull();
  });

  it("reuses the same key for network retry and maps 409/503", async () => {
    const domains = resource();
    const providers = resource();
    const tokens = resource();
    vi.mocked(api.createDomain)
      .mockRejectedValueOnce(new ApiError("offline", "network"))
      .mockResolvedValueOnce({ domain } as never);
    const { result } = renderHook(() =>
      useEnterpriseIdentityMutations({ scope, context, domains, providers, scimTokens: tokens }),
    );
    await act(async () => {
      await result.current.createDomain({ domain: "example.com", reason: "claim" });
    });
    expect(result.current.error?.retryAvailable).toBe(true);
    await act(async () => {
      await result.current.retry();
    });
    expect(vi.mocked(api.createDomain).mock.calls[1][2]).toMatchObject(
      vi.mocked(api.createDomain).mock.calls[0][2] as object,
    );

    vi.mocked(api.createDomain).mockRejectedValueOnce(
      new ApiError("conflict", "http", 409, {
        detail: { code: "tenant_domain_revision_conflict" },
      }),
    );
    await act(async () => {
      await result.current.createDomain({ domain: "second.example.com", reason: "claim" });
    });
    expect(result.current.error?.needsRefresh).toBe(true);
  });
});
