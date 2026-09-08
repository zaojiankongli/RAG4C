// @vitest-environment node

import { describe, expect, it } from "vitest";
import * as model from "./enterpriseAccessModel";

const projectInvitationMutation = (
  model as typeof model & {
    projectEnterpriseInvitationMutation: (input: unknown) => unknown;
  }
).projectEnterpriseInvitationMutation;

describe("Stage 8 enterprise invitation model", () => {
  it("projects lifecycle facts without retaining token material on list rows", () => {
    const invitation = model.projectEnterpriseInvitation({
      id: "invite-1",
      email: "member@example.com",
      role: "member",
      status: "pending",
      expires_at: "2026-09-02T08:00:00Z",
      invited_by: "account-admin",
      revision: 4,
      send_count: 2,
      last_sent_at: "2026-08-26T08:30:00Z",
      created_at: "2026-08-25T08:00:00Z",
      updated_at: "2026-08-26T08:30:00Z",
      token_hash: "never-render",
      invite_token: "never-render-raw",
    }) as unknown as Record<string, unknown>;

    expect(invitation).toMatchObject({
      id: "invite-1",
      revision: 4,
      send_count: 2,
      last_sent_at: "2026-08-26T08:30:00Z",
      created_at: "2026-08-25T08:00:00Z",
      updated_at: "2026-08-26T08:30:00Z",
    });
    expect(invitation).not.toHaveProperty("token_hash");
    expect(invitation).not.toHaveProperty("invite_token");
  });

  it("keeps the one-time token only in a mutation delivery result", () => {
    expect(typeof projectInvitationMutation).toBe("function");
    const result = projectInvitationMutation({
      invitation: {
        id: "invite-1",
        email: "member@example.com",
        role: "member",
        status: "pending",
        expires_at: "2026-09-02T08:00:00Z",
        invited_by: "account-admin",
        revision: 1,
        send_count: 1,
        last_sent_at: "2026-08-26T08:00:00Z",
      },
      delivery: {
        state: "manual_link_required",
        invite_token: "raw-one-time-token",
        expires_at: "2026-09-02T08:00:00Z",
      },
    }) as unknown as {
      invitation: Record<string, unknown>;
      delivery: Record<string, unknown> | null;
    };

    expect(result.delivery).toEqual({
      state: "manual_link_required",
      invite_token: "raw-one-time-token",
      expires_at: "2026-09-02T08:00:00Z",
    });
    expect(result.invitation).not.toHaveProperty("invite_token");
  });
});
