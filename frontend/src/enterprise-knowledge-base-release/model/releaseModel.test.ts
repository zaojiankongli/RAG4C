import { describe, expect, it } from "vitest";
import {
  projectReleaseChannelPage,
  projectReleaseDetail,
  projectReleaseMutationOutcome,
  projectReleaseAuditPage,
  projectReleaseHistoryPage,
  projectReleaseReadiness,
  releaseComparisonLabel,
  type ReleaseChannelPage,
} from "./releaseModel";

const channel = {
  id: "channel-prod",
  tenant_id: "tenant-a",
  code: "regulated-production",
  name: "受监管生产",
  status: "active",
  risk_tier: "high",
  promotion_order: 30,
  is_default_serving: true,
  revision: 4,
};

const manifest = {
  id: "release-42",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  release_number: 42,
  status: "candidate",
  profile_revision: 12,
  ownership_revision: 7,
  workspace_revision: 9,
  mutation_generation: 18,
  serving_generation: 3,
  schema_version: 1,
  manifest_digest: "a".repeat(64),
  readiness_state: "ready",
  readiness_fingerprint: "b".repeat(64),
  entry_count: 0,
  revision: 1,
  created_at: "2026-08-28T01:00:00Z",
  created_by: "account-1",
  reason: "准备灰度",
};

const summary = {
  channel_id: "channel-prod",
  configured: {
    profile_revision: 12,
    mutation_generation: 18,
    policy_digest: "policy-digest",
  },
  candidate: { release_id: "release-42", release_number: 42, status: "candidate", revision: 1 },
  effective: { release_id: "release-41", release_number: 41, status: "published", revision: 2 },
  serving: { release_id: "release-40", release_number: 40, status: "published", revision: 3 },
  serving_generation: 3,
  comparison_state: "drifted",
  readiness: { state: "ready", blocker_count: 0, blockers: [], fingerprint: "c".repeat(64) },
  revision: 4,
};

function page(value: Partial<ReleaseChannelPage> = {}) {
  return {
    items: [channel],
    count: 1,
    next_cursor: null,
    summaries: { "channel-prod": summary },
    ...value,
  };
}

describe("Stage19 Release model projectors", () => {
  it("projects custom channels and keeps zero-valued facts instead of treating them as missing", () => {
    const projected = projectReleaseChannelPage(page());
    expect(projected.items[0]).toEqual(
      expect.objectContaining({
        id: "channel-prod",
        code: "regulated-production",
        is_default_serving: true,
        promotion_order: 30,
        revision: 4,
      }),
    );
    expect(projected.summaries["channel-prod"]?.configured?.mutation_generation).toBe(18);
    expect(projected.summaries["channel-prod"]?.readiness.blocker_count).toBe(0);
  });

  it("rejects a malformed authority row rather than inventing a channel", () => {
    expect(
      projectReleaseChannelPage({ items: [{ ...channel, id: "", revision: 0 }], count: 1 }),
    ).toEqual({
      items: [],
      count: 1,
      next_cursor: null,
      summaries: {},
      invalid_item_count: 1,
    });
  });

  it("projects immutable manifest details and opaque entry cursors", () => {
    const detail = projectReleaseDetail({
      manifest,
      entries: {
        items: [
          {
            id: "entry-1",
            resource_type: "document_version",
            resource_id: "doc-version-1",
            resource_revision: 0,
            content_digest: "d".repeat(64),
            facts: { indexed_revision: 0, graph_revision: 2 },
          },
        ],
        count: 1,
        next_cursor: "opaque-entry-cursor",
      },
    });
    expect(detail?.manifest.entry_count).toBe(0);
    expect(detail?.entries.next_cursor).toBe("opaque-entry-cursor");
    expect(detail?.entries.items[0]?.resource_revision).toBe(0);
  });

  it("projects readiness as unavailable when the authority is absent, not as an empty ready set", () => {
    expect(
      projectReleaseReadiness({
        state: "unavailable",
        reason: "release capability is not installed",
      }),
    ).toEqual(
      expect.objectContaining({
        state: "unavailable",
        reason: "release capability is not installed",
        blockers: [],
        blocker_count: null,
      }),
    );
  });

  it("accepts only sanitized direct or approval mutation facts and never exposes raw tickets", () => {
    const direct = projectReleaseMutationOutcome({
      state: "applied",
      operation: "promote",
      resource_id: "release-42",
      revision: 5,
      message: "已发布",
      ticket: "opaque-ticket-must-not-leak",
      idempotency_key: "opaque-key-must-not-leak",
    });
    const approval = projectReleaseMutationOutcome({
      state: "approval_required",
      operation: "rollback",
      resource_id: "release-41",
      approval_request_id: "approval-9",
      message: "已提交审批",
      execution: { ticket: "opaque-ticket-must-not-leak" },
    });
    expect(direct).toEqual(
      expect.objectContaining({ state: "applied", resource_id: "release-42", revision: 5 }),
    );
    expect(approval).toEqual(
      expect.objectContaining({
        state: "approval_required",
        approval_request_id: "approval-9",
      }),
    );
    expect(JSON.stringify({ direct, approval })).not.toContain("opaque-ticket");
    expect(JSON.stringify({ direct, approval })).not.toContain("opaque-key");
  });

  it("labels drift as an overlay state rather than a lifecycle state", () => {
    expect(releaseComparisonLabel("drifted")).toBe("配置已漂移");
    expect(releaseComparisonLabel("aligned")).toBe("配置一致");
    expect(releaseComparisonLabel("unavailable")).toBe("权威事实不可用");
  });

  it("redacts credential-like display text and preserves malformed authority evidence", () => {
    const audit = projectReleaseAuditPage({
      items: [
        {
          id: "event-1",
          event: "promoted",
          actor: "owner-a",
          reason: "password=topsecret",
          request_id: "Idempotency-Key: raw-key",
        },
      ],
      count: 1,
      next_cursor: null,
    });
    expect(audit.items[0]?.reason).toBeNull();
    expect(audit.items[0]?.request_id).toBeNull();
    expect(
      projectReleaseMutationOutcome({
        state: "applied",
        operation: "promote",
        message: "ticket=opaque-ticket-value",
      }).message,
    ).toBeNull();
    for (const rawValue of [
      "raw-password-stage19-never-render",
      "stage19-idempotency-key-never-render",
    ]) {
      expect(
        projectReleaseMutationOutcome({
          state: "applied",
          operation: "promote",
          message: rawValue,
        }).message,
      ).toBeNull();
    }
    expect(
      projectReleaseReadiness({
        state: "blocked",
        blocker_count: 1,
        blockers: [{ code: "document_index_drift", severity: "blocked" }],
      }).blockers[0]?.label,
    ).toBe("document_index_drift");

    const malformed = projectReleaseHistoryPage({
      items: [{ id: "release-invalid" }],
      count: 1,
      next_cursor: null,
      summary: null,
    });
    expect(malformed.items).toEqual([]);
    expect(malformed.invalid_item_count).toBe(1);
    expect(malformed.count).toBe(1);
  });
});
