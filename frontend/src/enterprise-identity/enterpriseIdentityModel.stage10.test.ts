// @vitest-environment node

import { describe, expect, it } from "vitest";
import {
  SCIM_DATA_PLANE_REVISION,
  projectEnterpriseScimToken,
  projectScimDataPlaneEvidence,
} from "./enterpriseIdentityModel";

describe("Stage 10 SCIM data-plane model", () => {
  it("marks the data plane ready only for a clean 0022 readiness report", () => {
    expect(
      projectScimDataPlaneEvidence({
        expected_head: SCIM_DATA_PLANE_REVISION,
        current_revision: SCIM_DATA_PLANE_REVISION,
        status: "ready",
        missing_capability_groups: [],
      }),
    ).toEqual({
      state: "scim_data_plane_ready",
      ready: true,
      revision: SCIM_DATA_PLANE_REVISION,
      base_path: "/scim/v2",
      resources: ["Users", "Groups"],
      scopes: ["users:read", "users:write", "groups:read", "groups:write"],
    });

    expect(
      projectScimDataPlaneEvidence({
        expected_head: SCIM_DATA_PLANE_REVISION,
        current_revision: "0021_enterprise_identity_federation",
        status: "behind",
        missing_capability_groups: ["scim_provisioning_data_plane"],
      }),
    ).toMatchObject({
      state: "scim_data_plane_not_connected",
      ready: false,
      revision: "0021_enterprise_identity_federation",
    });
  });

  it("projects token last-used/use-count evidence without IP or bearer material", () => {
    const token = projectEnterpriseScimToken({
      id: "scim-1",
      name: "hr-sync",
      prefix: "r4c_scim_ab12",
      status: "active",
      scopes: ["users:read", "groups:read"],
      expires_at: "2026-09-26T08:00:00Z",
      revision: 4,
      last_used_at: "2026-08-26T09:30:00Z",
      use_count: 17,
      last_used_ip_hash: "never-project",
      token_hash: "never-project",
      scim_token: "never-project",
    });

    expect(token).toMatchObject({
      last_used_at: "2026-08-26T09:30:00Z",
      use_count: 17,
    });
    expect(token).not.toHaveProperty("last_used_ip_hash");
    expect(token).not.toHaveProperty("token_hash");
    expect(token).not.toHaveProperty("scim_token");
  });
});
