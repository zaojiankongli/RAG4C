// @vitest-environment jsdom

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import * as api from "../api/enterpriseIdentityApi";
import { SCIM_DATA_PLANE_REVISION } from "../enterpriseIdentityModel";
import { useEnterpriseIdentityCenter } from "./useEnterpriseIdentityCenter";

vi.mock("../api/enterpriseIdentityApi");

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
  effective_permissions: [],
  role_permissions: {},
  capabilities: { identity_federation: { state: "ready", label: "身份联合", reason: null } },
};
const emptyPage = { items: [], count: 0, next_before_id: null };

describe("Stage 10 identity-center hook", () => {
  beforeEach(() => {
    vi.mocked(api.fetchIdentityDomains).mockResolvedValue(emptyPage);
    vi.mocked(api.fetchIdentityProviders).mockResolvedValue(emptyPage);
    vi.mocked(api.fetchScimTokens).mockResolvedValue(emptyPage);
  });
  afterEach(() => vi.clearAllMocks());

  it("projects 0022 readiness into scim_data_plane_ready without calling SCIM resources", async () => {
    const { result } = renderHook(() =>
      useEnterpriseIdentityCenter(scope, context, {
        expected_head: SCIM_DATA_PLANE_REVISION,
        current_revision: SCIM_DATA_PLANE_REVISION,
        status: "ready",
        missing_capability_groups: [],
      }),
    );

    await waitFor(() => expect(result.current.domains.status).toBe("ready"));
    expect(result.current.scimDataPlane).toMatchObject({
      state: "scim_data_plane_ready",
      ready: true,
    });
    expect(api.fetchScimTokens).toHaveBeenCalledTimes(1);
  });
});
