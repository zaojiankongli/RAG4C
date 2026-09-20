import { Alert, Tag } from "tdesign-react";
import { ArrowRightIcon, SecuredIcon } from "tdesign-icons-react";

import { actionLabel, conditionLabel, triggerLabel } from "./automationUi";
import type { AutomationRuleDefinitionInput } from "./types";

export interface AutomationPreviewPanelProps {
  definition: AutomationRuleDefinitionInput;
  name: string;
  reason: string;
}

export default function AutomationPreviewPanel({
  definition,
  name,
  reason,
}: AutomationPreviewPanelProps) {
  return (
    <section className="automation-workflows__preview" data-testid="automation-preview-panel">
      <div className="automation-workflows__preview-heading">
        <div>
          <span className="automation-workflows__eyebrow">SAFE PREVIEW</span>
          <h3>规则预览</h3>
          <p>
            {name.trim() || "未命名规则"} · {reason.trim() || "未填写变更原因"}
          </p>
        </div>
        <Tag theme="success" variant="light-outline" size="small">
          <SecuredIcon aria-hidden="true" /> 只读演练
        </Tag>
      </div>
      <Alert
        theme="info"
        title="预览不会执行真实动作"
        message="只生成受限 Action Request，不发送通知、不执行审批、不重试任务，也不会修改来源权威。"
      />
      <ol className="automation-workflows__preview-steps" aria-label="预览路径">
        <li>
          <strong>WHEN</strong>
          <span>{triggerLabel(definition.trigger_code)}</span>
        </li>
        <li>
          <strong>IF</strong>
          <span>{conditionLabel(definition.condition_code)}</span>
        </li>
        <li>
          <strong>REQUEST</strong>
          <span>
            {definition.action_plan.map((step) => actionLabel(step.action_code)).join("、") ||
              "未配置动作"}
          </span>
        </li>
        <li>
          <strong>EVIDENCE</strong>
          <span>追加不可变事件</span>
        </li>
      </ol>
      <div className="automation-workflows__preview-note">
        <ArrowRightIcon aria-hidden="true" />
        <span>通过预览只验证 schema 与摘要是否一致；发布仍需由上层权威完成。</span>
      </div>
    </section>
  );
}
