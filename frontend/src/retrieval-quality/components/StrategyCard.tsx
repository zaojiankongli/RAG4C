import { Button, Card, Input, InputNumber, Select, Switch, Tag } from "../../ui";
import type { RetrievalVariantDraft } from "../model/contracts";
import type { VariantErrors } from "../model/validation";

const routes = [
  { label: "自动路由", value: "auto" }, { label: "混合检索", value: "hybrid" },
  { label: "向量 + 图谱", value: "vector_graph_rag" }, { label: "完整双分支", value: "full" },
];
const diversity = [{ label: "关闭", value: "off" }, { label: "仅按来源分组", value: "group_only" }, { label: "来源分组 + MMR", value: "group_mmr" }];
interface Props { value: RetrievalVariantDraft; index: number; errors?: VariantErrors; canRemove: boolean; canDuplicate: boolean; onChange: (value: RetrievalVariantDraft) => void; onDuplicate: () => void; onRemove: () => void; }

export default function StrategyCard({ value, index, errors, canRemove, canDuplicate, onChange, onDuplicate, onRemove }: Props) {
  const update = <K extends keyof RetrievalVariantDraft>(key: K, next: RetrievalVariantDraft[K]) => onChange({ ...value, [key]: next });
  return <Card className="rq-strategy-card" bordered header={<div className="rq-strategy-title"><span>策略 {index + 1}</span><Tag variant="light-outline">{value.route_target}</Tag></div>}>
    <fieldset className="rq-strategy-fields" aria-label={`检索策略 ${index + 1}`}>
      <label><span>策略名称</span><Input aria-label={`策略 ${index + 1} 名称`} aria-invalid={Boolean(errors?.name)} value={value.name} maxLength={64} onChange={(event: { target: { value: string } }) => update("name", event.target.value)}/>{errors?.name ? <small role="alert">{errors.name}</small> : null}</label>
      <label><span>路由目标</span><Select aria-label={`策略 ${index + 1} 路由目标`} value={value.route_target} options={routes} onChange={(next: unknown) => update("route_target", next as RetrievalVariantDraft["route_target"])}/></label>
      <label><span>Top K</span><InputNumber aria-label={`策略 ${index + 1} Top K`} aria-invalid={Boolean(errors?.top_k)} value={value.top_k} min={1} max={50} step={1} onChange={(next: unknown) => update("top_k", next == null || next === "" ? 0 : Number(next))}/>{errors?.top_k ? <small role="alert">{errors.top_k}</small> : null}</label>
      <label><span>来源多样性</span><Select aria-label={`策略 ${index + 1} 来源多样性`} value={value.source_diversity} options={diversity} onChange={(next: unknown) => update("source_diversity", next as RetrievalVariantDraft["source_diversity"])}/></label>
      <div className="rq-switch-grid">
        {([
          ["hybrid_search_on", "混合检索"], ["rerank_on", "重排"], ["graph_retrieval_on", "图谱检索"], ["sentence_window_on", "句子窗口"],
        ] as const).map(([key, label]) => <label key={key}><span>{label}</span><Switch aria-label={`策略 ${index + 1} ${label}`} checked={Boolean(value[key])} onChange={(next: boolean) => update(key, next)}/></label>)}
      </div>
      <div className="rq-strategy-actions"><Button disabled={!canDuplicate} aria-label={`复制策略 ${value.name}`} onClick={onDuplicate}>复制</Button><Button type="text" danger disabled={!canRemove} aria-label={`删除策略 ${value.name}`} onClick={onRemove}>删除</Button></div>
    </fieldset>
  </Card>;
}
