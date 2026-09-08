import { Tag } from "tdesign-react";
import { ArrowRightIcon, CheckCircleIcon, ErrorCircleIcon, TimeIcon } from "tdesign-icons-react";

import type { AutomationSummary } from "../model/automationModel";
import { formatAutomationCount } from "./automationUi";

export interface AutomationLifecycleRailProps {
  summary: AutomationSummary | null;
  activityCount: number;
}

export default function AutomationLifecycleRail({
  summary,
  activityCount,
}: AutomationLifecycleRailProps) {
  const stages = [
    {
      key: "when",
      label: "WHEN",
      title: "观察来源事件",
      value: "6 个显式触发器",
      status: "complete",
      icon: <CheckCircleIcon />,
    },
    {
      key: "if",
      label: "IF",
      title: "计算受限条件",
      value: `${formatAutomationCount(summary?.active_rule_count)} 条启用规则`,
      status: summary ? "current" : "pending",
      icon: <TimeIcon />,
    },
    {
      key: "request",
      label: "REQUEST",
      title: "生成动作请求",
      value: `${formatAutomationCount(summary?.pending_request_count)} 个待处理`,
      status: summary?.pending_request_count ? "warning" : summary ? "current" : "pending",
      icon: summary?.pending_request_count ? <ErrorCircleIcon /> : <TimeIcon />,
    },
    {
      key: "evidence",
      label: "EVIDENCE",
      title: "追加不可变证据",
      value: `${activityCount} 条事件链记录`,
      status: activityCount ? "complete" : "pending",
      icon: activityCount ? <CheckCircleIcon /> : <TimeIcon />,
    },
  ] as const;

  return (
    <section
      className="automation-workflows__rail-wrap"
      aria-labelledby="automation-lifecycle-title"
    >
      <div className="automation-workflows__section-heading">
        <div>
          <span className="automation-workflows__eyebrow">VERIFIED ORCHESTRATION PATH</span>
          <h2 id="automation-lifecycle-title">自动化编排路径</h2>
          <p>每一步都由固定适配器与不可变事实约束，页面不会直接执行下游动作。</p>
        </div>
        <Tag theme="primary" variant="light-outline" size="small">
          <ArrowRightIcon aria-hidden="true" /> WHEN → EVIDENCE
        </Tag>
      </div>
      <ol className="automation-workflows__rail" role="list" aria-label="自动化编排路径">
        {stages.map((stage, index) => (
          <li
            className={`automation-workflows__rail-stage automation-workflows__rail-stage--${stage.status}`}
            key={stage.key}
          >
            <span className="automation-workflows__rail-index">0{index + 1}</span>
            <span className="automation-workflows__rail-icon" aria-hidden="true">
              {stage.icon}
            </span>
            <div>
              <strong>{stage.label}</strong>
              <span>{stage.title}</span>
              <small>{stage.value}</small>
            </div>
            {index < stages.length - 1 ? (
              <span className="automation-workflows__rail-connector" aria-hidden="true" />
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}
