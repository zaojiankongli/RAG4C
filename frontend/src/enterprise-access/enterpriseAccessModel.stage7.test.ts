// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import * as model from "./enterpriseAccessModel";

const projectDatasetAccessSummary = (
  model as typeof model & {
    projectDatasetAccessSummary: (input: unknown) => unknown;
  }
).projectDatasetAccessSummary;

describe("stage 7 persistent Dataset ACL summary projection", () => {
  it("keeps the persisted ACL mode, revision, enable time, and actor", () => {
    expect(typeof projectDatasetAccessSummary).toBe("function");

    const result = projectDatasetAccessSummary({
      dataset_id: "dataset-1",
      owner_id: "account-owner",
      visibility: "private",
      enforcement_mode: "dataset_acl",
      actor_role: "admin",
      dataset_role: "manager",
      matched_grants: [],
      effective_permissions: ["knowledge.manage"],
      dataset_acl_supported: true,
      group_grants_supported: true,
      organization_inheritance_supported: true,
      warnings: [],
      acl_mode: "dataset_acl",
      acl_revision: 7,
      acl_enabled_at: "2026-08-26T08:00:00Z",
      acl_enabled_by: "account-owner",
    }) as unknown as Record<string, unknown>;

    expect(result).toMatchObject({
      acl_mode: "dataset_acl",
      acl_revision: 7,
      acl_enabled_at: "2026-08-26T08:00:00Z",
      acl_enabled_by: "account-owner",
    });
  });

  it("does not turn malformed persistent fields into a false tenant-role fallback", () => {
    const result = projectDatasetAccessSummary({
      dataset_id: "dataset-1",
      owner_id: null,
      visibility: "private",
      enforcement_mode: "dataset_acl",
      actor_role: "admin",
      effective_permissions: [],
      dataset_acl_supported: true,
      group_grants_supported: true,
      organization_inheritance_supported: true,
      warnings: ["持久 ACL 字段缺失"],
      acl_mode: "unexpected-mode",
      acl_revision: -1,
      acl_enabled_at: "not-a-date",
      acl_enabled_by: "",
    }) as unknown as Record<string, unknown>;

    expect(result.acl_mode).toBeNull();
    expect(result.acl_revision).toBeNull();
    expect(result.acl_enabled_at).toBeNull();
    expect(result.acl_enabled_by).toBeNull();
  });
});
