// @vitest-environment node

import { describe, expect, it } from "vitest";
import {
  projectAuditExportJob,
  projectAuditExportPage,
  projectLegalHold,
  projectLegalHoldPage,
  projectRetentionPolicy,
  projectRetentionPreview,
} from "./enterpriseComplianceModel";

describe("Stage 11 compliance model", () => {
  it("projects policy, preview and legal-hold revision evidence", () => {
    expect(
      projectRetentionPolicy({
        id: "policy-1",
        status: "active",
        audit_retention_days: 365,
        export_retention_days: 30,
        revision: 7,
        last_preview_at: "2026-08-26T08:00:00Z",
        last_executed_at: null,
      }),
    ).toMatchObject({ status: "active", audit_retention_days: 365, revision: 7 });

    expect(
      projectRetentionPreview({
        policy_revision: 7,
        cutoff_at: "2025-08-26T08:00:00Z",
        candidate_count: 120,
        protected_count: 15,
        deletable_count: 105,
        sequence_start: 10,
        sequence_end: 900,
        preview_fingerprint: "sha256:preview-fingerprint",
        generated_at: "2026-08-26T08:00:00Z",
      }),
    ).toMatchObject({ candidate_count: 120, protected_count: 15, deletable_count: 105 });

    expect(
      projectLegalHoldPage({
        items: [
          {
            id: "hold-1",
            name: "监管调查",
            reason: "保全审计证据",
            status: "active",
            revision: 2,
            sequence_start: 100,
            sequence_end: 500,
            created_at: "2026-08-26T08:00:00Z",
            created_by: "owner-1",
          },
        ],
        count: 1,
        next_before_id: null,
      }).items[0],
    ).toMatchObject({ id: "hold-1", status: "active", revision: 2 });
    expect(projectLegalHold({ id: "bad" })).toBeNull();
  });

  it("keeps export integrity evidence but drops storage paths and secrets", () => {
    const job = projectAuditExportJob({
      id: "export-1",
      format: "ndjson",
      status: "completed",
      filters: { action: "member.role.updated" },
      sha256: "a".repeat(64),
      byte_size: 4096,
      row_count: 88,
      revision: 3,
      requested_at: "2026-08-26T08:00:00Z",
      completed_at: "2026-08-26T08:01:00Z",
      expires_at: "2026-09-25T08:00:00Z",
      object_key: "tenant-1/private.ndjson",
      absolute_path: "D:/data/audit-exports/private.ndjson",
      bearer_token: "never-project",
    });

    expect(job).toMatchObject({ sha256: "a".repeat(64), byte_size: 4096, row_count: 88 });
    expect(job).not.toHaveProperty("object_key");
    expect(job).not.toHaveProperty("absolute_path");
    expect(job).not.toHaveProperty("bearer_token");

    expect(
      projectAuditExportPage({ items: [job], count: 1, next_before_id: null }).items,
    ).toHaveLength(1);
  });
});
