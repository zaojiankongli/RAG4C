import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { describe, expect, it, vi } from "vitest";
import { RunMonitorProvider, useRunMonitor } from "./RunMonitorContext";
import type { BackendRunEvent, QueryResponse } from "../types/rag";
import { synchronizeEventSequence, type RunEventSequenceState } from "./runMonitorSequence";

type MonitorValue = ReturnType<typeof useRunMonitor>;

function Probe({ capture }: { capture: (value: MonitorValue) => void }) {
  capture(useRunMonitor());
  return null;
}

describe("RunMonitorProvider", () => {
  it("keeps generated run sequences local to each mounted provider", () => {
    vi.spyOn(Date, "now").mockReturnValue(1000);
    let first: MonitorValue | undefined;
    let second: MonitorValue | undefined;
    let renderer: ReactTestRenderer | undefined;
    act(() => {
      renderer = create(
        <>
          <RunMonitorProvider>
            <Probe
              capture={(value) => {
                first = value;
              }}
            />
          </RunMonitorProvider>
          <RunMonitorProvider>
            <Probe
              capture={(value) => {
                second = value;
              }}
            />
          </RunMonitorProvider>
        </>,
      );
    });

    act(() => {
      expect(first?.startRun("one")).toBe("run-1000-1");
      expect(second?.startRun("two")).toBe("run-1000-1");
      expect(first?.startRun("three")).toBe("run-1000-2");
      expect(second?.startRun("four")).toBe("run-1000-2");
    });
    act(() => renderer?.unmount());
  });

  it("synchronizes the sequence ref when a run switches to typed backend identity", () => {
    const previous: RunEventSequenceState = {
      provisionalRunId: "local-1",
      runId: "local-1",
      seq: 0,
      typed: false,
    };
    expect(synchronizeEventSequence(previous, "backend-1", 7, true)).toEqual({
      provisionalRunId: "local-1",
      runId: "backend-1",
      seq: 7,
      typed: true,
    });
  });
  it("mounts, synchronizes a typed backend sequence, and allocates the next local sequence", () => {
    let monitor: MonitorValue | undefined;
    let renderer: ReactTestRenderer | undefined;
    act(() => {
      renderer = create(
        <RunMonitorProvider>
          <Probe
            capture={(value) => {
              monitor = value;
            }}
          />
        </RunMonitorProvider>,
      );
    });
    let provisional = "";
    act(() => {
      provisional = monitor!.startRun("question");
    });
    const started: BackendRunEvent = {
      schema_version: 1,
      run_id: "backend-1",
      seq: 1,
      occurred_at: "2026-08-23T12:00:00Z",
      elapsed_ms: 0,
      topology_id: "rag.query",
      topology_revision: "sha256:test",
      type: "run.started",
      attributes: {
        topology: {
          id: "rag.query",
          revision: "sha256:test",
          executor: "sequential_stream",
          nodes: [
            {
              id: "receive",
              label: "Receive",
              group: "input",
              description: "Receive",
              optional: false,
              repeatable: false,
              available: true,
              attributes: {},
            },
          ],
          edges: [],
        },
      },
    };
    act(() => {
      monitor!.applyRunEvent(provisional, started);
    });
    for (let seq = 2; seq <= 7; seq += 1) {
      act(() => {
        monitor!.applyRunEvent(provisional, {
          ...started,
          seq,
          occurred_at: `2026-08-23T12:00:0${seq}Z`,
          elapsed_ms: seq,
          type: "degraded",
          attributes: {},
          node_id: "receive",
          attempt: 1,
        });
      });
    }
    expect(monitor?.current).toEqual(expect.objectContaining({ id: "backend-1", seq: 7 }));

    const response: QueryResponse = {
      result: {
        query: "question",
        answer: "answer",
        citations: [],
        verdict: {},
        abstained: false,
        route: "hybrid",
        traces: [],
      },
      using_mock: false,
      duration_ms: 10,
    };
    let nextSeq: number | null = null;
    act(() => {
      nextSeq = monitor!.completeRun("backend-1", response, "rest_fallback");
    });

    expect(nextSeq).toBe(8);
    expect(monitor?.current?.status).toBe("completed");
    act(() => renderer?.unmount());
  });
});
