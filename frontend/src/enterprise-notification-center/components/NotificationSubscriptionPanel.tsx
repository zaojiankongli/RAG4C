import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Dialog,
  Empty,
  Loading,
  Pagination,
  PrimaryTable,
  Tag,
  type PrimaryTableCol,
} from "tdesign-react";
import { SettingIcon } from "tdesign-icons-react";

import type { EnterpriseNotificationsHook } from "../hooks/useEnterpriseNotifications";
import type { NotificationSeverity, NotificationSubscription } from "../model/notificationModel";
import "../notification-center.css";
import {
  categoryLabel,
  formatNotificationDate,
  severityLabel,
  SUBSCRIPTION_REASON,
  useNotificationMobile,
} from "./notificationCenterShared";

export interface NotificationSubscriptionPanelProps {
  controller: EnterpriseNotificationsHook;
  mobile?: boolean;
  readOnly?: boolean;
  onRequestNextPage?: () => void;
}

interface SubscriptionTableRow {
  rowId: string;
  subscription: NotificationSubscription;
}

interface PendingSubscription {
  subscription: NotificationSubscription;
  preference: NotificationSubscription["preference"];
  minimumSeverity: NotificationSeverity;
  mutedUntil: string | null;
}

const SEVERITIES: NotificationSeverity[] = ["info", "warning", "critical"];

function futureQuietWindow(): string {
  return new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();
}

function preferenceLabel(preference: NotificationSubscription["preference"]): string {
  return preference === "muted" ? "已静音" : "已订阅";
}

function SubscriptionFacts({ subscription }: { subscription: NotificationSubscription }) {
  return (
    <div className="notification-center__subscription-facts">
      <Tag theme={subscription.preference === "muted" ? "warning" : "success"}>
        {preferenceLabel(subscription.preference)}
      </Tag>
      <Tag theme="default">最低 {severityLabel(subscription.minimum_severity)}</Tag>
      {subscription.muted_until ? (
        <span>静音至 {formatNotificationDate(subscription.muted_until)}</span>
      ) : null}
    </div>
  );
}

function SubscriptionStateNotice({
  status,
  invalidItemCount,
}: {
  status: EnterpriseNotificationsHook["subscriptions"]["status"];
  invalidItemCount: number;
}) {
  if (status === "loading") {
    return (
      <div className="notification-center__state notification-center__state--loading">
        <Loading text="正在读取订阅权威…" />
      </div>
    );
  }
  if (status === "partial") {
    return (
      <div role="alert">
        <Alert
          theme="warning"
          title="部分订阅无法读取"
          message={`${invalidItemCount} 条订阅未通过安全校验，已从界面隐藏。`}
        />
      </div>
    );
  }
  if (status === "unavailable") {
    return (
      <div role="alert">
        <Alert
          theme="warning"
          title="订阅权威暂不可用"
          message="当前无法读取订阅设置，请稍后重试。"
        />
      </div>
    );
  }
  if (status === "error") {
    return (
      <div role="alert">
        <Alert theme="error" title="订阅读取失败" message="订阅设置读取失败，请重试。" />
      </div>
    );
  }
  return null;
}

function SubscriptionEmpty() {
  return (
    <div className="notification-center__empty-wrap">
      <Empty type="empty" title="暂无订阅设置" description="当前账号没有可管理的站内通知订阅。" />
    </div>
  );
}

