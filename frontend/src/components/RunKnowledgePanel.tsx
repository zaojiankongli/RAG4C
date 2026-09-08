import type {
  RunKnowledgeComponentFact,
  RunKnowledgeProjection,
  RunKnowledgeVerificationLayerFact,
} from "../run/serverRunProjection";
import type { RunNodeRollupDto } from "../types/runs";

const MISSING = "本次运行未记录该项";
const COMPONENTS: Array<[keyof RunKnowledgeProjection["components"], string]> = [
  ["hyde", "HyDE"], ["subqueries", "SubQueries"], ["stepback", "Stepback"],
  ["graph", "图检索"], ["rerank", "重排"], ["sentenceWindow", "父段落窗口"],
];
const VERIFICATION_NODE_IDS = new Set([
  "verify", "verify.l1", "verify_l1", "verify/l1", "verify.l2", "verify_l2", "verify/l2",
  "verify.l3", "verify_l3", "verify/l3",
]);
const STATE_LABEL = { enabled: "已启用", skipped: "已跳过", degraded: "已降级" } as const;
function duration(value: number | undefined | null): string {
  if (value === undefined || value === null) return MISSING;
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${Math.round(value)}ms`;
}
function value(value: string | number | undefined): string | number {
  return value === undefined ? MISSING : value;
}
function reason(rollup: RunNodeRollupDto): string {
  return rollup.degraded_reason ?? rollup.error_code ?? rollup.retry_reason ?? MISSING;
}
function ComponentFact({ label, fact }: { label: string; fact?: RunKnowledgeComponentFact }) {
  return <article className={`run-knowledge-component ${fact ? `is-${fact.state}` : "is-missing"}`} aria-label={label}>
    <h3>{label}</h3>
    <dl><div><dt>状态</dt><dd>{fact ? STATE_LABEL[fact.state] : MISSING}</dd></div><div><dt>Attempt</dt><dd>{fact?.attempt ?? MISSING}</dd></div><div><dt>耗时</dt><dd>{duration(fact?.durationMs)}</dd></div><div><dt>原因</dt><dd>{fact?.reason ?? MISSING}</dd></div></dl>
  </article>;
}
function VerificationLayer({ label, fact, fallbackCount }: { label: string; fact?: RunKnowledgeVerificationLayerFact; fallbackCount?: number }) {
  return <article className="run-knowledge-component run-verification-layer" aria-label={label}>
    <h3>{label}</h3>
    <dl>
      <div><dt>状态</dt><dd>{fact?.status ?? MISSING}</dd></div>
      <div><dt>Attempt</dt><dd>{fact?.attempt ?? MISSING}</dd></div>
      <div><dt>耗时</dt><dd>{duration(fact?.durationMs)}</dd></div>
      <div><dt>聚合计数</dt><dd>{fact?.count ?? fallbackCount ?? MISSING}</dd></div>
      <div><dt>原因</dt><dd>{fact?.reason ?? MISSING}</dd></div>
    </dl>
  </article>;
}
export default function RunKnowledgePanel({ knowledge, nodeRollup }: { knowledge: RunKnowledgeProjection; nodeRollup: readonly RunNodeRollupDto[] }) {
  const verificationLayers = knowledge.verificationLayers ?? {};
  const verificationRollup = nodeRollup.filter(({ node_id }) => VERIFICATION_NODE_IDS.has(node_id)).sort((a, b) => a.attempt - b.attempt || a.node_id.localeCompare(b.node_id));
  return <section className="run-knowledge-panel" aria-labelledby="run-knowledge-heading">
    <h2 id="run-knowledge-heading">资料关联</h2>
    <section aria-labelledby="knowledge-route-heading"><h3 id="knowledge-route-heading">路由与安全计数</h3><dl className="run-knowledge-facts">
      <div><dt>配置路由</dt><dd>{value(knowledge.route)}</dd></div><div><dt>实际路由</dt><dd>{value(knowledge.effectiveRoute)}</dd></div><div><dt>候选数</dt><dd>{value(knowledge.candidateCount)}</dd></div><div><dt>片段数</dt><dd>{value(knowledge.chunkCount)}</dd></div><div><dt>来源数</dt><dd>{value(knowledge.sourceCount)}</dd></div><div><dt>引用数</dt><dd>{value(knowledge.citationCount)}</dd></div><div><dt>降级原因</dt><dd>{value(knowledge.degradedReason)}</dd></div><div><dt>回退原因</dt><dd>{value(knowledge.fallbackReason)}</dd></div>
    </dl></section>
    <section aria-labelledby="knowledge-component-heading"><h3 id="knowledge-component-heading">检索组件</h3><div className="run-knowledge-components">{COMPONENTS.map(([key, label]) => <ComponentFact key={key} label={label} fact={knowledge.components[key]} />)}</div></section>
    <section aria-labelledby="knowledge-verification-heading"><h3 id="knowledge-verification-heading">核验层级</h3><div className="run-knowledge-components run-verification-layers">
      <VerificationLayer label="L1 引用检查" fact={verificationLayers.l1} fallbackCount={knowledge.citationCount} />
      <VerificationLayer label="L2 文本校验" fact={verificationLayers.l2} />
      <VerificationLayer label="L3 蕴含核验" fact={verificationLayers.l3} />
    </div></section>
    <table className="run-knowledge-attempts"><caption>核验 Attempt 对比</caption><thead><tr><th scope="col">节点</th><th scope="col">Attempt</th><th scope="col">状态</th><th scope="col">耗时</th><th scope="col">原因</th></tr></thead><tbody>{verificationRollup.length ? verificationRollup.map((rollup) => <tr key={`${rollup.node_id}:${rollup.attempt}`}><td><code>{rollup.node_id}</code></td><th scope="row">Attempt {rollup.attempt}</th><td>{rollup.status}</td><td>{duration(rollup.duration_ms)}</td><td>{reason(rollup)}</td></tr>) : <tr><td colSpan={5}>{MISSING}</td></tr>}</tbody></table>
  </section>;
}
