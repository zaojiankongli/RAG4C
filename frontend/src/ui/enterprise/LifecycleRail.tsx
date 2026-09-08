import type { CSSProperties, ReactNode } from "react";
import {
  CheckCircleIcon,
  ErrorCircleIcon,
  LoadingIcon,
  TimeIcon,
} from "tdesign-icons-react";

export type LifecycleStageStatus = "complete" | "current" | "pending" | "warning" | "error";

export interface LifecycleStage {
  id: string;
  label: string;
  status: LifecycleStageStatus;
  description?: ReactNode;
  meta?: ReactNode;
}

export interface LifecycleRailProps {
  stages: LifecycleStage[];
  ariaLabel?: string;
  orientation?: "horizontal" | "vertical";
}

function StageIcon({ status }: { status: LifecycleStageStatus }) {
  if (status === "complete") return <CheckCircleIcon />;
  if (status === "current") return <LoadingIcon />;
  if (status === "warning" || status === "error") return <ErrorCircleIcon />;
  return <TimeIcon />;
}

/** 面向知识运营的处理链路，比通用进度条保留更多异常、当前阶段和运营元数据。 */
export default function LifecycleRail({
  stages,
  ariaLabel = "知识处理链路",
  orientation = "horizontal",
}: LifecycleRailProps) {
  return (
    <ol
      className={`enterprise-lifecycle-rail is-${orientation}`}
      aria-label={ariaLabel}
      data-orientation={orientation}
      style={{ "--enterprise-lifecycle-count": Math.max(stages.length, 1) } as CSSProperties}
    >
      {stages.map((stage) => (
        <li
          key={stage.id}
          className={`enterprise-lifecycle-rail__stage is-${stage.status}`}
          aria-current={stage.status === "current" ? "step" : undefined}
        >
          <span className="enterprise-lifecycle-rail__connector" aria-hidden="true" />
          <span className="enterprise-lifecycle-rail__icon" aria-hidden="true">
            <StageIcon status={stage.status} />
          </span>
          <span className="enterprise-lifecycle-rail__copy">
            <strong>{stage.label}</strong>
            {stage.description ? <span>{stage.description}</span> : null}
            {stage.meta ? <small>{stage.meta}</small> : null}
          </span>
        </li>
      ))}
    </ol>
  );
}

