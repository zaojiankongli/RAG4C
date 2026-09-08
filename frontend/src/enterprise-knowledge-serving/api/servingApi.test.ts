// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  activateServingProfile,
  createServingIdempotencyKey,
  createServingPolicyRevision,
  createServingProfile,
  fetchServingEvents,
  fetchServingProfile,
  fetchServingSnapshot,
  fetchServingSnapshots,
  fetchServingStageFacts,
  fetchServingSummary,
  previewServingPolicy,
  type ServingApiScope,
} from "./servingApi";

const scope: ServingApiScope = {
  tenantId: "tenant-a",
  accountId: "account-a",
  datasetId: "dataset/a",
  actorToken: "actor-token",
};
const digest = "a".repeat(64);
const now = "2026-08-30T00:00:00Z";
const stage = (stage_code: string, sequence: number) => ({
  id: `${stage_code}-fact`,
  tenant_id: "tenant-a",
  profile_id: "profile-a",
  snapshot_id: "snapshot-a",
  stage_code,
  sequence,
  state: "ready",
  item_count: 1,
  ready_count: 1,
  warning_count: 0,
  pending_count: 0,
  error_count: 0,
  lag_seconds: 0,
  expected_revision: 4,
  observed_revision: 4,
  expected_digest: digest,
  observed_digest: digest,
  safe_error_code: null,
  safe_error: null,
  stage_digest: digest,
  observed_at: now,
});
const summary = {
  tenant_id: "tenant-a",
  dataset_id: "dataset/a",
  profile_id: "profile-a",
  profile_name: "知识库服务",
  profile_status: "active",
  state: "ready",
  as_of: now,
  snapshot_id: "snapshot-a",
  snapshot_digest: digest,
  policy_revision: 4,
  serving_generation: 18,
  source_count: 1,
  ready_source_count: 1,
  stale_source_count: 0,
  active_document_count: 1,
  failed_document_count: 0,
  pending_index_count: 0,
  stage_count: 5,
  ready_stage_count: 5,
  blocked_stage_count: 0,
  current_release_id: null,
  current_certification_id: null,
  reason_code: null,
  stage_facts: [
    stage("source", 1),
    stage("parse", 2),
    stage("chunk", 3),
    stage("index", 4),
    stage("serve", 5),
  ],
};
const profile = {
  id: "profile-a",
  tenant_id: "tenant-a",
  workspace_id: "workspace-a",
  dataset_id: "dataset/a",
  name: "知识库服务",
  normalized_name: String("知识库服务").toLocaleLowerCase(),
  status: "active",
  active_profile_key: "dataset/a",
  revision: 4,
  current_policy_revision_id: "policy-a",
  current_snapshot_id: "snapshot-a",
  created_at: now,
  created_by: "account-a",
  updated_at: now,
  updated_by: "account-a",
  archived_at: null,
  archived_by: null,
};
const page = { items: [], count: 0, next_cursor: null, invalid_item_count: 0 };
function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  localStorage.setItem("rag4c.base_url", "https://serving.test");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => response(summary)),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Stage26 serving API", () => {
  it("reads a tenant/dataset-scoped summary with an AbortSignal and canonical headers", async () => {
    const controller = new AbortController();
    vi.mocked(fetch).mockResolvedValueOnce(response(summary));
    const result = await fetchServingSummary(scope, { signal: controller.signal });
    expect(result.snapshot_id).toBe("snapshot-a");
    const [url, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(String(url)).toBe(
      "https://serving.test/api/enterprise/knowledge-bases/dataset%2Fa/serving/summary",
    );
    expect(init).toEqual(
      expect.objectContaining({ method: "GET", signal: expect.any(AbortSignal) }),
    );
    expect(init?.headers).toEqual(
      expect.objectContaining({
        "X-RAG4C-Tenant": "tenant-a",
        "X-RAG4C-Account": "account-a",
        Authorization: "Bearer actor-token",
      }),
    );
  });

  it("encodes identifiers and preserves opaque cursors for profile, snapshots, stages, and events", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(response({ profile, current_policy: null, current_snapshot: null }))
      .mockResolvedValueOnce(
        response({
          items: [
            {
              id: "snapshot-a",
              tenant_id: "tenant-a",
              profile_id: "profile-a",
              policy_revision_id: "policy-a",
              observation_key: "obs-a",
              state: "ready",
              source_count: 1,
              ready_source_count: 1,
              stale_source_count: 0,
              active_document_count: 1,
              failed_document_count: 0,
              pending_index_count: 0,
              expected_serving_generation: 18,
              observed_serving_generation: 18,
              current_release_id: null,
              current_certification_id: null,
              stage_count: 5,
              ready_stage_count: 5,
              blocked_stage_count: 0,
              snapshot_digest: digest,
              as_of: now,
              created_at: now,
              created_by: "account-a",
            },
          ],
          count: 1,
          next_cursor: "opaque/next",
          invalid_item_count: 0,
        }),
      )
      .mockResolvedValueOnce(response({ ...page, next_cursor: "stage-next" }))
      .mockResolvedValueOnce(response({ ...page, next_cursor: "event-next" }))
      .mockResolvedValueOnce(
        response({
          snapshot: {
            id: "snapshot-a",
            tenant_id: "tenant-a",
            profile_id: "profile-a",
            policy_revision_id: "policy-a",
            observation_key: "obs-a",
            state: "ready",
            source_count: 1,
            ready_source_count: 1,
            stale_source_count: 0,
            active_document_count: 1,
            failed_document_count: 0,
            pending_index_count: 0,
            expected_serving_generation: 18,
            observed_serving_generation: 18,
            current_release_id: null,
            current_certification_id: null,
            stage_count: 5,
            ready_stage_count: 5,
            blocked_stage_count: 0,
            snapshot_digest: digest,
            as_of: now,
            created_at: now,
            created_by: "account-a",
          },
          stage_facts: [],
          evidence_links: [],
          events: [],
        }),
      );
    await fetchServingProfile(scope);
    await fetchServingSnapshots(scope, { cursor: "opaque/in", limit: 20 });
    await fetchServingStageFacts(scope, { snapshotId: "snapshot/a", stageCode: "source" });
    await fetchServingEvents(scope, { cursor: "event/in" });
    await fetchServingSnapshot(scope, "snapshot/a");
    expect(vi.mocked(fetch).mock.calls.map(([input]) => String(input))).toEqual([
      "https://serving.test/api/enterprise/knowledge-bases/dataset%2Fa/serving/profile",
      "https://serving.test/api/enterprise/knowledge-bases/dataset%2Fa/serving/snapshots?cursor=opaque%2Fin&limit=20",
      "https://serving.test/api/enterprise/knowledge-bases/dataset%2Fa/serving/stage-facts?snapshot_id=snapshot%2Fa&stage_code=source",
      "https://serving.test/api/enterprise/knowledge-bases/dataset%2Fa/serving/events?cursor=event%2Fin",
      "https://serving.test/api/enterprise/knowledge-bases/dataset%2Fa/serving/snapshots/snapshot%2Fa",
    ]);
  });

  it("projects StageFact counters from the list API", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      response({ items: [stage("source", 1)], count: 1, next_cursor: null, invalid_item_count: 0 }),
    );

    const result = await fetchServingStageFacts(scope, { snapshotId: "snapshot-a" });

    expect(result.items[0]).toEqual(
      expect.objectContaining({ ready_count: 1, warning_count: 0, pending_count: 0 }),
    );
  });

  it("accepts a nullable workspace in profile DTOs and create requests", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      response({
        profile: { ...profile, workspace_id: null },
        current_policy: null,
        current_snapshot: null,
      }),
    );
    const projected = await fetchServingProfile(scope);
    expect(projected.profile.workspace_id).toBeNull();

    vi.mocked(fetch).mockResolvedValueOnce(
      response({
        state: "applied",
        operation: "profile_create",
        resource_id: "profile-a",
        revision: 1,
        message: null,
        retryable: false,
      }),
    );
    await createServingProfile(
      scope,
      { name: "知识服务", workspaceId: null, reason: "建立服务可靠性档案" },
      { idempotencyKey: "serving-key-null-workspace" },
    );
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1]?.[1]?.body))).toEqual({
      name: "知识服务",
      workspace_id: null,
      reason: "建立服务可靠性档案",
    });
  });

  it("sends exact revision-fenced profile and policy mutations with a retained idempotency key", async () => {
    vi.mocked(fetch).mockImplementation(async () =>
      response({
        state: "applied",
        operation: "profile_update",
        resource_id: "profile-a",
        revision: 5,
        message: "已保存",
        retryable: false,
      }),
    );
    expect(createServingIdempotencyKey()).toMatch(/^rag4c-serving-/);
    await createServingProfile(
      scope,
      { name: "知识服务", workspaceId: "workspace-a", reason: "建立服务可靠性档案" },
      { idempotencyKey: "serving-key" },
    );
    await createServingPolicyRevision(
      scope,
      {
        expectedRevision: 4,
        expectedPolicyDigest: digest,
        policy: {
          maxSourceStalenessSeconds: 3600,
          maxParseLagSeconds: 7200,
          maxIndexLagSeconds: 7200,
          maxFailedDocumentCount: 2,
          maxPendingIndexCount: 3,
          requireCurrentRelease: true,
          requirePassingCertification: false,
        },
        reason: "调整服务阈值",
      },
      { idempotencyKey: "serving-key" },
    );
    await activateServingProfile(
      scope,
      {
        policyRevisionId: "policy-a",
        expectedRevision: 5,
        expectedPolicyDigest: digest,
        reason: "启用服务策略",
      },
      { idempotencyKey: "serving-key" },
    );
    expect(vi.mocked(fetch).mock.calls.map(([input]) => String(input))).toEqual([
      "https://serving.test/api/enterprise/knowledge-bases/dataset%2Fa/serving/profile",
      "https://serving.test/api/enterprise/knowledge-bases/dataset%2Fa/serving/profile/revisions",
      "https://serving.test/api/enterprise/knowledge-bases/dataset%2Fa/serving/profile/activate",
    ]);
    const [, firstInit] = vi.mocked(fetch).mock.calls[0]!;
    expect(firstInit?.headers).toEqual(
      expect.objectContaining({ "Idempotency-Key": "serving-key" }),
    );
    expect(JSON.parse(String(firstInit?.body))).toEqual({
      name: "知识服务",
      workspace_id: "workspace-a",
      reason: "建立服务可靠性档案",
    });
    const [, secondInit] = vi.mocked(fetch).mock.calls[1]!;
    expect(JSON.parse(String(secondInit?.body))).toEqual({
      expected_profile_revision: 4,
      expected_policy_digest: digest,
      max_source_staleness_seconds: 3600,
      max_parse_lag_seconds: 7200,
      max_index_lag_seconds: 7200,
      max_failed_document_count: 2,
      max_pending_index_count: 3,
      require_current_release: true,
      require_passing_certification: false,
      reason: "调整服务阈值",
    });
  });

  it("keeps policy preview zero-write and strips unsafe response fields", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      response({
        state: "degraded",
        policy_revision: 4,
        stage_facts: [
          stage("source", 1),
          stage("parse", 2),
          stage("chunk", 3),
          stage("index", 4),
          { ...stage("serve", 5), state: "lagging" },
        ],
        blockers: [],
        preview: true,
      }),
    );
    const result = await previewServingPolicy(scope, {
      profileId: "profile-a",
      expectedRevision: 4,
      expectedPolicyDigest: digest,
      policy: {
        maxSourceStalenessSeconds: 30,
        maxParseLagSeconds: 60,
        maxIndexLagSeconds: 60,
        maxFailedDocumentCount: 1,
        maxPendingIndexCount: 1,
        requireCurrentRelease: false,
        requirePassingCertification: false,
      },
      reason: "预览阈值",
    });
    expect(result.preview).toBe(true);
    const [, init] = vi.mocked(fetch).mock.calls[0]!;
    expect(init?.method).toBe("POST");
    expect(init?.headers).not.toHaveProperty("Idempotency-Key");
    expect(JSON.parse(String(init?.body))).toEqual({
      profile_id: "profile-a",
      expected_profile_revision: 4,
      expected_policy_digest: digest,
      max_source_staleness_seconds: 30,
      max_parse_lag_seconds: 60,
      max_index_lag_seconds: 60,
      max_failed_document_count: 1,
      max_pending_index_count: 1,
      require_current_release: false,
      require_passing_certification: false,
      reason: "预览阈值",
    });
    expect(JSON.parse(String(init?.body))).not.toHaveProperty("raw_source_payload");
  });

  it("rejects unsafe integer query values before issuing a request", () => {
    expect(() => fetchServingSnapshots(scope, { limit: Number.MAX_SAFE_INTEGER + 1 })).toThrow(
      /safe integer/,
    );
    expect(vi.mocked(fetch)).not.toHaveBeenCalled();
  });

  it("fails closed before fetch for missing idempotency, unsafe reason, or malformed canonical response", async () => {
    const fetchMock = vi.mocked(fetch);
    expect(() => fetchServingStageFacts(scope, { stageCode: "ready" })).toThrow("allow-listed");
    expect(() =>
      createServingProfile(scope, { name: "知识服务", workspaceId: "workspace-a", reason: "建立" }),
    ).toThrow("Idempotency-Key");
    expect(() =>
      previewServingPolicy(scope, {
        profileId: "profile-a",
        expectedRevision: 4,
        expectedPolicyDigest: digest,
        policy: {
          maxSourceStalenessSeconds: 30,
          maxParseLagSeconds: 60,
          maxIndexLagSeconds: 60,
          maxFailedDocumentCount: 1,
          maxPendingIndexCount: 1,
          requireCurrentRelease: false,
          requirePassingCertification: false,
        },
        reason: "SELECT * FROM documents",
      }),
    ).toThrow("unsafe");
    fetchMock.mockResolvedValueOnce(response({ ...summary, raw_payload: "nope" }));
    await expect(fetchServingSummary(scope)).rejects.toThrow("unexpected field");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
