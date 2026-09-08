// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type {
  OperationsApiScope,
  OperationsMutationOutcome,
  OperationsPage,
  OperationsRequestOptions,
} from "../api/operationsApi";
import type {
  QualityAuthorityProjection,
  QualityOperationsAlert,
  QualityOperationsSummary,
  RecertificationJob,
} from "../model/operationsModel";
import {
  useReleaseQualityOperations,
  type ReleaseQualityOperationsApi,
} from "./useReleaseQualityOperations";

const scope: OperationsApiScope = {
  tenantId: "tenant-a",
  datasetId: "dataset-a",
  actorToken: "actor-token",
};

const applied: OperationsMutationOutcome = {
  state: "applied",
  operation: "quality_operations",
  resource_id: "resource-a",
  message: "已提交",
  retryable: false,
};

const unavailable: OperationsMutationOutcome = {
  state: "unavailable",
  operation: "quality_operations",
  resource_id: null,
  message: null,
  retryable: false,
};

function authorityFor(nextScope: OperationsApiScope): QualityAuthorityProjection {
  return {
    state: "ready",
    unavailable_reason: null,
    tenant_id: nextScope.tenantId,
    dataset_id: nextScope.datasetId,
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
  };
}

function summaryFor(
  nextScope: OperationsApiScope = scope,
  overrides: Partial<QualityOperationsSummary> = {},
): QualityOperationsSummary {
  return {
    state: "ready",
    tenant_id: nextScope.tenantId,
    dataset_id: nextScope.datasetId,
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
    items: [authorityFor(nextScope)],
    ...overrides,
  };
}

