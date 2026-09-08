// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  QualityOperationsAlert,
  QualityOperationsSummary,
  RecertificationJob,
} from "../model/operationsModel";
import QualityOperationsCenter from "./QualityOperationsCenter";

function media(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn().mockImplementation(() => ({
      matches,
      media: "(max-width: 768px)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

const summary: QualityOperationsSummary = {
  state: "ready",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  generated_at: "2026-08-29T10:00:00.000Z",
  last_completed_scan_at: "2026-08-29T09:55:00.000Z",
  horizon_counts: {
    expired: 1,
    "24_hours": 2,
    "7_days": 3,
    "30_days": 4,
    healthy: 7,
    unavailable: 1,
  },
  items: [
    {
      state: "ready",
      unavailable_reason: null,
      tenant_id: "tenant-a",
      dataset_id: "dataset-a",
      release_id: "release-a",
      channel_id: "channel-production",
      channel_name: "Production",
      release_role: "active",
      release_number: 42,
      gate_state: "passed",
      gate_reason: "certification_current",
      severity: "critical",
      horizon_band: "24_hours",
      minutes_to_certification_expiry: 720,
      minutes_to_waiver_expiry: null,
      certification_id: "certification-a",
      certification_valid_until: "2026-08-29T22:00:00.000Z",
      waiver_id: null,
      waiver_expires_at: null,
      active_alert_count: 1,
      recertification_job_status: "pending",
      last_observed_at: "2026-08-29T09:55:00.000Z",
      observation_digest: "a".repeat(64),
    },
    {
      state: "unavailable",
      unavailable_reason: "authority scope mismatch",
      tenant_id: "tenant-a",
      dataset_id: "dataset-a",
      release_id: "release-b",
      channel_id: "channel-testing",
      channel_name: "Testing",
      release_role: "pinned",
      release_number: null,
      gate_state: "unavailable",
      gate_reason: "quality_authority_unavailable",
      severity: "unavailable",
      horizon_band: "unavailable",
      minutes_to_certification_expiry: null,
      minutes_to_waiver_expiry: null,
      certification_id: null,
      certification_valid_until: null,
      waiver_id: null,
      waiver_expires_at: null,
      active_alert_count: 0,
      recertification_job_status: null,
      last_observed_at: null,
      observation_digest: null,
    },
  ],
};

const alerts: QualityOperationsAlert[] = [
  {
    id: "alert-a",
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    release_id: "release-a",
    channel_id: "channel-production",
    release_role: "active",
    alert_type: "certification_expiring",
    severity: "critical",
    status: "open",
    revision: 2,
    occurrence_count: 3,
    opened_at: "2026-08-29T08:00:00.000Z",
    last_observed_at: "2026-08-29T09:55:00.000Z",
    acknowledged_at: null,
    acknowledged_by: null,
    acknowledged_comment: null,
    resolved_at: null,
    suppressed_until: null,
  },
];

const jobs: RecertificationJob[] = [
  {
    id: "job-a",
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    release_id: "release-a",
    channel_id: "channel-production",
    release_role: "active",
    trigger: "certification_warning",
    status: "pending",
    cycle_key: "b".repeat(64),
    attempt_count: 0,
    max_attempts: 3,
    next_attempt_at: null,
    created_at: "2026-08-29T09:56:00.000Z",
    updated_at: "2026-08-29T09:56:00.000Z",
    safe_error_code: null,
    safe_error: null,
  },
];

beforeEach(() => media(false));
afterEach(() => cleanup());

describe("Stage21 Quality Operations Center", () => {
  it("renders the SLO Horizon Rail as the signature filter without a metric card wall", () => {
    const onHorizonChange = vi.fn();
    render(
      <QualityOperationsCenter
        datasetName="客服知识库"
        workspaceName="Production Workspace"
        summary={summary}
        alerts={alerts}
        jobs={jobs}
        selectedHorizon="24_hours"
        onHorizonChange={onHorizonChange}
      />,
    );
    expect(screen.getByRole("navigation", { name: "SLO Horizon Rail" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /24 HOURS.*2/i }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: /7 DAYS.*3/i }));
    expect(onHorizonChange).toHaveBeenCalledWith("7_days");
    expect(screen.queryByText(/hero/i)).toBeNull();
    expect(document.querySelector(".metric-card-wall")).toBeNull();
  });

  it("uses one desktop PrimaryTable surface and keeps unavailable authority fail-closed", () => {
    render(
      <QualityOperationsCenter
        datasetName="客服知识库"
        workspaceName="Production Workspace"
        summary={summary}
        alerts={alerts}
        jobs={jobs}
      />,
    );
    expect(screen.getByTestId("quality-operations-desktop-table")).toBeTruthy();
    expect(screen.queryByTestId("quality-operations-mobile-cards")).toBeNull();
    expect(screen.getByText("Production")).toBeTruthy();
    expect(screen.getByText("Authority unavailable")).toBeTruthy();
    expect(screen.queryByText("Testing healthy")).toBeNull();
  });

  it("switches to mobile priority cards without duplicating the desktop table", () => {
    media(true);
    render(
      <QualityOperationsCenter
        datasetName="客服知识库"
        workspaceName="Production Workspace"
        summary={summary}
        alerts={alerts}
        jobs={jobs}
      />,
    );
    expect(screen.getByTestId("quality-operations-mobile-cards")).toBeTruthy();
    expect(screen.queryByTestId("quality-operations-desktop-table")).toBeNull();
    expect(screen.getByRole("button", { name: /查看 Production 详情/ })).toBeTruthy();
  });

  it("keeps Alert Inbox and Job status durable and hides mutations in read-only mode", () => {
    render(
      <QualityOperationsCenter
        datasetName="客服知识库"
        workspaceName="Production Workspace"
        summary={summary}
        alerts={alerts}
        jobs={jobs}
        readOnly
      />,
    );
    expect(screen.getByRole("heading", { name: "Alert Inbox" })).toBeTruthy();
    expect(screen.getByText("certification_expiring")).toBeTruthy();
    expect(screen.getAllByText("pending").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "确认告警" })).toBeNull();
    expect(screen.queryByRole("button", { name: "排队再认证" })).toBeNull();
    expect(screen.getByText("只读模式")).toBeTruthy();
  });

  it("renders explicit loading, error and true empty states separately", () => {
    const { rerender } = render(
      <QualityOperationsCenter
        datasetName="客服知识库"
        workspaceName="Production Workspace"
        summary={null}
        alerts={[]}
        jobs={[]}
        loading
      />,
    );
    expect(screen.getByText("正在读取质量运营权威…")).toBeTruthy();
    rerender(
      <QualityOperationsCenter
        datasetName="客服知识库"
        workspaceName="Production Workspace"
        summary={null}
        alerts={[]}
        jobs={[]}
        error="Quality Operations unavailable"
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("Quality Operations unavailable");
    rerender(
      <QualityOperationsCenter
        datasetName="客服知识库"
        workspaceName="Production Workspace"
        summary={{
          ...summary,
          items: [],
          horizon_counts: { ...summary.horizon_counts, unavailable: 0 },
        }}
        alerts={[]}
        jobs={[]}
      />,
    );
    expect(screen.getByText("当前筛选范围没有 Release quality authority")).toBeTruthy();
  });
});
