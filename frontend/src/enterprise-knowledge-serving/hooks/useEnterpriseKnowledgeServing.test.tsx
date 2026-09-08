// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useEnterpriseKnowledgeServing } from "./useEnterpriseKnowledgeServing";
import type { ServingApi } from "../api/servingApi";
import type { ServingSummary } from "../model/servingModel";

const scope = {
  tenantId: "tenant-a",
  accountId: "account-a",
  datasetId: "dataset-a",
  actorToken: "actor-token",
};
const digest = "a".repeat(64);
const stage = (code: "source" | "parse" | "chunk" | "index" | "serve", sequence: number) => ({
  id: `${code}-fact`,
  tenant_id: "tenant-a",
  profile_id: "profile-a",
  snapshot_id: "snapshot-a",
  stage_code: code,
  sequence,
  state: "ready" as const,
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
  observed_at: "2026-08-30T00:00:00Z",
});
const summary: ServingSummary = {
  tenant_id: "tenant-a",
  dataset_id: "dataset-a",
  profile_id: "profile-a",
  profile_name: "知识服务",
  profile_status: "active",
  state: "ready",
  as_of: "2026-08-30T00:00:00Z",
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
const snapshot = {
  id: "snapshot-a",
  tenant_id: "tenant-a",
  profile_id: "profile-a",
  policy_revision_id: "policy-a",
  observation_key: "obs-a",
  state: "ready" as const,
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
  as_of: "2026-08-30T00:00:00Z",
  created_at: "2026-08-30T00:00:00Z",
  created_by: "account-a",
};
const snapshotDetail = {
  snapshot,
  stage_facts: summary.stage_facts,
  evidence_links: [],
  events: [],
};

const profile = {
  id: "profile-a",
  tenant_id: "tenant-a",
  workspace_id: "workspace-a",
  dataset_id: "dataset-a",
  name: "知识服务",
  normalized_name: String("知识服务").toLocaleLowerCase(),
  status: "active" as const,
  active_profile_key: "dataset-a",
  revision: 4,
  current_policy_revision_id: "policy-a",
  current_snapshot_id: "snapshot-a",
  created_at: "2026-08-30T00:00:00Z",
  created_by: "account-a",
  updated_at: "2026-08-30T00:00:00Z",
  updated_by: "account-a",
  archived_at: null,
  archived_by: null,
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
function makeApi(overrides: Partial<ServingApi> = {}): ServingApi {
  return {
    fetchSummary: vi.fn().mockResolvedValue(summary),
    fetchProfile: vi
      .fn()
      .mockResolvedValue({ profile, current_policy: null, current_snapshot: null }),
    fetchSnapshots: vi
      .fn()
      .mockResolvedValue({ items: [], count: 0, next_cursor: null, invalid_item_count: 0 }),
    fetchSnapshot: vi
      .fn()
      .mockResolvedValue({ snapshot: null, stage_facts: [], evidence_links: [], events: [] }),
    fetchStageFacts: vi.fn().mockResolvedValue({
      items: summary.stage_facts,
      count: 5,
      next_cursor: null,
      invalid_item_count: 0,
    }),
    fetchEvents: vi
      .fn()
      .mockResolvedValue({ items: [], count: 0, next_cursor: null, invalid_item_count: 0 }),
    createProfile: vi.fn().mockResolvedValue({
      state: "applied",
      operation: "profile_create",
      resource_id: "profile-a",
      revision: 1,
      message: null,
      retryable: false,
    }),
    createPolicyRevision: vi.fn().mockResolvedValue({
      state: "applied",
      operation: "policy_create",
      resource_id: "policy-a",
      revision: 5,
      message: null,
      retryable: false,
    }),
    activateProfile: vi.fn().mockResolvedValue({
      state: "applied",
      operation: "profile_activate",
      resource_id: "profile-a",
      revision: 5,
      message: null,
      retryable: false,
    }),
    previewPolicy: vi.fn().mockResolvedValue({
      preview: true,
      state: "ready",
      policy_revision: 4,
      stage_facts: summary.stage_facts,
      blockers: [],
    }),
    ...overrides,
  };
}
afterEach(cleanup);

describe("Stage26 serving hook", () => {
  it("loads summary and profile in parallel with AbortSignals and exposes ready authority", async () => {
    const api = makeApi();
    const { result } = renderHook(() =>
      useEnterpriseKnowledgeServing(scope, { enabled: true, api }),
    );
    await waitFor(() => expect(result.current.summary.value?.snapshot_id).toBe("snapshot-a"));
    expect(result.current.summary.status).toBe("ready");
    expect(result.current.profile.value?.profile.id).toBe("profile-a");
    expect(vi.mocked(api.fetchSummary).mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(vi.mocked(api.fetchProfile).mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("loads the snapshot list as part of the initial authority load", async () => {
    const api = makeApi({
      fetchSnapshots: vi.fn().mockResolvedValue({
        items: [snapshot],
        count: 1,
        next_cursor: null,
        invalid_item_count: 0,
      }),
    });
    const { result } = renderHook(() =>
      useEnterpriseKnowledgeServing(scope, { enabled: true, api }),
    );

    await waitFor(() => expect(result.current.snapshots.items).toEqual([snapshot]));
    expect(api.fetchSnapshots).toHaveBeenCalledWith(
      scope,
      {},
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(result.current.snapshots.status).toBe("ready");
  });

  it("accepts a configured profile without a snapshot and preserves unavailable snapshot_missing authority", async () => {
    const configuredProfile = {
      ...profile,
      workspace_id: null,
      current_snapshot_id: null,
    } as unknown as typeof profile;
    const snapshotMissingSummary = {
      ...summary,
      state: "unavailable" as const,
      as_of: "2026-08-30T00:00:00Z",
      snapshot_id: null,
      snapshot_digest: null,
      policy_revision: null,
      serving_generation: null,
      source_count: null,
      ready_source_count: null,
      stale_source_count: null,
      active_document_count: null,
      failed_document_count: null,
      pending_index_count: null,
      stage_count: 0,
      ready_stage_count: 0,
      blocked_stage_count: 0,
      stage_facts: [],
      reason_code: "snapshot_missing",
    };
    const api = makeApi({
      fetchSummary: vi.fn().mockResolvedValue(snapshotMissingSummary),
      fetchProfile: vi.fn().mockResolvedValue({
        profile: configuredProfile,
        current_policy: null,
        current_snapshot: null,
      }),
      fetchSnapshots: vi.fn().mockResolvedValue({
        items: [],
        count: 0,
        next_cursor: null,
        invalid_item_count: 0,
      }),
    });
    const { result } = renderHook(() =>
      useEnterpriseKnowledgeServing(scope, { enabled: true, api }),
    );

    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    expect(result.current.profile.value?.profile.workspace_id).toBeNull();
    expect(result.current.summary.value).toMatchObject({
      profile_id: "profile-a",
      state: "unavailable",
      stage_facts: [],
      reason_code: "snapshot_missing",
    });
    expect(result.current.snapshots.items).toEqual([]);
    expect(result.current.snapshots.status).toBe("empty");
    expect(api.fetchSnapshots).toHaveBeenCalledTimes(1);
  });

  it("lazy-loads one snapshot detail and history only on demand", async () => {
    const api = makeApi();
    const { result } = renderHook(() =>
      useEnterpriseKnowledgeServing(scope, { enabled: true, api }),
    );
    await waitFor(() => expect(result.current.summary.status).toBe("ready"));
    expect(api.fetchEvents).not.toHaveBeenCalled();
    await act(async () => {
      await result.current.detail.load("snapshot-a");
      await result.current.activity.load();
    });
    expect(api.fetchSnapshot).toHaveBeenCalledWith(
      scope,
      "snapshot-a",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(api.fetchEvents).toHaveBeenCalledWith(
      scope,
      expect.objectContaining({ snapshotId: "snapshot-a" }),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("aborts and clears old authority state when Dataset context changes", async () => {
    const first = deferred<ServingSummary>();
    const second = deferred<ServingSummary>();
    let summaryCall = 0;
    const api = makeApi({
      fetchSummary: vi.fn(() => (summaryCall++ === 0 ? first.promise : second.promise)),
    });
    const { result, rerender } = renderHook(
      ({ datasetId }) =>
        useEnterpriseKnowledgeServing({ ...scope, datasetId }, { enabled: true, api }),
      { initialProps: { datasetId: "dataset-a" } },
    );
    await waitFor(() => expect(api.fetchSummary).toHaveBeenCalled());
    const signal = vi.mocked(api.fetchSummary).mock.calls[0]?.[1]?.signal;
    rerender({ datasetId: "dataset-b" });
    expect(signal?.aborted).toBe(true);
    expect(result.current.summary.value).toBeNull();
    first.resolve(summary);
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.summary.value).toBeNull();
  });

  it("clears auto-loaded snapshots and detail when the account authority changes", async () => {
    const nextSnapshots = deferred<{
      items: (typeof snapshot)[];
      count: number;
      next_cursor: null;
      invalid_item_count: number;
    }>();
    const api = makeApi({
      fetchSnapshots: vi
        .fn()
        .mockResolvedValueOnce({
          items: [snapshot],
          count: 1,
          next_cursor: null,
          invalid_item_count: 0,
        })
        .mockReturnValueOnce(nextSnapshots.promise),
      fetchSnapshot: vi.fn().mockResolvedValue(snapshotDetail),
    });
    const { result, rerender } = renderHook(
      ({ accountId }) =>
        useEnterpriseKnowledgeServing({ ...scope, accountId }, { enabled: true, api }),
      { initialProps: { accountId: "account-a" } },
    );

    await waitFor(() => expect(result.current.snapshots.items).toEqual([snapshot]));
    await act(async () => {
      await result.current.detail.load("snapshot-a");
    });
    expect(result.current.detail.value?.snapshot.id).toBe("snapshot-a");

    rerender({ accountId: "account-b" });
    await waitFor(() => expect(result.current.snapshots.items).toEqual([]));
    expect(result.current.detail.value).toBeNull();
    expect(result.current.activity.items).toEqual([]);
    expect(api.fetchSnapshots).toHaveBeenCalledTimes(2);

    nextSnapshots.resolve({
      items: [],
      count: 0,
      next_cursor: null,
      invalid_item_count: 0,
    });
    await waitFor(() => expect(result.current.snapshots.status).toBe("empty"));
  });

  it("serializes metadata mutations and retains one idempotency key per operation", async () => {
    const first =
      deferred<ReturnType<ServingApi["createProfile"]> extends Promise<infer T> ? T : never>();
    let inFlight = 0;
    let maxInFlight = 0;
    const api = makeApi({
      createProfile: vi.fn(async (_scope, _input, options) => {
        inFlight++;
        maxInFlight = Math.max(maxInFlight, inFlight);
        expect(options.idempotencyKey).toMatch(/^rag4c-serving-/);
        await first.promise;
        inFlight--;
        return {
          state: "applied",
          operation: "profile_create",
          resource_id: "profile-a",
          revision: 1,
          message: null,
          retryable: false,
        } as const;
      }),
      createPolicyRevision: vi.fn().mockResolvedValue({
        state: "applied",
        operation: "policy_create",
        resource_id: "policy-a",
        revision: 5,
        message: null,
        retryable: false,
      }),
    });
    const { result } = renderHook(() =>
      useEnterpriseKnowledgeServing(scope, { enabled: true, api }),
    );
    await waitFor(() => expect(result.current.summary.status).toBe("ready"));
    let firstCall!: Promise<unknown>;
    let secondCall!: Promise<unknown>;
    await act(async () => {
      firstCall = result.current.mutation.createProfile({
        name: "知识服务",
        workspaceId: "workspace-a",
        reason: "建立",
      });
      secondCall = result.current.mutation.createPolicyRevision({
        expectedRevision: 1,
        expectedPolicyDigest: digest,
        policy: {
          maxSourceStalenessSeconds: 1,
          maxParseLagSeconds: 2,
          maxIndexLagSeconds: 3,
          maxFailedDocumentCount: 1,
          maxPendingIndexCount: 1,
          requireCurrentRelease: false,
          requirePassingCertification: false,
        },
        reason: "调整",
      });
      await Promise.resolve();
    });
    expect(maxInFlight).toBe(1);
    expect(api.createPolicyRevision).not.toHaveBeenCalled();
    first.resolve({
      state: "applied",
      operation: "profile_create",
      resource_id: "profile-a",
      revision: 1,
      message: null,
      retryable: false,
    });
    await act(async () => {
      await Promise.all([firstCall, secondCall]);
    });
    expect(api.createPolicyRevision).toHaveBeenCalledTimes(1);
  });

  it("does not submit mutations in read-only mode", async () => {
    const api = makeApi();
    const { result } = renderHook(() =>
      useEnterpriseKnowledgeServing(scope, { enabled: true, readOnly: true, api }),
    );
    await waitFor(() => expect(result.current.summary.status).toBe("ready"));
    await act(async () => {
      await result.current.mutation.createProfile({
        name: "知识服务",
        workspaceId: "workspace-a",
        reason: "建立",
      });
    });
    expect(api.createProfile).not.toHaveBeenCalled();
    expect(result.current.mutation.outcome).toBeNull();
  });
});
