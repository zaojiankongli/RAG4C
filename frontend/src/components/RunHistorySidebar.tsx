import type { ChangeEvent } from "react";
import type { RunAttention, RunListView, RunStatus, RunSummaryDto } from "../types/runs";

interface Props {
  items: readonly RunSummaryDto[];
  selectedRunId: string | null;
  view: RunListView;
  loading: boolean;
  hasMore: boolean;
  liveRunId?: string;
  retentionDays?: number;
  sourceLabel?: string;
  onSelect: (runId: string) => void;
  onViewChange: (view: RunListView) => void;
  onLoadMore: () => void;
}

const VIEW_LABELS: Record<RunListView, string> = {
  recent: "最近运行",
  active: "执行中",
  slow: "慢运行",
  errors: "错误",
  stuck: "卡住",
};
const STATUS_LABELS: Record<RunStatus, string> = {
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};
const ATTENTION_LABELS: Record<RunAttention, string> = {
  error: "错误",
  interrupted: "中断关注",
  cancelled: "取消关注",
  slow: "慢运行",
  stuck: "卡住",
};

function shortRunId(runId: string): string {
  return runId.slice(-8);
}
function formatDuration(ms: number): string {
  return ms >= 1000
    ? `${(ms / 1000).toFixed(ms >= 10000 ? 1 : 2)}s`
    : `${Math.max(0, Math.round(ms))}ms`;
}
function formatStartedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export default function RunHistorySidebar({
  items,
  selectedRunId,
  view,
  loading,
  hasMore,
  liveRunId,
  retentionDays,
  sourceLabel,
  onSelect,
  onViewChange,
  onLoadMore,
}: Props) {
  const ordered = liveRunId
    ? [...items].sort(
        (left, right) => Number(right.run_id === liveRunId) - Number(left.run_id === liveRunId),
      )
    : items;
  const handleViewChange = (event: ChangeEvent<HTMLSelectElement>) =>
    onViewChange(event.currentTarget.value as RunListView);
  return (
    <aside className="run-history" aria-label="运行记录">
      <div className="run-history-head">
        <div>
          <div className="run-section-title">运行账本</div>
          <div className="run-section-sub">仅显示服务端摘要，不含问题与回答正文</div>
        </div>
        <label className="run-history-filter">
          <span className="sr-only">筛选运行记录</span>
          <select aria-label="筛选运行记录" value={view} onChange={handleViewChange}>
            {Object.entries(VIEW_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="run-history-list" aria-busy={loading ? "true" : "false"}>
        {ordered.map((item) => {
          const isLive = item.run_id === liveRunId;
          const selected = item.run_id === selectedRunId;
          return (
            <button
              key={item.run_id}
              type="button"
              data-run-id={item.run_id}
              className={`run-history-item is-${item.status}${selected ? " is-active" : ""}`}
              aria-current={selected ? "true" : undefined}
              onClick={() => onSelect(item.run_id)}
            >
              <span className="run-history-status-mark" aria-hidden="true">
                {isLive ? "LIVE" : "RUN"}
              </span>
              <span className="run-history-main">
                <span className="run-history-id">
                  <span className="mono">…{shortRunId(item.run_id)}</span>
                  {isLive ? <span className="run-ledger-live">当前</span> : null}
                </span>
                <span className="run-ledger-line">
                  <span className="run-status-text">{STATUS_LABELS[item.status]}</span>
                  <span>seq {item.last_seq}</span>
                  <span>attempt {Math.max(1, item.retry_count + 1)}</span>
                </span>
                <span className="run-history-meta">
                  {formatStartedAt(item.started_at)} · {formatDuration(item.elapsed_ms)}
                </span>
                {item.attention.length ? (
                  <span className="run-attention-list">
                    {item.attention.map((attention) => (
                      <span key={attention} className={`run-attention is-${attention}`}>
                        {ATTENTION_LABELS[attention]}
                      </span>
                    ))}
                  </span>
                ) : null}
              </span>
            </button>
          );
        })}
        {!loading && ordered.length === 0 ? (
          <div className="run-history-empty">当前筛选条件下没有运行记录</div>
        ) : null}
        {loading ? (
          <div className="run-history-loading" role="status">
            正在加载运行记录…
          </div>
        ) : null}
      </div>
      <div className="run-history-foot">
        {sourceLabel || retentionDays !== undefined ? (
          <div className="run-history-authority">
            <span>{sourceLabel ?? "Registry"}</span>
            {retentionDays !== undefined ? <span>保留 {retentionDays} 天</span> : null}
          </div>
        ) : null}
        {hasMore ? (
          <button
            type="button"
            className="run-history-more"
            aria-label="加载更多运行记录"
            disabled={loading}
            onClick={onLoadMore}
          >
            加载更多
          </button>
        ) : null}
      </div>
    </aside>
  );
}
