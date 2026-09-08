import { Alert, Button, InputNumber, Tag, Textarea } from "tdesign-react";
import { Radio } from "../../ui";
import type { Judgment } from "../model/contracts";
import type { JudgmentDraft } from "../hooks/useJudgmentMutation";
interface Props { rank: number; judgments: Judgment[]; actorId: string; draft: JudgmentDraft; conflict: boolean; saving: boolean; onChange: (draft: JudgmentDraft) => void; onSave: () => void; }
export default function JudgmentEditor({ rank, judgments, actorId, draft, conflict, saving, onChange, onSave }: Props) {
  const owned = judgments.find((item) => item.created_by === actorId);
  return <section className="rq-judgment-editor" aria-label={`排名 ${rank} 判断`}>
    <div className="rq-judgment-head"><strong>排名 #{rank} 操作者判断</strong>{owned ? <Tag variant="light-outline">{owned.created_by} · r{owned.revision}</Tag> : <Tag variant="light-outline">尚未判断</Tag>}</div>
    {conflict ? <div role="alert"><Alert theme="warning" title="判断已被更新" message="已刷新最新 owner/revision；你的草稿仍保留，请复核后再次保存。" /></div> : null}
    <Radio.Group value={draft.relevanceLabel} options={[{label:"相关",value:"relevant"},{label:"部分相关",value:"partial"},{label:"不相关",value:"irrelevant"}]} onChange={(event: { target: { value: JudgmentDraft["relevanceLabel"] } }) =>
      onChange({ ...draft, relevanceLabel: event.target.value })}/>
    <label><span>质量分 0–3</span><InputNumber value={draft.score ?? undefined} min={0} max={3} step={1} onChange={(value) => onChange({ ...draft, score: value === undefined || value === "" ? null : Number(value) })}/></label>
    <label><span>备注</span><Textarea value={draft.note} maxlength={20000} autosize={{minRows:2,maxRows:5}} onChange={(value) => onChange({ ...draft, note: String(value) })}/></label>
    <Button theme="primary" loading={saving} onClick={onSave}>保存判断</Button>
    {judgments.filter((item) => item.created_by !== actorId).length ? <div className="rq-foreign-judgments"><span>其他操作者（只读）</span>{judgments.filter((item) => item.created_by !== actorId).map((item) => <div key={item.id}><Tag>{item.created_by} · r{item.revision}</Tag><span>{item.relevance_label} · {item.score ?? "未评分"}</span><p>{item.note || "无备注"}</p></div>)}</div> : null}
  </section>;
}

