import { describe, expect, it } from "vitest";
import {
  projectEnterpriseAuditEventListResponse,
  projectEnterpriseMember,
  projectEnterpriseMemberListResponse,
} from "./enterpriseAdminModel";

describe("enterprise admin data projections", () => {
  it("projects legacy members to unknown status without inventing revision", () => {
    const member = projectEnterpriseMember({
      membership_id: 9,
      account_id: "account-1",
      name: "林澈",
      email: "lin@example.com",
      role: "admin",
      joined_at: "2026-08-20T08:00:00Z",
    });

    expect(member.status).toBe("unknown");
    expect(member.revision).toBeUndefined();
    expect(member.suspended_at).toBeUndefined();
    expect(member.updated_at).toBeUndefined();
  });

  it("keeps extensible member status and real revision timestamps", () => {
    const member = projectEnterpriseMember({
      membership_id: 7,
      account_id: "account-2",
      name: "周宁",
      email: "zhou@example.com",
      role: "editor",
      joined_at: "2026-08-19T08:00:00Z",
      status: "pending_security_review",
      revision: 4,
      suspended_at: null,
      updated_at: "2026-08-21T08:00:00Z",
    });

    expect(member.status).toBe("pending_security_review");
    expect(member.revision).toBe(4);
    expect(member.suspended_at).toBeNull();
    expect(member.updated_at).toBe("2026-08-21T08:00:00Z");
  });

  it("deduplicates audit events by sequence while preserving the next cursor", () => {
    const response = projectEnterpriseAuditEventListResponse({
      items: [
        { sequence: 4, action: "member.updated" },
        { sequence: 4, action: "member.updated" },
        { sequence: 3, action: "member.suspended" },
      ],
      next_before_sequence: 3,
    });

    expect(response.items.map((item) => item.sequence)).toEqual([4, 3]);
    expect(response.next_before_sequence).toBe(3);
  });

  it("projects member list responses without changing authoritative pagination fields", () => {
    const response = projectEnterpriseMemberListResponse({
      items: [
        {
          membership_id: 9,
          account_id: "account-1",
          name: "林澈",
          email: "lin@example.com",
          role: "admin",
          joined_at: "2026-08-20T08:00:00Z",
        },
      ],
      count: 1,
      next_before_id: null,
    });

    expect(response.items[0].status).toBe("unknown");
    expect(response.count).toBe(1);
    expect(response.next_before_id).toBeNull();
  });
});
