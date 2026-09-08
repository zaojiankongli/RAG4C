import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { Alert, Button, Drawer, Empty, Loading, Tag, Tabs, Timeline } from "tdesign-react";
import {
  ArrowRightIcon,
  CheckCircleIcon,
  CloseIcon,
  DeleteTimeIcon,
  Edit1Icon,
} from "tdesign-icons-react";

import type {
  NotificationDetailState,
  NotificationLoadStatus,
} from "../hooks/useEnterpriseNotifications";
import type { NotificationDetail } from "../model/notificationModel";
import "../notification-center.css";
import AccessibleTabLabel from "./AccessibleTabLabel";
import {
  categoryLabel,
  formatNotificationDate,
  notificationSourceLabel,
  notificationTitle,
  safeFactEntries,
  severityLabel,
  severityTheme,
  type NotificationHandoff,
} from "./notificationCenterShared";

export interface NotificationDetailDrawerProps {
  visible: boolean;
  state: NotificationDetailState;
  readOnly?: boolean;
  loading?: boolean;
  onClose: () => void;
  onHandoff?: NotificationHandoff;
  onMarkRead?: (detail: NotificationDetail) => void | Promise<unknown>;
  onMarkUnread?: (detail: NotificationDetail) => void | Promise<unknown>;
  onArchive?: (detail: NotificationDetail) => void | Promise<unknown>;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

function DetailStateNotice({ status }: { status: NotificationLoadStatus }) {
  if (status === "loading") {
    return (
      <div className="notification-center__state notification-center__state--loading">
        <Loading text="正在读取通知详情…" />
      </div>
    );
  }
  if (status === "unavailable") {
    return (
      <div role="alert">
        <Alert
          theme="warning"
          title="详情权威暂不可用"
          message="当前无法读取通知详情，请稍后重试。"
        />
      </div>
    );
  }
  if (status === "error") {
    return (
      <div role="alert">
        <Alert theme="error" title="详情读取失败" message="通知详情读取失败，请重试。" />
      </div>
    );
  }
  return null;
}

function DetailOverview({ detail }: { detail: NotificationDetail }) {
  const facts = safeFactEntries(detail.notification.safe_facts);
  return (
    <div className="notification-center__detail-overview">
      <div className="notification-center__detail-hero">
        <div>
          <span className="notification-center__eyebrow">SAFE NOTIFICATION FACT</span>
          <h3>{notificationTitle(detail.notification)}</h3>
          <p>{detail.notification.summary_code}</p>
        </div>
        <Tag theme={severityTheme(detail.notification.severity)}>
          {severityLabel(detail.notification.severity)}
        </Tag>
      </div>
      <div className="notification-center__detail-facts">
        <div>
          <span>来源</span>
          <strong>{notificationSourceLabel(detail.notification)}</strong>
        </div>
        <div>
          <span>类别</span>
          <strong>{categoryLabel(detail.notification.category)}</strong>
        </div>
        <div>
          <span>通知 ID</span>
          <code>{detail.notification.id}</code>
        </div>
        <div>
          <span>来源修订</span>
          <code>{detail.notification.source_revision}</code>
        </div>
        <div>
          <span>回执状态</span>
          <strong>
            {detail.receipt.status === "unread"
              ? "未读"
              : detail.receipt.status === "read"
                ? "已读"
                : "已归档"}
          </strong>
        </div>
        <div>
          <span>发生时间</span>
          <strong>{formatNotificationDate(detail.notification.occurred_at)}</strong>
        </div>
      </div>
      {facts.length > 0 ? (
        <div className="notification-center__safe-facts">
          <div className="notification-center__section-heading">
            <div>
              <span className="notification-center__eyebrow">ALLOW-LISTED FACTS</span>
              <h4>来源事实</h4>
            </div>
            <Tag theme="success">Body-free</Tag>
          </div>
          <dl>
            {facts.map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : (
        <Empty
          type="empty"
          title="暂无安全事实"
          description="该通知没有可展示的 allow-list 字段。"
        />
      )}
    </div>
  );
}

function DetailActivity({ detail }: { detail: NotificationDetail }) {
  if (detail.events.length === 0) {
    return (
      <Empty type="empty" title="暂无活动记录" description="当前通知尚无可展示的生命周期事件。" />
    );
  }
  return (
    <div className="notification-center__detail-activity">
      <div className="notification-center__section-heading">
        <div>
          <span className="notification-center__eyebrow">IMMUTABLE EVENT CHAIN</span>
          <h4>通知活动链</h4>
        </div>
        <Tag theme="default">{detail.events.length} events</Tag>
      </div>
      <Timeline theme="dot" mode="same" layout="vertical">
        {detail.events.map((event) => (
          <Timeline.Item
            key={event.id}
            dotColor={event.event_type === "marked_read" ? "success" : "primary"}
            label={formatNotificationDate(event.occurred_at)}
          >
            <div className="notification-center__timeline-item">
              <strong>{event.event_type}</strong>
              <span>Sequence {event.sequence}</span>
              <code>{event.event_digest.slice(0, 12)}…</code>
            </div>
          </Timeline.Item>
        ))}
      </Timeline>
    </div>
  );
}

export default function NotificationDetailDrawer({
  visible,
  state,
  readOnly = false,
  loading = false,
  onClose,
  onHandoff,
  onMarkRead,
  onMarkUnread,
  onArchive,
  returnFocusRef,
}: NotificationDetailDrawerProps) {
  const closeSentRef = useRef(false);
  const [tab, setTab] = useState<"overview" | "activity">("overview");

  useEffect(() => {
    if (visible) {
      closeSentRef.current = false;
      setTab("overview");
      return;
    }
    if (returnFocusRef?.current) {
      window.setTimeout(() => returnFocusRef.current?.focus(), 0);
    }
  }, [returnFocusRef, visible]);

  const handleClose = useCallback(() => {
    if (closeSentRef.current) return;
    closeSentRef.current = true;
    onClose();
  }, [onClose]);

  const detail = state.value;
  const detailLabel = detail?.notification.id ?? "通知详情";
  const actionLoading = loading;

  const header = (
    <div className="notification-center__detail-header">
      <div>
        <span className="notification-center__eyebrow">NOTIFICATION DETAIL</span>
        <h2>通知详情</h2>
        {detail ? <code>{detailLabel}</code> : null}
      </div>
      <div className="notification-center__detail-header-actions">
        <Tag theme={detail ? severityTheme(detail.notification.severity) : "default"}>
          {detail ? severityLabel(detail.notification.severity) : "Unavailable"}
        </Tag>
        <Button
          variant="text"
          shape="square"
          aria-label="关闭"
          icon={<CloseIcon />}
          onClick={handleClose}
        />
      </div>
    </div>
  );

  const body = (
    <div role="dialog" aria-label="通知详情" className="notification-center__detail-dialog-shell">
      <div className="notification-center__detail-surface">
        {state.error && state.status !== "unavailable" ? (
          <DetailStateNotice status="error" />
        ) : null}
        {state.status === "unavailable" || state.status === "loading" ? (
          <DetailStateNotice status={state.status} />
        ) : null}
        {detail ? (
          <>
            <div className="notification-center__detail-toolbar">
              <div className="notification-center__detail-toolbar-actions">
                {onHandoff ? (
                  <Button
                    theme="primary"
                    icon={<ArrowRightIcon />}
                    onClick={() =>
                      onHandoff({
                        route: detail.notification.route,
                        notificationId: detail.notification.id,
                        sourceKind: detail.notification.source_kind,
                      })
                    }
                  >
                    打开通知来源
                  </Button>
                ) : null}
                {detail.receipt.status === "unread" ? (
                  <Button
                    variant="outline"
                    icon={<CheckCircleIcon />}
                    disabled={readOnly || actionLoading}
                    loading={actionLoading}
                    aria-label="标记已读"
                    onClick={() => onMarkRead?.(detail)}
                  >
                    标记已读
                  </Button>
                ) : detail.receipt.status === "read" ? (
                  <Button
                    variant="outline"
                    icon={<Edit1Icon />}
                    disabled={readOnly || actionLoading}
                    loading={actionLoading}
                    aria-label="标记未读"
                    onClick={() => onMarkUnread?.(detail)}
                  >
                    标记未读
                  </Button>
                ) : null}
                <Button
                  variant="text"
                  icon={<DeleteTimeIcon />}
                  disabled={readOnly || actionLoading || detail.receipt.status === "archived"}
                  aria-label="归档通知"
                  onClick={() => onArchive?.(detail)}
                >
                  归档
                </Button>
              </div>
              {readOnly ? (
                <Tag theme="warning">只读模式</Tag>
              ) : (
                <Tag theme="success">Safe handoff only</Tag>
              )}
            </div>
            <Tabs
              className="notification-center__detail-tabs"
              value={tab}
              onChange={(next) => setTab(String(next) as "overview" | "activity")}
            >
              <Tabs.TabPanel
                value="overview"
                label={
                  <AccessibleTabLabel
                    label="Overview"
                    active={tab === "overview"}
                    onActivate={() => setTab("overview")}
                  />
                }
                destroyOnHide
              >
                <DetailOverview detail={detail} />
              </Tabs.TabPanel>
              <Tabs.TabPanel
                value="activity"
                label={
                  <AccessibleTabLabel
                    label="Activity"
                    active={tab === "activity"}
                    onActivate={() => setTab("activity")}
                  />
                }
                destroyOnHide
              >
                <DetailActivity detail={detail} />
              </Tabs.TabPanel>
            </Tabs>
          </>
        ) : state.status !== "loading" && state.status !== "unavailable" ? (
          <Empty type="empty" title="详情为空" description="服务端没有返回可验证的通知详情。" />
        ) : null}
      </div>
    </div>
  );

  return (
    <Drawer
      className="notification-center__detail-drawer"
      closeBtn={false}
      visible={visible}
      placement="right"
      size="min(640px, 100vw)"
      header={header}
      footer={null}
      attach="body"
      destroyOnClose
      closeOnEscKeydown
      closeOnOverlayClick
      onClose={handleClose}
      onEscKeydown={handleClose}
    >
      {body}
    </Drawer>
  );
}
