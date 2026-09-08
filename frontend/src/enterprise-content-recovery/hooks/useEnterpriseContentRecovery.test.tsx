// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  useEnterpriseContentRecovery,
  type EnterpriseContentRecoveryApi,
} from "./useEnterpriseContentRecovery";
import type {
  ContentRetentionPolicy,
  LegalHold,
  PurgeRequest,
  RecoveryEntry,
  RecoveryEntryDetail,
  RecoveryMutationOutcome,
  RecoveryPage,
  RecoverySummary,
} from "../model/recoveryModel";
import type { RecoveryApiScope } from "../api/recoveryApi";

const timestamp = "2026-08-29T12:34:56.000000Z";
const scope: RecoveryApiScope = { tenantId: "tenant-a", actorToken: "actor-token" };
const scopeB: RecoveryApiScope = { tenantId: "tenant-b", actorToken: "actor-token-b" };

function summary(tenantId = "tenant-a"): RecoverySummary {
  return {
    tenant_id: tenantId,
    state: "ready",
    recycled_count: 1,
    expiring_count: 0,
    held_count: 0,
    pending_purge_count: 0,
    as_of: timestamp,
    reason_code: null,
  };
}
function entry(id = "entry-a", tenantId = "tenant-a"): RecoveryEntry {
  return {
    id,
    tenant_id: tenantId,
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
    safe_snapshot: { dataset_id: "dataset-a", document_id: "document-a" },
    snapshot_digest: "a".repeat(64),
    created_at: timestamp,
    updated_at: timestamp,
  };
}
function outcome(operation = "restore"): RecoveryMutationOutcome {
  return {
    state: "applied",
    operation,
    resource_id: "entry-a",
    approval_request_id: null,
    route: null,
    revision: 4,
    message: "完成",
    retryable: false,
  };
}
function page<T>(items: T[], invalid = 0): RecoveryPage<T> {
  return { items, next_cursor: null, invalid_item_count: invalid };
}

function apiFixture(
  overrides: Partial<EnterpriseContentRecoveryApi> = {},
): EnterpriseContentRecoveryApi {
  return {
    fetchSummary: vi.fn(async (requestScope) => summary(requestScope.tenantId)),
    fetchEntries: vi.fn(async () => page([entry()])),
    fetchDetail: vi.fn(async () => ({ entry: entry(), events: [] }) as RecoveryEntryDetail),
    fetchHolds: vi.fn(async () => page<LegalHold>([])),
    fetchPurgeRequests: vi.fn(async () => page<PurgeRequest>([])),
    fetchPolicy: vi.fn(
      async () =>
        ({
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
        }) as ContentRetentionPolicy,
    ),
    recycle: vi.fn(async () => outcome("recycle")),
    restore: vi.fn(async () => outcome("restore")),
    applyHold: vi.fn(async () => outcome("apply_hold")),
    releaseHold: vi.fn(async () => outcome("release_hold")),
    requestPurge: vi.fn(
      async () =>
        ({
          ...outcome("request_purge"),
          state: "approval_required",
          approval_request_id: "approval-a",
        }) as RecoveryMutationOutcome,
    ),
    cancelPurge: vi.fn(async () => outcome("cancel_purge")),
    updatePolicy: vi.fn(async () => outcome("update_policy")),
    ...overrides,
  };
}

afterEach(() => cleanup());

