import { Tag } from "tdesign-react";
import { CheckCircleIcon, ErrorCircleIcon, InfoCircleIcon } from "tdesign-icons-react";
import type { EnterpriseCapability } from "../model/enterpriseAdminModel";

export interface CapabilityStateProps {
  capability: EnterpriseCapability;
}

const PRESENTATION = {
  ready: {
    label: "已接入",
    theme: "success" as const,
    icon: <CheckCircleIcon />,
  },
  limited: {
    label: "能力受限",
    theme: "warning" as const,
    icon: <InfoCircleIcon />,
  },
  unavailable: {
    label: "尚未接入",
    theme: "default" as const,
    icon: <ErrorCircleIcon />,
  },
};

export default function CapabilityState({ capability }: CapabilityStateProps) {
  const presentation = PRESENTATION[capability.state];
  return (
    <article className={`enterprise-capability-state is-${capability.state}`}>
      <span className="enterprise-capability-state__icon" aria-hidden="true">
        {presentation.icon}
      </span>
      <div className="enterprise-capability-state__copy">
        <strong>{capability.label}</strong>
        <span>{capability.reason || "服务端已确认该能力可用"}</span>
      </div>
      <Tag theme={presentation.theme} variant="light-outline" size="small">
        {presentation.label}
      </Tag>
    </article>
  );
}
