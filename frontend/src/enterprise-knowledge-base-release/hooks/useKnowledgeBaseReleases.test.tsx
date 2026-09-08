// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useKnowledgeBaseReleases, type ReleaseApi } from "./useKnowledgeBaseReleases";
import type {
  ReleaseAuditPage,
  ReleaseChannelPage,
  ReleaseDetail,
  ReleaseHistoryPage,
  ReleaseImpact,
  ReleaseMutationOutcome,
  ReleaseReadiness,
} from "../model/releaseModel";

const scope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "actor-token" };

const channels: ReleaseChannelPage = {
  items: [
    {
      id: "channel-uat",
      tenant_id: "tenant-a",
      code: "uat",
      name: "UAT",
      status: "active",
      risk_tier: "medium",
      promotion_order: 10,
      is_default_serving: false,
      revision: 2,
      created_at: null,
      updated_at: null,
      summary: null,
    },
  ],
  count: 1,
  next_cursor: null,
  summaries: {},
};
const history: ReleaseHistoryPage = {
  items: [
    {
      id: "release-42",
      tenant_id: "tenant-a",
      dataset_id: "dataset-a",
      release_number: 42,
      status: "candidate",
      profile_revision: 12,
      ownership_revision: 7,
      workspace_revision: 9,
      mutation_generation: 18,
      serving_generation: 3,
      schema_version: 1,
      manifest_digest: "a".repeat(64),
      readiness_state: "ready",
      readiness_fingerprint: "b".repeat(64),
      entry_count: 2,
      revision: 1,
      created_at: "2026-08-28T01:00:00Z",
      created_by: "account-1",
      reason: "候选",
    },
  ],
  count: 1,
  next_cursor: "history-next",
  summary: {
    channel_id: "channel-uat",
    configured: {
      profile_revision: 12,
      mutation_generation: 18,
      ownership_revision: 7,
      workspace_id: "workspace-a",
      workspace_revision: 9,
      policy_digest: null,
    },
    candidate: { release_id: "release-42", release_number: 42, status: "candidate", revision: 1 },
    effective: null,
    serving: null,
    serving_generation: 0,
    comparison_state: "aligned",
    readiness: {
      state: "ready",
      blocker_count: 0,
      blockers: [],
      fingerprint: null,
      reason: null,
    },
    revision: 2,
  },
};
const detail: ReleaseDetail = {
  manifest: history.items[0]!,
  entries: { items: [], count: 0, next_cursor: null },
};
const readiness: ReleaseReadiness = {
  state: "ready",
  blocker_count: 0,
  blockers: [],
  fingerprint: null,
  reason: null,
};
const impact: ReleaseImpact = {
  state: "ready",
  count: 1,
  next_cursor: null,
  items: [{ type: "application", id: "app-1", label: "客服助手", action: "review", reason: null }],
};
const audit: ReleaseAuditPage = {
  items: [
    {
      id: "event-1",
      event: "candidate_created",
      actor: "account-1",
      occurred_at: "2026-08-28T01:00:00Z",
      reason: "候选",
      request_id: "request-1",
    },
  ],
  count: 1,
  next_cursor: null,
};
const applied: ReleaseMutationOutcome = {
  state: "applied",
  operation: "promote",
  resource_id: "release-42",
  approval_request_id: null,
  revision: 3,
  message: "已发布",
  retryable: false,
};

function makeApi(overrides: Partial<ReleaseApi> = {}): ReleaseApi {
  return {
    fetchChannels: vi.fn().mockResolvedValue(channels),
    fetchHistory: vi.fn().mockResolvedValue(history),
    fetchDetail: vi.fn().mockResolvedValue(detail),
    fetchReadiness: vi.fn().mockResolvedValue(readiness),
    fetchImpact: vi.fn().mockResolvedValue(impact),
    fetchAudit: vi.fn().mockResolvedValue(audit),
    capture: vi.fn().mockResolvedValue({ ...applied, operation: "capture" }),
    promote: vi.fn().mockResolvedValue(applied),
    rollback: vi.fn().mockResolvedValue({ ...applied, operation: "rollback" }),
    ...overrides,
  };
}

afterEach(cleanup);

