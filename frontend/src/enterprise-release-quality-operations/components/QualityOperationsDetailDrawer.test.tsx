// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  QualityAuthorityProjection,
  QualityOperationsAlert,
  RecertificationJob,
} from "../model/operationsModel";
import QualityOperationsDetailDrawer from "./QualityOperationsDetailDrawer";

const authority: QualityAuthorityProjection = {
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
  severity: "warning",
  horizon_band: "7_days",
  minutes_to_certification_expiry: 4320,
  minutes_to_waiver_expiry: null,
  certification_id: "certification-a",
  certification_valid_until: "2026-09-05T10:00:00.000Z",
  waiver_id: null,
  waiver_expires_at: null,
  active_alert_count: 1,
  recertification_job_status: "pending",
  last_observed_at: "2026-08-29T09:55:00.000Z",
  observation_digest: "a".repeat(64),
};

const alert: QualityOperationsAlert = {
  id: "alert-a",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  release_id: "release-a",
  channel_id: "channel-production",
  release_role: "active",
  alert_type: "certification_expiring",
  severity: "warning",
  status: "open",
  revision: 2,
  occurrence_count: 3,
  opened_at: "2026-08-29T08:00:00.000Z",
  last_observed_at: "2026-08-29T09:55:00.000Z",
  acknowledged_at: null,
  acknowledged_by: null,
  acknowledged_comment: "token=raw-alert-secret",
  resolved_at: null,
  suppressed_until: null,
};

const job: RecertificationJob = {
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
};

const observations = [
  {
    id: "observation-a",
    tenant_id: "tenant-a",
    dataset_id: "dataset-a",
    release_id: "release-a",
    channel_id: "channel-production",
    release_role: "active" as const,
    scan_run_id: "scan-run-a",
    observed_at: "2026-08-29T09:55:00.000Z",
    observed_by: "system-quality-scan",
    gate_state: "passed" as const,
    gate_reason: "certification_current",
    severity: "warning" as const,
    observation_digest: "c".repeat(64),
    event: "Observed",
    result_body: "result body must not be rendered",
  },
];

const auditFacts = [
  {
    id: "audit-a",
    occurred_at: "2026-08-29T09:56:00.000Z",
    event: "Warning opened",
    actor: "system-quality-alert",
    object_ref: "alert-a",
    revision: 2,
    digest: "d".repeat(64),
    server_confirmed: true,
  },
  {
    id: "audit-b",
    occurred_at: "2026-08-29T09:57:00.000Z",
    event: "Recertification queued",
    actor: "operator-a",
    object_ref: "job-a",
    revision: null,
    digest: null,
    server_confirmed: true,
  },
];

function renderDrawer(
  overrides: Partial<React.ComponentProps<typeof QualityOperationsDetailDrawer>> = {},
) {
  return render(
    <QualityOperationsDetailDrawer
      visible
      authority={authority}
      datasetName="客服知识库"
      workspaceName="Production Workspace"
      riskTier="high"
      channelRevision={7}
      alert={alert}
      job={job}
      observations={observations}
      auditFacts={auditFacts}
      timelineStatus="ready"
      onClose={vi.fn()}
      {...overrides}
    />,
  );
}

afterEach(() => cleanup());

