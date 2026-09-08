// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  QualityBaseline,
  QualityCertification,
  QualityGate,
  QualityPolicy,
} from "../model/qualityModel";
import QualityGateStrip from "./QualityGateStrip";
import ReleaseQualityPanel from "./ReleaseQualityPanel";

afterEach(cleanup);

const policy: QualityPolicy = {
  id: "quality-policy-1",
  tenant_id: "tenant-a",
  name: "生产质量门禁",
  scope_type: "channel",
  scope_value: "channel-production",
  channel_id: "channel-production",
  status: "active",
  revision: 2,
  min_experiment_count: 1,
  min_judged_result_count: 2,
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

const baseline: QualityBaseline = {
  id: "quality-baseline-1",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  name: "客服核心问答",
  baseline_revision: 1,
  parent_baseline_id: null,
  experiment_count: 1,
  query_count: 1,
  baseline_digest: "b".repeat(64),
  created_at: "2026-08-28T01:30:00",
  created_by: "owner-a",
  reason: "冻结证据",
  request_id: "request-1",
  items: [],
};

const certification: QualityCertification = {
  id: "quality-certification-1",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  release_id: "release-42",
  baseline_id: baseline.id,
  policy_id: policy.id,
  policy_revision: 2,
  release_manifest_digest: "c".repeat(64),
  release_mutation_generation: 18,
  release_serving_generation: 3,
  status: "passed",
  experiment_count: 1,
  completed_experiment_count: 1,
  degraded_experiment_count: 0,
  query_count: 1,
  judged_result_count: 2,
  total_result_count: 2,
  judgment_count: 3,
  judgment_coverage_bps: 10000,
  multi_judged_results: 1,
  unanimous_results: 1,
  conflicting_results: 0,
  exact_agreement_bps: 10000,
  mean_score_milli: 2000,
  failed_rule_count: 0,
  evidence_digest: "d".repeat(64),
  certification_digest: "e".repeat(64),
  valid_until: "2026-08-29T02:00:00",
  created_at: "2026-08-28T02:00:00",
  created_by: "owner-a",
  reason: "发布验证",
};

const gate: QualityGate = {
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  release_id: "release-42",
  channel_id: "channel-production",
  release_manifest_digest: "c".repeat(64),
  channel_revision: 4,
  risk_tier: "high",
  is_default_serving: true,
  state: "passed",
  reason: "certification_passed",
  policy,
  certification,
  waiver: null,
};

describe("Stage20 Release Quality UI", () => {
  it("renders a dense enterprise gate strip without exposing opaque facts", () => {
    render(<QualityGateStrip status="ready" gate={gate} error={null} />);
    const strip = screen.getByRole("region", { name: "Release Quality Gate" });
    expect(within(strip).getByText("质量门禁已通过")).toBeTruthy();
    expect(within(strip).getByText("生产质量门禁 · R2")).toBeTruthy();
    expect(strip.textContent).not.toContain("d".repeat(64));
  });

  it("shows policy thresholds versus observed certification facts and opens controlled certify dialog", async () => {
    const onCertify = vi.fn();
    const onLoadCertifications = vi.fn();
    render(
      <ReleaseQualityPanel
        gateStatus="ready"
        gate={gate}
        policies={[policy]}
        baselines={[baseline]}
        certifications={[certification]}
        certificationStatus="idle"
        readOnly={false}
        saving={false}
        error={null}
        onLoadCertifications={onLoadCertifications}
        onCertify={onCertify}
      />,
    );
    expect(screen.getByRole("heading", { name: "Release 质量认证" })).toBeTruthy();
    expect(screen.getAllByText("Judgment coverage").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("100.00%").length).toBeGreaterThanOrEqual(2);
    fireEvent.click(screen.getByRole("button", { name: "重新认证" }));
    await screen.findByText("创建 Release 质量认证");
    const dialog = document.body;
    fireEvent.change(within(dialog).getByRole("textbox", { name: "认证原因" }), {
      target: { value: "评测已完成" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "提交认证" }));
    expect(onCertify).toHaveBeenCalledWith(
      expect.objectContaining({
        baselineId: baseline.id,
        policyId: policy.id,
        expectedPolicyRevision: 2,
        expectedChannelRevision: 4,
        reason: "评测已完成",
      }),
    );
  });

  it("keeps unavailable authority explicit and disables mutations in read-only mode", () => {
    render(
      <ReleaseQualityPanel
        gateStatus="ready"
        gate={{
          ...gate,
          state: "unavailable",
          reason: "quality_policy_required",
          policy: null,
          certification: null,
        }}
        policies={[]}
        baselines={[]}
        certifications={[]}
        certificationStatus="idle"
        readOnly
        saving={false}
        error={null}
        onLoadCertifications={vi.fn()}
        onCertify={vi.fn()}
      />,
    );
    expect(screen.getByText("质量事实不可用")).toBeTruthy();
    expect(screen.getByRole("button", { name: "创建认证" })).toHaveProperty("disabled", true);
  });

  it("requests an approval-backed waiver from a blocked certified scope", async () => {
    const onRequestWaiver = vi.fn();
    render(
      <ReleaseQualityPanel
        gateStatus="ready"
        gate={{ ...gate, state: "blocked", reason: "certification_failed", certification }}
        policies={[policy]}
        baselines={[baseline]}
        certifications={[certification]}
        certificationStatus="ready"
        readOnly={false}
        saving={false}
        error={null}
        onLoadCertifications={vi.fn()}
        onCertify={vi.fn()}
        onRequestWaiver={onRequestWaiver}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "申请豁免" }));
    await screen.findByText("申请 Release 质量豁免");
    const dialog = document.body;
    fireEvent.change(within(dialog).getByRole("textbox", { name: "Approval Policy ID" }), {
      target: { value: "approval-policy-1" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "豁免有效期" }), {
      target: { value: "2026-08-29T08:00:00Z" },
    });
    fireEvent.change(within(dialog).getByRole("textbox", { name: "豁免原因" }), {
      target: { value: "等待人工复核" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "提交豁免审批" }));
    expect(onRequestWaiver).toHaveBeenCalledWith(
      expect.objectContaining({
        channelId: "channel-production",
        policyId: policy.id,
        expectedPolicyRevision: 2,
        expectedChannelRevision: 4,
        approvalPolicyId: "approval-policy-1",
        requestedExpiresAt: "2026-08-29T08:00:00Z",
        reason: "等待人工复核",
      }),
    );
  });
});
