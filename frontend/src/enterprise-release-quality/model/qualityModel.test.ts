import { describe, expect, it } from "vitest";
import {
  projectQualityBaselinePage,
  projectQualityCertificationPage,
  projectQualityGate,
  projectQualityPolicyPage,
  projectQualityMutationOutcome,
} from "./qualityModel";

const policy = {
  id: "quality-policy-1",
  tenant_id: "tenant-a",
  name: "生产质量门禁",
  scope_type: "channel",
  scope_value: "channel-production",
  channel_id: "channel-production",
  status: "active",
  revision: 2,
  min_experiment_count: 1,
  min_judged_result_count: 0,
  min_judgment_coverage_bps: 10000,
  min_exact_agreement_bps: 10000,
  min_mean_score_milli: 2000,
  max_conflicting_results: 0,
  require_all_experiments_completed: true,
  require_no_degraded_results: true,
  max_certification_age_minutes: 1440,
  policy_digest: "a".repeat(64),
  created_at: "2026-08-28T01:00:00",
  created_by: "owner-a",
  updated_at: "2026-08-28T02:00:00",
  updated_by: "owner-a",
};

const certification = {
  id: "quality-certification-1",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  release_id: "release-42",
  baseline_id: "quality-baseline-1",
  policy_id: "quality-policy-1",
  policy_revision: 2,
  release_manifest_digest: "b".repeat(64),
  release_mutation_generation: 18,
  release_serving_generation: 3,
  status: "passed",
  experiment_count: 1,
  completed_experiment_count: 1,
  degraded_experiment_count: 0,
  query_count: 1,
  judged_result_count: 0,
  total_result_count: 0,
  judgment_count: 0,
  judgment_coverage_bps: null,
  multi_judged_results: 0,
  unanimous_results: 0,
  conflicting_results: 0,
  exact_agreement_bps: null,
  mean_score_milli: null,
  failed_rule_count: 0,
  evidence_digest: "c".repeat(64),
  certification_digest: "d".repeat(64),
  valid_until: "2026-08-29T02:00:00",
  created_at: "2026-08-28T02:00:00",
  created_by: "owner-a",
  reason: "发布验证",
};

describe("Stage20 Release Quality model projectors", () => {
  it("projects strict policy, baseline and certification authority without losing zero facts", () => {
    const policies = projectQualityPolicyPage({ items: [policy], next_cursor: "policy-cursor" });
    const baselines = projectQualityBaselinePage({
      items: [
        {
          id: "quality-baseline-1",
          tenant_id: "tenant-a",
          dataset_id: "dataset-a",
          name: "核心问答",
          baseline_revision: 1,
          parent_baseline_id: null,
          experiment_count: 1,
          query_count: 1,
          baseline_digest: "e".repeat(64),
          created_at: "2026-08-28T01:30:00",
          created_by: "owner-a",
          reason: "冻结证据",
          request_id: "request-a",
        },
      ],
      next_cursor: null,
    });
    const certifications = projectQualityCertificationPage({
      items: [certification],
      next_cursor: null,
    });
    expect(policies.items[0]?.min_judged_result_count).toBe(0);
    expect(policies.items[0]?.max_conflicting_results).toBe(0);
    expect(baselines.items[0]?.baseline_revision).toBe(1);
    expect(certifications.items[0]?.total_result_count).toBe(0);
    expect(certifications.items[0]?.judgment_coverage_bps).toBeNull();
  });

  it("fails malformed gate authority closed as unavailable instead of inventing an empty success", () => {
    expect(projectQualityGate({ state: "passed", release_id: "release-42" })).toEqual(
      expect.objectContaining({
        state: "unavailable",
        reason: "invalid_quality_authority",
        policy: null,
        certification: null,
        waiver: null,
      }),
    );
  });

  it("projects a passed gate and keeps only safe policy/certification facts", () => {
    const gate = projectQualityGate({
      tenant_id: "tenant-a",
      dataset_id: "dataset-a",
      release_id: "release-42",
      channel_id: "channel-production",
      release_manifest_digest: "b".repeat(64),
      channel_revision: 4,
      risk_tier: "high",
      is_default_serving: true,
      state: "passed",
      reason: "certification_passed",
      policy,
      certification,
      waiver: null,
      raw_ticket: "opaque-ticket-must-not-leak",
    });
    expect(gate.state).toBe("passed");
    expect(gate.certification?.id).toBe("quality-certification-1");
    expect(JSON.stringify(gate)).not.toContain("opaque-ticket");
  });

  it("rejects mutation authority containing credential-like output", () => {
    expect(
      projectQualityMutationOutcome({
        state: "applied",
        operation: "release_quality_certify",
        resource_id: "quality-certification-1",
        message: "token=topsecret",
      }),
    ).toEqual(
      expect.objectContaining({ state: "unavailable", operation: "release_quality_certify" }),
    );
  });
});
