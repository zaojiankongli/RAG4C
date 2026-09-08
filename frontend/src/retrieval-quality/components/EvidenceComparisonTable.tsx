import type { VariantView } from "../model/contracts";

export default function EvidenceComparisonTable({ variants }: { variants: VariantView[] }) {
  const ranks = Array.from(new Set(variants.flatMap((variant) => variant.evidence.map((item) => item.rank)))).sort((a, b) => a - b);
  return <div className="rq-table-scroll" role="region" tabIndex={0} aria-label="证据排名对比，可横向滚动">
    <table className="rq-evidence-table" aria-label="证据排名对比"><caption>证据排名对比</caption><thead><tr><th scope="col">排名</th>{variants.map((variant) => <th scope="col" key={variant.experimentId}>{variant.name}</th>)}</tr></thead>
      <tbody>{ranks.length ? ranks.map((rank) => <tr key={rank}><th scope="row">#{rank}</th>{variants.map((variant) => { const item = variant.evidence.find((evidence) => evidence.rank === rank); return <td key={variant.experimentId}>{item ? <article className="rq-evidence-cell"><div className="rq-evidence-score"><strong>{item.score === null ? "—" : item.score.toFixed(3)}</strong><span>{item.branch ?? "分支未知"}</span></div><p>{item.excerpt ?? "快照未提供可安全展示的摘录。"}</p><dl><div><dt>文档</dt><dd>{item.documentId ?? "—"}</dd></div><div><dt>切片</dt><dd>{item.chunkId ?? "—"}</dd></div><div><dt>版本</dt><dd>D{item.documentRevision ?? "—"} · C{item.contentRevision ?? "—"}</dd></div><div><dt>内容哈希</dt><dd>{item.contentHash ?? "—"}</dd></div></dl>{item.lineage ? <small>证据链：{item.lineage.documentId ?? "—"} / {item.lineage.chunkId ?? "—"} · D{item.lineage.documentRevision ?? "—"} · C{item.lineage.contentRevision ?? "—"}</small> : null}</article> : <span className="rq-rank-missing">该策略没有此排名结果</span>}</td>; })}</tr>) : <tr><td colSpan={variants.length + 1}>所有策略均没有召回结果。</td></tr>}</tbody>
    </table>
  </div>;
}
