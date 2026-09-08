// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { OperationsApiScope } from "../api/operationsApi";
import type {
  QualityOperationsAlert,
  QualityOperationsSummary,
  RecertificationJob,
} from "../model/operationsModel";
import type { ReleaseQualityOperationsHook } from "../hooks/useReleaseQualityOperations";
import type { QualityOperationsCenterProps } from "./QualityOperationsCenter";
import type { QualityOperationsMutationDialogsProps } from "./QualityOperationsMutationDialogs";

const state = vi.hoisted(() => ({
  useOperations: vi.fn(),
  releaseProps: [] as Array<Record<string, unknown>>,
  centerProps: [] as QualityOperationsCenterProps[],
  dialogProps: [] as QualityOperationsMutationDialogsProps[],
}));

vi.mock("../../enterprise-knowledge-base-release/components/KnowledgeBaseReleaseCenter", () => ({
  default: (props: Record<string, unknown>) => {
    state.releaseProps.push(props);
    const scope = props.scope as OperationsApiScope;
    return (
      <section aria-label="Release Center surface">
        <output data-testid="release-projection">
          {`${String(props.active)}:${scope.tenantId}:${scope.datasetId}:${scope.actorToken}:${props.readOnly ? "readonly" : "managed"}:${props.scopeVerified ? "verified" : "unverified"}`}
        </output>
      </section>
    );
  },
}));

vi.mock("./QualityOperationsCenter", () => ({
  default: (props: QualityOperationsCenterProps) => {
    state.centerProps.push(props);
    return (
      <section aria-label="Quality Operations surface">
        <output data-testid="quality-projection">
          {`${props.summary?.items.length ?? 0}:${props.alerts.length}:${props.jobs.length}:${props.readOnly ? "readonly" : "managed"}`}
        </output>
      </section>
    );
  },
}));

vi.mock("./QualityOperationsMutationDialogs", () => ({
  default: (props: QualityOperationsMutationDialogsProps) => {
    state.dialogProps.push(props);
    return props.visible ? <section aria-label="Quality Operations mutation dialog" /> : null;
  },
}));

vi.mock("../hooks/useReleaseQualityOperations", () => ({
  useReleaseQualityOperations: state.useOperations,
}));

import QualityOperationsWorkspace from "./QualityOperationsWorkspace";

const scope: OperationsApiScope = {
  tenantId: "tenant-a",
  datasetId: "dataset-a",
  actorToken: "actor-token",
};

const summary: QualityOperationsSummary = {
  state: "ready",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  generated_at: "2026-08-29T10:01:00.000000Z",
  last_completed_scan_at: "2026-08-29T10:00:00.000000Z",
  horizon_counts: {
    expired: 0,
    "24_hours": 0,
    "7_days": 1,
    "30_days": 0,
    healthy: 0,
    unavailable: 0,
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
      severity: "warning",
      horizon_band: "7_days",
      minutes_to_certification_expiry: 10080,
      minutes_to_waiver_expiry: null,
      certification_id: "certification-a",
      certification_valid_until: "2026-09-05T10:00:00.000000Z",
      waiver_id: null,
      waiver_expires_at: null,
      active_alert_count: 1,
      recertification_job_status: "pending",
      last_observed_at: "2026-08-29T10:00:00.000000Z",
      observation_digest: "a".repeat(64),
    },
  ],
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
  revision: 7,
  occurrence_count: 1,
  opened_at: "2026-08-29T09:00:00.000000Z",
  last_observed_at: "2026-08-29T10:00:00.000000Z",
  acknowledged_at: null,
  acknowledged_by: null,
  acknowledged_comment: null,
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
  created_at: "2026-08-29T09:00:00.000000Z",
  updated_at: "2026-08-29T10:00:00.000000Z",
  safe_error_code: null,
  safe_error: null,
};

function makeOperationsHook(
  overrides: Partial<ReleaseQualityOperationsHook> = {},
): ReleaseQualityOperationsHook {
  const acknowledgeAlert = vi.fn().mockResolvedValue({
    state: "applied",
    operation: "acknowledge_quality_alert",
    resource_id: "alert-a",
    message: "已确认",
    retryable: false,
  });
  const hook: ReleaseQualityOperationsHook = {
    active: true,
    load: {
      status: "ready",
      error: null,
      reload: vi.fn().mockResolvedValue(true),
    },
    summary: { status: "ready", value: summary, error: null },
    alerts: {
      status: "ready",
      items: [alert],
      nextCursor: null,
      invalidItemCount: 0,
      error: null,
    },
    jobs: {
      status: "ready",
      items: [job],
      nextCursor: null,
      invalidItemCount: 0,
      error: null,
    },
    timeline: {
      status: "idle",
      available: false,
      items: [],
      error: null,
      load: vi.fn().mockResolvedValue(false),
    },
    observations: {
      status: "idle",
      available: false,
      items: [],
      error: null,
      load: vi.fn().mockResolvedValue(false),
    },
    mutation: {
      status: "idle",
      outcome: null,
      error: null,
      acknowledgeAlert,
      resolveAlert: vi.fn().mockResolvedValue(null),
      suppressAlert: vi.fn().mockResolvedValue(null),
      queueRecertification: vi.fn().mockResolvedValue(null),
      cancelRecertification: vi.fn().mockResolvedValue(null),
      requestScan: vi.fn().mockResolvedValue(null),
      retry: vi.fn().mockResolvedValue(null),
    },
  };
  return { ...hook, ...overrides };
}

