import { afterEach, describe, expect, it, vi } from "vitest";
import { request } from "../../api/client";
import {
  applyLegalHold,
  bulkRecycleDocuments,
  cancelDocumentPurgeRequest,
  createRecoveryIdempotencyKey,
  fetchLegalHolds,
  fetchPurgeRequests,
  fetchRecycleEntries,
  fetchRecycleEntry,
  fetchRecoverySummary,
  fetchRetentionPolicy,
  recycleDocument,
  requestDocumentPurge,
  restoreRecycleEntry,
  releaseLegalHold,
  updateContentRetentionPolicy,
  type RecoveryApiScope,
} from "./recoveryApi";

vi.mock("../../api/client", () => ({ request: vi.fn() }));

const mockedRequest = vi.mocked(request);
const scope: RecoveryApiScope = { tenantId: "tenant-a", actorToken: "actor-token" };
const timestamp = "2026-08-29T12:34:56.000000Z";
const digest = "a".repeat(64);

const entry = {
  id: "entry-a",
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  document_id: "document-a",
  recycle_generation: 1,
  active_recycle_key: "dataset-a:document-a",
  status: "recycled",
  revision: 3,
  document_mutation_generation: 8,
  original_lifecycle_state: "active",
  original_retrieval_enabled: true,
  retention_days_snapshot: 30,
  recycled_at: timestamp,
  recycled_by: "account-a",
  purge_eligible_at: "2026-09-28T12:34:56.000000Z",
  restored_at: null,
  restored_by: null,
  purge_requested_at: null,
  purged_at: null,
  purged_by: null,
  safe_snapshot_json: {
    dataset_id: "dataset-a",
    document_id: "document-a",
    original_lifecycle_state: "active",
    original_retrieval_enabled: true,
  },
  snapshot_digest: digest,
  created_at: timestamp,
  updated_at: timestamp,
};

const mutation = {
  state: "applied",
  operation: "restore",
  resource_id: "entry-a",
  approval_request_id: null,
  route: null,
  revision: 4,
  message: "已提交",
  retryable: false,
};

afterEach(() => vi.resetAllMocks());

