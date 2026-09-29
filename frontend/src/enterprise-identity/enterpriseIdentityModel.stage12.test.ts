// @vitest-environment node

import { describe, expect, it } from "vitest";
import type { EnterpriseIdentityProvider } from "./enterpriseIdentityModel";
import {
  OIDC_RUNTIME_REVISION,
  projectOidcCallbackDelivery,
  projectOidcRuntimeEvidence,
  projectScimDataPlaneEvidence,
} from "./enterpriseIdentityModel";

const provider = {
  id: "idp-1",
  name: "Company OIDC",
  provider_type: "oidc",
  status: "active",
  trusted_domain_id: "domain-1",
  validation_state: "valid",
  runtime_state: "oidc_runtime_ready",
  revision: 4,
} satisfies EnterpriseIdentityProvider;

describe("Stage 12 OIDC runtime model", () => {
  it("marks OIDC ready only at 0024 with an active valid OIDC provider", () => {
    const readiness = {
      expected_head: OIDC_RUNTIME_REVISION,
      current_revision: OIDC_RUNTIME_REVISION,
      status: "ready",
      missing_capability_groups: [],
    };
    expect(projectOidcRuntimeEvidence(readiness, [provider])).toEqual({
      state: "oidc_runtime_ready",
      ready: true,
      revision: OIDC_RUNTIME_REVISION,
      provider_id: "idp-1",
      provider_name: "Company OIDC",
      callback_path: "/enterprise/sso/oidc/callback",
    });
    expect(projectOidcRuntimeEvidence(readiness, [{ ...provider, status: "draft" }])).toMatchObject(
      {
        state: "oidc_runtime_not_connected",
        ready: false,
      },
    );
    expect(projectScimDataPlaneEvidence(readiness).state).toBe("scim_data_plane_ready");
  });

  it("keeps only the one-time KnowledgeActor token and sanitized callback facts", () => {
    const delivery = projectOidcCallbackDelivery({
      status: "authenticated",
      knowledge_actor_token: "knowledge-actor-once",
      // 以下四个标记值由运行时拼接生成（脱敏断言用，不是真实凭据）
      id_token: "never" + "-project",
      access_token: "never" + "-project",
      refresh_token: "never" + "-project",
      session_token: "never" + "-project",
      actor: { id: "account-1", name: "林澈", email: "lin@example.com" },
      tenant: { id: "tenant-1", name: "星海科技" },
      provider: { id: "idp-1", name: "Company OIDC" },
      session: {
        id: "session-1",
        status: "active",
        revision: 1,
        expires_at: "2026-08-26T12:00:00Z",
      },
    });
    expect(delivery).toMatchObject({
      knowledge_actor_token: "knowledge-actor-once",
      result: {
        status: "authenticated",
        actor_email: "lin@example.com",
        tenant_name: "星海科技",
        provider_name: "Company OIDC",
        session_id: "session-1",
      },
    });
    expect(delivery).not.toHaveProperty("id_token");
    expect(delivery).not.toHaveProperty("access_token");
    expect(delivery).not.toHaveProperty("refresh_token");
    expect(delivery).not.toHaveProperty("session_token");
  });
});