function latestCenterProps(): QualityOperationsCenterProps | undefined {
  return state.centerProps[state.centerProps.length - 1];
}

beforeEach(() => {
  state.useOperations.mockReset();
  state.useOperations.mockReturnValue(makeOperationsHook());
  state.releaseProps.length = 0;
  state.centerProps.length = 0;
  state.dialogProps.length = 0;
});

afterEach(cleanup);

describe("Stage21 Quality Operations workspace integration", () => {
  it("adds local TDesign tabs without creating another primary navigation surface", () => {
    render(
      <QualityOperationsWorkspace
        active
        scope={scope}
        datasetName="客服知识库"
        workspaceName="生产知识域"
      />,
    );

    expect(screen.getByRole("tablist", { name: "Releases workspace views" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Release Center" })).toBeTruthy();
    const qualityTab = screen.getByRole("tab", { name: "Quality Operations" });
    expect(qualityTab).toBeTruthy();
    expect(screen.getByRole("region", { name: "Release Center surface" })).toBeTruthy();
    fireEvent.click(qualityTab);
    expect(screen.getByRole("region", { name: "Quality Operations surface" })).toBeTruthy();
  });

  it("forwards active, verified scope and read-only state to both authorities", () => {
    render(<QualityOperationsWorkspace active scope={scope} readOnly scopeVerified={false} />);

    expect(screen.getByTestId("release-projection").textContent).toBe(
      "true:tenant-a:dataset-a:actor-token:readonly:unverified",
    );
    fireEvent.click(screen.getByRole("tab", { name: "Quality Operations" }));
    expect(state.useOperations).toHaveBeenCalledWith(
      scope,
      expect.objectContaining({ enabled: false, readOnly: true }),
    );
    expect(latestCenterProps()?.readOnly).toBe(true);
  });

  it("binds summary, alerts and jobs, and acknowledges only through a caller-owned key", async () => {
    const hook = makeOperationsHook();
    state.useOperations.mockReturnValue(hook);
    render(
      <QualityOperationsWorkspace
        active
        scope={scope}
        scopeVerified
        datasetName="客服知识库"
        workspaceName="生产知识域"
        onQueueRecertification={() => undefined}
      />,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Quality Operations" }));
    expect(screen.getByTestId("quality-projection").textContent).toBe("1:1:1:managed");
    const center = latestCenterProps();
    expect(center?.summary).toBe(summary);
    expect(center?.alerts).toEqual([alert]);
    expect(center?.jobs).toEqual([job]);
    expect(center?.onAcknowledgeAlert).toBeTypeOf("function");

    act(() => {
      center?.onAcknowledgeAlert?.(alert);
    });
    const dialog = state.dialogProps[state.dialogProps.length - 1];
    expect(dialog?.visible).toBe(true);
    expect(dialog?.mode).toBe("acknowledge");
    await act(async () => {
      await dialog?.onAcknowledge?.({
        expectedRevision: 7,
        reason: "operator acknowledged quality alert",
      });
    });

    expect(hook.mutation.acknowledgeAlert).toHaveBeenCalledWith(
      "alert-a",
      expect.objectContaining({
        expectedRevision: 7,
        reason: expect.any(String),
      }),
      { idempotencyKey: expect.stringMatching(/^rag4c-quality-operations-/) },
    );
  });

  it("surfaces unavailable authority explicitly and disables lifecycle handlers in read-only mode", () => {
    const hook = makeOperationsHook({
      load: {
        status: "unavailable",
        error: new Error("Quality Operations authority unavailable"),
        reload: vi.fn().mockResolvedValue(false),
      },
      summary: { status: "unavailable", value: null, error: new Error("summary unavailable") },
    });
    state.useOperations.mockReturnValue(hook);
    render(<QualityOperationsWorkspace active scope={scope} scopeVerified readOnly />);
    fireEvent.click(screen.getByRole("tab", { name: "Quality Operations" }));

    expect(latestCenterProps()?.error).toMatch(/Quality Operations authority unavailable/);
    expect(latestCenterProps()?.onAcknowledgeAlert).toBeUndefined();
    expect(latestCenterProps()?.onQueueRecertification).toBeUndefined();
  });

  it("does not mount an inactive workspace, enable authority, or break when activated later", () => {
    const { rerender } = render(<QualityOperationsWorkspace active={false} scope={scope} />);

    expect(screen.queryByRole("tablist", { name: "Releases workspace views" })).toBeNull();
    expect(state.useOperations).toHaveBeenCalledWith(
      scope,
      expect.objectContaining({ enabled: false }),
    );

    rerender(
      <QualityOperationsWorkspace
        active
        scope={scope}
        scopeVerified
        onQueueRecertification={() => undefined}
      />,
    );
    expect(screen.getByRole("tablist", { name: "Releases workspace views" })).toBeTruthy();
  });

  it("keeps lifecycle actions fail-closed until the Workspace scope is verified", () => {
    render(
      <QualityOperationsWorkspace
        active
        scope={scope}
        scopeVerified={false}
        onQueueRecertification={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Quality Operations" }));

    expect(latestCenterProps()?.readOnly).toBe(true);
    expect(latestCenterProps()?.onAcknowledgeAlert).toBeUndefined();
    expect(latestCenterProps()?.onQueueRecertification).toBeUndefined();
  });
});
