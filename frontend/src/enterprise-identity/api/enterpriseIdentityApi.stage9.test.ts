// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import * as api from "./enterpriseIdentityApi";

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};
function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
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
const provider = {
  id: "idp-1",
  name: "Company OIDC",
  provider_type: "oidc",
  status: "draft",
  trusted_domain_id: "domain-1",
  validation_state: "valid",
  runtime_state: "runtime_not_connected",
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

describe("Stage 9 identity federation API", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          jsonResponse({
            domain,
            provider,
            token: scim,
            delivery: { state: "token_returned_once", scim_token: "one-time" },
          }),
        ),
      ),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it.each([
    [
      "createDomain",
      "/api/enterprise/identity/domains",
      { domain: "example.com", reason: "claim" },
    ],
    [
      "verifyDomain",
      "/api/enterprise/identity/domains/domain-1/verify",
      { revision: 1, reason: "dns published" },
    ],
    [
      "revokeDomain",
      "/api/enterprise/identity/domains/domain-1/revoke",
      { revision: 1, reason: "retire" },
    ],
    [
      "activateProvider",
      "/api/enterprise/identity/providers/idp-1/activate",
      { revision: 1, reason: "activate" },
    ],
    [
      "disableProvider",
      "/api/enterprise/identity/providers/idp-1/disable",
      { revision: 1, reason: "disable" },
    ],
    [
      "revokeScimToken",
      "/api/enterprise/identity/scim-tokens/scim-1/revoke",
      { revision: 1, reason: "revoke" },
    ],
  ] as const)("sends %s with a stable Idempotency-Key", async (method, path, body) => {
    const options = { idempotencyKey: `${method}-key` };
    if (method === "createDomain") await api.createDomain(scope, body, options);
    if (method === "verifyDomain") await api.verifyDomain(scope, "domain-1", body, options);
    if (method === "revokeDomain") await api.revokeDomain(scope, "domain-1", body, options);
    if (method === "activateProvider") await api.activateProvider(scope, "idp-1", body, options);
    if (method === "disableProvider") await api.disableProvider(scope, "idp-1", body, options);
    if (method === "revokeScimToken") await api.revokeScimToken(scope, "scim-1", body, options);
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(`http://enterprise.test${path}`);
    expect(init?.headers).toMatchObject({
      "Idempotency-Key": `${method}-key`,
      "X-RAG4C-Tenant": "tenant-1",
    });
    expect(JSON.parse(String(init?.body))).toEqual(body);
  });

  it("creates OIDC/SAML providers and issues one-time SCIM tokens", async () => {
    expect(typeof api.createIdentityProvider).toBe("function");
    await api.createIdentityProvider(
      scope,
      {
        name: "Company OIDC",
        provider_type: "oidc",
        trusted_domain_id: "domain-1",
        issuer_url: "https://id.example.com",
        client_id: "client",
        secret_ref: "vault://identity/client",
        scopes: ["openid", "email"],
        reason: "configure",
      },
      { idempotencyKey: "provider-create" },
    );
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain(
      "/api/enterprise/identity/providers",
    );

    vi.mocked(fetch).mockClear();
    expect(typeof api.issueScimToken).toBe("function");
    const result = await api.issueScimToken(
      scope,
      {
        name: "hr-sync",
        scopes: ["Users.Read"],
        expires_in_days: 30,
        reason: "provisioning control",
      },
      { idempotencyKey: "scim-issue" },
    );
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain(
      "/api/enterprise/identity/scim-tokens",
    );
    expect(result.delivery?.state).toBe("token_returned_once");
  });
});
