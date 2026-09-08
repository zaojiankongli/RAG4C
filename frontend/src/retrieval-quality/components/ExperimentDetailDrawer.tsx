import { Alert, Button, Tag } from "tdesign-react";
import { Drawer } from "../../ui";
import type { RefObject } from "react";
import type { Agreement, ExperimentDetail } from "../model/contracts";
import { projectExperiment } from "../model/projection";
import type { JudgmentDraft } from "../hooks/useJudgmentMutation";
import AgreementPanel from "./AgreementPanel";
import JudgmentEditor from "./JudgmentEditor";
import SafeTracePanel from "./SafeTracePanel";

interface Props { visible: boolean; detail: ExperimentDetail | null; agreement: Agreement | null; status: string; error: Error | null; actorId: string; openerRef: RefObject<HTMLButtonElement | null>; conflictRanks: number[]; savingRanks: number[]; draftFor: (rank: number) => JudgmentDraft; setDraft: (rank: number, draft: JudgmentDraft) => void; onSave: (rank: number, draft: JudgmentDraft) => void; onClose: () => void; }
export default function ExperimentDetailDrawer(props: Props) {
  const view = props.detail ? projectExperiment(props.detail) : null;
  const close = () => { props.onClose(); props.openerRef.current?.focus(); };
  return <Drawer className="rq-detail-drawer" open={props.visible} title="检索实验详情" width="min(880px, 96vw)" destroyOnClose={false} onClose={close}>
    <div className="rq-drawer-actions"><Button variant="outline" aria-label="关闭实验详情" onClick={close}>关闭</Button></div>
    {props.status === "loading" ? <div role="status">正在读取不可变实验快照…</div> : null}
    {props.error ? <Alert theme="error" title="实验详情读取不完整" message={props.error.message} /> : null}
    {props.detail && view ? <div className="rq-detail-content">
      <section className="rq-immutable-banner"><div><span className="rq-eyebrow">KNOWLEDGE LIFELINE</span><h2>不可变实验快照</h2></div><p>服务代次 G{view.datasetServingGeneration ?? "—"}。后续文档或索引变化不会重写本次策略、结果和证据链。</p><Tag variant="light-outline">{props.detail.id}</Tag></section>
      <dl className="rq-detail-facts"><div><dt>运行</dt><dd>{props.detail.run_id ?? "—"}</dd></div><div><dt>状态</dt><dd>{props.detail.status}</dd></div><div><dt>路由</dt><dd>{view.route ?? "—"}</dd></div><div><dt>耗时</dt><dd>{props.detail.latency_ms} ms</dd></div><div><dt>创建者</dt><dd>{props.detail.created_by}</dd></div><div><dt>创建时间</dt><dd>{props.detail.created_at}</dd></div></dl>
      <SafeTracePanel traces={view.traces}/><AgreementPanel agreement={props.agreement}/>
      <section className="rq-rank-judgments" aria-label="按结果排名判断">{view.evidence.length ? view.evidence.map((evidence) => <JudgmentEditor key={evidence.rank} rank={evidence.rank} actorId={props.actorId} judgments={props.detail!.judgments.filter((item) => item.result_rank === evidence.rank)} draft={props.draftFor(evidence.rank)} conflict={props.conflictRanks.includes(evidence.rank)} saving={props.savingRanks.includes(evidence.rank)} onChange={(draft) => props.setDraft(evidence.rank, draft)} onSave={() => props.onSave(evidence.rank, props.draftFor(evidence.rank))}/>) : <p>该实验没有可判断的召回排名。</p>}</section>
      <aside className="rq-eval-note"><strong>Eval 能力说明</strong><p>不可变实验快照可作为评测素材，但当前没有实验转入 Eval 的真实接口，因此此处不提供伪操作。</p></aside>
    </div> : null}
  </Drawer>;
}
