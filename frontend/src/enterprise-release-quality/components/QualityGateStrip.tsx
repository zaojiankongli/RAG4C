import { Card, Tag } from "tdesign-react";
import {
  CheckCircleIcon,
  ErrorCircleIcon,
  InfoCircleIcon,
  SecuredIcon,
  TimeIcon,
} from "tdesign-icons-react";
import type { QualityGate } from "../model/qualityModel";
import "../release-quality.css";

export interface QualityGateStripProps {
  status: "idle" | "loading" | "ready" | "error";
  gate: QualityGate | null;
  error: Error | null;
}

function gateStateLabel(state: string): string {
  return (
    {
      passed: "已通过",
      failed: "未通过",
      blocked: "已阻断",
      waived: "已豁免",
      unavailable: "事实不可用",
      not_required: "无需门禁",
      loading: "核验中",
      idle: "未读取",
      error: "读取失败",
      ready: "已读取",
    }[state] ?? "未返回"
  );
}

function gatePresentation(gate: QualityGate | null) {
  if (!gate) {
    return {
      icon: <InfoCircleIcon />,
      theme: "default" as const,
      title: "质量门禁未返回",
      detail: "等待 Release 与 Channel 质量权威。",
    };
  }
  if (gate.state === "passed") {
    return {
      icon: <CheckCircleIcon />,
      theme: "success" as const,
      title: "质量门禁已通过",
      detail: gate.policy ? `${gate.policy.name} · R${gate.policy.revision}` : "认证已通过",
    };
  }
  if (gate.state === "waived") {
    return {
      icon: <SecuredIcon />,
      theme: "warning" as const,
      title: "已使用审批豁免",
      detail: gate.policy ? `${gate.policy.name} · R${gate.policy.revision}` : "审批豁免有效",
    };
  }
  if (gate.state === "not_required") {
    return {
      icon: <InfoCircleIcon />,
      theme: "default" as const,
      title: "当前 Channel 无强制门禁",
      detail: "低风险 Channel 未匹配质量策略。",
    };
  }
  if (gate.state === "blocked") {
    return {
      icon: <TimeIcon />,
      theme: "warning" as const,
      title: "质量门禁阻断发布",
      detail: gate.reason,
    };
  }
  return {
    icon: <ErrorCircleIcon />,
    theme: "danger" as const,
    title: "质量事实不可用",
    detail: gate.reason,
  };
}

export default function QualityGateStrip({ status, gate, error }: QualityGateStripProps) {
  const presentation = gatePresentation(gate);
  return (
    <Card
      className={`release-quality-gate-strip release-quality-gate-strip--${presentation.theme}`}
      size="small"
      bordered
    >
      <section role="region" aria-label="Release Quality Gate">
        <div className="release-quality-gate-strip__icon" aria-hidden="true">
          {presentation.icon}
        </div>
        <div className="release-quality-gate-strip__body">
          <span className="release-quality-kicker">QUALITY PUBLISH GATE</span>
          <strong>{status === "loading" ? "正在核验质量权威" : presentation.title}</strong>
          <span>{error ? "服务端没有返回可验证的质量事实。" : presentation.detail}</span>
        </div>
        <div className="release-quality-gate-strip__meta">
          <Tag theme={presentation.theme} variant="light-outline">
            {gateStateLabel(gate?.state ?? status)}
          </Tag>
          <span>
            {gate?.channel_revision ? `Channel R${gate.channel_revision}` : "Revision 未返回"}
          </span>
        </div>
      </section>
    </Card>
  );
}
