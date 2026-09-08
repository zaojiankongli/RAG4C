import type { CSSProperties } from "react";
import type { FlowNodeStatus } from "../run/runProjection";
import type { RunIdleGap, RunTimelineInterval, RunTimelineWave } from "../run/serverRunProjection";

interface Props {
  intervals: readonly RunTimelineInterval[];
  waves: readonly RunTimelineWave[];
  idleGaps: readonly RunIdleGap[];
  selectedNodeId?: string;
  onSelectNode: (nodeId: string) => void;
}

const STATUS_LABELS: Record<FlowNodeStatus | "open", string> = {
  pending: "等待中",
  active: "执行中",
  completed: "已完成",
  skipped: "已跳过",
  degraded: "已降级",
  failed: "失败",
  cancelled: "已取消",
  open: "进行中",
};
function formatMs(value: number | undefined): string {
  if (value === undefined) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.max(0, Math.round(value))}ms`;
}
function chronological(left: RunTimelineInterval, right: RunTimelineInterval): number {
  return (
    (left.startMs ?? Number.POSITIVE_INFINITY) - (right.startMs ?? Number.POSITIVE_INFINITY) ||
    (left.endMs ?? Number.POSITIVE_INFINITY) - (right.endMs ?? Number.POSITIVE_INFINITY) ||
    left.attempt - right.attempt ||
    left.nodeId.localeCompare(right.nodeId) ||
    left.id.localeCompare(right.id)
  );
}
function runEnd(
  intervals: readonly RunTimelineInterval[],
  waves: readonly RunTimelineWave[],
): number {
  return Math.max(
    1,
    ...waves.map((item) => item.endMs),
    ...intervals.map((item) => item.endMs ?? item.startMs ?? 0),
  );
}
function barStyle(interval: RunTimelineInterval, totalMs: number): CSSProperties {
  const left = Math.max(0, ((interval.startMs ?? interval.endMs ?? 0) / totalMs) * 100);
  const duration =
    interval.durationMs ??
    (interval.startMs !== undefined ? (interval.endMs ?? totalMs) - interval.startMs : 0);
  const width = Math.max(0, (duration / totalMs) * 100);
  return { left: `${left}%`, width: `max(6px, ${width}%)` };
}
function intervalFlags(interval: RunTimelineInterval): string[] {
  return [
    interval.retry ? "重试" : "",
    STATUS_LABELS[interval.status],
    interval.partial ? "部分记录" : "",
  ].filter(Boolean);
}

export default function RunTimeline({
  intervals,
  waves,
  idleGaps,
  selectedNodeId,
  onSelectNode,
}: Props) {
  const ordered = [...intervals].sort(chronological);
  const totalMs = runEnd(ordered, waves);
  return (
    <section className="run-timeline" aria-label="运行时间线">
      <div className="run-timeline-scale" aria-hidden="true">
        <span>0ms</span>
        <span>{formatMs(totalMs)}</span>
      </div>
      <div className="run-timeline-waves">
        {waves.flatMap((wave, index) => {
          const members = ordered.filter(
            (item) => item.wave === wave.id || wave.intervalIds.includes(item.id),
          );
          const nextWave = waves[index + 1];
          const gap = nextWave
            ? idleGaps.find(
                (item) =>
                  item.startMs === wave.endMs &&
                  item.endMs === nextWave.startMs &&
                  item.durationMs > 250,
              )
            : undefined;
          const waveNode = (
            <section
              key={`wave-${wave.id}`}
              className="run-timeline-wave"
              role="group"
              aria-label={`并发波 ${wave.id}，${members.length} 个节点，最大并发 ${wave.maxConcurrency}`}
            >
              <div className="run-timeline-wave-label">
                <strong>并发波 {wave.id}</strong>
                <span>
                  {members.length} 个节点 · 最大并发 {wave.maxConcurrency} · 墙钟{" "}
                  {formatMs(wave.wallTimeMs)}
                </span>
              </div>
              <div className="run-timeline-track">
                {members.map((interval) => {
                  const flags = intervalFlags(interval);
                  return (
                    <button
                      key={interval.id}
                      type="button"
                      className={`run-timeline-bar is-${interval.status}${interval.retry ? " is-retry" : ""}${interval.partial ? " is-partial" : ""}${interval.nodeId === selectedNodeId ? " is-selected" : ""}`}
                      data-interval-id={interval.id}
                      data-node-id={interval.nodeId}
                      style={barStyle(interval, totalMs)}
                      aria-current={interval.nodeId === selectedNodeId ? "true" : undefined}
                      aria-label={`${interval.nodeId}, attempt ${interval.attempt}, ${flags.join("，")}, seq ${interval.startSeq ?? "?"}-${interval.endSeq ?? "?"}`}
                      onClick={() => onSelectNode(interval.nodeId)}
                    >
                      <span className="run-timeline-bar-ledger">
                        <span>{interval.nodeId}</span>
                        <span>
                          seq {interval.startSeq ?? "?"}→{interval.endSeq ?? "?"}
                        </span>
                        <span>attempt {interval.attempt}</span>
                      </span>
                      <span className="run-timeline-bar-flags">
                        {flags.map((flag) => (
                          <span key={flag}>{flag}</span>
                        ))}
                      </span>
                    </button>
                  );
                })}
              </div>
            </section>
          );
          const gapNode = gap ? (
            <div
              key={`gap-${gap.startMs}-${gap.endMs}`}
              className="run-timeline-idle"
              data-idle-gap
              aria-label={`空闲 ${formatMs(gap.durationMs)}`}
            >
              <span>空闲 {formatMs(gap.durationMs)}</span>
              <span className="mono">
                {formatMs(gap.startMs)} → {formatMs(gap.endMs)}
              </span>
            </div>
          ) : null;
          return gapNode ? [waveNode, gapNode] : [waveNode];
        })}{" "}
      </div>
      <div className="run-timeline-table-wrap">
        <table className="run-timeline-table" aria-label="运行时间线明细表">
          <thead>
            <tr>
              <th>节点</th>
              <th>Attempt</th>
              <th>开始</th>
              <th>结束</th>
              <th>耗时</th>
              <th>状态</th>
              <th>Wave</th>
            </tr>
          </thead>
          <tbody>
            {ordered.map((interval) => (
              <tr
                key={interval.id}
                className={interval.nodeId === selectedNodeId ? "is-selected" : undefined}
              >
                <th scope="row">
                  <button
                    type="button"
                    className="run-timeline-table-select"
                    onClick={() => onSelectNode(interval.nodeId)}
                  >
                    {interval.nodeId}
                  </button>
                </th>
                <td className="mono">{interval.attempt}</td>
                <td className="mono">{formatMs(interval.startMs)}</td>
                <td className="mono">{formatMs(interval.endMs)}</td>
                <td className="mono">{formatMs(interval.durationMs)}</td>
                <td>{intervalFlags(interval).join(" · ")}</td>
                <td className="mono">{interval.wave ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {ordered.length === 0 ? (
        <div className="run-timeline-empty">当前运行还没有可展示的节点区间</div>
      ) : null}
    </section>
  );
}
