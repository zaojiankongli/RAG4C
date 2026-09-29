// @vitest-environment node

import { describe, expect, it } from "vitest";
import * as model from "./enterpriseIdentityModel";

const stage9 = model as typeof model & {
  projectEnterpriseDomain: (input: unknown) => unknown;
  projectEnterpriseIdentityProvider: (input: unknown) => unknown;
  projectEnterpriseScimToken: (input: unknown) => unknown;
  projectScimTokenMutation: (input: unknown) => unknown;
};

describe("Stage 9 identity federation model", () => {
  it("projects domain and provider control-plane evidence without raw secrets", () => {
    expect(typeof stage9.projectEnterpriseDomain).toBe("function");
    expect(
      stage9.projectEnterpriseDomain({
        id: "domain-1",
        domain: "example.com",
        normalized_domain: "example.com",
        status: "pending",
        verification_method: "dns_txt",
        txt_host: "_rag4c-verify.example.com",
        txt_value: "rag4c-verification=challenge",
        revision: 2,
      }),
    ).toMatchObject({ id: "domain-1", status: "pending", revision: 2 });

    const provider = stage9.projectEnterpriseIdentityProvider({
      id: "idp-1",
      name: "Company OIDC",
      provider_type: "oidc",
      status: "draft",
      trusted_domain_id: "domain-1",
      validation_state: "valid",
      runtime_state: "runtime_not_connected",
      issuer_url: "https://id.example.com",
      client_id: "client-id",
      secret_ref: "vault://identity/client",
      revision: 3,
      client_secret: ["never", "-list"].join(""),
    }) as unknown as Record<string, unknown>;
    expect(provider).toMatchObject({ runtime_state: "runtime_not_connected", revision: 3 });
    expect(provider).not.toHaveProperty("client_secret");
  });

  it("keeps raw SCIM token only in one-time mutation delivery", () => {
    const row = stage9.projectEnterpriseScimToken({
      id: "scim-1",
      name: "hr-sync",
      prefix: "r4c_scim_ab12",
      status: "active",
      scopes: ["Users.Read", "Groups.Read"],
      expires_at: "2026-09-26T08:00:00Z",
      revision: 1,
      token_hash: "never-list",
      scim_token: "never-list",
    }) as unknown as Record<string, unknown>;
    expect(row).not.toHaveProperty("token_hash");
    expect(row).not.toHaveProperty("scim_token");

    expect(typeof stage9.projectScimTokenMutation).toBe("function");
    const result = stage9.projectScimTokenMutation({
      token: row,
      delivery: { state: "token_returned_once", scim_token: "one-time-scim-token" },
    }) as unknown as { token: Record<string, unknown>; delivery: Record<string, unknown> | null };
    expect(result.delivery).toEqual({
      state: "token_returned_once",
      scim_token: "one-time-scim-token",
    });
    expect(result.token).not.toHaveProperty("scim_token");
  });
});
