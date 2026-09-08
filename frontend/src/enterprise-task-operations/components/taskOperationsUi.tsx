import { useCallback, useEffect, useRef, type RefObject } from "react";
import { Alert, Empty, Loading, Tag } from "tdesign-react";
import { CheckCircleIcon, ErrorCircleIcon, LoadingIcon, TimeIcon } from "tdesign-icons-react";

import type {
  TaskOperation,
  TaskOperationStatus,
  TaskOperationsSummary,
  TaskResourceStatus,
} from "./taskOperationsTypes";

export function formatTaskCount(value: number | null | undefined): string {
  return value === null || value === undefined ? "未返回" : value.toLocaleString("zh-CN");
}

export function formatTaskDate(value: string | null | undefined): string {
  if (!value) return "未返回";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间不可用";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatDuration(durationMs: number | null | undefined): string {
  if (durationMs === null || durationMs === undefined) return "未返回";
  if (durationMs < 1000) return `${durationMs} ms`;
  const seconds = Math.round(durationMs / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function taskStatusLabel(status: TaskOperationStatus): string {
  const labels: Record<TaskOperationStatus, string> = {
    queued: "排队中",
    running: "运行中",
    failed: "失败",
    completed: "已完成",
    cancelled: "已取消",
    blocked: "已阻塞",
    unavailable: "来源不可用",
  };
  return labels[status];
}

export function taskStatusTheme(
  status: TaskOperationStatus,
): "default" | "primary" | "success" | "warning" | "danger" {
  if (status === "running") return "primary";
  if (status === "completed") return "success";
  if (status === "failed" || status === "blocked") return "danger";
  if (status === "queued" || status === "unavailable") return "warning";
  return "default";
}

export function taskStatusDescription(task: TaskOperation): string {
  if (task.status === "failed") return task.safe_error ?? "任务失败，等待人工处理";
  if (task.status === "running") {
    return task.progress === null ? "执行进度未返回" : `执行进度 ${task.progress}%`;
  }
  if (task.status === "queued") return "等待工作队列分配执行器";
  if (task.status === "completed") return "结果已写入任务权威";
  if (task.status === "blocked") return task.safe_error ?? "任务被来源权威阻塞，等待人工确认";
  if (task.status === "unavailable") return "来源权威暂不可用，投影不会推断未知事实";
  return "任务已被显式取消";
}

export function safeTaskSnapshotEntries(
  snapshot: Record<string, string | number | boolean | null> | undefined,
): Array<[string, string]> {
  const labels: Record<string, string> = {
    source_revision: "来源修订",
    queue_name: "队列",
    shard_count: "分片数量",
    worker_id: "执行器",
    attempt_number: "尝试次数",
    duration_ms: "耗时",
    error_code: "错误码",
  };
  const allowed = new Set(Object.keys(labels));
  return Object.entries(snapshot ?? {})
    .filter(([key]) => allowed.has(key))
    .map(([key, value]) => [
      labels[key],
      value === null
        ? "未返回"
        : key === "duration_ms"
          ? formatDuration(Number(value))
          : String(value),
    ]);
}

export function useTaskFocusReturn(): {
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

export function useTaskEscape(onClose: () => void, active: boolean): void {
  useEffect(() => {
    if (!active) return undefined;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [active, onClose]);
}

export function TaskStateNotice({
  status,
  invalidItemCount = 0,
  hasItems = false,
  resourceLabel = "任务记录",
  emptyTitle = "暂无任务记录",
  emptyDescription = "任务进入队列后，会在这里留下可追溯的执行记录。",
}: {
  status: TaskResourceStatus;
  invalidItemCount?: number;
  hasItems?: boolean;
  resourceLabel?: string;
  emptyTitle?: string;
  emptyDescription?: string;
}) {
  if (status === "loading" && !hasItems) {
    return (
      <div
        className="task-operations__state task-operations__state--loading"
        data-testid="task-operations-loading"
      >
        <Loading text={`正在读取${resourceLabel}…`} />
      </div>
    );
  }
  if (status === "unavailable") {
    return (
      <div className="task-operations__state-alert" role="alert">
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
      <div className="task-operations__state-alert" role="alert">
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
      <div className="task-operations__state-alert" role="alert">
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

export function TaskBoundaryTag({ readOnly }: { readOnly: boolean }) {
  return readOnly ? (
    <Tag theme="warning" variant="light-outline" size="small">
      只读模式
    </Tag>
  ) : (
    <Tag theme="success" variant="light-outline" size="small">
      操作权威已连接
    </Tag>
  );
}

export function TaskStatusIcon({
  status,
}: {
  status: "complete" | "current" | "pending" | "warning";
}) {
  if (status === "complete") return <CheckCircleIcon />;
  if (status === "current") return <LoadingIcon />;
  if (status === "warning") return <ErrorCircleIcon />;
  return <TimeIcon />;
}

export function summaryField(
  summary: TaskOperationsSummary | null,
  key: keyof TaskOperationsSummary,
): number | null | undefined {
  return summary?.[key] as number | null | undefined;
}
