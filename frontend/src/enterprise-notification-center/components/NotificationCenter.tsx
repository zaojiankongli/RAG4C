import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Empty, Loading, Tag, Tabs, Timeline } from "tdesign-react";
import { CheckCircleIcon, TimeIcon } from "tdesign-icons-react";

import type { EnterpriseNotificationsHook } from "../hooks/useEnterpriseNotifications";
import type { NotificationDetail, NotificationInboxItem } from "../model/notificationModel";
import "../notification-center.css";
import AccessibleTabLabel from "./AccessibleTabLabel";
import NotificationBell from "./NotificationBell";
import NotificationDetailDrawer from "./NotificationDetailDrawer";
import NotificationDrawer, { SignalRoutingRail } from "./NotificationDrawer";
import NotificationSubscriptionPanel from "./NotificationSubscriptionPanel";
import {
  formatNotificationDate,
  notificationSourceLabel,
  notificationTitle,
  READ_REASON,
  type NotificationHandoff,
  type NotificationPageTab,
} from "./notificationCenterShared";

export interface NotificationCenterProps {
  controller: EnterpriseNotificationsHook;
  capabilityReady?: boolean;
  readOnly?: boolean;
  mobile?: boolean;
  title?: string;
  tenantLabel?: string;
  onHandoff?: NotificationHandoff;
}

function ActivityPanel({ controller }: { controller: EnterpriseNotificationsHook }) {
  useEffect(() => {
    if (controller.active && controller.history.status === "idle") void controller.history.load();
  }, [controller.active, controller.history]);

  if (controller.history.status === "loading") {
    return (
      <div className="notification-center__page-state">
        <Loading text="正在读取通知活动…" />
      </div>
    );
  }
  if (controller.history.status === "unavailable") {
    return (
      <div role="alert">
        <Alert theme="warning" title="活动权威暂不可用" message="当前无法读取通知活动链。" />
      </div>
    );
  }
  if (controller.history.status === "error") {
    return (
      <div role="alert">
        <Alert theme="error" title="活动读取失败" message="通知活动读取失败，请重试。" />
      </div>
    );
  }
  if (controller.history.items.length === 0) {
    return (
      <Empty type="empty" title="暂无活动" description="通知生命周期事件将在安全物化后显示。" />
    );
  }

  return (
    <section
      className="notification-center__activity-panel"
      aria-labelledby="notification-activity-title"
    >
      <div className="notification-center__section-heading">
        <div>
          <span className="notification-center__eyebrow">ACTIVITY AUTHORITY</span>
          <h3 id="notification-activity-title">通知活动链</h3>
          <p>这里只展示当前账号可见的安全摘要，不推断未返回的历史。</p>
        </div>
        <Tag theme="success">
          <TimeIcon aria-hidden="true" /> {controller.history.items.length} records
        </Tag>
      </div>
      {controller.history.status === "partial" ? (
        <div role="alert">
          <Alert
            theme="warning"
            title="部分活动可用"
            message={`${controller.history.invalidItemCount} 条记录未通过安全校验，已隐藏。`}
          />
        </div>
      ) : null}
      <Timeline theme="dot" mode="same" layout="vertical">
        {controller.history.items.map((item) => (
          <Timeline.Item
            key={item.notification.id}
            dotColor={item.notification.action_required ? "warning" : "primary"}
            label={formatNotificationDate(item.notification.occurred_at)}
          >
            <div className="notification-center__timeline-item">
              <strong>{notificationTitle(item.notification)}</strong>
              <span>{notificationSourceLabel(item.notification)}</span>
              <code>{item.notification.id}</code>
            </div>
          </Timeline.Item>
        ))}
      </Timeline>
    </section>
  );
}

