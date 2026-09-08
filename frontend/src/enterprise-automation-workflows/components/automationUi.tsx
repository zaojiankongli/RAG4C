import { useCallback, useEffect, useRef, type RefObject } from "react";
import { Alert, Empty, Loading, Tag } from "tdesign-react";
import { ErrorCircleIcon, SecuredIcon } from "tdesign-icons-react";

import type {
  AutomationActionCode,
  AutomationConditionCode,
  AutomationRuleStatus,
  AutomationRunStatus,
  AutomationRequestStatus,
  AutomationTriggerCode,
} from "../model/automationModel";
import type { AutomationResourceStatus } from "./types";

export function formatAutomationDate(value: string | null | undefined): string {
  if (!value) return "未返回";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(parsed);
}

export function formatAutomationCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return "未返回";
  return new Intl.NumberFormat("zh-CN").format(value);
}

export function triggerLabel(code: AutomationTriggerCode): string {
  const labels: Record<AutomationTriggerCode, string> = {
    task_failed: "任务失败",
    task_source_stale: "任务来源过期",
    source_sync_failed: "来源同步失败",
    release_quality_alert_opened: "质量告警打开",
    release_recertification_blocked: "复认证阻塞",
    approval_request_terminal: "审批请求终态",
  };
  return labels[code];
}

export function conditionLabel(code: AutomationConditionCode): string {
  const labels: Record<AutomationConditionCode, string> = {
    always: "始终满足",
    status_is: "状态等于",
    action_required: "需要操作",
    severity_at_least: "严重级别至少",
    attempt_exhausted: "尝试次数耗尽",
    source_is_stale: "来源已过期",
  };
  return labels[code];
}

export function actionLabel(code: AutomationActionCode): string {
  const labels: Record<AutomationActionCode, string> = {
    notify_operator: "通知操作员",
    request_approval: "请求审批",
    open_task_attention: "打开任务关注",
    pause_rule: "暂停规则",
  };
  return labels[code];
}

export function ruleStatusLabel(status: AutomationRuleStatus): string {
  const labels: Record<AutomationRuleStatus, string> = {
    draft: "草稿",
    active: "已启用",
    paused: "已暂停",
    archived: "已归档",
  };
  return labels[status];
}

export function runStatusLabel(status: AutomationRunStatus): string {
  const labels: Record<AutomationRunStatus, string> = {
    started: "已开始",
    not_matched: "条件未命中",
    requested: "执行请求已生成",
    completed: "已完成",
    failed: "执行失败",
    blocked: "已阻塞",
  };
  return labels[status];
}

export function requestStatusLabel(status: AutomationRequestStatus): string {
  const labels: Record<AutomationRequestStatus, string> = {
    requested: "待处理",
    dispatched: "已分发",
    applied: "已应用",
    rejected: "已拒绝",
    expired: "已过期",
  };
  return labels[status];
}

export function statusTheme(
  status: string,
): "success" | "warning" | "danger" | "primary" | "default" {
  if (["active", "completed", "applied"].includes(status)) return "success";
  if (["requested", "started", "draft"].includes(status)) return "primary";
  if (["failed", "blocked", "rejected"].includes(status)) return "danger";
  if (["paused", "archived", "expired", "not_matched"].includes(status)) return "warning";
  return "default";
}

const SAFE_SNAPSHOT_KEYS: Record<string, string> = {
  action_code: "动作",
  approval_request_id: "审批请求",
  attempt_number: "尝试次数",
  condition_code: "条件",
  event_digest: "事件摘要",
  revision: "revision",
  severity: "严重级别",
  source_digest: "来源摘要",
  source_id: "来源",
  source_revision: "来源修订",
  status: "状态",
  task_id: "任务",
  target_id: "目标",
  target_kind: "目标类型",
  trigger_code: "触发器",
};

export function safeSnapshotEntries(
  snapshot: Readonly<Record<string, string | number | boolean | null>> | undefined,
): Array<[string, string]> {
  return Object.entries(snapshot ?? {})
    .filter(([key]) => key in SAFE_SNAPSHOT_KEYS)
    .map(([key, value]) => [SAFE_SNAPSHOT_KEYS[key], value === null ? "未返回" : String(value)]);
}

export function useAutomationFocusReturn(): {
  capture: () => void;
  restore: () => void;
  returnFocusRef: RefObject<HTMLElement | null>;
} {
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const capture = useCallback(() => {
    if (typeof document !== "undefined" && document.activeElement instanceof HTMLElement) {
      returnFocusRef.current = document.activeElement;
    }
  }, []);
  const restore = useCallback(() => {
    const target = returnFocusRef.current;
    if (!target || !target.isConnected) return;
    window.setTimeout(() => target.focus(), 0);
  }, []);
  return { capture, restore, returnFocusRef };
}

export function useAutomationEscape(onClose: () => void, active: boolean): void {
  useEffect(() => {
    if (!active) return undefined;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [active, onClose]);
}

export function AutomationBoundaryTag({ readOnly }: { readOnly: boolean }) {
  return readOnly ? (
    <Tag theme="warning" variant="light-outline" size="small">
      只读模式
    </Tag>
  ) : (
    <Tag theme="success" variant="light-outline" size="small">
      仅请求，不执行
    </Tag>
  );
}

export function AutomationStateNotice({
  status,
  invalidItemCount = 0,
  hasItems = false,
  resourceLabel = "自动化记录",
  emptyTitle = "暂无自动化记录",
  emptyDescription = "规则、运行与请求事实将在安全写入后显示。",
}: {
  status: AutomationResourceStatus;
  invalidItemCount?: number;
  hasItems?: boolean;
  resourceLabel?: string;
  emptyTitle?: string;
  emptyDescription?: string;
}) {
  if (status === "loading" && !hasItems) {
    return (
      <div className="automation-workflows__state automation-workflows__state--loading">
        <Loading text={`正在读取${resourceLabel}…`} />
      </div>
    );
  }
  if (status === "unavailable") {
    return (
      <div className="automation-workflows__state-alert" role="alert">
        <Alert
          theme="warning"
          title={`${resourceLabel}暂不可用`}
          message={`当前无法读取${resourceLabel}，未返回的数据不会被估算。`}
        />
      </div>
    );
  }
  if (status === "error") {
    return (
      <div className="automation-workflows__state-alert" role="alert">
        <Alert
          theme="error"
          title={`${resourceLabel}读取失败`}
          message={`当前${resourceLabel}读取失败，请稍后重试。`}
        />
      </div>
    );
  }
  if (status === "partial") {
    return (
      <div className="automation-workflows__state-alert" role="alert">
        <Alert
          theme="warning"
          title={`部分${resourceLabel}无法读取`}
          message={`${invalidItemCount} 条记录未通过安全校验，已从界面隐藏。`}
        />
      </div>
    );
  }
  if ((status === "ready" || status === "empty") && !hasItems) {
    return <Empty type="empty" title={emptyTitle} description={emptyDescription} />;
  }
  return null;
}

export function AutomationErrorIcon() {
  return <ErrorCircleIcon aria-hidden="true" />;
}

export function AutomationAuthorityIcon() {
  return <SecuredIcon aria-hidden="true" />;
}
