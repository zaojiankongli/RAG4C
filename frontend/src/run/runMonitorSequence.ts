export interface RunEventSequenceState {
  provisionalRunId: string;
  runId: string;
  seq: number;
  typed: boolean;
}

export function synchronizeEventSequence(
  previous: RunEventSequenceState,
  runId: string,
  seq: number,
  typed: boolean,
): RunEventSequenceState {
  return { ...previous, runId, seq, typed };
}