describe("Stage21 QualityOperationsDetailDrawer", () => {
  it("renders one coordinated TDesign Drawer with the five operations tabs", () => {
    renderDrawer();

    expect(document.querySelectorAll(".t-drawer")).toHaveLength(1);
    expect(screen.getByRole("dialog", { name: /Production.*Release 42/ })).toBeTruthy();
    expect(screen.getAllByRole("tab")).toHaveLength(5);
    expect(screen.getByRole("tab", { name: "Overview" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Timeline" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Certification" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Alert" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Recertification" })).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: /确认|抑制|解决/ })).toBeNull();
  });

  it("keeps Overview bound to projected authority facts and redacts protected text", () => {
    renderDrawer();

    expect(screen.getByRole("heading", { name: "Release 42" })).toBeTruthy();
    expect(screen.getByText("客服知识库")).toBeTruthy();
    expect(screen.getByText("Production Workspace")).toBeTruthy();
    expect(screen.getByText("high")).toBeTruthy();
    const channelRevision = screen.getByText("Channel revision").parentElement;
    expect(channelRevision?.textContent).toContain("7");
    expect(screen.getByText("Certification current")).toBeTruthy();
    expect(screen.getByText("certification-a")).toBeTruthy();
    expect(screen.getByText(/aaaaaaaaaaaa/)).toBeTruthy();
    expect(screen.queryByText("result body must not be rendered")).toBeNull();
    expect(screen.queryByText("token=raw-alert-secret")).toBeNull();
    expect(screen.queryByText(/raw-alert-secret/)).toBeNull();
  });

  it("uses only immutable observations and audit facts in Timeline", () => {
    renderDrawer();

    fireEvent.click(screen.getByRole("tab", { name: "Timeline" }));

    expect(screen.getByText("Observed")).toBeTruthy();
    expect(screen.getByText("Warning opened")).toBeTruthy();
    expect(screen.getByText("Recertification queued")).toBeTruthy();
    expect(screen.getByRole("tabpanel").textContent).toContain("system-quality-scan");
    expect(screen.getByRole("tabpanel").textContent).toContain("scan-run-a");
    expect(screen.getByRole("tabpanel").textContent).toContain("cccccccccccc");
    expect(screen.queryByText("Alert resolved")).toBeNull();
    expect(screen.queryByText("result body must not be rendered")).toBeNull();
  });

  it("renders Certification and Recertification with TDesign Tags and Steps", () => {
    renderDrawer({ onOpenCertification: vi.fn(), onCancelRecertification: vi.fn() });

    fireEvent.click(screen.getByRole("tab", { name: "Certification" }));
    expect(screen.getByText("Certification authority")).toBeTruthy();
    expect(screen.getByText("有效至")).toBeTruthy();
    expect(screen.getByText("Certification current")).toBeTruthy();
    expect(screen.getByRole("button", { name: "打开 Release Certification 详情" })).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Recertification" }));
    expect(screen.getByText("再认证生命周期")).toBeTruthy();
    expect(screen.getByText("排队")).toBeTruthy();
    expect(screen.getByText("certification_warning")).toBeTruthy();
    expect(screen.getByText("Attempt 0 / 3")).toBeTruthy();
    expect(screen.getByRole("button", { name: "取消再认证" })).toBeTruthy();
  });

  it("delegates Alert lifecycle actions without implementing a nested Dialog", () => {
    const onAcknowledgeAlert = vi.fn();
    const onResolveAlert = vi.fn();
    const onSuppressAlert = vi.fn();
    renderDrawer({ onAcknowledgeAlert, onResolveAlert, onSuppressAlert });

    fireEvent.click(screen.getByRole("tab", { name: "Alert" }));
    fireEvent.click(screen.getByRole("button", { name: "确认告警" }));
    fireEvent.click(screen.getByRole("button", { name: "解决告警" }));
    fireEvent.click(screen.getByRole("button", { name: "抑制告警" }));

    expect(onAcknowledgeAlert).toHaveBeenCalledWith(alert);
    expect(onResolveAlert).toHaveBeenCalledWith(alert);
    expect(onSuppressAlert).toHaveBeenCalledWith(alert);
    expect(screen.queryByRole("dialog", { name: /告警/ })).toBeNull();
  });

  it("keeps lifecycle actions truly disabled in read-only mode", () => {
    const onAcknowledgeAlert = vi.fn();
    const onQueueRecertification = vi.fn();
    renderDrawer({ readOnly: true, onAcknowledgeAlert, onQueueRecertification });

    expect(screen.getByText("只读事实")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Alert" }));
    const acknowledgeText = screen.getByText("确认告警");
    const acknowledge = acknowledgeText.closest("button") as HTMLButtonElement | null;
    if (!acknowledge) throw new Error("确认告警 button is missing");
    expect(acknowledge.disabled).toBe(true);
    fireEvent.click(acknowledgeText);
    expect(onAcknowledgeAlert).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("tab", { name: "Recertification" }));
    const queueText = screen.getByText("排队再认证");
    const queue = queueText.closest("button") as HTMLButtonElement | null;
    if (!queue) throw new Error("排队再认证 button is missing");
    expect(queue.disabled).toBe(true);
    fireEvent.click(queueText);
    expect(onQueueRecertification).not.toHaveBeenCalled();
  });

  it("exposes explicit unavailable, empty and error states instead of guessing authority history", () => {
    const unavailable: QualityAuthorityProjection = {
      ...authority,
      state: "unavailable",
      unavailable_reason: "quality_authority_unavailable",
      gate_state: "unavailable",
      severity: "unavailable",
      horizon_band: "unavailable",
      certification_id: null,
      certification_valid_until: null,
      observation_digest: null,
    };
    const { rerender } = renderDrawer({ authority: unavailable, timelineStatus: "empty" });
    expect(screen.getByTestId("quality-operations-detail-unavailable").textContent).toContain(
      "Quality authority unavailable",
    );
    expect(screen.getByText("quality_authority_unavailable")).toBeTruthy();
    expect(screen.getByText("当前 authority 不可用")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Timeline" }));
    expect(screen.getByText("尚无运营时间线")).toBeTruthy();
    expect(screen.queryByText("Alert resolved")).toBeNull();

    rerender(
      <QualityOperationsDetailDrawer
        visible
        authority={authority}
        alert={alert}
        job={job}
        observations={[]}
        auditFacts={[]}
        timelineStatus="error"
        timelineError="timeline backend unavailable"
        error="Quality Operations unavailable"
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByTestId("quality-operations-detail-error").textContent).toContain(
      "Quality Operations unavailable",
    );
    fireEvent.click(screen.getByRole("tab", { name: "Timeline" }));
    expect(screen.getByTestId("quality-operations-detail-timeline-error").textContent).toContain(
      "timeline backend unavailable",
    );
  });

  it("moves tab focus with ArrowRight without using a released synthetic event", async () => {
    renderDrawer();
    const overview = screen.getByRole("tab", { name: "Overview" });
    const timeline = screen.getByRole("tab", { name: "Timeline" });
    overview.focus();
    fireEvent.keyDown(overview, { key: "ArrowRight" });
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(document.activeElement).toBe(timeline);
    expect(timeline.getAttribute("aria-selected")).toBe("true");
  });

  it("calls onClose and restores focus to the supplied focus entry", async () => {
    const focusEntry = document.createElement("button");
    focusEntry.setAttribute("aria-label", "open quality authority");
    document.body.appendChild(focusEntry);
    focusEntry.focus();
    const onClose = vi.fn();

    renderDrawer({ onClose, focusEntry });
    fireEvent.click(screen.getByRole("button", { name: "关闭 Quality Operations 详情" }));
    await Promise.resolve();

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(focusEntry);
    focusEntry.remove();
  });

  it("does not render when hidden", () => {
    renderDrawer({ visible: false });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("tab", { name: "Overview" })).toBeNull();
  });
});
