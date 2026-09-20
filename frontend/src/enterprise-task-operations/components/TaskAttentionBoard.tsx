import { Tag } from "tdesign-react";
import { ErrorCircleIcon, LoadingIcon, TimeIcon } from "tdesign-icons-react";

import type { TaskOperationsSummary } from "./taskOperationsTypes";
import { formatTaskCount } from "./taskOperationsUi";

export interface TaskAttentionBoardProps {
  summary: TaskOperationsSummary | null;
}

const metrics: Array<{
  key: keyof Pick<
    TaskOperationsSummary,
    | "queued_count"
    | "running_count"
    | "failed_count"
    | "completed_count"
    | "stale_count"
    | "reconciliation_count"
  >;
  label: string;
  hint: string;
  tone: "neutral" | "primary" | "danger" | "success" | "warning";
}> = [
  { key: "failed_count", label: "失败待处理", hint: "需要人工确认的失败尝试", tone: "danger" },
  { key: "running_count", label: "正在运行", hint: "当前活跃执行尝试", tone: "primary" },
  { key: "queued_count", label: "队列等待", hint: "尚未领取执行器的任务", tone: "warning" },
  { key: "completed_count", label: "已完成", hint: "权威结果已落库", tone: "success" },
  { key: "stale_count", label: "陈旧状态", hint: "需要对账的记录", tone: "neutral" },
  { key: "reconciliation_count", label: "对账事项", hint: "待处理状态差异", tone: "neutral" },
];

export default function TaskAttentionBoard({ summary }: TaskAttentionBoardProps) {
  return (
    <section className="task-operations__attention" role="region" aria-label="任务运营关注面板">
      <div className="task-operations__section-heading">
        <div>
          <span className="task-operations__eyebrow">ATTENTION BOARD</span>
          <h2>运行关注面板</h2>
          <p>先看需要介入的状态，再进入任务明细处理。</p>
        </div>
        <Tag theme="primary" variant="light-outline">
          租户范围内
        </Tag>
      </div>
      <div className="task-operations__metric-grid">
        {metrics.map((metric) => (
          <article
            className={`task-operations__metric task-operations__metric--${metric.tone}`}
            key={metric.key}
          >
            <div className="task-operations__metric-icon" aria-hidden="true">
              {metric.tone === "danger" ? (
                <ErrorCircleIcon />
              ) : metric.tone === "primary" ? (
                <LoadingIcon />
              ) : (
                <TimeIcon />
              )}
            </div>
            <div className="task-operations__metric-copy">
              <span>{metric.label}</span>
              <strong>{formatTaskCount(summary?.[metric.key])}</strong>
              <small>{metric.hint}</small>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
