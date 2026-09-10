import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchConsistencyDeadLetters,
  fetchConsistencySummary,
  requeueConsistencyDeadLetter,
} from "./client";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("knowledge consistency client", () => {
  beforeEach(() => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn((key: string) =>
        key === "rag4c.knowledge_actor_token" ? "actor-token" : null,
      ),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reuses knowledge auth headers for summary and dead-letter reads", async () => {
    let responseIndex = 0;
    const responses = [
      jsonResponse({
        mode: "report-only",
        best_effort: true,
        counts: {},
        drift_categories: {},
        manifest_ref: "ref-manifest",
        complete: false,
        confirmable: false,
        snapshot_guarantee: "catalog_only",
        has_drift: false,
      }),
      jsonResponse({ items: [], count: 0 }),
    ];
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      responses[responseIndex++],
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchConsistencySummary("dataset / A", { tenantId: "tenant-a" });
    await fetchConsistencyDeadLetters("dataset / A", { tenantId: "tenant-a" });

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "http://localhost:8010/api/knowledge-bases/dataset%20%2F%20A/consistency/summary",
      "http://localhost:8010/api/knowledge-bases/dataset%20%2F%20A/consistency/dead-letters",
    ]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(init).toEqual(
        expect.objectContaining({
          method: "GET",
          headers: expect.objectContaining({
            Authorization: "Bearer actor-token",
            "X-RAG4C-Tenant": "tenant-a",
          }),
        }),
      );
    }
  });

  it("posts a scoped dead-letter requeue with an encoded reference", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      jsonResponse(
        {
          status: "enqueued",
          dead_letter_ref: "ref-dead / letter",
          operation_ref: "ref-operation",
        },
        202,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await requeueConsistencyDeadLetter("dataset-a", "ref-dead / letter", {
      tenantId: "tenant-a",
      operatorNote: "lineage checked",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(
      "http://localhost:8010/api/knowledge-bases/dataset-a/consistency/dead-letters/ref-dead%20%2F%20letter/requeue",
    );
    expect(init).toEqual(
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({
          Authorization: "Bearer actor-token",
          "X-RAG4C-Tenant": "tenant-a",
        }),
      }),
    );
    expect(JSON.parse(String(init?.body))).toEqual({ operator_note: "lineage checked" });
  });
  it("fails closed before fetch when authenticated tenant scope is missing", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchConsistencySummary("dataset-a")).rejects.toThrow("tenantId");
    await expect(fetchConsistencyDeadLetters("dataset-a")).rejects.toThrow("tenantId");
    await expect(requeueConsistencyDeadLetter("dataset-a", "ref-dead")).rejects.toThrow(
      "tenantId",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

});
