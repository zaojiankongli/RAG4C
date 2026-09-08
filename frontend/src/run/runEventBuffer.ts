import type { BackendRunEvent } from "../types/rag";
import { parseBackendRunEvent } from "../api/runEventValidation";

export interface RunEventBufferInternalState {
  readonly capacity: number;
  readonly integrityPartial: boolean;
  readonly truncatedFloor: number;
  readonly terminalSeq?: number;
}

export interface RunEventBufferSnapshot {
  readonly runId: string;
  readonly events: readonly BackendRunEvent[];
  readonly lastContiguousSeq: number;
  readonly earliestAvailableSeq: number;
  readonly historyState: "complete" | "partial";
  readonly topologyId?: string;
  readonly topologyRevision?: string;
  readonly terminal: boolean;
  readonly internalState: RunEventBufferInternalState;
}

type BufferMetadata = RunEventBufferInternalState;
const DEFAULT_MAX_EVENTS = 1000;
const TERMINAL_EVENT_TYPES = new Set<BackendRunEvent["type"]>([
  "run.completed",
  "run.failed",
  "run.cancelled",
]);

function normalizedCapacity(maxEvents: number): number {
  if (!Number.isFinite(maxEvents)) return DEFAULT_MAX_EVENTS;
  return Math.max(2, Math.floor(maxEvents));
}

function metadataOf(snapshot: RunEventBufferSnapshot): BufferMetadata {
  return (
    snapshot.internalState ?? {
      capacity: DEFAULT_MAX_EVENTS,
      integrityPartial: snapshot.historyState === "partial",
      truncatedFloor: 1,
    }
  );
}

function frozenCopy(value: unknown): unknown {
  if (Array.isArray(value)) {
    return Object.freeze(value.map((item) => frozenCopy(item)));
  }
  if (value !== null && typeof value === "object") {
    const copy: Record<string, unknown> = {};
    for (const [key, nested] of Object.entries(value)) {
      copy[key] = frozenCopy(nested);
    }
    return Object.freeze(copy);
  }
  return value;
}

function copyEvent(event: BackendRunEvent): BackendRunEvent {
  return frozenCopy(event) as BackendRunEvent;
}

function valuesEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) {
      return false;
    }
    return left.every((item, index) => valuesEqual(item, right[index]));
  }
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") {
    return false;
  }
  const leftRecord = left as Record<string, unknown>;
  const rightRecord = right as Record<string, unknown>;
  const leftKeys = Object.keys(leftRecord);
  const rightKeys = Object.keys(rightRecord);
  if (leftKeys.length !== rightKeys.length) return false;
  return leftKeys.every(
    (key) =>
      Object.prototype.hasOwnProperty.call(rightRecord, key) &&
      valuesEqual(leftRecord[key], rightRecord[key]),
  );
}

function calculateFrontier(
  previousFrontier: number,
  eventsBySeq: ReadonlyMap<number, BackendRunEvent>,
): number {
  let frontier = previousFrontier;
  while (eventsBySeq.has(frontier + 1)) frontier += 1;
  return frontier;
}

function boundedEvents(
  eventsBySeq: ReadonlyMap<number, BackendRunEvent>,
  capacity: number,
): { events: readonly BackendRunEvent[]; truncatedFloor: number; truncated: boolean } {
  const ordered = Array.from(eventsBySeq.values()).sort((left, right) => left.seq - right.seq);
  if (ordered.length <= capacity) {
    return { events: Object.freeze(ordered), truncatedFloor: 1, truncated: false };
  }
  const started = eventsBySeq.get(1);
  const tailCapacity = started ? capacity - 1 : capacity;
  const tail = ordered.filter(({ seq }) => seq !== 1).slice(-tailCapacity);
  const retained = started ? [started, ...tail] : tail;
  return {
    events: Object.freeze(retained),
    truncatedFloor: tail[0]?.seq ?? Math.max(1, ordered[ordered.length - 1]!.seq + 1),
    truncated: true,
  };
}

function availability(
  events: readonly BackendRunEvent[],
  lastContiguousSeq: number,
  truncatedFloor: number,
): { earliestAvailableSeq: number; locallyComplete: boolean } {
  if (events.length === 0) {
    return { earliestAvailableSeq: 1, locallyComplete: lastContiguousSeq === 0 };
  }
  const seqs = new Set(events.map(({ seq }) => seq));
  const highestSeq = Math.max(lastContiguousSeq, events[events.length - 1]!.seq);
  let retainedPrefix = 0;
  while (seqs.has(retainedPrefix + 1)) retainedPrefix += 1;
  if (retainedPrefix === highestSeq && truncatedFloor === 1) {
    return { earliestAvailableSeq: 1, locallyComplete: true };
  }
  const firstTailSeq = events.find(({ seq }) => seq > retainedPrefix)?.seq;
  return {
    earliestAvailableSeq: firstTailSeq ?? Math.max(truncatedFloor, retainedPrefix + 1),
    locallyComplete: false,
  };
}

