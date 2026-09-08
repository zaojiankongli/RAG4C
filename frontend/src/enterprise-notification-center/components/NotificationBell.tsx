import { Badge, Button } from "tdesign-react";
import { NotificationIcon } from "tdesign-icons-react";

import type { NotificationSummaryState } from "../hooks/useEnterpriseNotifications";
import "../notification-center.css";

export interface NotificationBellProps {
  summary: NotificationSummaryState;
  capabilityReady?: boolean;
  onOpen?: () => void;
  disabled?: boolean;
}

export default function NotificationBell({
  summary,
  capabilityReady = false,
  onOpen,
  disabled = false,
}: NotificationBellProps) {
  const value = summary.value;
  const ready = capabilityReady && summary.status === "ready" && value?.state === "ready";
  const unreadCount = value?.unread_count;
  const canShowCount =
    ready &&
    value?.unread_state !== "unavailable" &&
    typeof unreadCount === "number" &&
    Number.isInteger(unreadCount) &&
    unreadCount >= 0;

  if (!canShowCount) return null;

  const visualCount = unreadCount > 99 ? "99+" : unreadCount;
  const accessibleLabel = unreadCount > 0 ? `通知中心，${unreadCount} 条未读` : "通知中心，无未读";

  return (
    <span className="notification-center__bell" data-testid="notification-bell">
      <span data-testid="notification-bell-badge">
        <Badge count={visualCount} maxCount={99} showZero={false}>
          <Button
            className="notification-center__bell-button"
            variant="text"
            shape="circle"
            icon={<NotificationIcon />}
            aria-label={accessibleLabel}
            disabled={disabled}
            onClick={onOpen}
          />
        </Badge>
      </span>
      <span className="notification-center__sr-only" data-testid="notification-bell-accurate-count">
        准确未读数 {unreadCount}
      </span>
    </span>
  );
}
