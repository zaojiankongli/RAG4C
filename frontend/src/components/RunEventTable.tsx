import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import type { BackendRunEvent, BackendRunTopology } from "../types/rag";

export interface RunEventFilters {
  type?: string;
  node?: string;
  attempt?: number;
  failed?: boolean;
  degraded?: boolean;
  retry?: boolean;
}
interface Props {
  events: readonly BackendRunEvent[];
  topology: BackendRunTopology;
  pageSize?: number;
  filters: RunEventFilters;
  onFiltersChange: (filters: RunEventFilters) => void;
  writeText?: (text: string) => Promise<void>;
}
const FAILED_TYPES = new Set(["node.failed", "retry.failed", "run.failed"]);
function duration(value: number | undefined): string {
  if (value === undefined) return "本次运行未记录该项";
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.round(value)}ms`;
}
function summary(event: BackendRunEvent): string {
  const reason = typeof event.attributes.reason === "string" ? event.attributes.reason : undefined;
  const pieces = [event.error?.code ?? event.error?.type, reason].filter(Boolean);
  return pieces.length ? pieces.join(" · ") : "已记录安全运行元数据";
}
function defaultWriteText(text: string): Promise<void> {
  if (!navigator.clipboard?.writeText) return Promise.reject(new Error("clipboard unavailable"));
  return navigator.clipboard.writeText(text);
}
export default function RunEventTable({ events, topology, pageSize = 200, filters, onFiltersChange, writeText = defaultWriteText }: Props) {
  const [page, setPage] = useState(1);
  const [announcement, setAnnouncement] = useState({ text: "", token: 0 });
  const eventTypes = useMemo(() => [...new Set(events.map(({ type }) => type))].sort(), [events]);
  const attempts = useMemo(() => [...new Set(events.flatMap(({ attempt }) => attempt === undefined ? [] : [attempt]))].sort((a, b) => a - b), [events]);
  const nodeLabels = useMemo(() => new Map(topology.nodes.map((node) => [node.id, node.label])), [topology]);
  const filtered = useMemo(() => events.filter((event) => {
    if (filters.type && event.type !== filters.type) return false;
    if (filters.node && event.node_id !== filters.node) return false;
    if (filters.attempt !== undefined && event.attempt !== filters.attempt) return false;
    if (filters.failed && !FAILED_TYPES.has(event.type)) return false;
    if (filters.degraded && event.type !== "degraded" && event.attributes.degraded !== true) return false;
    if (filters.retry && !event.type.startsWith("retry.") && (event.attempt ?? 1) <= 1) return false;
    return true;
  }), [events, filters]);
  const effectivePageSize = Math.max(1, Math.min(1000, pageSize));
  const totalPages = Math.max(1, Math.ceil(filtered.length / effectivePageSize));
  useEffect(() => setPage(1), [filters]);
  useEffect(() => setPage((current) => Math.min(current, totalPages)), [totalPages]);
  const visible = filtered.slice((page - 1) * effectivePageSize, page * effectivePageSize);
  const update = <K extends keyof RunEventFilters>(key: K, value: RunEventFilters[K]) => onFiltersChange({ ...filters, [key]: value });
  const announce = (text: string) => setAnnouncement((current) => ({ text, token: current.token + 1 }));
  const toggleDetailsFromKeyboard = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    const details = event.currentTarget.closest("details");
    if (details) details.open = !details.open;
  };
  const copy = async (event: BackendRunEvent) => {
    try { await writeText(JSON.stringify(event, null, 2)); announce(`已复制事件 ${event.seq}`); }
    catch { announce(`复制事件 ${event.seq} 失败`); }
  };
  return <section className="run-event-panel" aria-labelledby="run-event-heading">
    <h2 id="run-event-heading">事件账本</h2>
    <div className="run-event-filters" role="group" aria-label="事件筛选">
      <label>事件类型<select value={filters.type ?? ""} onChange={(e) => update("type", e.currentTarget.value || undefined)}><option value="">全部</option>{eventTypes.map((type) => <option key={type}>{type}</option>)}</select></label>
      <label>节点<select value={filters.node ?? ""} onChange={(e) => update("node", e.currentTarget.value || undefined)}><option value="">全部</option>{topology.nodes.map((node) => <option key={node.id} value={node.id}>{node.label}</option>)}</select></label>
      <label>Attempt<select value={filters.attempt ?? ""} onChange={(e) => update("attempt", e.currentTarget.value ? Number(e.currentTarget.value) : undefined)}><option value="">全部</option>{attempts.map((attempt) => <option key={attempt} value={attempt}>{attempt}</option>)}</select></label>
      <label><input type="checkbox" checked={Boolean(filters.failed)} onChange={(e) => update("failed", e.currentTarget.checked || undefined)} />仅失败</label>
      <label><input type="checkbox" checked={Boolean(filters.degraded)} onChange={(e) => update("degraded", e.currentTarget.checked || undefined)} />仅降级</label>
      <label><input type="checkbox" checked={Boolean(filters.retry)} onChange={(e) => update("retry", e.currentTarget.checked || undefined)} />仅重试</label>
    </div>
    <div
      className="run-event-table-wrap"
      role="region"
      aria-label="事件账本，可横向滚动"
      tabIndex={0}
    ><table className="run-event-table">
      <colgroup>
        <col className="run-event-col-seq" style={{ width: 72 }} />
        <col className="run-event-col-time" style={{ width: 230 }} />
        <col className="run-event-col-type" style={{ width: 160 }} />
        <col className="run-event-col-node" style={{ width: 210 }} />
        <col className="run-event-col-attempt" style={{ width: 90 }} />
        <col className="run-event-col-duration" style={{ width: 110 }} />
        <col className="run-event-col-summary" style={{ width: 220 }} />
        <col className="run-event-col-details" style={{ width: 220 }} />
      </colgroup>
      <caption>脱敏后的 canonical 运行事件</caption>
      <thead><tr><th scope="col">Seq</th><th scope="col">时间</th><th scope="col">类型</th><th scope="col">节点</th><th scope="col">Attempt</th><th scope="col">耗时</th><th scope="col">摘要</th><th scope="col">详情</th></tr></thead>
      <tbody>{visible.length ? visible.map((event) => <tr key={event.seq} className="run-event-row">
        <td className="run-event-col-seq tabular-nums">{event.seq}</td><td className="run-event-col-time"><span className="tabular-nums">{duration(event.elapsed_ms)}</span> · <time dateTime={event.occurred_at}>{event.occurred_at}</time></td><td className="run-event-col-type"><code>{event.type}</code></td><td className="run-event-col-node">{event.node_id ? <><span>{nodeLabels.get(event.node_id) ?? event.node_id}</span> · <code>{event.node_id}</code></> : "—"}</td><td className="run-event-col-attempt">{event.attempt ?? "—"}</td><td className="run-event-col-duration">{duration(event.duration_ms)}</td><td className="run-event-col-summary">{summary(event)}</td>
        <td className="run-event-col-details"><details><summary onKeyDown={toggleDetailsFromKeyboard}>查看事件 {event.seq} JSON</summary><button type="button" onClick={() => void copy(event)} aria-label={`复制事件 ${event.seq}`}>复制 JSON</button><pre>{JSON.stringify(event, null, 2)}</pre></details></td>
      </tr>) : <tr><td colSpan={8}>没有符合筛选条件的事件</td></tr>}</tbody>
    </table></div>
    <div className="run-event-pagination" aria-label="事件分页"><button type="button" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button><span>第 {page} / {totalPages} 页</span><button type="button" disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)}>下一页</button></div>
    <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">{announcement.text ? <span key={announcement.token} data-announcement-token={announcement.token}>{announcement.text}</span> : null}</p>
  </section>;
}
