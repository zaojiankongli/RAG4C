import { Alert, Button, Input, Popconfirm, Tag, Textarea } from "tdesign-react";
import { CopyIcon, DeleteIcon, RefreshIcon } from "tdesign-icons-react";
import type { UseParseInterventionResult } from "../hooks/useParseIntervention";
import { buildDiff } from "../model/parseInterventionModel";
export default function ChunkEditorPane({state,onChanged}:{state:UseParseInterventionResult;onChanged?:()=>void}){
 const selected=state.selected;if(!selected)return <section className="parse-pane parse-editor-pane" role="region" aria-label="切片编辑器"><div className="parse-empty">选择一个切片开始检查</div></section>;
 const readOnly=selected.enabled===false||state.authorityMode==="off";const diff=buildDiff(selected.text,state.draft);
 const modeMessage=state.authorityMode==="shadow"?"Shadow 模式会先记录权威 Revision，再同步更新旧 Milvus/图谱投影；同步返回仍不替代一致性检查。":"Active 模式先执行 ChunkHead CAS，再由持久操作异步追赶 Milvus/图谱。";
 return <section className="parse-pane parse-editor-pane" role="region" aria-label="切片编辑器">
  <div className="parse-pane-heading"><div><span>AUTHORITATIVE EDIT</span><h2>切片编辑器</h2></div><Tag theme={readOnly?"default":"primary"} variant="light">{selected.enabled===false?"墓碑 · 只读":`内容 Revision ${selected.content_revision}`}</Tag></div>
  <div className="parse-cas-grid"><div><span>Chunk ID</span><code>{selected.chunk_id}</code></div><div><span>Expected Revision</span><strong>{state.expectedRevision}</strong></div><div><span>Document Revision</span><strong>{selected.document_revision??"未提供"}</strong></div><div><span>Projection Fence</span><strong>{selected.desired_index_revision??selected.content_revision}</strong></div></div>
  <label className="parse-editor-field"><span>当前内容</span><Textarea aria-label="切片正文" value={state.draft} disabled={readOnly} onChange={v=>state.setDraft(String(v))} autosize={{minRows:12,maxRows:24}}/></label>
  <label className="parse-editor-field"><span>修改原因</span><Input aria-label="修改原因" value={state.reason} disabled={readOnly} onChange={v=>state.setReason(String(v))} placeholder="例如：修正 OCR 错字或恢复遗漏条款"/></label>
  <Alert theme="info" title="当前 API 不持久化修改原因" message="原因只用于本次工作区确认与回执，不会冒充服务端审计记录。"/><Alert theme="warning" title="投影影响" message={modeMessage}/>
  <div className="parse-editor-secondary"><Button variant="text" icon={<CopyIcon/>} onClick={()=>void navigator.clipboard?.writeText(state.draft)}>复制正文</Button><Button variant="text" icon={<RefreshIcon/>} disabled={readOnly} onClick={state.resetDraft}>重置草稿</Button><Popconfirm theme="danger" content="删除会写入只读墓碑。" confirmBtn={{content:"写入墓碑",theme:"danger"}} onConfirm={()=>void state.removeSelected().then(ok=>{if(ok)onChanged?.();})}><Button variant="text" theme="danger" disabled={readOnly} icon={<DeleteIcon/>}>删除切片</Button></Popconfirm></div>
  <div className="parse-section-heading"><h3>修改 Diff</h3><span>{state.dirty?"待提交":"没有内容变更"}</span></div><div className="parse-diff" role="region" tabIndex={0} aria-label="修改差异预览">{diff.map((l,i)=><div key={`${l.kind}-${i}`} className={`is-${l.kind}`}><b>{l.kind==="add"?"+":l.kind==="remove"?"−":" "}</b><code>{l.text||" "}</code></div>)}</div>
  {state.orphanDraft?<div className="parse-conflict" role="alert"><h3>{state.orphanDraft.resolution==="conflict"?"Revision 冲突":state.orphanDraft.resolution==="tombstone"?"切片已成为墓碑":"切片已不存在"}</h3><p>本地草稿与原因已独立保留，不会随分页、搜索或选择变化丢失。</p><div className="parse-conflict-columns"><div><span>服务器状态</span><pre>{state.orphanDraft.server?.text??"资源不可用"}</pre></div><div><span>我的草稿</span><pre>{state.orphanDraft.draft}</pre></div></div><div><Button variant="outline" onClick={()=>void navigator.clipboard?.writeText(state.orphanDraft!.draft)}>复制草稿</Button><Button variant="text" onClick={state.discardOrphanDraft}>丢弃草稿</Button>{state.canRebaseOrphan?<Button theme="warning" onClick={state.rebaseOrphanDraft}>基于新 Revision 继续修改</Button>:null}</div></div>:null}
  <Alert theme="info" title="当前没有切片 Revision 历史接口" message="这里只展示当前 ChunkHead 与 CAS facts，不会伪造历史记录。"/>
 </section>;
}
