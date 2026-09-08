import { describe, expect, it } from "vitest";
import { parseBackendRunEvent } from "./runEventValidation";

function value(occurred_at: string) {
  return {
    schema_version: 1,
    run_id: "run-1",
    seq: 1,
    occurred_at,
    elapsed_ms: 0,
    topology_id: "rag.query",
    topology_revision: "sha256:test",
    type: "degraded",
    attributes: {},
  };
}

describe("parseBackendRunEvent", () => {
  it.each(["not-a-date", "2026-08-23T12:00:00"])(
    "rejects invalid or timezone-naive occurred_at %s",
    (occurredAt) => {
      expect(() => parseBackendRunEvent(value(occurredAt))).toThrow(/occurred_at/);
    },
  );

  it.each(["2026-08-23T12:00:00Z", "2026-08-23T20:00:00+08:00"])(
    "accepts timezone-aware occurred_at %s",
    (occurredAt) => {
      expect(parseBackendRunEvent(value(occurredAt)).occurred_at).toBe(occurredAt);
    },
  );
});