describe("Stage19 Release Center hook", () => {
  it("is inactive-safe and does not request channels while kept alive", () => {
    const api = makeApi();
    const { result } = renderHook(() => useKnowledgeBaseReleases(scope, false, { api }));
    expect(result.current.channels.status).toBe("idle");
    expect(api.fetchChannels).not.toHaveBeenCalled();
  });

  it("loads channels/history, merges opaque cursor pages and loads impact/audit only on demand", async () => {
    const api = makeApi({
      fetchHistory: vi
        .fn()
        .mockResolvedValueOnce(history)
        .mockResolvedValueOnce({
          ...history,
          items: [{ ...history.items[0]!, id: "release-41", release_number: 41 }],
          next_cursor: null,
        }),
    });
    const { result } = renderHook(() => useKnowledgeBaseReleases(scope, true, { api }));
    await waitFor(() => expect(result.current.history.items).toHaveLength(1));
    expect(api.fetchImpact).not.toHaveBeenCalled();
    expect(api.fetchAudit).not.toHaveBeenCalled();
    await act(async () => {
      await result.current.history.loadMore();
      await result.current.detail.loadImpact();
      await result.current.detail.loadAudit();
    });
    expect(result.current.history.items.map((item) => item.id)).toEqual([
      "release-42",
      "release-41",
    ]);
    expect(api.fetchHistory).toHaveBeenCalledTimes(2);
    expect(api.fetchImpact).toHaveBeenCalledTimes(1);
    expect(api.fetchAudit).toHaveBeenCalledTimes(1);
  });

  it("ignores a stale history response after the selected channel changes", async () => {
    let resolveFirst: ((value: ReleaseHistoryPage) => void) | undefined;
    const first = new Promise<ReleaseHistoryPage>((resolve) => {
      resolveFirst = resolve;
    });
    const api = makeApi({
      fetchChannels: vi.fn().mockResolvedValue({
        ...channels,
        items: [
          channels.items[0]!,
          { ...channels.items[0]!, id: "channel-prod", code: "production", name: "生产" },
        ],
      }),
      fetchHistory: vi.fn().mockImplementation((_scope, channelId: string) =>
        channelId === "channel-uat"
          ? first
          : Promise.resolve({
              ...history,
              items: [{ ...history.items[0]!, id: "release-prod" }],
              summary: { ...history.summary!, channel_id: "channel-prod" },
            }),
      ),
    });
    const { result } = renderHook(() => useKnowledgeBaseReleases(scope, true, { api }));
    await waitFor(() => expect(result.current.channels.items).toHaveLength(2));
    await act(async () => result.current.selectChannel("channel-prod"));
    await waitFor(() => expect(result.current.history.items[0]?.id).toBe("release-prod"));
    await act(async () => resolveFirst?.(history));
    expect(result.current.history.items[0]?.id).toBe("release-prod");
  });

  it("reuses the same idempotency key when retrying a transient mutation", async () => {
    const promote = vi
      .fn()
      .mockRejectedValueOnce(new Error("temporary"))
      .mockResolvedValueOnce(applied);
    const api = makeApi({ promote });
    const { result } = renderHook(() => useKnowledgeBaseReleases(scope, true, { api }));
    await waitFor(() => expect(result.current.channels.status).toBe("ready"));
    await act(async () => {
      await result.current.mutation.submit("promote", {
        releaseId: "release-42",
        channelId: "channel-uat",
        expectedChannelRevision: 2,
        expectedProfileRevision: 12,
        expectedOwnershipRevision: 7,
        expectedWorkspaceRevision: 9,
        expectedServingGeneration: 3,
        reason: "发布",
      });
    });
    expect(result.current.mutation.status).toBe("error");
    await act(async () => result.current.mutation.retry());
    expect(promote).toHaveBeenCalledTimes(2);
    expect(promote.mock.calls[0]?.[2]?.idempotencyKey).toBe(
      promote.mock.calls[1]?.[2]?.idempotencyKey,
    );
    expect(result.current.mutation.outcome?.state).toBe("applied");
  });

  it("clears old Dataset authority immediately when the Release scope changes", async () => {
    let resolveTenantB: ((value: ReleaseChannelPage) => void) | undefined;
    const tenantBChannels = new Promise<ReleaseChannelPage>((resolve) => {
      resolveTenantB = resolve;
    });
    const api = makeApi({
      fetchChannels: vi
        .fn()
        .mockImplementation((nextScope) =>
          nextScope.tenantId === "tenant-b" ? tenantBChannels : Promise.resolve(channels),
        ),
    });
    const { result, rerender } = renderHook(
      ({ nextScope }) => useKnowledgeBaseReleases(nextScope, true, { api }),
      { initialProps: { nextScope: scope } },
    );
    await waitFor(() => expect(result.current.channels.status).toBe("ready"));
    expect(result.current.channels.items).toHaveLength(1);

    rerender({
      nextScope: { tenantId: "tenant-b", datasetId: "dataset-b", actorToken: "token-b" },
    });
    await waitFor(() => expect(result.current.channels.status).toBe("loading"));
    expect(result.current.channels.items).toEqual([]);
    expect(result.current.history.items).toEqual([]);
    expect(result.current.detail.value).toBeNull();
    expect(result.current.mutation.retry).toBeTypeOf("function");

    await act(async () => {
      resolveTenantB?.({
        ...channels,
        items: [
          {
            ...channels.items[0]!,
            id: "channel-b",
            tenant_id: "tenant-b",
            summary: null,
          },
        ],
      });
    });
  });
});
