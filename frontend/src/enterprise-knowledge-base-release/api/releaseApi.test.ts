// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  captureReleaseCandidate,
  createReleaseIdempotencyKey,
  fetchReleaseChannels,
  fetchReleaseHistory,
  fetchReleaseImpact,
  promoteRelease,
  rollbackRelease,
} from "./releaseApi";

const scope = { tenantId: "tenant-a", datasetId: "dataset-a", actorToken: "actor-token" };

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  localStorage.setItem("rag4c.base_url", "https://release.test");
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(jsonResponse({ items: [] }))),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("Stage19 Release API contract", () => {
  it("uses tenant-authenticated dataset-scoped paths and preserves opaque cursors", async () => {
    vi.mocked(fetch).mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/release-channels")) {
        return Promise.resolve(
          jsonResponse({
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
              },
            ],
            count: 1,
            next_cursor: "opaque-channel-cursor",
          }),
        );
      }
      return Promise.resolve(
        jsonResponse({
          items: [],
          count: 0,
          next_cursor: "opaque-release-cursor",
        }),
      );
    });

    const channels = await fetchReleaseChannels(scope, { limit: 20 });
    const history = await fetchReleaseHistory(scope, "channel-uat", { cursor: "opaque-in" });
    expect(channels.next_cursor).toBe("opaque-channel-cursor");
    expect(history.next_cursor).toBe("opaque-release-cursor");
    expect(vi.mocked(fetch).mock.calls.map(([input]) => String(input))).toEqual([
      "https://release.test/api/enterprise/release-channels?limit=20",
      "https://release.test/api/enterprise/knowledge-bases/dataset-a/releases?channel_id=channel-uat&cursor=opaque-in&limit=50",
    ]);
    expect(vi.mocked(fetch).mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        headers: expect.objectContaining({
          "X-RAG4C-Tenant": "tenant-a",
          Authorization: "Bearer actor-token",
        }),
      }),
    );
  });

  it("sends exact revision-fenced mutation bodies and reuses a caller-owned idempotency key", async () => {
    const calls: Array<{ input: string; init?: RequestInit }> = [];
    vi.mocked(fetch).mockImplementation((input, init) => {
      calls.push({ input: String(input), init });
      return Promise.resolve(
        jsonResponse({ state: "applied", operation: "promote", resource_id: "release-42" }),
      );
    });

    expect(createReleaseIdempotencyKey()).toMatch(/^rag4c-release-/);
    await captureReleaseCandidate(
      scope,
      {
        expectedProfileRevision: 12,
        expectedOwnershipRevision: 7,
        expectedWorkspaceRevision: 9,
        expectedMutationGeneration: 18,
        expectedServingGeneration: 3,
        reason: "  生成候选  ",
      },
      { idempotencyKey: "release-key" },
    );
    await promoteRelease(
      scope,
      "release-42",
      {
        channelId: "channel-uat",
        expectedChannelRevision: 2,
        expectedProfileRevision: 12,
        expectedOwnershipRevision: 7,
        expectedWorkspaceRevision: 9,
        expectedServingGeneration: 3,
        reason: "  推进 UAT  ",
      },
      { idempotencyKey: "release-key" },
    );
    await rollbackRelease(
      scope,
      "channel-uat",
      {
        targetReleaseId: "release-41",
        expectedChannelRevision: 3,
        expectedServingGeneration: 4,
        reason: "回滚验证",
      },
      { idempotencyKey: "release-key" },
    );

    expect(calls.map((call) => call.input)).toEqual([
      "https://release.test/api/enterprise/knowledge-bases/dataset-a/releases",
      "https://release.test/api/enterprise/knowledge-bases/dataset-a/releases/release-42/promote",
      "https://release.test/api/enterprise/knowledge-bases/dataset-a/channels/channel-uat/rollback",
    ]);
    expect(JSON.parse(String(calls[0]?.init?.body))).toEqual({
      expected_profile_revision: 12,
      expected_ownership_revision: 7,
      expected_workspace_revision: 9,
      expected_mutation_generation: 18,
      expected_serving_generation: 3,
      reason: "生成候选",
    });
    expect(JSON.parse(String(calls[1]?.init?.body))).toEqual({
      channel_id: "channel-uat",
      expected_channel_revision: 2,
      expected_profile_revision: 12,
      expected_ownership_revision: 7,
      expected_workspace_revision: 9,
      expected_serving_generation: 3,
      reason: "推进 UAT",
    });
    expect(JSON.parse(String(calls[2]?.init?.body))).toEqual({
      target_release_id: "release-41",
      expected_channel_revision: 3,
      expected_serving_generation: 4,
      reason: "回滚验证",
    });
    expect(
      calls.every(
        (call) =>
          call.init?.headers &&
          (call.init.headers as Record<string, string>)["Idempotency-Key"] === "release-key",
      ),
    ).toBe(true);
  });

  it("loads impact through its dedicated lazy endpoint", async () => {
    vi.mocked(fetch).mockResolvedValue(
      jsonResponse({
        state: "ready",
        items: [{ type: "application", id: "app-1", label: "客服助手", action: "review" }],
        count: 1,
        next_cursor: null,
      }),
    );
    const result = await fetchReleaseImpact(scope, "release-42");
    expect(result.items[0]?.id).toBe("app-1");
    expect(String(vi.mocked(fetch).mock.calls[0]?.[0])).toBe(
      "https://release.test/api/enterprise/knowledge-bases/dataset-a/releases/release-42/impact?limit=50",
    );
  });
});
