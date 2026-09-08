// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { EnterpriseContext, EnterpriseScope } from "../../enterprise-admin/model";
import * as api from "../api/enterpriseAccessApi";
import { useEnterpriseInvitationMutations } from "./useEnterpriseInvitationMutations";

vi.mock("../api/enterpriseAccessApi");

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
  actor: { id: "account-admin", name: "管理员", email: "admin@example.com", role: "admin" },
  member_count: 1,
  dataset_count: 1,
  effective_permissions: ["knowledge.manage"],
  role_permissions: { admin: ["knowledge.manage"] },
  capabilities: { invitations: { state: "ready", label: "成员邀请", reason: null } },
} satisfies EnterpriseContext;
const invitation = {
  id: "invite-1",
  email: "member@example.com",
  role: "member",
  status: "pending",
  expires_at: "2026-09-02T08:00:00Z",
  invited_by: "account-admin",
  revision: 1,
  send_count: 1,
  last_sent_at: "2026-08-26T08:00:00Z",
};
const resultValue = {
  invitation,
  delivery: {
    state: "manual_link_required",
    invite_token: "transient-token",
    expires_at: invitation.expires_at,
  },
};

function resource() {
  return { upsert: vi.fn(), reload: vi.fn().mockResolvedValue(undefined) };
}

describe("Stage 8 invitation mutation hook", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    let key = 0;
    vi.mocked(api.createEnterpriseIdempotencyKey).mockImplementation(() => `invite-key-${++key}`);
    vi.mocked(api.createEnterpriseInvitation).mockResolvedValue(resultValue as never);
    vi.mocked(api.resendEnterpriseInvitation).mockResolvedValue(resultValue as never);
    vi.mocked(api.revokeEnterpriseInvitation).mockResolvedValue({
      invitation: { ...invitation, status: "revoked" },
      delivery: null,
    } as never);
    vi.mocked(api.acceptEnterpriseInvitation).mockResolvedValue({
      invitation: { ...invitation, status: "accepted" },
      delivery: null,
    } as never);
  });

  it("keeps a one-time token only in transient delivery state and clears it on close", async () => {
    const invitations = resource();
    const { result } = renderHook(() =>
      useEnterpriseInvitationMutations({ scope, context, invitations }),
    );
    await act(async () => {
      await result.current.create({
        email: "member@example.com",
        role: "member",
        expires_in_days: 7,
        reason: "邀请",
      });
    });

    expect(result.current.delivery?.invite_token).toBe("transient-token");
    expect(invitations.upsert).toHaveBeenCalledWith(invitation);
    expect(invitations.upsert.mock.calls[0][0]).not.toHaveProperty("invite_token");
    act(() => result.current.clearDelivery());
    expect(result.current.delivery).toBeNull();
  });

  it("reuses the same Idempotency-Key for a network retry", async () => {
    const invitations = resource();
    vi.mocked(api.resendEnterpriseInvitation)
      .mockRejectedValueOnce(new ApiError("offline", "network"))
      .mockResolvedValueOnce(resultValue as never);
    const { result } = renderHook(() =>
      useEnterpriseInvitationMutations({ scope, context, invitations }),
    );

    await act(async () => {
      await result.current.resend(invitation as never, {
        revision: 1,
        expires_in_days: 7,
        reason: "重试",
      });
    });
    expect(result.current.error?.retryAvailable).toBe(true);
    await act(async () => {
      await result.current.retry();
    });

    const first = vi.mocked(api.resendEnterpriseInvitation).mock.calls[0][3] as {
      idempotencyKey?: string;
    };
    const second = vi.mocked(api.resendEnterpriseInvitation).mock.calls[1][3] as {
      idempotencyKey?: string;
    };
    expect(first.idempotencyKey).toBeTruthy();
    expect(second.idempotencyKey).toBe(first.idempotencyKey);
  });

  it("maps invitation conflict and migration-required errors honestly", async () => {
    const invitations = resource();
    vi.mocked(api.createEnterpriseInvitation).mockRejectedValueOnce(
      new ApiError("conflict", "http", 409, {
        detail: { code: "tenant_invitation_revision_conflict" },
      }),
    );
    const { result } = renderHook(() =>
      useEnterpriseInvitationMutations({ scope, context, invitations }),
    );
    await act(async () => {
      await result.current.create({
        email: "member@example.com",
        role: "member",
        expires_in_days: 7,
        reason: "冲突",
      });
    });
    expect(result.current.error).toMatchObject({ needsRefresh: true, retryAvailable: false });

    vi.mocked(api.createEnterpriseInvitation).mockRejectedValueOnce(
      new ApiError("migration", "http", 503, {
        detail: { code: "tenant_invitation_migration_required" },
      }),
    );
    await act(async () => {
      await result.current.create({
        email: "second@example.com",
        role: "member",
        expires_in_days: 7,
        reason: "迁移",
      });
    });
    expect(result.current.error).toMatchObject({ migrationRequired: true });
  });
});
