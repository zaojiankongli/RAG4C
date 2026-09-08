import { ArrowRightIcon, CheckCircleIcon, ErrorCircleIcon, TimeIcon } from "tdesign-icons-react";
import type { ServingStageFact } from "../model/servingModel";
import { STAGE_DESCRIPTIONS, STAGE_LABELS, StageTag } from "./servingUi";

export default function ServingLifecycleRail({ stages }: { stages: ServingStageFact[] }) {
  return (
    <section className="knowledge-serving__rail-wrap" aria-labelledby="serving-rail-title">
      <div className="knowledge-serving__section-heading">
        <div>
          <span className="knowledge-serving__eyebrow">SERVING READINESS RAIL</span>
          <h2 id="serving-rail-title">SOURCE → PARSE → CHUNK → INDEX → SERVE</h2>
        </div>
        <span className="knowledge-serving__evidence-caption">五个阶段 · 顺序不可变</span>
      </div>
      <ol className="knowledge-serving__rail" aria-label="知识服务生命周期">
        {stages.map((stage, index) => (
          <li
            className={
              "knowledge-serving__rail-stage knowledge-serving__rail-stage--" + stage.state
            }
            key={stage.stage_code}
          >
            <div className="knowledge-serving__rail-node">
              {stage.state === "ready" ? (
                <CheckCircleIcon />
              ) : stage.state === "lagging" ? (
                <TimeIcon />
              ) : (
                <ErrorCircleIcon />
              )}
            </div>
            <div className="knowledge-serving__rail-copy">
              <span className="knowledge-serving__rail-code">{STAGE_LABELS[stage.stage_code]}</span>
              <strong>{STAGE_DESCRIPTIONS[stage.stage_code]}</strong>
              <div>
                <StageTag state={stage.state} />{" "}
                <small>
                  {stage.item_count.toLocaleString("zh-CN")} items · {stage.lag_seconds}s lag
                </small>
              </div>
              {stage.safe_error ? <p>{stage.safe_error}</p> : null}
            </div>
            {index < stages.length - 1 ? (
              <ArrowRightIcon className="knowledge-serving__rail-arrow" aria-hidden="true" />
            ) : null}
          </li>
        ))}
      </ol>
      {stages.length !== 5 ? (
        <div role="alert" className="knowledge-serving__rail-warning">
          Serving authority 未返回完整的五阶段证据。
        </div>
      ) : null}
    </section>
  );
}
