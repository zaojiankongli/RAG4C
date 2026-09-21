import type { DocumentChunkItem, DocumentItem } from "../../types/rag";
import { Alert, Tag } from "tdesign-react";
import { buildChunkOutline, sanitizeSourceFact } from "../model/parseInterventionModel";
import { formatChunkingDecision } from "../model/chunkDiagnostics";

const ENGINE_LABELS: Record<string, string> = { fast: "快速通道（文字层）", vision: "视觉解析（OCR）", router: "自动分流" };
const STAGE_LABELS: Record<string, string> = { parse: "解析", split: "切分", contextualize: "上下文增强", embed: "向量化", graph: "图谱", persist: "持久化" };

function Fact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return <div className="parse-fact"><dt>{label}</dt><dd className={mono ? "is-mono" : undefined}>{value}</dd></div>;
}

export default function ParsedContextPane({ document, chunks }: { document: DocumentItem; chunks: DocumentChunkItem[] }) {
  const meta = document.parser_meta ?? {};
  const chunkDiag = formatChunkingDecision(meta);
  const stages = Object.entries(meta.stage_ms ?? {}).filter(([, value]) => Number.isFinite(Number(value)) && Number(value) >= 0);
  const stageTotal = stages.reduce((sum, [, value]) => sum + Number(value), 0);
  const maxStage = Math.max(1, ...stages.map(([, value]) => Number(value)));
  const outline = buildChunkOutline(chunks);
  const source = sanitizeSourceFact(document.source_uri || document.source_id || document.external_id || meta.file_name || document.name);
  return (
    <section className="parse-pane parse-context-pane" role="region" aria-label="解析上下文">
      <div className="parse-pane-heading"><div><span>PARSED CONTEXT</span><h2>解析上下文</h2></div><Tag variant="light">只读事实</Tag></div>
      <dl className="parse-facts">
        <Fact label="文档" value={document.name} /><Fact label="文档 ID" value={document.id} mono />
        <Fact label="工作区" value={`${document.tenant_id ?? "未记录"} / ${document.dataset_id ?? "未记录"}`} mono />
        <Fact label="文档 Revision" value={String(document.mutation_generation ?? "未记录")} mono />
        <Fact label="来源类型" value={document.source_type || "未记录"} /><Fact label="安全来源" value={source} mono />
        <Fact label="解析引擎" value={ENGINE_LABELS[meta.engine ?? ""] ?? meta.engine ?? "未记录"} />
        <Fact label="切分策略" value={chunkDiag.modeLabel} />
        <Fact
          label="决策理由"
          value={chunkDiag.reason ?? "未记录（历史文档可能缺 parser_meta 诊断字段）"}
        />
        {chunkDiag.factsSummary ? (
          <Fact label="决策依据" value={chunkDiag.factsSummary} mono />
        ) : null}
        <Fact label="文档形态" value={meta.pdf_type ?? document.doc_type ?? "未记录"} />
        <Fact label="覆盖" value={`${meta.page_count ?? "?"} 页 · ${meta.layout_blocks ?? "?"} 版面块 · ${meta.text_chars ?? "?"} 字符`} />
      </dl>
      <div className="parse-section-heading"><h3>阶段瀑布</h3><span>{stages.length ? `${stageTotal.toFixed(1)}ms 已记录` : "无阶段时序"}</span></div>
      <div className="parse-stage-list" aria-label="解析阶段耗时">
        {stages.map(([name, value]) => <div className="parse-stage" key={name}><div><span>{STAGE_LABELS[name] ?? name}</span><b>{Number(value) < 1 ? "<1ms" : `${Number(value).toFixed(1)}ms`}</b></div><i aria-hidden="true"><em style={{ width: `${Math.max(4, Number(value) / maxStage * 100)}%` }} /></i></div>)}
        {!stages.length ? <p className="parse-muted">当前文档没有可用的阶段耗时 metadata。</p> : null}
      </div>
      <div className="parse-section-heading"><h3>页码 / 章节大纲</h3><span>{outline.length} 个分组</span></div>
      <nav className="parse-outline" aria-label="文档页码和章节">{outline.map((node) => <div key={node.key} className="parse-outline-node"><strong>{node.label}</strong><span>{node.chunkIds.length} 个切片</span>{node.headings.length ? <ul>{node.headings.map((heading) => <li key={heading}>{heading}</li>)}</ul> : <small>没有标题 metadata</small>}</div>)}</nav>
      <Alert theme="info" title="当前没有原始文档预览能力" message="界面只展示解析与切片 metadata；不会用切片正文伪装原始文件。建议后端未来提供受控 source-preview 端点。" />
    </section>
  );
}