function buildSnapshot(
  runId: string,
  eventsBySeq: ReadonlyMap<number, BackendRunEvent>,
  lastContiguousSeq: number,
  topologyId: string | undefined,
  topologyRevision: string | undefined,
  terminal: boolean,
  metadata: BufferMetadata,
): RunEventBufferSnapshot {
  const bounded = boundedEvents(eventsBySeq, metadata.capacity);
  const truncatedFloor = bounded.truncated ? bounded.truncatedFloor : metadata.truncatedFloor;
  const available = availability(bounded.events, lastContiguousSeq, truncatedFloor);
  const integrityPartial = metadata.integrityPartial || bounded.truncated;
  const snapshot = {
    runId,
    events: bounded.events,
    lastContiguousSeq,
    earliestAvailableSeq: available.earliestAvailableSeq,
    historyState:
      integrityPartial || !available.locallyComplete ? ("partial" as const) : ("complete" as const),
    topologyId,
    topologyRevision,
    terminal,
    internalState: Object.freeze({
      ...metadata,
      integrityPartial,
      truncatedFloor,
    }),
  };
  return Object.freeze(snapshot);
}

export function createRunEventBuffer(
  runId: string,
  maxEvents = DEFAULT_MAX_EVENTS,
): RunEventBufferSnapshot {
  if (!runId) throw new Error("runId is required");
  return buildSnapshot(runId, new Map(), 0, undefined, undefined, false, {
    capacity: normalizedCapacity(maxEvents),
    integrityPartial: false,
    truncatedFloor: 1,
  });
}

export function appendRunEvent(
  current: RunEventBufferSnapshot,
  event: BackendRunEvent,
  maxEvents?: number,
): RunEventBufferSnapshot {
  const canonicalEvent = (() => {
    try {
      return parseBackendRunEvent(event);
    } catch {
      return null;
    }
  })();
  if (!canonicalEvent) return markRunEventBufferPartial(current);
  if (canonicalEvent.run_id !== current.runId) return markRunEventBufferPartial(current);
  const currentMetadata = metadataOf(current);
  if (
    (current.topologyId !== undefined && canonicalEvent.topology_id !== current.topologyId) ||
    (current.topologyRevision !== undefined &&
      canonicalEvent.topology_revision !== current.topologyRevision)
  ) {
    return markRunEventBufferPartial(current);
  }

  const eventsBySeq = new Map(current.events.map((stored) => [stored.seq, stored]));
  const stored = eventsBySeq.get(canonicalEvent.seq);
  if (stored) {
    if (valuesEqual(stored, canonicalEvent)) return current;
    return buildSnapshot(
      current.runId,
      eventsBySeq,
      current.lastContiguousSeq,
      current.topologyId,
      current.topologyRevision,
      current.terminal,
      { ...currentMetadata, integrityPartial: true },
    );
  }
  if (
    currentMetadata.truncatedFloor > 1 &&
    canonicalEvent.seq !== 1 &&
    canonicalEvent.seq < currentMetadata.truncatedFloor
  ) {
    return current;
  }
  if (
    current.terminal &&
    currentMetadata.terminalSeq !== undefined &&
    canonicalEvent.seq > currentMetadata.terminalSeq
  ) {
    return current;
  }

  eventsBySeq.set(canonicalEvent.seq, copyEvent(canonicalEvent));
  const terminalEvent = TERMINAL_EVENT_TYPES.has(canonicalEvent.type);
  const metadata: BufferMetadata = {
    ...currentMetadata,
    capacity: maxEvents === undefined ? currentMetadata.capacity : normalizedCapacity(maxEvents),
    terminalSeq: terminalEvent
      ? Math.max(currentMetadata.terminalSeq ?? 0, canonicalEvent.seq)
      : currentMetadata.terminalSeq,
  };
  return buildSnapshot(
    current.runId,
    eventsBySeq,
    calculateFrontier(current.lastContiguousSeq, eventsBySeq),
    current.topologyId ?? canonicalEvent.topology_id,
    current.topologyRevision ?? canonicalEvent.topology_revision,
    current.terminal || terminalEvent,
    metadata,
  );
}

export function mergeRunEvents(
  current: RunEventBufferSnapshot,
  events: readonly BackendRunEvent[],
  maxEvents?: number,
): RunEventBufferSnapshot {
  let merged = current;
  for (const event of events) {
    merged = appendRunEvent(merged, event, maxEvents);
  }
  return merged;
}

export function markRunEventBufferPartial(current: RunEventBufferSnapshot): RunEventBufferSnapshot {
  const metadata = metadataOf(current);
  if (metadata.integrityPartial && current.historyState === "partial") return current;
  return buildSnapshot(
    current.runId,
    new Map(current.events.map((event) => [event.seq, event])),
    current.lastContiguousSeq,
    current.topologyId,
    current.topologyRevision,
    current.terminal,
    { ...metadata, integrityPartial: true },
  );
}
