import { Card } from "tdesign-react";
import type { Agreement } from "../model/contracts";
export default function AgreementPanel({ agreement }: { agreement: Agreement | null }) {
  return <Card bordered className="rq-agreement" header="判断一致性"><dl>{[
    ["已判断结果", agreement?.judged_results], ["判断总数", agreement?.judgment_count], ["多人判断", agreement?.multi_judged_results], ["一致结果", agreement?.unanimous_results], ["冲突结果", agreement?.conflicting_results], ["一致率", agreement?.exact_agreement_rate === null || agreement?.exact_agreement_rate === undefined ? "—" : `${(agreement.exact_agreement_rate * 100).toFixed(1)}%`], ["平均分", agreement?.mean_score === null || agreement?.mean_score === undefined ? "—" : agreement.mean_score.toFixed(2)],
  ].map(([label,value]) => <div key={String(label)}><dt>{label}</dt><dd>{value ?? 0}</dd></div>)}</dl><p>相关 {agreement?.label_counts.relevant ?? 0} · 部分相关 {agreement?.label_counts.partial ?? 0} · 不相关 {agreement?.label_counts.irrelevant ?? 0}</p></Card>;
}