describe("useEnterpriseContentRecovery", () => {
  it("is inactive-safe and performs no reads or mutations when disabled", async () => {
    const api = apiFixture();
    const { result } = renderHook(() =>
      useEnterpriseContentRecovery(scope, { enabled: false, api }),
    );
    expect(result.current.active).toBe(false);
    expect(result.current.load.status).toBe("idle");
    expect(result.current.entries.status).toBe("idle");
    await act(async () => {
      expect(
        await result.current.mutation.restore("entry-a", { expectedRevision: 3, reason: "x" }),
      ).toBeNull();
      expect(await result.current.detail.load("entry-a")).toBe(false);
    });
    expect(api.fetchSummary).not.toHaveBeenCalled();
    expect(api.restore).not.toHaveBeenCalled();
  });

  it("starts summary and entries reads in parallel and exposes exact ready state", async () => {
    let resolveSummary!: (value: RecoverySummary) => void;
    let resolveEntries!: (value: RecoveryPage<RecoveryEntry>) => void;
    const api = apiFixture({
      fetchSummary: vi.fn(
        () =>
          new Promise<RecoverySummary>((resolve) => {
            resolveSummary = resolve;
          }),
      ),
      fetchEntries: vi.fn(
        () =>
          new Promise<RecoveryPage<RecoveryEntry>>((resolve) => {
            resolveEntries = resolve;
          }),
      ),
    });
    const { result } = renderHook(() =>
      useEnterpriseContentRecovery(scope, { enabled: true, api }),
    );
    await waitFor(() => expect(api.fetchSummary).toHaveBeenCalled());
    expect(api.fetchEntries).toHaveBeenCalled();
    expect(api.fetchSummary).toHaveBeenCalledBefore(api.fetchEntries as ReturnType<typeof vi.fn>);
    await act(async () => {
      resolveSummary(summary());
      resolveEntries(page([entry()]));
    });
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    expect(result.current.summary.value?.recycled_count).toBe(1);
    expect(result.current.entries.items).toHaveLength(1);
  });

  it("keeps detail, holds, purge requests and policy lazy until explicitly loaded", async () => {
    const api = apiFixture();
    const { result } = renderHook(() =>
      useEnterpriseContentRecovery(scope, { enabled: true, api }),
    );
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    expect(api.fetchDetail).not.toHaveBeenCalled();
    expect(api.fetchHolds).not.toHaveBeenCalled();
    expect(api.fetchPurgeRequests).not.toHaveBeenCalled();
    expect(api.fetchPolicy).not.toHaveBeenCalled();
    await act(async () => {
      await result.current.detail.load("entry-a");
      await result.current.holds.load("entry-a");
      await result.current.purgeRequests.load("entry-a");
      await result.current.policy.load();
    });
    expect(api.fetchDetail).toHaveBeenCalledTimes(1);
    expect(api.fetchHolds).toHaveBeenCalledTimes(1);
    expect(api.fetchPurgeRequests).toHaveBeenCalledTimes(1);
    expect(api.fetchPolicy).toHaveBeenCalledTimes(1);
    expect(result.current.detail.value?.entry.id).toBe("entry-a");
  });

  it("ignores stale responses and aborts the old context after a scope change", async () => {
    let resolveOld!: (value: RecoverySummary) => void;
    const api = apiFixture({
      fetchSummary: vi.fn((requestScope) =>
        requestScope.tenantId === "tenant-a"
          ? new Promise<RecoverySummary>((resolve) => {
              resolveOld = resolve;
            })
          : Promise.resolve(summary("tenant-b")),
      ),
      fetchEntries: vi.fn(async (requestScope) => page([entry("entry-b", requestScope.tenantId)])),
    });
    const { result, rerender } = renderHook(
      ({ currentScope }) => useEnterpriseContentRecovery(currentScope, { enabled: true, api }),
      { initialProps: { currentScope: scope } },
    );
    await waitFor(() => expect(api.fetchSummary).toHaveBeenCalledTimes(1));
    rerender({ currentScope: scopeB });
    await waitFor(() => expect(result.current.summary.value?.tenant_id).toBe("tenant-b"));
    await act(async () => resolveOld(summary("tenant-a")));
    expect(result.current.summary.value?.tenant_id).toBe("tenant-b");
  });

  it("serializes mutations, preserves idempotency for retry and blocks read-only writes", async () => {
    let releaseFirst!: () => void;
    const firstGate = new Promise<void>((resolve) => {
      releaseFirst = resolve;
    });
    const restore = vi
      .fn()
      .mockImplementationOnce(
        async (
          _scope: RecoveryApiScope,
          _entryId: string,
          _input: unknown,
          _options: { idempotencyKey?: string },
        ) => {
          await firstGate;
          return outcome();
        },
      )
      .mockRejectedValueOnce(new Error("temporary recovery unavailable"))
      .mockResolvedValueOnce(outcome());
    const api = apiFixture({ restore });
    const { result } = renderHook(() =>
      useEnterpriseContentRecovery(scope, { enabled: true, api }),
    );
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    let first!: Promise<RecoveryMutationOutcome | null>;
    let second!: Promise<RecoveryMutationOutcome | null>;
    await act(async () => {
      first = result.current.mutation.restore(
        "entry-a",
        { expectedRevision: 3, reason: "first" },
        { idempotencyKey: "same-key" },
      );
      second = result.current.mutation.restore(
        "entry-a",
        { expectedRevision: 3, reason: "second" },
        { idempotencyKey: "second-key" },
      );
    });
    expect(restore).toHaveBeenCalledTimes(1);
    await act(async () => releaseFirst());
    await expect(first).resolves.toMatchObject({ state: "applied" });
    await expect(second).rejects.toThrow(/unavailable/i);
    await waitFor(() => expect(result.current.mutation.status).toBe("error"));
    await act(async () => {
      await result.current.mutation.retry();
    });
    expect(restore).toHaveBeenCalledTimes(3);
    expect(restore.mock.calls[1]?.[3]).toEqual(
      expect.objectContaining({ idempotencyKey: "second-key" }),
    );
    expect(restore.mock.calls[2]?.[3]).toEqual(
      expect.objectContaining({ idempotencyKey: "second-key" }),
    );

    const readOnlyApi = apiFixture();
    const readOnly = renderHook(() =>
      useEnterpriseContentRecovery(scope, { enabled: true, readOnly: true, api: readOnlyApi }),
    );
    await waitFor(() => expect(readOnly.result.current.load.status).toBe("ready"));
    await act(async () => {
      await expect(
        readOnly.result.current.mutation.restore("entry-a", { expectedRevision: 3, reason: "x" }),
      ).resolves.toBeNull();
    });
    expect(readOnlyApi.restore).not.toHaveBeenCalled();
    readOnly.unmount();
  });

  it("reloads when the injected API context changes and exposes the policy alias", async () => {
    const firstApi = apiFixture();
    const secondApi = apiFixture();
    const { result, rerender } = renderHook(
      ({ currentApi }) => useEnterpriseContentRecovery(scope, { enabled: true, api: currentApi }),
      { initialProps: { currentApi: firstApi } },
    );
    await waitFor(() => expect(result.current.load.status).toBe("ready"));
    rerender({ currentApi: secondApi });
    await waitFor(() => expect(secondApi.fetchSummary).toHaveBeenCalled());
    expect(result.current.retentionPolicy).toStrictEqual(result.current.policy);
  });

  it("reports partial authority when one parallel read fails but the other is useful", async () => {
    const api = apiFixture({
      fetchSummary: vi.fn(async () => {
        throw new Error("summary failed");
      }),
    });
    const { result } = renderHook(() =>
      useEnterpriseContentRecovery(scope, { enabled: true, api }),
    );
    await waitFor(() => expect(result.current.load.status).toBe("partial"));
    expect(result.current.entries.items).toHaveLength(1);
    expect(result.current.summary.status).toBe("error");
  });
});
