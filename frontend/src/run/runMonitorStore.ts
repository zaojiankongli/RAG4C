import type {
  BackendRunEvent,
  QueryResponse,
  StreamPhase,
  StreamRunEventDesync,
} from "../types/rag";
import { parseBackendRunEvent } from "../api/runEventValidation";
import {
  projectTypedLocalTerminal,
  projectTypedRunEvent,
  projectTypedRunStarted,
  type FlowRunProjection,
} from "./runProjection";
import {
  appendRunEvent,
  createRunEventBuffer,
  markRunEventBufferPartial,
  type RunEventBufferSnapshot,
} from "./runEventBuffer";

export type MonitoredRunStatus = "running" | "completed" | "failed" | "cancelled";
export type RunExecutionSource = "live_stream" | "rest_fallback" | "demo";

export interface RunPhaseMetadata {
  route?: string;
  chunks?: number;
}

export interface MonitoredRun {
  id: string;
  provisionalId: string;
  query: string;
  startedAt: number;
  finishedAt?: number;
  phase: StreamPhase | string | null;
  phaseStartedAt: number;
  phaseDurations: Record<string, number>;
  seq: number;
  route?: string;
  chunks?: number;
  retrievalAttempt: number;
  source: RunExecutionSource;
  status: MonitoredRunStatus;
  topologySource: "coarse-phase" | "typed-events";
  topologyId?: string;
  topologyRevision?: string;
  syncStatus: "synchronized" | "desynchronized";
  liveTransportDesync: boolean;
  syncWarning?: string;
  eventBuffer?: RunEventBufferSnapshot;
  typedProjection?: FlowRunProjection;
  response?: QueryResponse;
  error?: string;
}

export interface RunMonitorState {
  current: MonitoredRun | null;
}

export const initialRunMonitorState: RunMonitorState = { current: null };

export type RunMonitorAction =
  | { type: "start"; id: string; query: string; startedAt: number; source?: RunExecutionSource }
  | { type: "run-event"; provisionalRunId: string; event: BackendRunEvent }
  | { type: "run-event-invalid"; provisionalRunId: string }
  | {
      type: "run-event-desync";
      provisionalRunId: string;
      marker: StreamRunEventDesync;
    }
  | {
      type: "phase";
      runId: string;
      seq: number;
      phase: StreamPhase | string | null;
      at: number;
      metadata?: RunPhaseMetadata;
    }
  | {
      type: "complete";
      runId: string;
      seq: number;
      response: QueryResponse;
      source: RunExecutionSource;
      finishedAt: number;
    }
  | { type: "cancel"; runId: string; seq: number; finishedAt: number }
  | { type: "fail"; runId: string; seq: number; error: string; finishedAt: number };

function settlePhase(run: MonitoredRun, at: number): Record<string, number> {
  if (!run.phase) return run.phaseDurations;
  const elapsed = Math.max(0, at - run.phaseStartedAt);
  return {
    ...run.phaseDurations,
    [run.phase]: (run.phaseDurations[run.phase] ?? 0) + elapsed,
  };
}

