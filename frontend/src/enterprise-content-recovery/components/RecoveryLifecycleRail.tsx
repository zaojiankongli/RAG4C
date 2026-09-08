import type { CSSProperties, ReactNode } from "react";
import {
  ArrowRightIcon,
  CheckCircleIcon,
  ErrorCircleIcon,
  SecuredIcon,
  TimeIcon,
} from "tdesign-icons-react";

import type { RecoveryResourceStatus, RecoverySummary } from "./contentRecoveryTypes";

export type RecoveryRailStageStatus = "complete" | "current" | "pending" | "warning";

export interface RecoveryLifecycleRailProps {
  summary: RecoverySummary | null;
  status?: RecoveryResourceStatus;
}

interface RailStage {
  id: string;
  label: string;
  description: string;
  meta: string;
  status: RecoveryRailStageStatus;
  icon: ReactNode;
}

function stageIcon(status: RecoveryRailStageStatus, icon: ReactNode) {
  if (status === "complete") return <CheckCircleIcon />;
  if (status === "warning") return <ErrorCircleIcon />;
  if (status === "current") return icon;
  return <TimeIcon />;
}

export default function RecoveryLifecycleRail({
  summary,
  status = "ready",
}: RecoveryLifecycleRailProps) {
  const ready = status === "ready" || status === "partial";
  const held = summary?.held_count ?? null;
  const pending = summary?.purge_pending_count ?? summary?.pending_purge_count ?? null;
  const stages: RailStage[] = [
    {
      id: "recycle",
      label: "RECYCLE",
      description: "移入回收权威",
      meta:
        ready && summary?.recycled_count !== null && summary
          ? `${numberLabel(summary.recycled_count)} 条`
          : "未返回",
      status: ready ? "complete" : "pending",
      icon: <SecuredIcon />,
    },
    {
      id: "retain",
      label: "RETAIN",
      description: "按租户策略保留",
      meta: ready ? "保留期快照" : "等待权威",
      status: ready ? "current" : "pending",
      icon: <TimeIcon />,
    },
    {
      id: "hold-eligible",
      label: "HOLD / ELIGIBLE",
      description:
        held === null ? "法律保全与资格检查" : held > 0 ? "存在法律保全" : "可检查清除资格",
      meta: held === null ? "未返回" : `${numberLabel(held)} 条保全`,
      status: held !== null && held > 0 ? "warning" : ready ? "current" : "pending",
      icon: <SecuredIcon />,
    },
    {
      id: "restore-purge",
      label: "RESTORE / PURGE REQUEST",
      description: "恢复或提交清除审批",
      meta: pending === null ? "未返回" : `${numberLabel(pending)} 项待审批`,
      status: pending !== null && pending > 0 ? "current" : ready ? "pending" : "pending",
      icon: <ArrowRightIcon />,
    },
  ];

  return (
    <section
      className="content-recovery__rail-wrap"
      aria-label="内容生命周期治理"
      data-testid="recovery-lifecycle-rail"
    >
      <div className="content-recovery__section-heading">
        <div>
          <span className="content-recovery__eyebrow">LIFECYCLE GOVERNANCE</span>
          <h2>恢复边界与清除资格</h2>
          <p>每一步都由租户权威事实和版本栅栏驱动。</p>
        </div>
        <span className="content-recovery__rail-caption">RELEASE CONTROL / 0033</span>
      </div>
      <ol
        className="content-recovery__rail"
        aria-label="内容恢复生命周期"
        style={{ "--content-recovery-rail-count": stages.length } as CSSProperties}
      >
        {stages.map((stage) => (
          <li
            className={`content-recovery__rail-stage is-${stage.status}`}
            key={stage.id}
            aria-current={stage.status === "current" ? "step" : undefined}
          >
            <span className="content-recovery__rail-connector" aria-hidden="true" />
            <span className="content-recovery__rail-icon" aria-hidden="true">
              {stageIcon(stage.status, stage.icon)}
            </span>
            <span className="content-recovery__rail-copy">
              <strong>{stage.label}</strong>
              <span>{stage.description}</span>
              <small>{stage.meta}</small>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function numberLabel(value: number | null): string {
  return value === null ? "未返回" : new Intl.NumberFormat("zh-CN").format(value);
}
