import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import type {
  BackendRunEvent,
  QueryResponse,
  StreamPhase,
  StreamRunEventDesync,
} from "../types/rag";
import {
  initialRunMonitorState,
  runMonitorReducer,
  type RunExecutionSource,
  type RunMonitorState,
  type RunPhaseMetadata,
} from "./runMonitorStore";
import type { RunEventBufferSnapshot } from "./runEventBuffer";
import { synchronizeEventSequence, type RunEventSequenceState } from "./runMonitorSequence";

interface RunMonitorValue extends RunMonitorState {
  readonly eventBuffer: RunEventBufferSnapshot | null;
  startRun: (query: string, source?: RunExecutionSource) => string;
  applyRunEvent: (provisionalRunId: string, event: BackendRunEvent) => void;
  applyRunEventDesync: (provisionalRunId: string, marker: StreamRunEventDesync) => void;
  markRunEventInvalid: (provisionalRunId: string) => void;
  setRunPhase: (
    runId: string,
    phase: StreamPhase | string | null,
    metadata?: RunPhaseMetadata,
  ) => void;
  completeRun: (
    runId: string,
    response: QueryResponse,
    source: RunExecutionSource,
  ) => number | null;
  cancelRun: (runId: string) => void;
  failRun: (runId: string, error: string) => void;
}

const RunMonitorContext = createContext<RunMonitorValue | null>(null);

export function RunMonitorProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(runMonitorReducer, initialRunMonitorState);
  const runSequence = useRef(0);
  const currentRunId = state.current?.id ?? "";
  const currentSeq = state.current?.seq ?? 0;
  const currentTyped = state.current?.topologySource === "typed-events";
  const eventSequence = useRef<RunEventSequenceState>({
    provisionalRunId: "",
    runId: "",
    seq: 0,
    typed: false,
  });

  useEffect(() => {
    if (!currentRunId) return;
    eventSequence.current = synchronizeEventSequence(
      eventSequence.current,
      currentRunId,
      currentSeq,
      currentTyped,
    );
  }, [currentRunId, currentSeq, currentTyped]);

  const nextSequence = useCallback((runId: string, allowTyped = false): number | null => {
    if (
      (eventSequence.current.typed && !allowTyped) ||
      (eventSequence.current.runId !== runId && eventSequence.current.provisionalRunId !== runId)
    ) {
      return null;
    }
    eventSequence.current.seq += 1;
    return eventSequence.current.seq;
  }, []);

  const startRun = useCallback((query: string, source: RunExecutionSource = "live_stream") => {
    const id = `run-${Date.now()}-${++runSequence.current}`;
    eventSequence.current = { provisionalRunId: id, runId: id, seq: 0, typed: false };
    dispatch({ type: "start", id, query, source, startedAt: Date.now() });
    return id;
  }, []);
  const applyRunEvent = useCallback((provisionalRunId: string, event: BackendRunEvent) => {
    if (eventSequence.current.provisionalRunId !== provisionalRunId) return;
    dispatch({ type: "run-event", provisionalRunId, event });
  }, []);
  const applyRunEventDesync = useCallback(
    (provisionalRunId: string, marker: StreamRunEventDesync) => {
      if (eventSequence.current.provisionalRunId !== provisionalRunId) return;
      dispatch({ type: "run-event-desync", provisionalRunId, marker });
    },
    [],
  );
  const markRunEventInvalid = useCallback((provisionalRunId: string) => {
    if (eventSequence.current.provisionalRunId !== provisionalRunId) return;
    dispatch({ type: "run-event-invalid", provisionalRunId });
  }, []);
  const setRunPhase = useCallback(
    (runId: string, phase: StreamPhase | string | null, metadata?: RunPhaseMetadata) => {
      const seq = nextSequence(runId);
      if (seq === null) return;
      dispatch({ type: "phase", runId, seq, phase, metadata, at: Date.now() });
    },
    [nextSequence],
  );
  const completeRun = useCallback(
    (runId: string, response: QueryResponse, source: RunExecutionSource) => {
      const seq = nextSequence(runId, true);
      if (seq === null) return null;
      dispatch({ type: "complete", runId, seq, response, source, finishedAt: Date.now() });
      return seq;
    },
    [nextSequence],
  );
  const cancelRun = useCallback(
    (runId: string) => {
      const seq = nextSequence(runId, true);
      if (seq === null) return;
      dispatch({ type: "cancel", runId, seq, finishedAt: Date.now() });
    },
    [nextSequence],
  );
  const failRun = useCallback(
    (runId: string, error: string) => {
      const seq = nextSequence(runId);
      if (seq === null) return;
      dispatch({ type: "fail", runId, seq, error, finishedAt: Date.now() });
    },
    [nextSequence],
  );

  const value = useMemo<RunMonitorValue>(
    () => ({
      ...state,
      eventBuffer: state.current?.eventBuffer ?? null,
      startRun,
      applyRunEvent,
      applyRunEventDesync,
      setRunPhase,
      completeRun,
      cancelRun,
      markRunEventInvalid,
      failRun,
    }),
    [
      applyRunEvent,
      applyRunEventDesync,
      cancelRun,
      completeRun,
      failRun,
      setRunPhase,
      markRunEventInvalid,
      startRun,
      state,
    ],
  );

  return <RunMonitorContext.Provider value={value}>{children}</RunMonitorContext.Provider>;
}

export function useRunMonitor(): RunMonitorValue {
  const value = useContext(RunMonitorContext);
  if (!value) throw new Error("useRunMonitor 必须在 RunMonitorProvider 内使用");
  return value;
}
