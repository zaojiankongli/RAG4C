// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import * as api from "./enterpriseAccessApi";

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};

const invitation = {
  id: "invite/1",
  email: "member@example.com",
  role: "member",
  status: "pending",
  expires_at: "2026-09-02T08:00:00Z",
  invited_by: "account-admin",
  revision: 3,
  send_count: 2,
  last_sent_at: "2026-08-26T08:30:00Z",
};

const delivery = {
  state: "manual_link_required",
  invite_token: "one-time-token",
  expires_at: "2026-09-02T08:00:00Z",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Stage 8 invitation mutation API", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ invitation, delivery }))),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("creates an invitation with a stable Idempotency-Key and manual delivery result", async () => {
    expect(typeof api.createEnterpriseInvitation).toBe("function");
    const result = await api.createEnterpriseInvitation(
      scope,
      {
        email: "  member@example.com ",
        role: "member",
        expires_in_days: 7,
        reason: "加入知识运营团队",
      },
      { idempotencyKey: "invite-create-1" },
    );

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("http://enterprise.test/api/enterprise/invitations");
    expect(init?.method).toBe("POST");
    expect(init?.headers).toMatchObject({
      "Idempotency-Key": "invite-create-1",
      "X-RAG4C-Tenant": "tenant-1",
      Authorization: "Bearer actor-token",
    });
    expect(JSON.parse(String(init?.body))).toEqual({
      email: "member@example.com",
      role: "member",
      expires_in_days: 7,
      reason: "加入知识运营团队",
    });
    expect(result.delivery?.state).toBe("manual_link_required");
  });

  it.each([
    [
      "resend",
      "resendEnterpriseInvitation",
      "/api/enterprise/invitations/invite%2F1/resend",
      { revision: 3, expires_in_days: 14, reason: "重新生成安全链接" },
    ],
    [
      "revoke",
      "revokeEnterpriseInvitation",
      "/api/enterprise/invitations/invite%2F1/revoke",
      { revision: 3, reason: "撤销未使用邀请" },
    ],
  ] as const)("uses the %s lifecycle endpoint", async (_label, method, path, payload) => {
    const mutation =
      method === "resendEnterpriseInvitation"
        ? api.resendEnterpriseInvitation
        : api.revokeEnterpriseInvitation;
    expect(typeof mutation).toBe("function");
    await mutation(scope, "invite/1", payload as never, {
      idempotencyKey: `invite-${method}-1`,
    });

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(`http://enterprise.test${path}`);
    expect(init?.method).toBe("POST");
    expect(init?.headers).toMatchObject({
      "Idempotency-Key": `invite-${method}-1`,
    });
    expect(JSON.parse(String(init?.body))).toEqual(payload);
  });

  it("accepts a route token through the dedicated accept endpoint", async () => {
    expect(typeof api.acceptEnterpriseInvitation).toBe("function");
    await api.acceptEnterpriseInvitation(
      scope,
      { invite_token: "route-token" },
      { idempotencyKey: "invite-accept-1" },
    );

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("http://enterprise.test/api/enterprise/invitations/accept");
    expect(init?.method).toBe("POST");
    expect(init?.headers).toMatchObject({ "Idempotency-Key": "invite-accept-1" });
    expect(JSON.parse(String(init?.body))).toEqual({ invite_token: "route-token" });
  });
});