export function runMonitorReducer(
  state: RunMonitorState,
  action: RunMonitorAction,
): RunMonitorState {
  if (action.type === "start") {
    return {
      current: {
        id: action.id,
        provisionalId: action.id,
        query: action.query,
        startedAt: action.startedAt,
        phase: "retrieving",
        phaseStartedAt: action.startedAt,
        phaseDurations: {},
        seq: 0,
        retrievalAttempt: 1,
        source: action.source ?? "live_stream",
        status: "running",
        topologySource: "coarse-phase",
        syncStatus: "synchronized",
        liveTransportDesync: false,
      },
    };
  }
  const current = state.current;
  if (!current) return state;
  if (action.type === "run-event-invalid") {
    if (current.status !== "running" || action.provisionalRunId !== current.provisionalId) {
      return state;
    }
    return {
      current: {
        ...current,
        eventBuffer: current.eventBuffer
          ? markRunEventBufferPartial(current.eventBuffer)
          : undefined,
        syncStatus: "desynchronized",
        liveTransportDesync: true,
        syncWarning: "运行事件未通过脱敏契约校验，已拒绝该事件并停止接收后续运行事件。",
      },
    };
  }
  if (action.type === "run-event-desync") {
    if (
      current.status !== "running" ||
      current.topologySource !== "typed-events" ||
      !current.typedProjection ||
      current.syncStatus === "desynchronized" ||
      action.provisionalRunId !== current.provisionalId ||
      action.marker.run_id !== current.id
    ) {
      return state;
    }
    return {
      current: {
        ...current,
        syncStatus: "desynchronized",
        liveTransportDesync: true,
        syncWarning:
          `运行事件缓冲区溢出：期望序号 ${action.marker.expected_seq}（${action.marker.reason}）。` +
          "流程图可能缺少部分步骤，已停止接收后续运行事件。",
      },
    };
  }
  if (action.type === "run-event") {
    if (current.status !== "running") return state;
    const { event } = action;
    try {
      parseBackendRunEvent(event);
    } catch {
      return {
        current: {
          ...current,
          eventBuffer: current.eventBuffer
            ? markRunEventBufferPartial(current.eventBuffer)
            : undefined,
          syncStatus: "desynchronized",
          liveTransportDesync: true,
          syncWarning: "运行事件未通过脱敏契约校验，已拒绝该事件并停止接收后续运行事件。",
        },
      };
    }
    if (event.type === "run.started") {
      if (
        current.topologySource === "typed-events" ||
        action.provisionalRunId !== current.id ||
        event.seq !== 1
      ) {
        return state;
      }
      const typedProjection = projectTypedRunStarted(current.query, event, current.source);
      if (!typedProjection) return state;
      const eventBuffer = appendRunEvent(createRunEventBuffer(event.run_id), event);
      return {
        current: {
          ...current,
          id: event.run_id,
          phase: null,
          seq: event.seq,
          route: typedProjection.route,
          topologySource: "typed-events",
          topologyId: event.topology_id,
          topologyRevision: event.topology_revision,
          typedProjection,
          eventBuffer,
        },
      };
    }
    if (
      current.topologySource !== "typed-events" ||
      event.run_id !== current.id ||
      !current.typedProjection
    ) {
      return state;
    }
    if (current.syncStatus === "desynchronized" || !current.eventBuffer) return state;
    const eventBuffer = appendRunEvent(current.eventBuffer, event);
    if (
      event.topology_id !== current.topologyId ||
      event.topology_revision !== current.topologyRevision ||
      event.seq <= current.seq
    ) {
      return eventBuffer === current.eventBuffer ? state : { current: { ...current, eventBuffer } };
    }
    if (event.seq !== current.seq + 1) {
      return {
        current: {
          ...current,
          eventBuffer,
          syncStatus: "desynchronized",
          liveTransportDesync: true,
          syncWarning:
            `运行事件不完整：期望序号 ${current.seq + 1}，收到 ${event.seq}。` +
            "流程图可能缺少部分步骤，已停止接收后续运行事件。",
        },
      };
    }
    const typedProjection = projectTypedRunEvent(current.typedProjection, event);
    if (!typedProjection) return state;
    const terminal = event.type.startsWith("run.");
    const status: MonitoredRunStatus =
      typedProjection.status === "failed"
        ? "failed"
        : typedProjection.status === "cancelled"
          ? "cancelled"
          : terminal
            ? "completed"
            : "running";
    const occurredAt = Date.parse(event.occurred_at);
    return {
      current: {
        ...current,
        eventBuffer,
        seq: event.seq,
        route: typedProjection.route,
        status,
        finishedAt:
          terminal && Number.isFinite(occurredAt)
            ? occurredAt
            : terminal
              ? current.startedAt + event.elapsed_ms
              : current.finishedAt,
        error:
          event.type === "run.failed"
            ? (event.error?.code ?? event.error?.type ?? current.error)
            : current.error,
        typedProjection,
      },
    };
  }
  if (current.status !== "running") return state;
  if (current.topologySource === "typed-events") {
    if (
      (action.type !== "complete" && action.type !== "cancel") ||
      (action.runId !== current.id && action.runId !== current.provisionalId) ||
      !current.typedProjection
    ) {
      return state;
    }
    const durationMs = Math.max(0, action.finishedAt - current.startedAt);
    if (action.type === "complete") {
      const degraded =
        action.response.result.abstained || action.response.using_mock || action.source === "demo";
      return {
        current: {
          ...current,
          status: "completed",
          source: action.source,
          response: action.response,
          finishedAt: action.finishedAt,
          typedProjection: projectTypedLocalTerminal(
            current.typedProjection,
            degraded ? "degraded" : "completed",
            action.response.duration_ms || durationMs,
            action.source,
          ),
        },
      };
    }
    return {
      current: {
        ...current,
        status: "cancelled",
        finishedAt: action.finishedAt,
        typedProjection: projectTypedLocalTerminal(
          current.typedProjection,
          "cancelled",
          durationMs,
          current.source,
        ),
      },
    };
  }
  if (action.runId !== current.id || action.seq <= current.seq) return state;

  switch (action.type) {
    case "phase": {
      const phaseChanged = action.phase !== current.phase;
      return {
        current: {
          ...current,
          phase: action.phase,
          phaseStartedAt: phaseChanged ? action.at : current.phaseStartedAt,
          phaseDurations: phaseChanged ? settlePhase(current, action.at) : current.phaseDurations,
          seq: action.seq,
          route: action.metadata?.route ?? current.route,
          chunks: action.metadata?.chunks ?? current.chunks,
          retrievalAttempt:
            action.phase === "retrieving_again"
              ? Math.max(2, current.retrievalAttempt)
              : current.retrievalAttempt,
        },
      };
    }
    case "complete":
      return {
        current: {
          ...current,
          status: "completed",
          source: action.source,
          response: action.response,
          finishedAt: action.finishedAt,
          phaseDurations: settlePhase(current, action.finishedAt),
          seq: action.seq,
        },
      };
    case "cancel":
      return {
        current: {
          ...current,
          status: "cancelled",
          finishedAt: action.finishedAt,
          phaseDurations: settlePhase(current, action.finishedAt),
          seq: action.seq,
        },
      };
    case "fail":
      return {
        current: {
          ...current,
          status: "failed",
          error: action.error,
          finishedAt: action.finishedAt,
          phaseDurations: settlePhase(current, action.finishedAt),
          seq: action.seq,
        },
      };
  }
}
