// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  certifyReleaseQuality,
  createQualityBaseline,
  createQualityIdempotencyKey,
  fetchQualityBaselines,
  fetchQualityGate,
  fetchQualityPolicies,
  fetchReleaseCertifications,
  requestQualityWaiver,
  updateQualityPolicy,
} from "./qualityApi";

const scope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "actor-token" };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  localStorage.setItem("rag4c.base_url", "https://quality.test");
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Stage20 Release Quality API", () => {
  it("uses exact read endpoints, opaque cursors and tenant auth headers", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: "policy-next" }))
      .mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: "baseline-next" }))
      .mockResolvedValueOnce(jsonResponse({ items: [], next_cursor: "cert-next" }))
      .mockResolvedValueOnce(
        jsonResponse({
          tenant_id: "tenant-a",
          dataset_id: "dataset-a",
          release_id: "release-42",
          channel_id: "channel-production",
          release_manifest_digest: "a".repeat(64),
          channel_revision: 4,
          risk_tier: "high",
          is_default_serving: true,
          state: "blocked",
          reason: "certification_required",
          policy: null,
          certification: null,
          waiver: null,
        }),
      );
    const policies = await fetchQualityPolicies(scope, { cursor: "policy-in", limit: 20 });
    const baselines = await fetchQualityBaselines(scope, { cursor: "baseline-in", limit: 30 });
    const certifications = await fetchReleaseCertifications(scope, "release-42", {
      cursor: "cert-in",
      limit: 40,
    });
    const gate = await fetchQualityGate(scope, "release-42", "channel-production");
    expect([policies.next_cursor, baselines.next_cursor, certifications.next_cursor]).toEqual([
      "policy-next",
      "baseline-next",
      "cert-next",
    ]);
    expect(gate.state).toBe("blocked");
    expect(vi.mocked(fetch).mock.calls.map(([input]) => String(input))).toEqual([
      "https://quality.test/api/enterprise/release-quality/policies?cursor=policy-in&limit=20",
      "https://quality.test/api/enterprise/knowledge-bases/dataset-a/quality-baselines?cursor=baseline-in&limit=30",
      "https://quality.test/api/enterprise/knowledge-bases/dataset-a/releases/release-42/certifications?cursor=cert-in&limit=40",
      "https://quality.test/api/enterprise/knowledge-bases/dataset-a/releases/release-42/quality-gate?channel_id=channel-production",
    ]);
    expect(vi.mocked(fetch).mock.calls[0]?.[1]?.headers).toEqual(
      expect.objectContaining({
        "X-RAG4C-Tenant": "tenant-a",
        Authorization: "Bearer actor-token",
      }),
    );
  });

  it("sends revision-fenced mutation bodies with a caller-owned idempotency key", async () => {
    const calls: Array<{ input: string; init?: RequestInit }> = [];
    vi.mocked(fetch).mockImplementation((input, init) => {
      calls.push({ input: String(input), init });
      return Promise.resolve(
        jsonResponse({
          state: "applied",
          operation: "release_quality_certify",
          resource_id: "quality-certification-1",
        }),
      );
    });
    expect(createQualityIdempotencyKey()).toMatch(/^rag4c-quality-/);
    await createQualityBaseline(
      scope,
      { name: " 核心问答 ", experimentIds: ["experiment-1"], reason: " 冻结证据 " },
      { idempotencyKey: "quality-key" },
    );
    await certifyReleaseQuality(
      scope,
      "release-42",
      {
        channelId: "channel-production",
        baselineId: "quality-baseline-1",
        policyId: "quality-policy-1",
        expectedPolicyRevision: 2,
        expectedChannelRevision: 4,
        reason: " 发布验证 ",
      },
      { idempotencyKey: "quality-key" },
    );
    await updateQualityPolicy(
      scope,
      "quality-policy-1",
      { expectedRevision: 2, minMeanScoreMilli: 2500, reason: " 提高门槛 " },
      { idempotencyKey: "quality-key" },
    );
    await requestQualityWaiver(
      scope,
      "release-42",
      {
        channelId: "channel-production",
        policyId: "quality-policy-1",
        expectedPolicyRevision: 2,
        expectedChannelRevision: 4,
        approvalPolicyId: "approval-policy-1",
        requestedExpiresAt: "2026-08-29T08:00:00Z",
        reason: " 临时豁免 ",
      },
      { idempotencyKey: "quality-key" },
    );
    expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({
      name: "核心问答",
      experiment_ids: ["experiment-1"],
      parent_baseline_id: null,
      reason: "冻结证据",
    });
    expect(JSON.parse(String(calls[1]?.init?.body))).toEqual({
      channel_id: "channel-production",
      baseline_id: "quality-baseline-1",
      policy_id: "quality-policy-1",
      expected_policy_revision: 2,
      expected_channel_revision: 4,
      reason: "发布验证",
    });
    expect(JSON.parse(String(calls[2]?.init?.body))).toEqual({
      expected_revision: 2,
      min_mean_score_milli: 2500,
      reason: "提高门槛",
    });
    expect(JSON.parse(String(calls[3]?.init?.body))).toEqual({
      channel_id: "channel-production",
      policy_id: "quality-policy-1",
      expected_policy_revision: 2,
      expected_channel_revision: 4,
      approval_policy_id: "approval-policy-1",
      requested_expires_at: "2026-08-29T08:00:00.000Z",
      reason: "临时豁免",
    });
    expect(
      calls.every(
        (call) =>
          (call.init?.headers as Record<string, string> | undefined)?.["Idempotency-Key"] ===
          "quality-key",
      ),
    ).toBe(true);
  });
});
