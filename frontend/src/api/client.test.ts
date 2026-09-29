import { afterEach, describe, expect, it, vi } from "vitest";
import type { BackendRunEvent, QueryResponse } from "../types/rag";
import { graphSearch, graphSubgraph, request, streamAnswer } from "./client";

const response: QueryResponse = {
  result: {
    query: "问题",
    answer: "答案",
    citations: [],
    verdict: {},
    abstained: false,
    route: "hybrid",
    traces: [],
  },
  using_mock: false,
  duration_ms: 20,
};

function runEvent(
  type: BackendRunEvent["type"],
  seq: number,
  overrides: Partial<BackendRunEvent> = {},
): BackendRunEvent {
  return {
    schema_version: 1,
    run_id: "run-backend",
    seq,
    occurred_at: "2026-08-23T00:00:00Z",
    elapsed_ms: seq * 10,
    topology_id: "rag.query",
    topology_revision: "sha256:test",
    type,
    attributes: {},
    ...overrides,
  };
}

function sseResponse(frames: unknown[]): Response {
  const body = frames.map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join("");
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("streamAnswer", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("delivers typed run events separately while preserving legacy callbacks and final result", async () => {
    const started = runEvent("run.started", 1);
    const searchStarted = runEvent("node.started", 2, { node_id: "search", attempt: 1 });
    const desyncMarker = {
      type: "run_event_desync",
      run_id: "run-backend",
      expected_seq: 3,
      reason: "typed_buffer_overflow",
    } as const;
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          { type: "run_event", event: started },
          { type: "phase", phase: "retrieving" },
          { type: "run_event", event: searchStarted },
          desyncMarker,
          { type: "token", text: "答案" },
          { type: "done", result: response },
        ]),
      ),
    );
    const onRunEvent = vi.fn();
    const onRunEventDesync = vi.fn();
    const onPhase = vi.fn();
    const onToken = vi.fn();

    const result = await streamAnswer(
      { query: "问题" },
      { onRunEvent, onRunEventDesync, onPhase, onToken },
    );

    expect(onRunEvent.mock.calls).toEqual([[started], [searchStarted]]);
    expect(onRunEventDesync).toHaveBeenCalledWith(desyncMarker);
    expect(onPhase).toHaveBeenCalledWith("retrieving", expect.anything());
    expect(onToken).toHaveBeenCalledWith("答案");
    expect(result).toEqual(response);
  });

  it("rejects sensitive run events while preserving tokens and the final result", async () => {
    const invalid = runEvent("degraded", 2, { attributes: { prompt: "SECRET_PROMPT" } });
    vi.stubGlobal("localStorage", { getItem: vi.fn(() => null) });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          { type: "run_event", event: invalid },
          { type: "token", text: "答案" },
          { type: "done", result: response },
        ]),
      ),
    );
    const onRunEvent = vi.fn();
    const onRunEventInvalid = vi.fn();
    const onToken = vi.fn();

    const result = await streamAnswer(
      { query: "问题" },
      { onRunEvent, onRunEventInvalid, onToken },
    );

    expect(onRunEvent).not.toHaveBeenCalled();
    expect(onRunEventInvalid).toHaveBeenCalledTimes(1);
    expect(onToken).toHaveBeenCalledWith("答案");
    expect(result).toEqual(response);
  });
});

it.each(["not-a-date", "2026-08-23T12:00:00"])(
  "rejects run events with invalid occurred_at %s without breaking done",
  async (occurredAt) => {
    vi.stubGlobal("localStorage", { getItem: vi.fn(() => null) });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        sseResponse([
          { type: "run_event", event: runEvent("degraded", 2, { occurred_at: occurredAt }) },
          { type: "done", result: response },
        ]),
      ),
    );
    const onRunEvent = vi.fn();
    const onRunEventInvalid = vi.fn();
    await expect(
      streamAnswer({ query: "问题" }, { onRunEvent, onRunEventInvalid }),
    ).resolves.toEqual(response);
    expect(onRunEvent).not.toHaveBeenCalled();
    expect(onRunEventInvalid).toHaveBeenCalledTimes(1);
  },
);


it("returns undefined for an authenticated 204 response instead of parsing an empty JSON body", async () => {
  vi.stubGlobal("localStorage", { getItem: vi.fn(() => null) });
  vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));

  await expect(
    request<void>("/api/knowledge-bases/dataset/qa/qa/alternatives/alt", {
      method: "DELETE",
    }),
  ).resolves.toBeUndefined();
});

it("sends the workspace tenant header for graph visualization reads", async () => {
  vi.stubGlobal("localStorage", {
    getItem: vi.fn(() => null),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
  const fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify({ entities: [], relations: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await graphSearch("query");
  await graphSubgraph({ entity_ids: ["entity-a"] });

  expect(fetchMock).toHaveBeenCalledTimes(2);
  for (const [, init] of fetchMock.mock.calls as unknown as Array<[string, RequestInit?]>) {
    expect(init?.headers).toMatchObject({ "X-RAG4C-Tenant": "default" });
  }
});
