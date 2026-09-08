import { DeleteTimeIcon, SecuredIcon, TimeIcon, UploadIcon } from "tdesign-icons-react";

import MetricStrip, { type MetricStripItem } from "../../ui/enterprise/MetricStrip";
import type { RecoveryResourceStatus, RecoverySummary } from "./contentRecoveryTypes";
import { numberLabel } from "./recoveryUi";

export interface ContentRecoveryMetricStripProps {
  summary: RecoverySummary | null;
  status: RecoveryResourceStatus;
}

function metricValue(summary: RecoverySummary | null, key: keyof RecoverySummary): string {
  if (!summary || summary.state === "unavailable" || summary.state === "error") return "未返回";
  const value =
    key === "purge_pending_count"
      ? (summary.purge_pending_count ?? summary.pending_purge_count)
      : summary[key];
  return typeof value === "number" ? numberLabel(value) : "未返回";
}

export default function ContentRecoveryMetricStrip({
  summary,
  status,
}: ContentRecoveryMetricStripProps) {
  const metrics: MetricStripItem[] = [
    {
      id: "recycled",
      label: "已回收",
      value: metricValue(summary, "recycled_count"),
      unit: "条",
      tone: "primary",
      icon: <DeleteTimeIcon />,
      hint: "当前租户中仍由回收权威管理的文档条目",
    },
    {
      id: "expiring",
      label: "即将到期",
      value: metricValue(summary, "expiring_count"),
      unit: "条",
      tone: "warning",
      icon: <TimeIcon />,
      hint: "进入清除资格窗口的保留条目，仍需经过法律保全与审批检查",
    },
    {
      id: "held",
      label: "法律保留",
      value: metricValue(summary, "held_count"),
      unit: "条",
      tone: "success",
      icon: <SecuredIcon />,
      hint: "存在有效法律保全、因此禁止提交永久清除申请的条目",
    },
    {
      id: "purge-pending",
      label: "待审批清除",
      value: metricValue(summary, "purge_pending_count"),
      unit: "项",
      tone: "danger",
      icon: <UploadIcon />,
      hint: "已提交审批申请但本中心不会批准或执行物理清除",
    },
  ];

  return (
    <div
      className={`content-recovery__metrics-wrap is-${status}`}
      data-testid="recovery-metric-strip"
    >
      <MetricStrip metrics={metrics} ariaLabel="回收治理核心指标" columns={4} />
      {status === "partial" ? (
        <span className="content-recovery__metrics-note">指标以当前可验证的租户事实为准</span>
      ) : null}
    </div>
  );
}
