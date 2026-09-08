// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  acknowledgeQualityAlert,
  cancelRecertificationJob,
  createOperationsIdempotencyKey,
  fetchQualityAlerts,
  fetchQualityOperationsSummary,
  fetchRecertificationJobs,
  queueRecertificationJob,
  requestQualityOperationsScan,
  resolveQualityAlert,
  suppressQualityAlert,
} from "./operationsApi";

const scope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "actor-token" };

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  localStorage.setItem("rag4c.base_url", "https://operations.test");
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Stage21 Quality Operations API", () => {
  it("loads summary, Alert inbox and Job queue with exact Tenant/Dataset headers", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(
        response({
          tenant_id: "tenant-a",
          dataset_id: "dataset-a",
          generated_at: "2026-08-29T10:00:00.000000Z",
          last_completed_scan_at: null,
          horizon_counts: {
            expired: 0,
            "24_hours": 0,
            "7_days": 0,
            "30_days": 0,
            healthy: 0,
            unavailable: 0,
          },
          authorities: [],
        }),
      )
      .mockResolvedValueOnce(response({ items: [], next_cursor: "alert-next" }))
      .mockResolvedValueOnce(response({ items: [], next_cursor: "job-next" }));

    const summary = await fetchQualityOperationsSummary(scope);
    const alerts = await fetchQualityAlerts(scope, {
      cursor: "alert-in",
      limit: 20,
      status: "open",
      severity: "critical",
    });
    const jobs = await fetchRecertificationJobs(scope, {
      cursor: "job-in",
      limit: 30,
      status: "pending",
    });

    expect(summary.state).toBe("ready");
    expect(alerts.next_cursor).toBe("alert-next");
    expect(jobs.next_cursor).toBe("job-next");
    expect(vi.mocked(fetch).mock.calls.map(([input]) => String(input))).toEqual([
      "https://operations.test/api/enterprise/knowledge-bases/dataset-a/quality-operations/summary",
      "https://operations.test/api/enterprise/knowledge-bases/dataset-a/quality-alerts?cursor=alert-in&limit=20&status=open&severity=critical",
      "https://operations.test/api/enterprise/knowledge-bases/dataset-a/recertification-jobs?cursor=job-in&limit=30&status=pending",
    ]);
    expect(vi.mocked(fetch).mock.calls[0]?.[1]?.headers).toEqual(
      expect.objectContaining({
        "X-RAG4C-Tenant": "tenant-a",
        Authorization: "Bearer actor-token",
      }),
    );
  });

  it("keeps a caller-owned Idempotency-Key across all lifecycle mutations", async () => {
    vi.mocked(fetch).mockImplementation(() =>
      Promise.resolve(
        response({ state: "applied", operation: "quality_operations", resource_id: "resource-a" }),
      ),
    );
    expect(createOperationsIdempotencyKey()).toMatch(/^rag4c-quality-operations-/);

    await acknowledgeQualityAlert(
      scope,
      "alert-a",
      { expectedRevision: 2, comment: "owner accepted", reason: "take ownership" },
      { idempotencyKey: "same-operations-key" },
    );
    await resolveQualityAlert(
      scope,
      "alert-a",
      { expectedRevision: 3, comment: "evidence restored", reason: "resolve cycle" },
      { idempotencyKey: "same-operations-key" },
    );
    await suppressQualityAlert(
      scope,
      "alert-a",
      {
        expectedRevision: 3,
        suppressedUntil: "2026-08-30T10:00:00.000000Z",
        comment: "maintenance window",
        reason: "bounded suppression",
      },
      { idempotencyKey: "same-operations-key" },
    );
    await queueRecertificationJob(
      scope,
      {
        releaseId: "release-a",
        channelId: "channel-production",
        releaseRole: "active",
        baselineId: "baseline-a",
        policyId: "policy-a",
        expectedPolicyRevision: 2,
        sloPolicyId: "slo-policy-a",
        expectedSloPolicyRevision: 3,
        expectedManifestDigest: "a".repeat(64),
        expectedEvidenceDigest: "b".repeat(64),
        expectedChannelRevision: 4,
        trigger: "manual",
        reason: "operator queue",
      },
      { idempotencyKey: "same-operations-key" },
    );
    await cancelRecertificationJob(
      scope,
      "job-a",
      { expectedStatus: "pending", reason: "obsolete cycle" },
      { idempotencyKey: "same-operations-key" },
    );
    await requestQualityOperationsScan(
      scope,
      { reason: "operator scan" },
      { idempotencyKey: "same-operations-key" },
    );

    for (const [, init] of vi.mocked(fetch).mock.calls) {
      expect(init?.headers).toEqual(
        expect.objectContaining({ "Idempotency-Key": "same-operations-key" }),
      );
    }
    expect(vi.mocked(fetch).mock.calls.map(([input]) => String(input))).toEqual([
      "https://operations.test/api/enterprise/knowledge-bases/dataset-a/quality-alerts/alert-a/acknowledge",
      "https://operations.test/api/enterprise/knowledge-bases/dataset-a/quality-alerts/alert-a/resolve",
      "https://operations.test/api/enterprise/knowledge-bases/dataset-a/quality-alerts/alert-a/suppress",
      "https://operations.test/api/enterprise/knowledge-bases/dataset-a/recertification-jobs",
      "https://operations.test/api/enterprise/knowledge-bases/dataset-a/recertification-jobs/job-a/cancel",
      "https://operations.test/api/enterprise/knowledge-bases/dataset-a/quality-operations/scan",
    ]);
  });

  it("rejects missing idempotency keys and unsafe exact integer inputs before fetch", async () => {
    await expect(
      acknowledgeQualityAlert(scope, "alert-a", {
        expectedRevision: 1,
        comment: "owner accepted",
        reason: "take ownership",
      }),
    ).rejects.toThrow(/idempotency/i);
    expect(() =>
      acknowledgeQualityAlert(
        scope,
        "alert-a",
        {
          expectedRevision: 1.5,
          comment: "owner accepted",
          reason: "take ownership",
        },
        { idempotencyKey: "key-a" },
      ),
    ).toThrow(/revision/i);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("does not serialize arbitrary note, ticket, query or credential fields", async () => {
    vi.mocked(fetch).mockResolvedValue(
      response({
        state: "applied",
        operation: "quality_alert_acknowledge",
        resource_id: "alert-a",
      }),
    );
    await acknowledgeQualityAlert(
      scope,
      "alert-a",
      { expectedRevision: 2, comment: "bounded comment", reason: "take ownership" },
      { idempotencyKey: "key-a" },
    );
    const body = String(vi.mocked(fetch).mock.calls[0]?.[1]?.body);
    expect(body).toContain('"expected_revision":2');
    expect(body).not.toMatch(/ticket|query|credential|judgment_note|idempotency/i);
  });
});
