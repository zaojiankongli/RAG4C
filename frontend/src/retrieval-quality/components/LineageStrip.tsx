import type { VariantView } from "../model/contracts";

export default function LineageStrip({ view }: { view: VariantView }) {
  return <div className="rq-lineage-strip" aria-label={`${view.name} 知识生命线`}>
    <span>策略快照 r{view.strategy.revision ?? "—"}</span><i aria-hidden="true"/><span>结果快照</span><i aria-hidden="true"/><span>证据链</span><i aria-hidden="true"/><span>服务代次 G{view.datasetServingGeneration ?? "—"}</span>
  </div>;
}