describe("enterprise content recovery API", () => {
  it("uses tenant-scoped read routes and projects pages/detail/policy", async () => {
    mockedRequest
      .mockResolvedValueOnce({
        tenant_id: "tenant-a",
        state: "ready",
        recycled_count: 1,
        expiring_count: 0,
        held_count: 0,
        pending_purge_count: 0,
        as_of: timestamp,
      })
      .mockResolvedValueOnce({ items: [entry], next_cursor: "next", invalid_item_count: 0 })
      .mockResolvedValueOnce({ entry, events: [] })
      .mockResolvedValueOnce({ items: [], next_cursor: null, invalid_item_count: 0 })
      .mockResolvedValueOnce({ items: [], next_cursor: null, invalid_item_count: 0 })
      .mockResolvedValueOnce({
        id: "policy-a",
        tenant_id: "tenant-a",
        status: "active",
        retention_days: 30,
        auto_purge_enabled: false,
        purge_requires_approval: true,
        revision: 1,
        created_at: timestamp,
        created_by: "account-a",
        updated_at: timestamp,
        updated_by: "account-a",
      });

    const controller = new AbortController();
    const summary = await fetchRecoverySummary(scope, { signal: controller.signal });
    const page = await fetchRecycleEntries(
      scope,
      { cursor: "cursor-a", limit: 20, status: "recycled", datasetId: "dataset-a" },
      { signal: controller.signal },
    );
    const detail = await fetchRecycleEntry(scope, "entry/a", { signal: controller.signal });
    await fetchLegalHolds(scope, "entry-a", { limit: 10 }, { signal: controller.signal });
    await fetchPurgeRequests(
      scope,
      "entry-a",
      { status: "pending_approval" },
      { signal: controller.signal },
    );
    const policy = await fetchRetentionPolicy(scope, { signal: controller.signal });

    expect(summary.recycled_count).toBe(1);
    expect(page.items[0]?.id).toBe("entry-a");
    expect(detail.entry.id).toBe("entry-a");
    expect(policy.retention_days).toBe(30);
    expect(mockedRequest).toHaveBeenNthCalledWith(
      1,
      "/api/enterprise/recovery/summary",
      expect.objectContaining({ method: "GET", signal: controller.signal }),
    );
    expect(mockedRequest).toHaveBeenNthCalledWith(
      2,
      "/api/enterprise/recycle-bin?dataset_id=dataset-a&cursor=cursor-a&limit=20&status=recycled",
      expect.objectContaining({ method: "GET", signal: controller.signal }),
    );
    expect(mockedRequest).toHaveBeenNthCalledWith(
      3,
      "/api/enterprise/recycle-bin/entry%2Fa",
      expect.objectContaining({ method: "GET", signal: controller.signal }),
    );
    expect(mockedRequest.mock.calls[0]?.[1]?.headers).toEqual({
      "Content-Type": "application/json",
      "X-RAG4C-Tenant": "tenant-a",
      Authorization: "Bearer actor-token",
    });
  });

  it("uses every designed mutation route with strict revisions and Idempotency-Key", async () => {
    mockedRequest
      .mockResolvedValueOnce(mutation)
      .mockResolvedValueOnce(mutation)
      .mockResolvedValueOnce(mutation)
      .mockResolvedValueOnce(mutation)
      .mockResolvedValueOnce({
        ...mutation,
        state: "approval_required",
        operation: "request_purge",
        approval_request_id: "approval-a",
        route: {
          target_route_code: "enterprise_approval",
          target_route_params_json: { approval_request_id: "approval-a" },
        },
      })
      .mockResolvedValueOnce(mutation)
      .mockResolvedValueOnce(mutation);
    const options = { idempotencyKey: "recovery-key", signal: new AbortController().signal };

    await recycleDocument(
      scope,
      "document-a",
      { datasetId: "dataset-a", expectedMutationGeneration: 8, reason: "operator reviewed" },
      options,
    );
    await restoreRecycleEntry(
      scope,
      "entry-a",
      { expectedRevision: 3, reason: "restore request" },
      options,
    );
    await applyLegalHold(
      scope,
      "entry-a",
      { expectedRevision: 3, reasonCode: "legal_review", safeReason: "Legal review" },
      options,
    );
    await releaseLegalHold(
      scope,
      "entry-a",
      "hold-a",
      { expectedRevision: 1, reason: "release request" },
      options,
    );
    await requestDocumentPurge(
      scope,
      "entry-a",
      { expectedRevision: 3, reason: "purge request" },
      options,
    );
    await cancelDocumentPurgeRequest(
      scope,
      "entry-a",
      "purge-a",
      { expectedRevision: 1, reason: "cancel request" },
      options,
    );
    await updateContentRetentionPolicy(
      scope,
      {
        expectedRevision: 1,
        status: "paused",
        retentionDays: 60,
        autoPurgeEnabled: false,
        purgeRequiresApproval: true,
        reason: "policy review",
      },
      options,
    );

    expect(mockedRequest.mock.calls.map(([path]) => path)).toEqual([
      "/api/enterprise/recycle-bin/documents/document-a",
      "/api/enterprise/recycle-bin/entry-a/restore",
      "/api/enterprise/recycle-bin/entry-a/holds",
      "/api/enterprise/recycle-bin/entry-a/holds/hold-a/release",
      "/api/enterprise/recycle-bin/entry-a/purge-requests",
      "/api/enterprise/recycle-bin/entry-a/purge-requests/purge-a/cancel",
      "/api/enterprise/recovery/retention-policy",
    ]);
    for (const [, init] of mockedRequest.mock.calls) {
      expect(init?.headers).toEqual(expect.objectContaining({ "Idempotency-Key": "recovery-key" }));
    }
    expect(mockedRequest.mock.calls[0]?.[1]?.body).toBe(
      JSON.stringify({
        dataset_id: "dataset-a",
        expected_mutation_generation: 8,
        reason: "operator reviewed",
      }),
    );
    expect(mockedRequest.mock.calls[2]?.[1]?.body).toBe(
      JSON.stringify({
        expected_revision: 3,
        reason_code: "legal_review",
        safe_reason: "Legal review",
      }),
    );
    expect(mockedRequest.mock.calls[6]?.[1]?.body).toBe(
      JSON.stringify({
        expected_revision: 1,
        status: "paused",
        retention_days: 60,
        auto_purge_enabled: false,
        purge_requires_approval: true,
        reason: "policy review",
      }),
    );
    expect(() =>
      restoreRecycleEntry(scope, "entry-a", { expectedRevision: 3, reason: "restore" }),
    ).toThrow(/idempotency/i);
  });

  it("bounds batch recycle to 100 unique documents and keeps Approval handoff safe", async () => {
    mockedRequest.mockResolvedValue({
      ...mutation,
      operation: "recycle",
      resource_id: "document-a",
    });
    const documents = Array.from({ length: 100 }, (_, index) => `document-${index}`);
    await bulkRecycleDocuments(
      scope,
      { datasetId: "dataset-a", documents, expectedMutationGeneration: 8, reason: "batch recycle" },
      { idempotencyKey: "bulk-key" },
    );
    expect(mockedRequest).toHaveBeenCalledTimes(100);
    expect(() =>
      bulkRecycleDocuments(
        scope,
        {
          datasetId: "dataset-a",
          documents: Array.from({ length: 101 }, (_, index) => `document-${index}`),
          expectedMutationGeneration: 8,
          reason: "too many",
        },
        { idempotencyKey: "bulk-key" },
      ),
    ).toThrow(/100|bulk/i);
    expect(() =>
      bulkRecycleDocuments(
        scope,
        {
          datasetId: "dataset-a",
          documents: ["document-a", "document-a"],
          expectedMutationGeneration: 8,
          reason: "duplicate",
        },
        { idempotencyKey: "bulk-key" },
      ),
    ).toThrow(/unique|duplicate/i);
  });

  it("rejects malformed scope, query, revision and policy values before network calls", async () => {
    expect(() => fetchRecycleEntries(scope, { limit: 101 })).toThrow(/limit/i);
    expect(() => fetchRecycleEntries(scope, { cursor: "bad\nvalue" })).toThrow(/cursor/i);
    expect(() =>
      recycleDocument(
        scope,
        "document-a",
        { datasetId: "", expectedMutationGeneration: 1, reason: "x" },
        { idempotencyKey: "key" },
      ),
    ).toThrow(/dataset/i);
    expect(() =>
      recycleDocument(
        scope,
        "document-a",
        { datasetId: "dataset-a", expectedMutationGeneration: true as unknown as number, reason: "x" },
        { idempotencyKey: "key" },
      ),
    ).toThrow(/generation|integer/i);
    expect(() =>
      updateContentRetentionPolicy(
        scope,
        {
          expectedRevision: 1,
          status: "paused",
          retentionDays: 0,
          autoPurgeEnabled: false,
          purgeRequiresApproval: true,
          reason: "x",
        },
        { idempotencyKey: "key" },
      ),
    ).toThrow(/retention/i);
    expect(() => fetchRecoverySummary({ tenantId: "", actorToken: "token" })).toThrow(/tenant/i);
    expect(mockedRequest).not.toHaveBeenCalled();
  });

  it("rejects unknown page envelope fields and bounds long batch idempotency keys", async () => {
    mockedRequest.mockResolvedValueOnce({
      items: [entry],
      next_cursor: null,
      invalid_item_count: 0,
      unexpected: "must fail closed",
    });
    await expect(fetchRecycleEntries(scope)).rejects.toThrow(/unexpected|field|page/i);

    mockedRequest.mockResolvedValue({
      ...mutation,
      operation: "recycle",
      resource_id: "document-a",
    });
    await bulkRecycleDocuments(
      scope,
      { datasetId: "dataset-a", documents: ["document-a"], expectedMutationGeneration: 8, reason: "batch recycle" },
      { idempotencyKey: "k".repeat(128) },
    );
    const lastInit = mockedRequest.mock.calls[mockedRequest.mock.calls.length - 1]?.[1];
    expect(lastInit?.headers).toEqual(
      expect.objectContaining({ "Idempotency-Key": expect.stringMatching(/^k+-1$/) }),
    );
    expect((lastInit?.headers as Record<string, string>)["Idempotency-Key"]).toHaveLength(128);
  });

  it("creates nonempty client idempotency keys", () => {
    expect(createRecoveryIdempotencyKey()).toMatch(/^rag4c-recovery-/);
  });
});
