import { describe, expect, it } from "vitest";
import type { BackendRunEvent } from "../types/rag";
import { appendRunEvent, createRunEventBuffer, mergeRunEvents } from "./runEventBuffer";

function event(
  seq: number,
  type: BackendRunEvent["type"],
  overrides: Partial<BackendRunEvent> = {},
): BackendRunEvent {
  return {
    schema_version: 1,
    run_id: "run-1",
    seq,
    occurred_at: `2026-08-23T00:00:${String(seq).padStart(2, "0")}.000Z`,
    elapsed_ms: seq * 10,
    topology_id: "rag.query",
    topology_revision: "sha256:one",
    type,
    attributes: {},
    ...overrides,
  };
}

describe("runEventBuffer", () => {
  it("deduplicates and advances only the contiguous frontier", () => {
    let buffer = createRunEventBuffer("run-1");
    buffer = appendRunEvent(buffer, event(1, "run.started"));
    buffer = appendRunEvent(buffer, event(3, "node.completed", { node_id: "search", attempt: 1 }));

    expect(buffer.lastContiguousSeq).toBe(1);
    expect(buffer.earliestAvailableSeq).toBe(3);
    expect(buffer.historyState).toBe("partial");

    buffer = appendRunEvent(buffer, event(2, "node.started", { node_id: "search", attempt: 1 }));

    expect(buffer.events.map(({ seq }) => seq)).toEqual([1, 2, 3]);
    expect(buffer.lastContiguousSeq).toBe(3);
    expect(buffer.earliestAvailableSeq).toBe(1);
    expect(buffer.historyState).toBe("complete");
  });

  it("returns the same snapshot for an exact duplicate", () => {
    const first = event(1, "run.started", { attributes: { mode: "stream" } });
    const buffer = appendRunEvent(createRunEventBuffer("run-1"), first);
    const duplicate = appendRunEvent(buffer, {
      ...first,
      attributes: { mode: "stream" },
    });

    expect(duplicate).toBe(buffer);
  });

  it("keeps the first conflicting duplicate and marks history partial", () => {
    const first = event(1, "run.started", { attributes: { mode: "stream" } });
    const buffer = appendRunEvent(createRunEventBuffer("run-1"), first);
    const conflict = appendRunEvent(
      buffer,
      event(1, "run.started", { attributes: { mode: "batch" } }),
    );

    expect(conflict).not.toBe(buffer);
    expect(conflict.events).toHaveLength(1);
    expect(conflict.events[0]?.attributes).toEqual({ mode: "stream" });
    expect(conflict.historyState).toBe("partial");
  });

  it("rejects run or topology mismatches and marks history sticky partial", () => {
    const buffer = appendRunEvent(createRunEventBuffer("run-1"), event(1, "run.started"));

    const wrongRun = appendRunEvent(
      buffer,
      event(2, "node.started", { run_id: "run-2", node_id: "search", attempt: 1 }),
    );
    const wrongTopology = appendRunEvent(
      buffer,
      event(1, "run.started", { topology_revision: "sha256:other" }),
    );

    expect(wrongRun.events).toEqual(buffer.events);
    expect(wrongRun.historyState).toBe("partial");
    expect(wrongTopology.events).toEqual(buffer.events);
    expect(wrongTopology.historyState).toBe("partial");
    expect(appendRunEvent(wrongRun, event(2, "degraded")).historyState).toBe("partial");
  });

  it("keeps run.started plus the newest tail within its bounded capacity", () => {
    let buffer = createRunEventBuffer("run-1", 3);
    for (let seq = 1; seq <= 6; seq += 1) {
      buffer = appendRunEvent(buffer, event(seq, seq === 1 ? "run.started" : "degraded"));
    }

    expect(buffer.events.map(({ seq }) => seq)).toEqual([1, 5, 6]);
    expect(buffer.lastContiguousSeq).toBe(6);
    expect(buffer.earliestAvailableSeq).toBe(5);
    expect(buffer.historyState).toBe("partial");
  });

  it("records a terminal event, accepts missing server fill, and rejects later events", () => {
    let buffer = createRunEventBuffer("run-1");
    buffer = appendRunEvent(buffer, event(1, "run.started"));
    buffer = appendRunEvent(
      buffer,
      event(3, "run.completed", { attributes: { outcome: "answered" } }),
    );

    expect(buffer.terminal).toBe(true);
    const filled = mergeRunEvents(buffer, [
      event(2, "node.completed", { node_id: "search", attempt: 1 }),
    ]);
    const late = appendRunEvent(filled, event(4, "degraded"));

    expect(filled.events.map(({ seq }) => seq)).toEqual([1, 2, 3]);
    expect(filled.lastContiguousSeq).toBe(3);
    expect(filled.terminal).toBe(true);
    expect(late).toBe(filled);
  });

  it("merges server fill events in sequence order without replacing live events", () => {
    const liveSecond = event(2, "node.started", {
      node_id: "search",
      attempt: 1,
      attributes: { source: "live" },
    });
    let buffer = createRunEventBuffer("run-1");
    buffer = mergeRunEvents(buffer, [event(1, "run.started"), liveSecond, event(4, "degraded")]);
    buffer = mergeRunEvents(buffer, [
      event(3, "node.completed", { node_id: "search", attempt: 1 }),
      event(2, "node.started", { node_id: "search", attempt: 1, attributes: { source: "server" } }),
    ]);

    expect(buffer.events.map(({ seq }) => seq)).toEqual([1, 2, 3, 4]);
    expect(buffer.events[1]?.attributes).toEqual({ source: "live" });
    expect(buffer.lastContiguousSeq).toBe(4);
    expect(buffer.historyState).toBe("partial");
  });

  it("stores deeply frozen copies instead of caller-owned event objects", () => {
    const original = event(1, "run.started", {
      attributes: { mode: { nested: [{ id: "search" }] } },
    });
    const buffer = appendRunEvent(createRunEventBuffer("run-1"), original);
    const stored = buffer.events[0];

    expect(stored).not.toBe(original);
    expect(Object.isFrozen(buffer)).toBe(true);
    expect(Object.isFrozen(buffer.events)).toBe(true);
    expect(Object.isFrozen(stored)).toBe(true);
    expect(Object.isFrozen(stored?.attributes)).toBe(true);
    expect(Object.isFrozen(stored?.attributes.topology)).toBe(true);

    original.attributes = { mode: "mutated" };
    expect(stored?.attributes).toEqual({
      mode: { nested: [{ id: "search" }] },
    });
  });
});

