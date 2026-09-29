import { Alert, Card, Tag } from "../../ui";
import type { CSSProperties } from "react";
import type { RunResponse } from "../model/contracts";
import { projectExperiment } from "../model/projection";
import EvidenceComparisonTable from "./EvidenceComparisonTable";
import LineageStrip from "./LineageStrip";
import SafeTracePanel from "./SafeTracePanel";

function statusColor(state: string) { return state === "failed" ? "danger" : state === "no-hit" ? "warning" : "success"; }
export default function ComparisonResults({ response }: { response: RunResponse | null }) {
  if (!response) return <section className="rq-results rq-empty-results" aria-label="检索对比结果"><strong>等待纯检索对比</strong><p>运行后将在这里按策略和排名对齐证据。</p></section>;
  const variants = response.items.map(projectExperiment);
  return <section className="rq-results" aria-labelledby="rq-results-title">
    <div className="rq-generation-rail"><div><span className="rq-eyebrow">IMMUTABLE RETRIEVAL SNAPSHOTS</span><h2 id="rq-results-title">数据集服务代次 G{response.dataset_serving_generation}</h2></div><p>本次检索绑定该服务代次。策略、结果和证据链快照不可变；后续索引变化不会改写这次实验。</p><code>{response.run_id}</code></div>
    <div className="rq-variant-summary-grid" style={{ "--rq-variant-count": variants.length } as CSSProperties}>{variants.map((view) => <Card bordered key={view.experimentId} className={`rq-variant-summary is-${view.state}`}><div className="rq-summary-head"><div><span className="rq-eyebrow">{view.experimentId}</span><h3>{view.name}</h3></div><Tag theme={statusColor(view.state) as "success" | "warning" | "danger"} variant="light">{view.state === "failed" ? "检索失败" : view.state === "no-hit" ? "没有召回结果" : "检索完成"}</Tag></div><dl className="rq-summary-facts"><div><dt>实际路由</dt><dd>{view.route ?? "—"}</dd></div><div><dt>耗时</dt><dd>{view.latencyMs} ms</dd></div><div><dt>结果</dt><dd>{view.resultCount}</dd></div><div><dt>重排</dt><dd>{view.reranked ? "已生效" : "未生效"}</dd></div><div><dt>降级</dt><dd>{view.degraded ? "是" : "否"}</dd></div><div><dt>Top K</dt><dd>{view.strategy.topK ?? "—"}</dd></div></dl>{view.state === "failed" ? <Alert theme="error" title="检索失败" message={`实验已按失败状态持久化${view.failureCode ? `：${view.failureCode}` : "。"}`} /> : view.state === "no-hit" ? <Alert theme="warning" title="没有召回结果" message="该策略成功执行，但没有命中可用证据。" /> : null}<LineageStrip view={view}/><SafeTracePanel traces={view.traces}/></Card>)}</div>
    <EvidenceComparisonTable variants={variants}/>
    <aside className="rq-neutral-note">默认保持中立。只有真实的操作者判断数据会进入判断对比，页面不自动指定优胜策略。</aside>
  </section>;
}


