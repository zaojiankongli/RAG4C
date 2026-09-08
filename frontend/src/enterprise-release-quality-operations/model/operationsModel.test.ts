import { describe, expect, it } from "vitest";

import {
  projectQualityAlert,
  projectQualityAuthority,
  projectQualityOperationsSummary,
  projectRecertificationJob,
  sanitizeOperationsMessage,
} from "./operationsModel";

const scope = { tenantId: "tenant-a", datasetId: "dataset-a" } as const;

function authority(overrides: Record<string, unknown> = {}) {
  return {
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
    minutes_to_certification_expiry: 0,
    minutes_to_waiver_expiry: null,
    certification_id: "certification-a",
    certification_valid_until: "2026-08-29T12:00:00.000000Z",
    waiver_id: null,
    waiver_expires_at: null,
    active_alert_count: 1,
    recertification_job_status: "pending",
    last_observed_at: "2026-08-29T10:00:00.000000Z",
    observation_digest: "a".repeat(64),
    ...overrides,
  };
}

describe("Stage 21 Quality Operations projectors", () => {
  it("preserves zero versus null signed minute authority", () => {
    const projected = projectQualityAuthority(authority(), scope);
    expect(projected.state).toBe("ready");
    expect(projected.minutes_to_certification_expiry).toBe(0);
    expect(projected.minutes_to_waiver_expiry).toBeNull();

    const expired = projectQualityAuthority(
      authority({ minutes_to_certification_expiry: -15, horizon_band: "expired" }),
      scope,
    );
    expect(expired.minutes_to_certification_expiry).toBe(-15);
    expect(expired.horizon_band).toBe("expired");
  });

  it("fails closed on nested Tenant or Dataset scope mismatch", () => {
    const projected = projectQualityAuthority(authority({ dataset_id: "dataset-b" }), scope);
    expect(projected.state).toBe("unavailable");
    expect(projected.severity).toBe("unavailable");
    expect(projected.gate_state).toBe("unavailable");
    expect(projected.unavailable_reason).toContain("scope");
  });

  it("never turns malformed declared authority into empty or healthy", () => {
    const summary = projectQualityOperationsSummary(
      {
        tenant_id: "tenant-a",
        dataset_id: "dataset-a",
        generated_at: "2026-08-29T10:01:00.000000Z",
        last_completed_scan_at: null,
        horizon_counts: {
          expired: 0,
          "24_hours": 0,
          "7_days": 0,
          "30_days": 0,
          healthy: 0,
          unavailable: 1,
        },
        authorities: [authority({ severity: "green", observation_digest: "forged" })],
      },
      scope,
    );
    expect(summary.state).toBe("unavailable");
    expect(summary.items).toHaveLength(1);
    expect(summary.items[0]?.state).toBe("unavailable");
    expect(summary.items[0]?.severity).toBe("unavailable");
    expect(summary.horizon_counts.unavailable).toBe(1);
  });

  it("projects Alert lifecycle evidence and sanitizes unsafe comments", () => {
    const projected = projectQualityAlert(
      {
        id: "alert-a",
        tenant_id: "tenant-a",
        dataset_id: "dataset-a",
        release_id: "release-a",
        channel_id: "channel-production",
        release_role: "active",
        alert_type: "certification_expiring",
        severity: "critical",
        status: "acknowledged",
        revision: 3,
        occurrence_count: 2,
        opened_at: "2026-08-29T08:00:00.000000Z",
        last_observed_at: "2026-08-29T10:00:00.000000Z",
        acknowledged_at: "2026-08-29T10:01:00.000000Z",
        acknowledged_by: "owner-a",
        acknowledged_comment: "ticket=opaque-secret-ticket",
        suppressed_until: null,
        resolved_at: null,
      },
      scope,
    );
    expect(projected.status).toBe("acknowledged");
    expect(projected.revision).toBe(3);
    expect(projected.acknowledged_comment).toBe("受保护信息已隐藏");
    expect(repr(projected)).not.toContain("opaque-secret-ticket");
  });

  it("projects Job cycle identity without exposing raw idempotency", () => {
    const projected = projectRecertificationJob(
      {
        id: "job-a",
        tenant_id: "tenant-a",
        dataset_id: "dataset-a",
        release_id: "release-a",
        channel_id: "channel-production",
        release_role: "active",
        trigger: "certification_warning",
        status: "ready_to_certify",
        cycle_key: "b".repeat(64),
        attempt_count: 1,
        max_attempts: 3,
        next_attempt_at: null,
        created_at: "2026-08-29T09:00:00.000000Z",
        updated_at: "2026-08-29T10:00:00.000000Z",
        safe_error_code: null,
        safe_error: null,
        idempotency_key: "raw-key-must-not-render",
      },
      scope,
    );
    expect(projected.status).toBe("ready_to_certify");
    expect(projected.cycle_key).toBe("b".repeat(64));
    expect(repr(projected)).not.toContain("raw-key-must-not-render");
    expect("idempotency_key" in projected).toBe(false);
  });

  it("rejects booleans and floats where exact integers are required", () => {
    expect(() =>
      projectQualityAlert(
        {
          id: "alert-a",
          tenant_id: "tenant-a",
          dataset_id: "dataset-a",
          release_id: "release-a",
          channel_id: "channel-production",
          release_role: "active",
          alert_type: "quality_gate_blocked",
          severity: "critical",
          status: "open",
          revision: true,
          occurrence_count: 1.5,
          opened_at: "2026-08-29T08:00:00.000000Z",
          last_observed_at: "2026-08-29T10:00:00.000000Z",
        },
        scope,
      ),
    ).toThrow(/revision|integer/i);
  });

  it("uses the shared bounded secret sanitizer for server safe errors", () => {
    expect(sanitizeOperationsMessage("postgresql://user:pass@example.com/db", "Unavailable")).toBe(
      "受保护信息已隐藏",
    );
    expect(sanitizeOperationsMessage("Lease expired; retry is safe", "Unavailable")).toBe(
      "Lease expired; retry is safe",
    );
    expect(sanitizeOperationsMessage("x".repeat(900), "Unavailable")).toBe("Unavailable");
  });
});

function repr(value: unknown): string {
  return JSON.stringify(value);
}
