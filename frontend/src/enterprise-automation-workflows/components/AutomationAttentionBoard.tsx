import { CheckCircleIcon, ErrorCircleIcon, TimeIcon } from "tdesign-icons-react";

import type { AutomationSummary } from "../model/automationModel";
import { Alert } from "../../ui";
import { formatAutomationCount } from "./automationUi";
import type { AutomationResource } from "./types";

export interface AutomationAttentionBoardProps {
  summary: AutomationResource<AutomationSummary>;
}

const metrics = [
  { key: "active_rule_count", label: "启用规则", tone: "primary", icon: <CheckCircleIcon /> },
  { key: "paused_rule_count", label: "暂停规则", tone: "warning", icon: <TimeIcon /> },
  { key: "failed_run_count", label: "失败运行", tone: "danger", icon: <ErrorCircleIcon /> },
  { key: "pending_request_count", label: "待处理请求", tone: "success", icon: <TimeIcon /> },
] as const;

export default function AutomationAttentionBoard({ summary }: AutomationAttentionBoardProps) {
  const value = summary.value;
  const summaryState = value?.state ?? summary.status;
  return (
    <section className="automation-workflows__attention" role="region" aria-label="自动化关注面板">
      <div className="automation-workflows__section-heading">
        <div>
          <span className="automation-workflows__eyebrow">AUTOMATION ATTENTION BOARD</span>
          <h2>自动化关注面板</h2>
          <p>只展示租户范围内已验证的规则、运行与请求事实。</p>
        </div>
        <span className="automation-workflows__attention-caption">
          <CheckCircleIcon aria-hidden="true" /> NO DIRECT EXECUTION
        </span>
      </div>
      {summaryState === "partial" ? (
        <div>
          <Alert
            theme="warning"
            title="自动化摘要部分可用"
            message="部分计数未返回，界面不会用推断值填充。"
          />
        </div>
      ) : null}
      {summaryState === "unavailable" ? (
        <div>
          <Alert
            theme="warning"
            title="自动化摘要暂不可用"
            message="当前无法读取自动化摘要，未返回的数据不会被估算。"
          />
        </div>
      ) : null}
      {summaryState === "error" ? (
        <div>
          <Alert
            theme="error"
            title="自动化摘要读取失败"
            message="当前无法读取自动化摘要，请稍后重试。"
          />
        </div>
      ) : null}
      <div className="automation-workflows__metric-grid">
        {metrics.map((metric) => {
          const count = value?.[metric.key] ?? null;
          return (
            <article
              className={`automation-workflows__metric automation-workflows__metric--${metric.tone}`}
              key={metric.key}
            >
              <span className="automation-workflows__metric-icon" aria-hidden="true">
                {metric.icon}
              </span>
              <div className="automation-workflows__metric-copy">
                <span>{metric.label}</span>
                <strong>{formatAutomationCount(count)}</strong>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}
