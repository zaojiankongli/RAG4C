import { Tag } from "tdesign-react";
import {
  ArrowRightIcon,
  CheckCircleIcon,
  ErrorCircleIcon,
  LoadingIcon,
  TimeIcon,
} from "tdesign-icons-react";

import type { TaskOperationsSummary } from "./taskOperationsTypes";
import { formatTaskCount } from "./taskOperationsUi";

export interface TaskOperationsLifecycleRailProps {
  summary: TaskOperationsSummary | null;
}

export default function TaskOperationsLifecycleRail({ summary }: TaskOperationsLifecycleRailProps) {
  const stages = [
    {
      key: "source",
      label: "SOURCE",
      title: "来源",
      value: "Tenant 数据源",
      icon: <CheckCircleIcon />,
      status: "complete",
    },
    {
      key: "queue",
      label: "QUEUE",
      title: "队列",
      value: `${formatTaskCount(summary?.queued_count)} 等待`,
      icon: <TimeIcon />,
      status: summary ? "current" : "pending",
    },
    {
      key: "attempt",
      label: "ATTEMPT",
      title: "尝试",
      value: `${formatTaskCount(summary?.running_count)} 活跃`,
      icon: <LoadingIcon />,
      status: summary ? "current" : "pending",
    },
    {
      key: "outcome",
      label: "OUTCOME",
      title: "结果",
      value: `${formatTaskCount(summary?.completed_count)} 完成 · ${formatTaskCount(summary?.failed_count)} 失败`,
      icon: summary?.failed_count ? <ErrorCircleIcon /> : <CheckCircleIcon />,
      status: summary?.failed_count ? "warning" : "complete",
    },
  ] as const;

  return (
    <section className="task-operations__rail-wrap" aria-labelledby="task-operations-rail-title">
      <div className="task-operations__section-heading">
        <div>
          <span className="task-operations__eyebrow">EXECUTION TRACE</span>
          <h2 id="task-operations-rail-title">任务执行链路</h2>
          <p>每个任务都沿同一条可审计路径推进，状态不会被 UI 猜测。</p>
        </div>
        <span className="task-operations__rail-caption">
          <ArrowRightIcon aria-hidden="true" /> SOURCE → OUTCOME
        </span>
      </div>
      <ol className="task-operations__rail" role="list" aria-label="任务执行生命周期">
        {stages.map((stage, index) => (
          <li
            className={`task-operations__rail-stage task-operations__rail-stage--${stage.status}`}
            key={stage.key}
          >
            <span className="task-operations__rail-index">0{index + 1}</span>
            <span className="task-operations__rail-icon" aria-hidden="true">
              {stage.icon}
            </span>
            <div>
              <strong>{stage.label}</strong>
              <span>{stage.title}</span>
              <small>{stage.value}</small>
            </div>
            {index < stages.length - 1 ? (
              <span className="task-operations__rail-connector" aria-hidden="true" />
            ) : null}
          </li>
        ))}
      </ol>
      <div className="task-operations__rail-note">
        <Tag theme="success" variant="light-outline" size="small">
          状态全程留痕
        </Tag>
        <span>队列领取、执行尝试、最终结果均保留 revision 与事件链。</span>
      </div>
    </section>
  );
}
