import { Button, Card, Popconfirm, Tag } from "../../ui";
import { summarizeSourceConfig } from "../model/sourceProjection";
import type { SourceRecord } from "../model/sourceModels";
import ReferenceText from "./ReferenceText";

interface Props { sources: SourceRecord[]; selectedId: string | null; mutatingId: string | null; onSelect: (source: SourceRecord) => void; onEdit: (source: SourceRecord) => void; onToggle: (source: SourceRecord, enabled: boolean) => void; onSync: (source: SourceRecord) => void; }
const kindLabels = { local_dir: "本地目录", github_repo: "GitHub 仓库" } as const;
function resultNumber(source: SourceRecord, key: string): number | null { const value = source.last_result[key]; return typeof value === "number" ? value : null; }
function time(value: string | null): string { return value ? new Date(value).toLocaleString("zh-CN") : "尚未同步"; }

export default function SourceList({ sources, selectedId, mutatingId, onSelect, onEdit, onToggle, onSync }: Props) {
  return <section aria-labelledby="source-list-title"><div className="source-section-heading"><div><span className="source-eyebrow">AUTHORITY / SOURCES</span><h2 id="source-list-title">已注册来源</h2></div><span>{sources.length} 个</span></div>
    <div className="source-control-grid">{sources.map((source) => { const summary = summarizeSourceConfig(source); const disabling = source.status === "active"; return <Card key={source.id} className={`source-control-card${selectedId === source.id ? " is-selected" : ""}`}>
      <button type="button" className="source-card-main" onClick={() => onSelect(source)} aria-label={`查看 ${source.name} 的运行历史`}>
        <span className="source-generation-rail">G{source.generation}</span><span className="source-card-copy"><span className="source-card-title"><strong>{source.name}</strong><Tag color={source.status === "active" ? "success" : "default"}>{source.status === "active" ? "已启用" : "已停用"}</Tag></span><span className="source-kind">{kindLabels[source.kind]} · {summary.primary}</span><span className="source-config-summary">{summary.details}</span></span>
      </button>
      <div className="source-card-facts"><span>{time(source.last_sync_at)}</span>{resultNumber(source, "fetched") !== null ? <b>{resultNumber(source, "fetched")} 抓取</b> : null}{resultNumber(source, "ingested") !== null ? <b>{resultNumber(source, "ingested")} 入库</b> : null}{resultNumber(source, "failed") !== null ? <b>{resultNumber(source, "failed")} 失败</b> : null}{summary.credentialBadge ? <Tag variant="light-outline">{summary.credentialBadge}</Tag> : null}</div>
      {source.last_error ? <p className="source-last-error" role="status">{source.last_error.slice(0, 240)}</p> : null}
      <div className="source-card-actions"><ReferenceText value={source.id} label="来源引用"/><Button size="small" onClick={() => onEdit(source)}>编辑</Button><Button size="small" type="primary" disabled={source.status !== "active" || mutatingId === source.id} onClick={() => onSync(source)}>立即同步</Button><Popconfirm title={disabling ? `停用 ${source.name}` : `启用 ${source.name}`} description={`将以当前 G${source.generation} 代次提交。`} okText={disabling ? "确认停用" : "确认启用"} onConfirm={() => onToggle(source, !disabling)}><Button size="small" danger={disabling} aria-label={`${disabling ? "停用" : "启用"} ${source.name}`} disabled={mutatingId === source.id}>{disabling ? "停用" : "启用"}</Button></Popconfirm></div>
    </Card>; })}</div>
  </section>;
}
