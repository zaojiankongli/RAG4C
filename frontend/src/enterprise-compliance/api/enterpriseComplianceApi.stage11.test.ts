// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { EnterpriseScope } from "../../enterprise-admin/model";
import {
  createAuditExport,
  createComplianceIdempotencyKey,
  createLegalHold,
  downloadAuditExport,
  executeRetention,
  fetchAuditExportJobs,
  fetchLegalHolds,
  fetchRetentionPolicy,
  previewRetention,
  releaseLegalHold,
  updateRetentionPolicy,
} from "./enterpriseComplianceApi";

const scope: EnterpriseScope = {
  tenantId: "tenant-1",
  datasetId: "dataset-1",
  actorToken: "actor-token",
};
function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
const policy = {
  id: "policy-1",
  status: "active",
  audit_retention_days: 365,
  export_retention_days: 30,
  revision: 7,
};
const hold = {
  id: "hold-1",
  name: "监管调查",
  reason: "保全证据",
  status: "active",
  revision: 2,
  created_at: "2026-08-26T08:00:00Z",
  created_by: "owner-1",
};
const job = {
  id: "export-1",
  format: "ndjson",
  status: "completed",
  filters: {},
  sha256: "a".repeat(64),
  byte_size: 100,
  row_count: 2,
  revision: 1,
  requested_at: "2026-08-26T08:00:00Z",
  expires_at: "2026-09-25T08:00:00Z",
};

describe("Stage 11 compliance API", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("rag4c.base_url", "http://enterprise.test");
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/download"))
          return Promise.resolve(
            new Response("audit-export", {
              status: 200,
              headers: {
                "Content-Type": "application/x-ndjson",
                "Content-Disposition": 'attachment; filename="audit-export.ndjson"',
              },
            }),
          );
        if (url.includes("retention-policy")) return Promise.resolve(jsonResponse({ policy }));
        if (url.includes("retention/preview"))
          return Promise.resolve(
            jsonResponse({
              policy_revision: 7,
              cutoff_at: "2025-08-26T08:00:00Z",
              candidate_count: 10,
              protected_count: 2,
              deletable_count: 8,
              sequence_start: 1,
              sequence_end: 10,
              preview_fingerprint: "sha256:preview",
              generated_at: "2026-08-26T08:00:00Z",
            }),
          );
        if (url.includes("retention/execute"))
          return Promise.resolve(jsonResponse({ deleted_count: 8, policy_revision: 7 }));
        if (url.includes("legal-holds"))
          return Promise.resolve(
            jsonResponse({ hold, items: [hold], count: 1, next_before_id: null }),
          );
        return Promise.resolve(jsonResponse({ job, items: [job], count: 1, next_before_id: null }));
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("reads policy, holds and export jobs with tenant and actor authority", async () => {
    await fetchRetentionPolicy(scope);
    await fetchLegalHolds(scope);
    await fetchAuditExportJobs(scope);
    for (const [, init] of vi.mocked(fetch).mock.calls) {
      expect(init?.headers).toMatchObject({
        "X-RAG4C-Tenant": "tenant-1",
        Authorization: "Bearer actor-token",
      });
    }
  });

  it("uses stable 1..128 Idempotency-Key headers for every compliance mutation", async () => {
    const key = "k".repeat(128);
    await updateRetentionPolicy(
      scope,
      {
        audit_retention_days: 365,
        export_retention_days: 30,
        status: "active",
        revision: 7,
        reason: "annual policy",
      },
      { idempotencyKey: key },
    );
    await executeRetention(
      scope,
      {
        policy_revision: 7,
        preview_fingerprint: "sha256:preview",
        reason: "approved cleanup",
        confirmation: "EXECUTE RETENTION",
      },
      { idempotencyKey: key },
    );
    await createLegalHold(
      scope,
      { name: "监管调查", reason: "preserve", sequence_start: 1, sequence_end: 100 },
      { idempotencyKey: key },
    );
    await releaseLegalHold(
      scope,
      "hold-1",
      { revision: 2, reason: "case closed" },
      { idempotencyKey: key },
    );
    await createAuditExport(
      scope,
      { format: "ndjson", filters: { action: "member.role.updated" }, reason: "regulatory export" },
      { idempotencyKey: key },
    );
    expect(vi.mocked(fetch).mock.calls).toHaveLength(5);
    for (const [, init] of vi.mocked(fetch).mock.calls)
      expect(init?.headers).toMatchObject({ "Idempotency-Key": key });

    await expect(
      createAuditExport(
        scope,
        { format: "csv", filters: {}, reason: "too long key" },
        { idempotencyKey: "x".repeat(129) },
      ),
    ).rejects.toThrow(/128/);
    expect(createComplianceIdempotencyKey().length).toBeLessThanOrEqual(128);
  });

  it("keeps preview read-only and downloads through the authenticated API path", async () => {
    await previewRetention(scope, { policy_revision: 7 });
    const [, previewInit] = vi.mocked(fetch).mock.calls[0];
    expect(previewInit?.headers).not.toHaveProperty("Idempotency-Key");

    vi.mocked(fetch).mockClear();
    const download = await downloadAuditExport(scope, "export-1");
    expect(download.filename).toBe("audit-export.ndjson");
    expect(await download.blob.text()).toBe("audit-export");
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toBe(
      "http://enterprise.test/api/enterprise/compliance/audit-exports/export-1/download",
    );
  });
});