function SubscriptionDialog({
  pending,
  readOnly,
  saving,
  onClose,
  onPreferenceChange,
  onSeverityChange,
  onConfirm,
}: {
  pending: PendingSubscription | null;
  readOnly: boolean;
  saving: boolean;
  onClose: () => void;
  onPreferenceChange: (preference: PendingSubscription["preference"]) => void;
  onSeverityChange: (severity: NotificationSeverity) => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      header="调整站内通知订阅"
      visible={pending !== null}
      width="480px"
      attach="body"
      destroyOnClose
      closeOnEscKeydown
      closeOnOverlayClick
      confirmBtn="保存订阅设置"
      cancelBtn="取消"
      confirmLoading={saving}
      onConfirm={onConfirm}
      onCancel={onClose}
      onClose={onClose}
    >
      <div role="dialog" aria-label="调整站内通知订阅">
        {pending ? (
          <div className="notification-center__subscription-dialog-body">
            <Alert
              theme="info"
              title="服务端权威校验"
              message="浏览器只提交偏好意图；revision、静音时间与权限由服务端最终校验。"
            />
            <div className="notification-center__dialog-fact-grid">
              <div>
                <span>通知类别</span>
                <strong>{categoryLabel(pending.subscription.category)}</strong>
              </div>
              <div>
                <span>当前修订</span>
                <code>{pending.subscription.revision}</code>
              </div>
            </div>
            <div
              className="notification-center__dialog-choice-group"
              role="group"
              aria-label="通知偏好"
            >
              <span>接收方式</span>
              <div className="notification-center__choice-row">
                <Button
                  variant={pending.preference === "subscribed" ? "base" : "outline"}
                  theme={pending.preference === "subscribed" ? "primary" : "default"}
                  aria-pressed={pending.preference === "subscribed"}
                  disabled={readOnly}
                  onClick={() => onPreferenceChange("subscribed")}
                >
                  订阅
                </Button>
                <Button
                  variant={pending.preference === "muted" ? "base" : "outline"}
                  theme={pending.preference === "muted" ? "warning" : "default"}
                  aria-pressed={pending.preference === "muted"}
                  disabled={readOnly}
                  onClick={() => onPreferenceChange("muted")}
                >
                  静音 7 天
                </Button>
              </div>
            </div>
            <div
              className="notification-center__dialog-choice-group"
              role="group"
              aria-label="最低严重性"
            >
              <span>最低严重性</span>
              <div className="notification-center__choice-row">
                {SEVERITIES.map((severity) => (
                  <Button
                    key={severity}
                    variant={pending.minimumSeverity === severity ? "base" : "outline"}
                    theme={pending.minimumSeverity === severity ? "primary" : "default"}
                    aria-pressed={pending.minimumSeverity === severity}
                    disabled={readOnly}
                    onClick={() => onSeverityChange(severity)}
                  >
                    {severityLabel(severity)}
                  </Button>
                ))}
              </div>
            </div>
            <p className="notification-center__safe-copy">
              {pending.preference === "muted" && pending.mutedUntil
                ? `本次建议静音至 ${formatNotificationDate(pending.mutedUntil)}`
                : "恢复订阅后，符合最低严重性且有权限的通知将进入收件箱。"}
            </p>
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}

function DesktopSubscriptionTable({
  items,
  readOnly,
  onEdit,
}: {
  items: NotificationSubscription[];
  readOnly: boolean;
  onEdit: (subscription: NotificationSubscription) => void;
}) {
  const rows = useMemo<SubscriptionTableRow[]>(
    () => items.map((subscription) => ({ rowId: subscription.id, subscription })),
    [items],
  );
  const columns = useMemo<PrimaryTableCol<SubscriptionTableRow>[]>(
    () => [
      {
        colKey: "category",
        title: "类别",
        width: 140,
        cell: ({ row }) => <strong>{categoryLabel(row.subscription.category)}</strong>,
      },
      {
        colKey: "preference",
        title: "偏好",
        minWidth: 260,
        cell: ({ row }) => <SubscriptionFacts subscription={row.subscription} />,
      },
      {
        colKey: "revision",
        title: "Revision",
        width: 110,
        cell: ({ row }) => <code>{row.subscription.revision}</code>,
      },
      {
        colKey: "action",
        title: "操作",
        width: 150,
        cell: ({ row }) => (
          <Button
            variant="text"
            size="small"
            icon={<SettingIcon />}
            disabled={readOnly}
            aria-label={`调整${categoryLabel(row.subscription.category)}订阅`}
            onClick={() => onEdit(row.subscription)}
          >
            调整设置
          </Button>
        ),
      },
    ],
    [onEdit, readOnly],
  );

  return (
    <div className="notification-center__subscription-desktop-table">
      <PrimaryTable
        rowKey="rowId"
        data={rows}
        columns={columns}
        size="small"
        bordered={false}
        hover
        stripe
        tableLayout="auto"
        empty=""
      />
    </div>
  );
}

function MobileSubscriptionCards({
  items,
  readOnly,
  onEdit,
}: {
  items: NotificationSubscription[];
  readOnly: boolean;
  onEdit: (subscription: NotificationSubscription) => void;
}) {
  return (
    <div className="notification-center__subscription-mobile-cards">
      {items.map((subscription) => (
        <article className="notification-center__subscription-card" key={subscription.id}>
          <div className="notification-center__subscription-card-heading">
            <span className="notification-center__eyebrow">SUBSCRIPTION</span>
            <strong>{categoryLabel(subscription.category)}</strong>
          </div>
          <SubscriptionFacts subscription={subscription} />
          <span className="notification-center__subscription-revision">
            Revision {subscription.revision}
          </span>
          <Button
            block
            variant="outline"
            icon={<SettingIcon />}
            disabled={readOnly}
            aria-label={`调整${categoryLabel(subscription.category)}订阅`}
            onClick={() => onEdit(subscription)}
          >
            调整设置
          </Button>
        </article>
      ))}
    </div>
  );
}

export default function NotificationSubscriptionPanel({
  controller,
  mobile: mobileOverride,
  readOnly = false,
  onRequestNextPage,
}: NotificationSubscriptionPanelProps) {
  const mobile = useNotificationMobile(mobileOverride);
  const [pending, setPending] = useState<PendingSubscription | null>(null);
  const status = controller.subscriptions.status;

  useEffect(() => {
    if (controller.active && status === "idle") void controller.subscriptions.load();
  }, [controller.active, controller.subscriptions, status]);

  const openEditor = useCallback((subscription: NotificationSubscription) => {
    setPending({
      subscription,
      preference: subscription.preference,
      minimumSeverity: subscription.minimum_severity,
      mutedUntil: subscription.muted_until ?? futureQuietWindow(),
    });
  }, []);

  const closeEditor = useCallback(() => setPending(null), []);

  const handleConfirm = useCallback(() => {
    if (readOnly || !pending) return;
    const mutedUntil =
      pending.preference === "muted" ? (pending.mutedUntil ?? futureQuietWindow()) : null;
    void controller.mutation
      .updateSubscription(
        pending.subscription.id,
        {
          expectedRevision: pending.subscription.revision,
          preference: pending.preference,
          minimumSeverity: pending.minimumSeverity,
          mutedUntil,
          reason: SUBSCRIPTION_REASON,
        },
        undefined,
      )
      .then((outcome) => {
        if (outcome?.state !== "unavailable") {
          setPending(null);
          void controller.subscriptions.load();
        }
      });
  }, [controller.mutation, controller.subscriptions, pending, readOnly]);

  const errorNotice =
    controller.mutation.status === "error" ? (
      <div role="alert">
        <Alert theme="error" title="订阅更新失败" message="服务端没有接受本次订阅变更，请重试。" />
      </div>
    ) : null;

  return (
    <section
      className="notification-center__subscription-panel"
      aria-labelledby="notification-subscription-title"
    >
      <div className="notification-center__section-heading">
        <div>
          <span className="notification-center__eyebrow">SUBSCRIPTION AUTHORITY</span>
          <h3 id="notification-subscription-title">站内通知订阅</h3>
          <p>只管理当前租户账号的站内偏好，不改变通知来源或权限。</p>
        </div>
        {readOnly ? (
          <Tag theme="warning">只读模式</Tag>
        ) : (
          <Tag theme="success">Server governed</Tag>
        )}
      </div>
      {errorNotice}
      <SubscriptionStateNotice
        status={status}
        invalidItemCount={controller.subscriptions.invalidItemCount}
      />
      {mobile ? (
        <div
          className="notification-center__subscription-mobile-surface"
          data-testid="notification-subscription-mobile-cards"
        >
          {controller.subscriptions.items.length === 0 ? (
            status !== "loading" ? (
              <SubscriptionEmpty />
            ) : null
          ) : (
            <MobileSubscriptionCards
              items={controller.subscriptions.items}
              readOnly={readOnly}
              onEdit={openEditor}
            />
          )}
        </div>
      ) : (
        <div
          className="notification-center__subscription-desktop-surface"
          data-testid="notification-subscription-desktop-table"
        >
          {controller.subscriptions.items.length === 0 ? (
            status !== "loading" ? (
              <SubscriptionEmpty />
            ) : null
          ) : (
            <DesktopSubscriptionTable
              items={controller.subscriptions.items}
              readOnly={readOnly}
              onEdit={openEditor}
            />
          )}
        </div>
      )}
      {controller.subscriptions.nextCursor ? (
        <Pagination
          className="notification-center__pagination"
          current={1}
          pageSize={Math.max(1, controller.subscriptions.items.length)}
          total={controller.subscriptions.items.length + 1}
          showPageSize={false}
          showJumper={false}
          onCurrentChange={() => onRequestNextPage?.()}
          totalContent="还有更多订阅"
        />
      ) : null}
      <SubscriptionDialog
        pending={pending}
        readOnly={readOnly}
        saving={controller.mutation.status === "saving"}
        onClose={closeEditor}
        onPreferenceChange={(preference) => {
          setPending((current) => {
            if (!current) return current;
            return {
              ...current,
              preference,
              mutedUntil:
                preference === "muted" ? (current.mutedUntil ?? futureQuietWindow()) : null,
            };
          });
        }}
        onSeverityChange={(minimumSeverity) => {
          setPending((current) => (current ? { ...current, minimumSeverity } : current));
        }}
        onConfirm={handleConfirm}
      />
    </section>
  );
}
