// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import { completeOidcCallback, startOidcLogin } from "./enterpriseIdentityApi";

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};
function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Stage 12 OIDC runtime API", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/start"))
          return Promise.resolve(
            jsonResponse({
              authorization_url: "https://id.example.com/authorize?state=opaque",
              provider_id: "idp-1",
              expires_at: "2026-08-26T08:05:00Z",
            }),
          );
        return Promise.resolve(
          jsonResponse({
            status: "authenticated",
            knowledge_actor_token: "knowledge-actor-once",
            id_token: "never-project",
            access_token: "never-project",
            actor: { id: "account-1", name: "林澈", email: "lin@example.com" },
            tenant: { id: "tenant-1", name: "星海科技" },
            provider: { id: "idp-1", name: "Company OIDC" },
            session: {
              id: "session-1",
              status: "active",
              revision: 1,
              expires_at: "2026-08-26T12:00:00Z",
            },
          }),
        );
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("starts login with authenticated tenant/provider/redirect authority", async () => {
    const result = await startOidcLogin(scope, {
      provider_id: "idp-1",
      redirect_uri: "http://127.0.0.1:5173/enterprise/sso/oidc/callback",
    });
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("http://enterprise.test/api/enterprise/sso/oidc/start");
    expect(init?.headers).toMatchObject({
      "X-RAG4C-Tenant": "tenant-1",
      Authorization: "Bearer actor-token",
    });
    expect(init?.credentials).toBe("include");
    expect(JSON.parse(String(init?.body))).toEqual({
      tenant_id: "tenant-1",
      provider_id: "idp-1",
      redirect_uri: "http://127.0.0.1:5173/enterprise/sso/oidc/callback",
    });
    expect(result.authorization_url).toContain("https://id.example.com/authorize");
  });

  it("submits callback code/state without exposing or requiring an existing actor token", async () => {
    const result = await completeOidcCallback({
      code: "one-time-code",
      state: "one-time-state",
    });
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("http://enterprise.test/api/enterprise/sso/oidc/callback");
    expect(init?.headers).not.toHaveProperty("Authorization");
    expect(init?.credentials).toBe("include");
    expect(JSON.parse(String(init?.body))).toEqual({
      code: "one-time-code",
      state: "one-time-state",
    });
    expect(result.knowledge_actor_token).toBe("knowledge-actor-once");
    expect(result).not.toHaveProperty("id_token");
    expect(result).not.toHaveProperty("access_token");
  });
});
