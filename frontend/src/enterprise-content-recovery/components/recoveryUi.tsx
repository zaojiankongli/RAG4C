import { useCallback, useEffect, useRef, type ReactNode, type RefObject } from "react";
import { Alert, Empty, Loading, Tag } from "tdesign-react";
import { CheckCircleIcon, ErrorCircleIcon, LoadingIcon, TimeIcon } from "tdesign-icons-react";

import type {
  RecoveryEntry,
  RecoveryEntryStatus,
  RecoveryResourceStatus,
  RecoverySummary,
} from "./contentRecoveryTypes";

export function formatRecoveryDate(value: string | null | undefined): string {
  if (!value) return "未返回";
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(timestamp);
}

export function numberLabel(value: number | null | undefined): string {
  if (value === null || value === undefined) return "未返回";
  return new Intl.NumberFormat("zh-CN").format(value);
}

export function recoveryStatusLabel(status: RecoveryEntryStatus): string {
  const labels: Record<RecoveryEntryStatus, string> = {
    recycled: "已回收",
    restoring: "恢复中",
    restored: "已恢复",
    purge_requested: "清除待审批",
    purged: "已清除",
    failed: "处理失败",
  };
  return labels[status];
}

export function recoveryStatusTheme(
  status: RecoveryEntryStatus,
): "success" | "warning" | "danger" | "primary" | "default" {
  if (status === "restored") return "success";
  if (status === "restoring" || status === "purge_requested") return "primary";
  if (status === "failed") return "danger";
  if (status === "recycled") return "warning";
  return "default";
}

export function purgeEligibility(entry: RecoveryEntry, now = Date.now()): boolean {
  if (entry.status !== "recycled") return false;
  if ((entry.active_hold_count ?? 0) > 0) return false;
  if (!entry.purge_eligible_at) return false;
  const eligibleAt = Date.parse(entry.purge_eligible_at);
  return !Number.isNaN(eligibleAt) && eligibleAt <= now;
}

export function safeSnapshotEntries(
  snapshot: Record<string, string | number | boolean | null> | undefined,
): Array<[string, string]> {
  const allowed = new Set([
    "document_type",
    "source_kind",
    "source_label",
    "original_lifecycle_state",
    "original_retrieval_enabled",
    "retention_days_snapshot",
    "recycle_generation",
    "document_mutation_generation",
  ]);
  const labels: Record<string, string> = {
    document_type: "文档类型",
    source_kind: "来源类型",
    source_label: "来源",
    original_lifecycle_state: "原生命周期",
    original_retrieval_enabled: "原检索状态",
    retention_days_snapshot: "保留天数快照",
    recycle_generation: "回收代次",
    document_mutation_generation: "文档修订代次",
  };
  return Object.entries(snapshot ?? {})
    .filter(([key]) => allowed.has(key))
    .map(([key, value]) => [labels[key] ?? key, value === null ? "未返回" : String(value)]);
}

export function useFocusReturn(): {
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

export function RecoveryStateNotice({
  status,
  invalidItemCount = 0,
  hasItems = false,
  resourceLabel = "回收条目",
  emptyTitle = "回收站为空",
  emptyDescription = "移入回收站的知识文档会在这里保留，并可在满足条件后恢复。",
}: {
  status: RecoveryResourceStatus;
  invalidItemCount?: number;
  hasItems?: boolean;
  resourceLabel?: string;
  emptyTitle?: string;
  emptyDescription?: string;
}) {
  if (status === "loading" && !hasItems) {
    return (
      <div
        className="content-recovery__state content-recovery__state--loading"
        data-testid="recovery-loading"
      >
        <Loading text={`正在读取${resourceLabel}权威…`} />
      </div>
    );
  }
  if (status === "unavailable") {
    return (
      <div role="alert" className="content-recovery__state-alert">
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
      <div role="alert" className="content-recovery__state-alert">
        <Alert
          theme="error"
          title={`${resourceLabel}读取失败`}
          message={`当前${resourceLabel}读取失败，请重试。`}
        />
      </div>
    );
  }
  if (status === "partial") {
    return (
      <div role="alert" className="content-recovery__state-alert">
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

export function RecoveryStatusIcon({
  status,
}: {
  status: "complete" | "current" | "pending" | "warning";
}) {
  if (status === "complete") return <CheckCircleIcon />;
  if (status === "current") return <LoadingIcon />;
  if (status === "warning") return <ErrorCircleIcon />;
  return <TimeIcon />;
}

export function RecoveryBoundaryTag({ readOnly }: { readOnly: boolean }) {
  return readOnly ? (
    <Tag theme="warning" variant="light-outline" size="small">
      只读模式
    </Tag>
  ) : (
    <Tag theme="success" variant="light-outline" size="small">
      权威回收治理
    </Tag>
  );
}

export function safeNode(value: ReactNode): ReactNode {
  return value ?? "未返回";
}

export function useStableDialogClose(
  onClose: () => void,
  returnFocusRef?: RefObject<HTMLElement | null>,
) {
  return useCallback(() => {
    onClose();
    const target = returnFocusRef?.current;
    if (target && target.isConnected) window.setTimeout(() => target.focus(), 0);
  }, [onClose, returnFocusRef]);
}

export function useEscape(onClose: () => void) {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);
}

export function summaryReady(summary: RecoverySummary | null): boolean {
  return summary?.state === "ready" || summary?.state === "partial";
}