it("preserves capacity terminal and gap-healing metadata after ordinary cloning", () => {
  let buffer = createRunEventBuffer("run-1", 3);
  buffer = mergeRunEvents(buffer, [
    event(1, "run.started"),
    event(3, "run.completed", { attributes: { outcome: "answered" } }),
  ]);
  const cloned = structuredClone(buffer);
  const healed = appendRunEvent(cloned, event(2, "degraded"));

  expect(healed.events.map(({ seq }) => seq)).toEqual([1, 2, 3]);
  expect(healed.lastContiguousSeq).toBe(3);
  expect(healed.terminal).toBe(true);
  expect(healed.historyState).toBe("complete");
  const bounded = appendRunEvent(healed, event(4, "degraded"));
  expect(bounded).toBe(healed);
  expect(bounded.internalState.capacity).toBe(3);
});

it("rejects sensitive event attributes and marks the buffer partial", () => {
  const buffer = appendRunEvent(createRunEventBuffer("run-1"), event(1, "run.started"));
  const invalid = appendRunEvent(buffer, event(2, "degraded", { attributes: { query: "SECRET" } }));
  expect(invalid.events).toEqual(buffer.events);
  expect(invalid.historyState).toBe("partial");
  expect(JSON.stringify(invalid)).not.toContain("SECRET");
});

it("stores only the canonical parser result, never extra top-level or error fields", () => {
  let buffer = appendRunEvent(createRunEventBuffer("run-1"), event(1, "run.started"));
  const tainted = {
    ...event(2, "run.failed", {
      error: { type: "RuntimeError", code: "failed", recoverable: false },
    }),
    query: "SECRET_QUERY",
    answer: "SECRET_ANSWER",
    error: {
      type: "RuntimeError",
      code: "failed",
      recoverable: false,
      message: "SECRET_MESSAGE",
    },
  } as BackendRunEvent;
  buffer = appendRunEvent(buffer, tainted);
  expect(buffer.events.map(({ seq }) => seq)).toEqual([1, 2]);
  expect(JSON.stringify(buffer)).not.toMatch(/SECRET_QUERY|SECRET_ANSWER|SECRET_MESSAGE/);
});
