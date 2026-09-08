// @vitest-environment jsdom

import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReleaseQualityApi } from "../api/qualityApi";
import type { QualityMutationOutcome } from "../model/qualityModel";
import { useReleaseQuality } from "./useReleaseQuality";

const scope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "actor-token" };
const gate = {
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  release_id: "release-42",
  channel_id: "channel-production",
  release_manifest_digest: "a".repeat(64),
  channel_revision: 4,
  risk_tier: "high",
  is_default_serving: true,
  state: "blocked" as const,
  reason: "certification_required",
  policy: null,
  certification: null,
  waiver: null,
};

function makeApi(overrides: Partial<ReleaseQualityApi> = {}): ReleaseQualityApi {
  return {
    fetchPolicies: vi
      .fn()
      .mockResolvedValue({ items: [], next_cursor: null, invalid_item_count: 0 }),
    fetchBaselines: vi
      .fn()
      .mockResolvedValue({ items: [], next_cursor: null, invalid_item_count: 0 }),
    fetchBaseline: vi.fn(),
    fetchCertifications: vi
      .fn()
      .mockResolvedValue({ items: [], next_cursor: null, invalid_item_count: 0 }),
    fetchGate: vi.fn().mockResolvedValue(gate),
    createBaseline: vi.fn(),
    certify: vi.fn(),
    updatePolicy: vi.fn(),
    requestWaiver: vi.fn(),
    ...overrides,
  };
}

describe("useReleaseQuality", () => {
  it("stays inactive without issuing reads and loads gate/baselines/policies only when enabled", async () => {
    const api = makeApi();
    const { result, rerender } = renderHook(
      ({ enabled }) =>
        useReleaseQuality(scope, {
          enabled,
          releaseId: "release-42",
          channelId: "channel-production",
          api,
        }),
      { initialProps: { enabled: false } },
    );
    expect(api.fetchGate).not.toHaveBeenCalled();
    expect(result.current.gate.status).toBe("idle");
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.gate.status).toBe("ready"));
    expect(api.fetchGate).toHaveBeenCalledTimes(1);
    expect(api.fetchBaselines).toHaveBeenCalledTimes(1);
    expect(api.fetchPolicies).toHaveBeenCalledTimes(1);
    expect(api.fetchCertifications).not.toHaveBeenCalled();
  });

  it("loads certification history lazily and reuses one idempotency key on retry", async () => {
    const certify = vi.fn().mockRejectedValueOnce(new Error("temporary")).mockResolvedValueOnce({
      state: "applied",
      operation: "release_quality_certify",
      resource_id: "quality-certification-1",
      message: "已认证",
      retryable: false,
    });
    const api = makeApi({ certify });
    const { result } = renderHook(() =>
      useReleaseQuality(scope, {
        enabled: true,
        releaseId: "release-42",
        channelId: "channel-production",
        api,
      }),
    );
    await waitFor(() => expect(result.current.gate.status).toBe("ready"));
    await act(async () => result.current.certifications.load());
    expect(api.fetchCertifications).toHaveBeenCalledTimes(1);
    await act(async () => {
      await result.current.mutation.certify({
        channelId: "channel-production",
        baselineId: "quality-baseline-1",
        policyId: "quality-policy-1",
        expectedPolicyRevision: 2,
        expectedChannelRevision: 4,
        reason: "发布认证",
      });
    });
    expect(result.current.mutation.status).toBe("error");
    await act(async () => result.current.mutation.retry());
    expect(certify).toHaveBeenCalledTimes(2);
    expect(certify.mock.calls[0]?.[3]?.idempotencyKey).toBe(
      certify.mock.calls[1]?.[3]?.idempotencyKey,
    );
  });

  it("treats unavailable mutation outcomes as errors and ignores stale context results", async () => {
    let resolveOld: ((value: QualityMutationOutcome) => void) | undefined;
    const pending = new Promise<QualityMutationOutcome>((resolve) => {
      resolveOld = resolve;
    });
    const certify = vi
      .fn()
      .mockResolvedValueOnce({
        state: "unavailable",
        operation: "release_quality_certify",
        resource_id: null,
        message: null,
        retryable: false,
      })
      .mockReturnValueOnce(pending);
    const api = makeApi({ certify });
    const { result, rerender } = renderHook(
      ({ nextScope, releaseId }) =>
        useReleaseQuality(nextScope, {
          enabled: true,
          releaseId,
          channelId: "channel-production",
          api,
        }),
      { initialProps: { nextScope: scope, releaseId: "release-42" } },
    );
    await waitFor(() => expect(result.current.gate.status).toBe("ready"));
    await act(async () => {
      await result.current.mutation.certify({
        channelId: "channel-production",
        baselineId: "baseline-1",
        policyId: "policy-1",
        expectedPolicyRevision: 1,
        expectedChannelRevision: 1,
        reason: "fail closed",
      });
    });
    expect(result.current.mutation.status).toBe("error");

    let stalePromise: Promise<unknown> | undefined;
    await act(async () => {
      stalePromise = result.current.mutation.certify({
        channelId: "channel-production",
        baselineId: "baseline-1",
        policyId: "policy-1",
        expectedPolicyRevision: 1,
        expectedChannelRevision: 1,
        reason: "old context",
      });
    });
    rerender({
      nextScope: { tenantId: "tenant-b", datasetId: "dataset-b", actorToken: "token-b" },
      releaseId: "release-b",
    });
    await act(async () => {
      resolveOld?.({
        state: "applied",
        operation: "release_quality_certify",
        resource_id: "old-cert",
        message: "old",
        retryable: false,
      });
      await stalePromise;
    });
    expect(result.current.mutation.outcome).toBeNull();
  });
});
