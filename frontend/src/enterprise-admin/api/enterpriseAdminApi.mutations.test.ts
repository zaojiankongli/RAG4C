// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchEnterpriseAuditEvents,
  patchEnterpriseMember,
  restoreEnterpriseMember,
  suspendEnterpriseMember,
  updateEnterpriseMemberRole,
} from "./enterpriseAdminApi";
import type { EnterpriseScope } from "../model/enterpriseAdminModel";

const scope: EnterpriseScope = {
  tenantId: "tenant 企业/一",
  actorToken: "signed.actor.token",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const member = {
  membership_id: 42,
  account_id: "account-42",
  name: "周宁",
  email: "zhou@example.com",
  role: "editor",
  joined_at: "2026-08-19T08:00:00Z",
  status: "active",
  revision: 2,
};

describe("enterpriseAdminApi member mutations and tenant audit", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("routes an encoded account role change with tenant and bearer authentication and projects the response", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        member,
        audit_metadata: { event_id: "evt-1", sequence: 91, action: "member.updated" },
      }),
    );

    const response = await patchEnterpriseMember(scope, "account/42", {
      role: "editor",
      reason: "职责调整",
      expected_revision: 1,
    });

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("http://enterprise.test/api/enterprise/members/account%2F42/role");
    expect(init?.method).toBe("PATCH");
    expect(init?.headers).toMatchObject({
      "X-RAG4C-Tenant": "tenant 企业/一",
      Authorization: "Bearer signed.actor.token",
    });
    expect(init?.body).toBe(
      JSON.stringify({ role: "editor", reason: "职责调整", expected_revision: 1 }),
    );
    expect(response.member.status).toBe("active");
    expect(response.member.revision).toBe(2);
    expect(response.audit_metadata).toEqual({
      event_id: "evt-1",
      sequence: 91,
      action: "member.updated",
    });
  });

  it("routes role, suspend, and restore helpers through the explicit backend action endpoints", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({ member }))
      .mockResolvedValueOnce(jsonResponse({ member: { ...member, status: "suspended" } }))
      .mockResolvedValueOnce(jsonResponse({ member: { ...member, status: "active" } }));

    await updateEnterpriseMemberRole(scope, "account-42", "admin", "晋升管理员", 2);
    await suspendEnterpriseMember(scope, "account-42", "安全事件调查", 3);
    await restoreEnterpriseMember(scope, "account-42", "调查结束恢复访问", 4);

    expect(
      vi.mocked(fetch).mock.calls.map(([url, init]) => [url, init?.method, init?.body]),
    ).toEqual([
      [
        "http://enterprise.test/api/enterprise/members/account-42/role",
        "PATCH",
        JSON.stringify({ role: "admin", reason: "晋升管理员", expected_revision: 2 }),
      ],
      [
        "http://enterprise.test/api/enterprise/members/account-42/suspend",
        "POST",
        JSON.stringify({ reason: "安全事件调查", expected_revision: 3 }),
      ],
      [
        "http://enterprise.test/api/enterprise/members/account-42/resume",
        "POST",
        JSON.stringify({ reason: "调查结束恢复访问", expected_revision: 4 }),
      ],
    ]);
  });

  it("fails closed before fetch when a member mutation has no actor token", () => {
    const fetchMock = vi.mocked(fetch);

    expect(() =>
      patchEnterpriseMember({ ...scope, actorToken: "" }, "account-42", {
        status: "suspended",
        reason: "reason",
        expected_revision: 1,
      }),
    ).toThrow("actorToken");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects blank reasons and invalid revision values before fetch", () => {
    const fetchMock = vi.mocked(fetch);

    expect(() =>
      patchEnterpriseMember(scope, "account-42", {
        status: "suspended",
        reason: "   ",
        expected_revision: 1,
      }),
    ).toThrow("reason");
    expect(() =>
      patchEnterpriseMember(scope, "account-42", {
        status: "suspended",
        reason: "reason",
        expected_revision: -1,
      }),
    ).toThrow("expected_revision");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("encodes audit keyset filters and enforces the one-to-two-hundred page limit", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({
        items: [{ sequence: 90, action: "member.updated" }],
        next_before_sequence: 89,
      }),
    );

    const response = await fetchEnterpriseAuditEvents(scope, {
      beforeSequence: 91,
      limit: 25,
      actor: "account/一",
      action: "member.updated/suspend",
      resource: "membership/42",
      request: "request/7",
      time: "2026-08-26T00:00:00Z/2026-08-26T23:59:59Z",
    });

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe(
      "http://enterprise.test/api/enterprise/audit-events?before_sequence=91&limit=25" +
        "&actor=account%2F%E4%B8%80&action=member.updated%2Fsuspend" +
        "&resource=membership%2F42&request=request%2F7" +
        "&time=2026-08-26T00%3A00%3A00Z%2F2026-08-26T23%3A59%3A59Z",
    );
    expect(init?.headers).toMatchObject({
      "X-RAG4C-Tenant": "tenant 企业/一",
      Authorization: "Bearer signed.actor.token",
    });
    expect(response.items[0].sequence).toBe(90);
    expect(response.next_before_sequence).toBe(89);

    expect(() => fetchEnterpriseAuditEvents(scope, { limit: 0 })).toThrow("limit");
    expect(() => fetchEnterpriseAuditEvents(scope, { limit: 201 })).toThrow("limit");
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  });

  it("fails closed before fetch when audit has no actor token", () => {
    const fetchMock = vi.mocked(fetch);

    expect(() => fetchEnterpriseAuditEvents({ ...scope, actorToken: "" })).toThrow("actorToken");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("preserves a backend 503 as an ApiError for the hook to project", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse({ detail: "route not mounted" }, 503));

    await expect(fetchEnterpriseAuditEvents(scope)).rejects.toMatchObject({
      kind: "http",
      status: 503,
      message: "route not mounted",
      body: { detail: "route not mounted" },
    });
  });
});
