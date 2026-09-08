import { Button, Tag } from "tdesign-react";
import { RefreshIcon, SecuredIcon } from "tdesign-icons-react";

import type { RecoveryResourceStatus, RecoverySummary } from "./contentRecoveryTypes";
import { formatRecoveryDate, RecoveryBoundaryTag } from "./recoveryUi";

export interface ContentRecoveryHeaderProps {
  tenantLabel: string;
  summary: RecoverySummary | null;
  summaryStatus: RecoveryResourceStatus;
  capabilityReady?: boolean;
  readOnly?: boolean;
  refreshing?: boolean;
  onRefresh?: () => void;
}

function statusLabel(status: RecoveryResourceStatus, capabilityReady: boolean): string {
  if (!capabilityReady) return "能力未就绪";
  if (status === "ready") return "权威数据";
  if (status === "partial") return "部分可用";
  if (status === "empty") return "暂无条目";
  if (status === "loading") return "正在读取";
  if (status === "unavailable") return "暂不可用";
  if (status === "error") return "读取失败";
  return "等待读取";
}

function statusTheme(
  status: RecoveryResourceStatus,
  capabilityReady: boolean,
): "success" | "warning" | "danger" | "primary" | "default" {
  if (!capabilityReady) return "warning";
  if (status === "ready") return "success";
  if (status === "partial" || status === "loading") return "warning";
  if (status === "error") return "danger";
  return "default";
}

export default function ContentRecoveryHeader({
  tenantLabel,
  summary,
  summaryStatus,
  capabilityReady = true,
  readOnly = false,
  refreshing = false,
  onRefresh,
}: ContentRecoveryHeaderProps) {
  return (
    <header className="content-recovery__header" data-testid="content-recovery-header">
      <div className="content-recovery__brand-mark" aria-hidden="true">
        <SecuredIcon />
      </div>
      <div className="content-recovery__header-copy">
        <span className="content-recovery__eyebrow">CONTENT RECOVERY AUTHORITY</span>
        <h1>内容恢复中心</h1>
        <p>
          <span className="content-recovery__tenant-label">{tenantLabel}</span> ·
          回收、保留、法律保全与清除审批的统一治理面板
        </p>
      </div>
      <div className="content-recovery__header-actions">
        <div className="content-recovery__header-status" aria-label="回收中心状态">
          <Tag theme={statusTheme(summaryStatus, capabilityReady)} variant="light-outline">
            {statusLabel(summaryStatus, capabilityReady)}
          </Tag>
          <RecoveryBoundaryTag readOnly={readOnly} />
        </div>
        {summary?.as_of ? (
          <time className="content-recovery__as-of" dateTime={summary.as_of}>
            权威时间 {formatRecoveryDate(summary.as_of)}
          </time>
        ) : (
          <span className="content-recovery__as-of">等待权威时间</span>
        )}
        {onRefresh ? (
          <Button
            variant="outline"
            size="small"
            icon={<RefreshIcon />}
            loading={refreshing}
            disabled={refreshing || !capabilityReady}
            aria-label="刷新内容恢复中心"
            onClick={onRefresh}
          >
            刷新
          </Button>
        ) : null}
      </div>
    </header>
  );
}
