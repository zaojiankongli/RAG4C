import { useEffect, useState } from "react";

import type { NotificationLoadStatus } from "../hooks/useEnterpriseNotifications";
import type {
  Notification,
  NotificationInboxItem,
  NotificationSafeFacts,
  NotificationSeverity,
} from "../model/notificationModel";

export type NotificationView = "unread" | "all" | "quality" | "approvals";
export type NotificationPageTab = "inbox" | "subscriptions" | "activity";

export interface NotificationHandoffContext {
  route: Notification["route"];
  notificationId: string;
  sourceKind: Notification["source_kind"];
}

export type NotificationHandoff = (context: NotificationHandoffContext) => void;

const SECRET_MARKERS =
  /password|passwd|secret|credential|authorization|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|client[_ -]?secret|idempotency|ticket|bearer|query|body|note/i;
const SAFE_KEY = /^[a-z][a-z0-9_.-]{0,63}$/;

export function useNotificationMobile(override?: boolean): boolean {
  const query = "(max-width: 768px)";
  const [mobile, setMobile] = useState(() => {
    if (override !== undefined) return override;
    return typeof window !== "undefined" && window.matchMedia?.(query).matches === true;
  });

  useEffect(() => {
    if (override !== undefined) {
      setMobile(override);
      return;
    }
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(query);
    const update = () => setMobile(media.matches);
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, [override]);

  return mobile;
}

export function formatNotificationDate(value: string | null | undefined): string {
  if (!value) return "未返回";
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return "未返回";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(timestamp);
}

export function notificationTitle(notification: Notification): string {
  return notification.source_kind === "quality_alert" ? "质量告警需要关注" : "审批请求待处理";
}

export function notificationSourceLabel(notification: Notification): string {
  return notification.source_kind === "quality_alert" ? "质量运营" : "企业审批";
}

export function categoryLabel(category: Notification["category"]): string {
  return category === "quality" ? "质量" : "审批";
}

export function severityLabel(severity: NotificationSeverity): string {
  return severity === "critical" ? "Critical" : severity === "warning" ? "Warning" : "Info";
}

export function severityTheme(severity: NotificationSeverity): "danger" | "warning" | "default" {
  if (severity === "critical") return "danger";
  if (severity === "warning") return "warning";
  return "default";
}

export function sourceStatusLabel(status: NotificationLoadStatus): string {
  switch (status) {
    case "loading":
      return "正在读取权威数据";
    case "partial":
      return "部分数据可用";
    case "empty":
      return "暂无记录";
    case "unavailable":
      return "权威暂不可用";
    case "error":
      return "读取失败";
    default:
      return "已同步";
  }
}

export function safeFactEntries(facts: NotificationSafeFacts): Array<[string, string]> {
  return Object.entries(facts)
    .filter(([key, value]) => {
      if (!SAFE_KEY.test(key) || SECRET_MARKERS.test(key)) return false;
      return value === null || ["string", "number", "boolean"].includes(typeof value);
    })
    .map(([key, value]) => [key, value === null ? "—" : String(value)] as [string, string])
    .sort(([left], [right]) => left.localeCompare(right));
}

export function safeNoticeText(status: NotificationLoadStatus, fallback: string): string {
  if (status === "unavailable") return "当前权威暂不可用，请稍后重试。";
  if (status === "error") return fallback;
  return fallback;
}

export function itemId(item: NotificationInboxItem): string {
  return item.notification.id;
}

export function isUnread(item: NotificationInboxItem): boolean {
  return item.receipt.status === "unread";
}

export const READ_REASON = "通知中心显式标记为已读";
export const SUBSCRIPTION_REASON = "通知中心显式调整订阅";