export default function NotificationCenter({
  controller,
  capabilityReady = false,
  readOnly = false,
  mobile,
  title = "消息中心",
  tenantLabel = "当前租户",
  onHandoff,
}: NotificationCenterProps) {
  const [pageTab, setPageTab] = useState<NotificationPageTab>("inbox");
  const [detailVisible, setDetailVisible] = useState(false);
  const detailReturnFocusRef = useRef<HTMLElement | null>(null);

  const handleOpenDetail = useCallback(
    (item: NotificationInboxItem) => {
      if (typeof document !== "undefined" && document.activeElement instanceof HTMLElement) {
        detailReturnFocusRef.current = document.activeElement;
      }
      setDetailVisible(true);
      void controller.detail.load(item.notification.id);
    },
    [controller.detail],
  );

  const handleCloseDetail = useCallback(() => setDetailVisible(false), []);

  const handleMarkRead = useCallback(
    (detail: NotificationDetail) => {
      if (readOnly) return;
      void controller.mutation.markRead(detail.notification.id, {
        expectedRevision: detail.receipt.revision,
        reason: READ_REASON,
      });
    },
    [controller.mutation, readOnly],
  );

  const handleMarkUnread = useCallback(
    (detail: NotificationDetail) => {
      if (readOnly) return;
      void controller.mutation.markUnread(detail.notification.id, {
        expectedRevision: detail.receipt.revision,
        reason: "通知中心显式标记为未读",
      });
    },
    [controller.mutation, readOnly],
  );

  const handleArchive = useCallback(
    (detail: NotificationDetail) => {
      if (readOnly) return;
      void controller.mutation.archive(detail.notification.id, {
        expectedRevision: detail.receipt.revision,
        reason: "通知中心显式归档通知",
      });
    },
    [controller.mutation, readOnly],
  );

  return (
    <section
      role="region"
      className="notification-center"
      aria-labelledby="notification-center-title"
    >
      <header className="notification-center__page-header">
        <div className="notification-center__page-title-block">
          <div className="notification-center__brand-mark" aria-hidden="true">
            <CheckCircleIcon />
          </div>
          <div>
            <span className="notification-center__eyebrow">ENTERPRISE NOTIFICATION CENTER</span>
            <h1 id="notification-center-title">{title}</h1>
            <p>{tenantLabel} · 站内通知权威与安全业务交接</p>
          </div>
        </div>
        <NotificationBell
          summary={controller.summary}
          capabilityReady={capabilityReady}
          onOpen={() => setPageTab("inbox")}
        />
      </header>

      <SignalRoutingRail />

      <Tabs
        className="notification-center__page-tabs"
        value={pageTab}
        onChange={(next) => setPageTab(String(next) as NotificationPageTab)}
        theme="card"
      >
        <Tabs.TabPanel
          value="inbox"
          label={
            <AccessibleTabLabel
              label="Inbox"
              active={pageTab === "inbox"}
              onActivate={() => setPageTab("inbox")}
            />
          }
          destroyOnHide
        >
          <NotificationDrawer
            controller={controller}
            visible
            inline
            mobile={mobile}
            readOnly={readOnly}
            onClose={() => undefined}
            onOpenDetail={handleOpenDetail}
            onHandoff={onHandoff}
            showRail={false}
          />
        </Tabs.TabPanel>
        <Tabs.TabPanel
          value="subscriptions"
          label={
            <AccessibleTabLabel
              label="Subscriptions"
              active={pageTab === "subscriptions"}
              onActivate={() => setPageTab("subscriptions")}
            />
          }
          destroyOnHide
        >
          <NotificationSubscriptionPanel
            controller={controller}
            mobile={mobile}
            readOnly={readOnly}
          />
        </Tabs.TabPanel>
        <Tabs.TabPanel
          value="activity"
          label={
            <AccessibleTabLabel
              label="Activity"
              active={pageTab === "activity"}
              onActivate={() => setPageTab("activity")}
            />
          }
          destroyOnHide
        >
          <ActivityPanel controller={controller} />
        </Tabs.TabPanel>
      </Tabs>

      <NotificationDetailDrawer
        visible={detailVisible}
        state={controller.detail}
        readOnly={readOnly}
        loading={controller.mutation.status === "saving"}
        onClose={handleCloseDetail}
        onHandoff={onHandoff}
        onMarkRead={handleMarkRead}
        onMarkUnread={handleMarkUnread}
        onArchive={handleArchive}
        returnFocusRef={{ current: detailReturnFocusRef.current }}
      />
    </section>
  );
}
