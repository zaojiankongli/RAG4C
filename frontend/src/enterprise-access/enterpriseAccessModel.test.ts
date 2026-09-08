// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import {
  projectAccessGrantPage,
  projectEnterpriseInvitation,
  projectOrganizationUnitPage,
} from "./enterpriseAccessModel";

describe("enterpriseAccessModel", () => {
  it("projects organization keyset facts without inventing a cursor", () => {
    const page = projectOrganizationUnitPage({
      items: [
        {
          id: "ou-1",
          parent_id: null,
          name: "集团总部",
          code: "HQ",
          status: "active",
          member_count: 18,
          child_count: 2,
        },
      ],
      count: 7,
    });

    expect(page.items[0]).toEqual({
      id: "ou-1",
      parent_id: null,
      name: "集团总部",
      code: "HQ",
      status: "active",
      member_count: 18,
      child_count: 2,
    });
    expect(page.count).toBe(7);
    expect(page.next_before_id).toBeNull();
  });

  it("removes invitation token_hash even when a server payload accidentally includes it", () => {
    const invitation = projectEnterpriseInvitation({
      id: "invite-1",
      email: "new@example.com",
      role: "editor",
      status: "pending",
      expires_at: "2026-09-01T08:00:00Z",
      invited_by: "account-admin",
      token_hash: "must-never-render",
    });

    expect(invitation).toEqual({
      id: "invite-1",
      email: "new@example.com",
      role: "editor",
      status: "pending",
      expires_at: "2026-09-01T08:00:00Z",
      invited_by: "account-admin",
    });
    expect("token_hash" in invitation).toBe(false);
  });

  it("keeps ACL revision and subject identity from the authoritative response", () => {
    const page = projectAccessGrantPage({
      items: [
        {
          id: "grant-1",
          dataset_id: "dataset-1",
          subject_type: "group",
          subject_id: "group-rd",
          subject_name: "研发协作组",
          role: "editor",
          status: "active",
          revision: 4,
        },
      ],
      count: 1,
      next_before_id: "grant-0",
    });

    expect(page.items[0].revision).toBe(4);
    expect(page.items[0].subject_name).toBe("研发协作组");
    expect(page.next_before_id).toBe("grant-0");
  });
});
