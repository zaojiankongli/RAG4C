import { Button, Tag } from "tdesign-react";
import { AddIcon, RefreshIcon } from "tdesign-icons-react";

import {
  AutomationAuthorityIcon,
  AutomationBoundaryTag,
  formatAutomationDate,
} from "./automationUi";

export interface AutomationHeaderProps {
  tenantLabel?: string;
  asOf?: string | null;
  readOnly?: boolean;
  onRefresh?: () => void;
  onCreateRule?: () => void;
}

export default function AutomationHeader({
  tenantLabel = "当前租户",
  asOf,
  readOnly = false,
  onRefresh,
  onCreateRule,
}: AutomationHeaderProps) {
  return (
    <header className="automation-workflows__header">
      <div className="automation-workflows__title-block">
        <div className="automation-workflows__brand-mark" aria-hidden="true">
          <AutomationAuthorityIcon />
        </div>
        <div className="automation-workflows__title-copy">
          <span className="automation-workflows__eyebrow">ENTERPRISE AUTOMATION CENTER</span>
          <h1 id="automation-center-title">自动化中心</h1>
          <p>
            <span className="automation-workflows__tenant-label">{tenantLabel}</span>
            <span aria-hidden="true"> · 规则、受限请求与不可变证据的统一控制面</span>
          </p>
        </div>
      </div>
      <div className="automation-workflows__header-actions">
        <div
          className="automation-workflows__authority-badge"
          role="status"
          aria-label="租户自动化权威"
        >
          <AutomationAuthorityIcon />
          <span>租户自动化权威</span>
          <Tag theme="success" variant="light-outline" size="small">
            已连接
          </Tag>
        </div>
        <AutomationBoundaryTag readOnly={readOnly} />
        {asOf ? (
          <span className="automation-workflows__as-of">权威时间 {formatAutomationDate(asOf)}</span>
        ) : null}
        {onRefresh ? (
          <Button variant="outline" size="small" icon={<RefreshIcon />} onClick={onRefresh}>
            刷新状态
          </Button>
        ) : null}
        {onCreateRule ? (
          <Button
            theme="primary"
            size="small"
            icon={<AddIcon />}
            aria-disabled={readOnly}
            className={readOnly ? "automation-workflows__button-is-disabled" : undefined}
            onClick={() => {
              if (!readOnly) onCreateRule();
            }}
          >
            新建自动化规则
          </Button>
        ) : null}
      </div>
    </header>
  );
}