const alert: QualityOperationsAlert = {
  id: "alert-a",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  release_id: "release-a",
  channel_id: "channel-production",
  release_role: "active",
  alert_type: "certification_expiring",
  severity: "critical",
  status: "open",
  revision: 1,
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

function emptyPage<T>(): OperationsPage<T> {
  return { items: [], next_cursor: null, invalid_item_count: 0 };
}

function page<T>(items: T[]): OperationsPage<T> {
  return { items, next_cursor: null, invalid_item_count: 0 };
}

function makeApi(
  overrides: Partial<ReleaseQualityOperationsApi> = {},
): ReleaseQualityOperationsApi {
  return {
    fetchSummary: vi.fn().mockResolvedValue(summaryFor()),
    fetchAlerts: vi.fn().mockResolvedValue(page([alert])),
    fetchJobs: vi.fn().mockResolvedValue(page([job])),
    acknowledgeAlert: vi.fn().mockResolvedValue(applied),
    resolveAlert: vi.fn().mockResolvedValue(applied),
    suppressAlert: vi.fn().mockResolvedValue(applied),
    queueRecertification: vi.fn().mockResolvedValue(applied),
    cancelRecertification: vi.fn().mockResolvedValue(applied),
    requestScan: vi.fn().mockResolvedValue(applied),
    ...overrides,
  };
}

function scanInput() {
  return { reason: "operator requested a bounded scan" };
}

describe("useReleaseQualityOperations", () => {
  it("stays inactive without reads or mutations until enabled", async () => {
    const api = makeApi();
    const { result, rerender } = renderHook(
      ({ enabled }) =>
        useReleaseQualityOperations(scope, {
          enabled,
          api,
        }),
      { initialProps: { enabled: false } },
    );

    expect(result.current.active).toBe(false);
    expect(result.current.load.status).toBe("idle");
    expect(result.current.timeline.status).toBe("idle");
    expect(result.current.observations.status).toBe("idle");
    expect(api.fetchSummary).not.toHaveBeenCalled();
    expect(api.fetchAlerts).not.toHaveBeenCalled();
    expect(api.fetchJobs).not.toHaveBeenCalled();

    await act(async () => {
      await expect(
        result.current.mutation.requestScan(scanInput(), { idempotencyKey: "inactive-key" }),
      ).resolves.toBeNull();
    });
    expect(api.requestScan).not.toHaveBeenCalled();

    rerender({ enabled: true });
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    expect(api.fetchSummary).toHaveBeenCalledTimes(1);
    expect(api.fetchAlerts).toHaveBeenCalledTimes(1);
    expect(api.fetchJobs).toHaveBeenCalledTimes(1);
  });

  it("starts summary, Alert inbox and Job queue with one shared AbortSignal", async () => {
    let resolveSummary: ((value: QualityOperationsSummary) => void) | undefined;
    let resolveAlerts: ((value: OperationsPage<QualityOperationsAlert>) => void) | undefined;
    let resolveJobs: ((value: OperationsPage<RecertificationJob>) => void) | undefined;
    const summaryPromise = new Promise<QualityOperationsSummary>((resolve) => {
      resolveSummary = resolve;
    });
    const alertsPromise = new Promise<OperationsPage<QualityOperationsAlert>>((resolve) => {
      resolveAlerts = resolve;
    });
    const jobsPromise = new Promise<OperationsPage<RecertificationJob>>((resolve) => {
      resolveJobs = resolve;
    });
    const api = makeApi({
      fetchSummary: vi.fn().mockReturnValue(summaryPromise),
      fetchAlerts: vi.fn().mockReturnValue(alertsPromise),
      fetchJobs: vi.fn().mockReturnValue(jobsPromise),
    });

    const { result } = renderHook(() =>
      useReleaseQualityOperations(scope, {
        enabled: true,
        api,
      }),
    );

    await waitFor(() => {
      expect(api.fetchSummary).toHaveBeenCalledTimes(1);
      expect(api.fetchAlerts).toHaveBeenCalledTimes(1);
      expect(api.fetchJobs).toHaveBeenCalledTimes(1);
    });
    expect(result.current.load.status).toBe("loading");
    const summarySignal = vi.mocked(api.fetchSummary).mock.calls[0]?.[1]?.signal;
    const alertsSignal = vi.mocked(api.fetchAlerts).mock.calls[0]?.[2]?.signal;
    const jobsSignal = vi.mocked(api.fetchJobs).mock.calls[0]?.[2]?.signal;
    expect(summarySignal).toBeInstanceOf(AbortSignal);
    expect(alertsSignal).toBe(summarySignal);
    expect(jobsSignal).toBe(summarySignal);

    await act(async () => {
      resolveSummary?.(summaryFor());
      resolveAlerts?.(page([alert]));
      resolveJobs?.(page([job]));
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    expect(result.current.summary.status).toBe("ready");
    expect(result.current.summary.value?.items).toHaveLength(1);
    expect(result.current.alerts.status).toBe("ready");
    expect(result.current.jobs.status).toBe("ready");
  });

  it("keeps partial error, empty and unavailable resource states distinct", async () => {
    const unavailableSummary = summaryFor(scope, {
      state: "unavailable",
      generated_at: null,
      last_completed_scan_at: null,
      horizon_counts: {
        expired: 0,
        "24_hours": 0,
        "7_days": 0,
        "30_days": 0,
        healthy: 0,
        unavailable: 1,
      },
      items: [],
    });
    const api = makeApi({
      fetchSummary: vi.fn().mockResolvedValue(unavailableSummary),
      fetchAlerts: vi.fn().mockRejectedValue(new Error("alert transport unavailable")),
      fetchJobs: vi.fn().mockResolvedValue(emptyPage()),
    });

    const { result } = renderHook(() =>
      useReleaseQualityOperations(scope, {
        enabled: true,
        api,
      }),
    );

    await waitFor(() => expect(result.current.load.status).toBe("partial"));
    expect(result.current.summary.status).toBe("unavailable");
    expect(result.current.summary.value?.state).toBe("unavailable");
    expect(result.current.alerts.status).toBe("error");
    expect(result.current.alerts.error?.message).toMatch(/alert transport unavailable/);
    expect(result.current.jobs.status).toBe("empty");

    const emptyApi = makeApi({
      fetchSummary: vi.fn().mockResolvedValue(summaryFor(scope, { items: [] })),
      fetchAlerts: vi.fn().mockResolvedValue(emptyPage()),
      fetchJobs: vi.fn().mockResolvedValue(emptyPage()),
    });
    const emptyHook = renderHook(() =>
      useReleaseQualityOperations(scope, {
        enabled: true,
        api: emptyApi,
      }),
    );
    await waitFor(() => expect(emptyHook.result.current.load.status).toBe("empty"));
    expect(emptyHook.result.current.summary.status).toBe("empty");
    expect(emptyHook.result.current.alerts.status).toBe("empty");
    expect(emptyHook.result.current.jobs.status).toBe("empty");
  });

  it("exposes Timeline and Observations as explicit lazy unavailable placeholders", async () => {
    const api = makeApi();
    const { result } = renderHook(() =>
      useReleaseQualityOperations(scope, {
        enabled: true,
        api,
      }),
    );

    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    expect(result.current.timeline.status).toBe("idle");
    expect(result.current.timeline.available).toBe(false);
    expect(result.current.observations.status).toBe("idle");
    expect(result.current.observations.available).toBe(false);

    await act(async () => {
      await result.current.timeline.load();
      await result.current.observations.load();
    });
    expect(result.current.timeline.status).toBe("unavailable");
    expect(result.current.timeline.items).toEqual([]);
    expect(result.current.observations.status).toBe("unavailable");
    expect(result.current.observations.items).toEqual([]);
    expect(api.fetchSummary).toHaveBeenCalledTimes(1);
    expect(api.fetchAlerts).toHaveBeenCalledTimes(1);
    expect(api.fetchJobs).toHaveBeenCalledTimes(1);
  });

  it("aborts and ignores a stale response after the Tenant/Dataset context changes", async () => {
    let resolveOldSummary: ((value: QualityOperationsSummary) => void) | undefined;
    const oldSummary = new Promise<QualityOperationsSummary>((resolve) => {
      resolveOldSummary = resolve;
    });
    const nextScope: OperationsApiScope = {
      tenantId: "tenant-b",
      datasetId: "dataset-b",
      actorToken: "actor-token-b",
    };
    const api = makeApi({
      fetchSummary: vi.fn((next: OperationsApiScope, _options?: OperationsRequestOptions) =>
        next.tenantId === "tenant-a" ? oldSummary : Promise.resolve(summaryFor(next)),
      ),
      fetchAlerts: vi.fn(() => Promise.resolve(emptyPage<QualityOperationsAlert>())),
      fetchJobs: vi.fn(() => Promise.resolve(emptyPage<RecertificationJob>())),
    });

    const { result, rerender } = renderHook(
      ({ currentScope }) =>
        useReleaseQualityOperations(currentScope, {
          enabled: true,
          api,
        }),
      { initialProps: { currentScope: scope } },
    );

    await waitFor(() => expect(api.fetchSummary).toHaveBeenCalledTimes(1));
    const oldSignal = vi.mocked(api.fetchSummary).mock.calls[0]?.[1]?.signal;
    rerender({ currentScope: nextScope });
    await waitFor(() => expect(result.current.summary.value?.tenant_id).toBe("tenant-b"));
    expect(oldSignal?.aborted).toBe(true);

    await act(async () => {
      resolveOldSummary?.(summaryFor(scope));
      await Promise.resolve();
    });
    expect(result.current.summary.value?.tenant_id).toBe("tenant-b");
    expect(result.current.summary.value?.dataset_id).toBe("dataset-b");
  });

  it("serializes mutations while preserving each queued operation", async () => {
    let resolveFirst: ((value: OperationsMutationOutcome) => void) | undefined;
    let resolveSecond: ((value: OperationsMutationOutcome) => void) | undefined;
    const first = new Promise<OperationsMutationOutcome>((resolve) => {
      resolveFirst = resolve;
    });
    const second = new Promise<OperationsMutationOutcome>((resolve) => {
      resolveSecond = resolve;
    });
    const requestScan = vi.fn().mockReturnValueOnce(first).mockReturnValueOnce(second);
    const api = makeApi({ requestScan });
    const { result } = renderHook(() =>
      useReleaseQualityOperations(scope, {
        enabled: true,
        api,
      }),
    );
    await waitFor(() => expect(result.current.load.status).toBe("ready"));

    let firstResult: Promise<OperationsMutationOutcome | null>;
    let secondResult: Promise<OperationsMutationOutcome | null>;
    await act(async () => {
      firstResult = result.current.mutation.requestScan(scanInput(), {
        idempotencyKey: "scan-key-1",
      });
      secondResult = result.current.mutation.requestScan(
        { reason: "operator requested the next bounded scan" },
        { idempotencyKey: "scan-key-2" },
      );
      await Promise.resolve();
    });
    expect(requestScan).toHaveBeenCalledTimes(1);
    expect(result.current.mutation.status).toBe("saving");

    await act(async () => {
      resolveFirst?.(applied);
      await firstResult!;
    });
    await waitFor(() => expect(requestScan).toHaveBeenCalledTimes(2));
    expect(requestScan.mock.calls[1]?.[2]?.idempotencyKey).toBe("scan-key-2");

    await act(async () => {
      resolveSecond?.(applied);
      await secondResult!;
    });
    expect(result.current.mutation.status).toBe("success");
    expect(result.current.mutation.outcome).toEqual(applied);
  });

  it("retries with the caller-owned Idempotency-Key instead of generating a new key", async () => {
    const requestScan = vi
      .fn()
      .mockRejectedValueOnce(new Error("temporary network failure"))
      .mockResolvedValueOnce(applied);
    const api = makeApi({ requestScan });
    const { result } = renderHook(() =>
      useReleaseQualityOperations(scope, {
        enabled: true,
        api,
      }),
    );
    await waitFor(() => expect(result.current.load.status).toBe("ready"));

    await act(async () => {
      await result.current.mutation.requestScan(scanInput(), {
        idempotencyKey: "caller-owned-same-key",
      });
    });
    expect(result.current.mutation.status).toBe("error");

    await act(async () => {
      await result.current.mutation.retry();
    });
    expect(requestScan).toHaveBeenCalledTimes(2);
    expect(requestScan.mock.calls[0]?.[2]?.idempotencyKey).toBe("caller-owned-same-key");
    expect(requestScan.mock.calls[1]?.[2]?.idempotencyKey).toBe("caller-owned-same-key");
    expect(result.current.mutation.status).toBe("success");
    expect(result.current.mutation.outcome).toEqual(applied);
  });

  it("blocks mutations in read-only mode and treats unavailable outcomes as errors", async () => {
    const readOnlyApi = makeApi();
    const readOnlyHook = renderHook(() =>
      useReleaseQualityOperations(scope, {
        enabled: true,
        readOnly: true,
        api: readOnlyApi,
      }),
    );
    await waitFor(() => expect(readOnlyHook.result.current.load.status).toBe("ready"));

    await act(async () => {
      await readOnlyHook.result.current.mutation.requestScan(scanInput(), {
        idempotencyKey: "read-only-key",
      });
    });
    expect(readOnlyApi.requestScan).not.toHaveBeenCalled();
    expect(readOnlyHook.result.current.mutation.status).toBe("error");
    expect(readOnlyHook.result.current.mutation.error?.message).toMatch(/read.only|只读/i);

    const unavailableApi = makeApi({
      requestScan: vi.fn().mockResolvedValue(unavailable),
    });
    const unavailableHook = renderHook(() =>
      useReleaseQualityOperations(scope, {
        enabled: true,
        api: unavailableApi,
      }),
    );
    await waitFor(() => expect(unavailableHook.result.current.load.status).toBe("ready"));
    await act(async () => {
      await unavailableHook.result.current.mutation.requestScan(scanInput(), {
        idempotencyKey: "unavailable-key",
      });
    });
    expect(unavailableHook.result.current.mutation.status).toBe("error");
    expect(unavailableHook.result.current.mutation.outcome).toBeNull();
    expect(unavailableHook.result.current.mutation.error?.message).toMatch(/unavailable/i);
  });

  it("fences a pending mutation when its context changes", async () => {
    let resolveOld: ((value: OperationsMutationOutcome) => void) | undefined;
    const oldMutation = new Promise<OperationsMutationOutcome>((resolve) => {
      resolveOld = resolve;
    });
    const nextScope: OperationsApiScope = {
      tenantId: "tenant-b",
      datasetId: "dataset-b",
      actorToken: "actor-token-b",
    };
    const requestScan = vi.fn(
      (
        next: OperationsApiScope,
        _input: { reason: string },
        _options?: OperationsRequestOptions,
      ) => (next.tenantId === "tenant-a" ? oldMutation : Promise.resolve(applied)),
    );
    const api = makeApi({
      fetchSummary: vi.fn((next: OperationsApiScope) => Promise.resolve(summaryFor(next))),
      requestScan,
    });
    const { result, rerender } = renderHook(
      ({ currentScope }) =>
        useReleaseQualityOperations(currentScope, {
          enabled: true,
          api,
        }),
      { initialProps: { currentScope: scope } },
    );
    await waitFor(() => expect(result.current.load.status).toBe("ready"));

    let oldResult: Promise<OperationsMutationOutcome | null>;
    await act(async () => {
      oldResult = result.current.mutation.requestScan(scanInput(), {
        idempotencyKey: "old-context-key",
      });
      await Promise.resolve();
    });
    const oldSignal = vi.mocked(requestScan).mock.calls[0]?.[2]?.signal;
    rerender({ currentScope: nextScope });
    await waitFor(() => expect(result.current.summary.value?.tenant_id).toBe("tenant-b"));
    expect(oldSignal?.aborted).toBe(true);

    await act(async () => {
      resolveOld?.(applied);
      await oldResult!;
    });
    expect(result.current.mutation.outcome).toBeNull();
  });
});
