import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Empty,
  Drawer,
  Loading,
  Pagination,
  PrimaryTable,
  Tag,
  Tabs,
  type PrimaryTableCol,
} from "tdesign-react";
import {
  ArrowRightIcon,
  CheckCircleIcon,
  ChevronRightIcon,
  CloseIcon,
  RefreshIcon,
} from "tdesign-icons-react";

import type {
  EnterpriseNotificationsHook,
  NotificationLoadStatus,
} from "../hooks/useEnterpriseNotifications";
import type { NotificationInboxItem } from "../model/notificationModel";
import "../notification-center.css";
import AccessibleTabLabel from "./AccessibleTabLabel";
import {
  categoryLabel,
  formatNotificationDate,
  isUnread,
  itemId,
  notificationSourceLabel,
  notificationTitle,
  READ_REASON,
  severityLabel,
  severityTheme,
  sourceStatusLabel,
  type NotificationHandoff,
  type NotificationView,
  useNotificationMobile,
} from "./notificationCenterShared";

export interface NotificationDrawerProps {
  controller: EnterpriseNotificationsHook;
  visible: boolean;
  onClose: () => void;
  inline?: boolean;
  mobile?: boolean;
  readOnly?: boolean;
  onOpenDetail?: (item: NotificationInboxItem) => void;
  onHandoff?: NotificationHandoff;
  onRequestNextPage?: (view: NotificationView) => void;
  showRail?: boolean;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

interface NotificationTableRow {
  rowId: string;
  item: NotificationInboxItem;
}

const VIEW_LABELS: Array<{ value: NotificationView; label: string }> = [
  { value: "unread", label: "未读" },
  { value: "all", label: "全部" },
  { value: "quality", label: "质量" },
  { value: "approvals", label: "审批" },
];

export function SignalRoutingRail() {
  return (
    <ol className="notification-center__routing-rail" aria-label="Signal Routing Rail">
      <li className="notification-center__routing-step notification-center__routing-step--source">
        <span className="notification-center__routing-index">01</span>
        <span>
          <strong>SOURCE</strong>
          <small>质量告警 / 审批</small>
        </span>
      </li>
      <li className="notification-center__routing-step notification-center__routing-step--recipient">
        <span className="notification-center__routing-index">02</span>
        <span>
          <strong>RECIPIENT</strong>
          <small>当前租户账号</small>
        </span>
      </li>
      <li className="notification-center__routing-step notification-center__routing-step--receipt">
        <span className="notification-center__routing-index">03</span>
        <span>
          <strong>RECEIPT</strong>
          <small>未读 / 已读事实</small>
        </span>
      </li>
      <li className="notification-center__routing-step notification-center__routing-step--handoff">
        <span className="notification-center__routing-index">04</span>
        <span>
          <strong>HANDOFF</strong>
          <small>安全业务跳转</small>
        </span>
      </li>
    </ol>
  );
}

function selectionLabel(view: NotificationView): string {
  return view === "unread" ? "选择当前页全部未读通知" : "选择当前页全部通知";
}

function collectionStatus(
  controller: EnterpriseNotificationsHook,
  view: NotificationView,
): {
  status: NotificationLoadStatus;
  items: NotificationInboxItem[];
  nextCursor: string | null;
  invalidItemCount: number;
} {
  const source = view === "unread" ? controller.unread : controller.history;
  const items =
    view === "quality"
      ? source.items.filter((item) => item.notification.category === "quality")
      : view === "approvals"
        ? source.items.filter((item) => item.notification.category === "approval")
        : source.items;
  return {
    status: source.status,
    items,
    nextCursor: source.nextCursor,
    invalidItemCount: source.invalidItemCount,
  };
}

function StateNotice({
  status,
  invalidItemCount,
  hasItems,
}: {
  status: NotificationLoadStatus;
  invalidItemCount: number;
  hasItems: boolean;
}) {
  if (status === "loading" && !hasItems) {
    return (
      <div
        className="notification-center__state notification-center__state--loading"
        data-testid="notification-loading"
      >
        <Loading text="正在读取通知权威…" />
      </div>
    );
  }
  if (status === "partial") {
    return (
      <div role="alert">
        <Alert
          theme="warning"
          title="部分通知无法读取"
          message={`${invalidItemCount} 条通知未通过安全校验，已从界面隐藏。`}
        />
      </div>
    );
  }
  if (status === "unavailable") {
    return (
      <div role="alert">
        <Alert theme="warning" title="通知权威暂不可用" message="当前无法读取通知，请稍后重试。" />
      </div>
    );
  }
  if (status === "error") {
    return (
      <div role="alert">
        <Alert theme="error" title="通知读取失败" message="通知列表读取失败，请重试。" />
      </div>
    );
  }
  return null;
}

function EmptyCollection({ view }: { view: NotificationView }) {
  const description = view === "unread" ? "当前没有待处理的未读通知" : "当前筛选范围没有通知记录";
  return (
    <div className="notification-center__empty-wrap">
      <Empty type="empty" title="暂无通知" description={description} />
    </div>
  );
}

function NotificationIdentity({ item }: { item: NotificationInboxItem }) {
  const notification = item.notification;
  return (
    <div className="notification-center__identity">
      <div className="notification-center__identity-title">
        <span
          className="notification-center__unread-dot"
          aria-hidden="true"
          data-unread={isUnread(item)}
        />
        <strong>{notificationTitle(notification)}</strong>
      </div>
      <div className="notification-center__identity-meta">
        <code>{notification.source_kind}</code>
        <span>{notificationSourceLabel(notification)}</span>
        <span>{formatNotificationDate(notification.occurred_at)}</span>
      </div>
      <span className="notification-center__summary-code">{notification.summary_code}</span>
    </div>
  );
}

function NotificationActions({
  item,
  readOnly,
  onOpenDetail,
  onHandoff,
  onMarkRead,
}: {
  item: NotificationInboxItem;
  readOnly: boolean;
  onOpenDetail?: (item: NotificationInboxItem) => void;
  onHandoff?: NotificationHandoff;
  onMarkRead: (item: NotificationInboxItem) => void;
}) {
  const notification = item.notification;
  return (
    <div className="notification-center__row-actions">
      <Button
        variant="text"
        size="small"
        aria-label={`查看通知 ${notification.id}`}
        icon={<ChevronRightIcon />}
        onClick={() => onOpenDetail?.(item)}
      >
        查看详情
      </Button>
      {onHandoff ? (
        <Button
          variant="text"
          size="small"
          icon={<ArrowRightIcon />}
          aria-label={`打开通知来源 ${notification.id}`}
          onClick={() =>
            onHandoff({
              route: notification.route,
              notificationId: notification.id,
              sourceKind: notification.source_kind,
            })
          }
        >
          打开来源
        </Button>
      ) : null}
      {isUnread(item) ? (
        <Button
          variant="text"
          size="small"
          icon={<CheckCircleIcon />}
          disabled={readOnly}
          aria-label={`标记通知已读 ${notification.id}`}
          onClick={() => onMarkRead(item)}
        >
          标记已读
        </Button>
      ) : null}
    </div>
  );
}

function DesktopNotificationTable({
  items,
  selectedIds,
  onToggle,
  onToggleAll,
  readOnly,
  onOpenDetail,
  onHandoff,
  onMarkRead,
}: {
  items: NotificationInboxItem[];
  selectedIds: Set<string>;
  onToggle: (id: string, checked: boolean) => void;
  onToggleAll: (checked: boolean) => void;
  readOnly: boolean;
  onOpenDetail?: (item: NotificationInboxItem) => void;
  onHandoff?: NotificationHandoff;
  onMarkRead: (item: NotificationInboxItem) => void;
}) {
  const rows = useMemo<NotificationTableRow[]>(
    () => items.map((item) => ({ rowId: itemId(item), item })),
    [items],
  );
  const allSelected = items.length > 0 && items.every((item) => selectedIds.has(itemId(item)));
  const columns = useMemo<PrimaryTableCol<NotificationTableRow>[]>(
    () => [
      {
        colKey: "select",
        title: (
          <Checkbox
            checked={allSelected}
            indeterminate={selectedIds.size > 0 && !allSelected}
            aria-label="选择当前页全部通知"
            onChange={(checked) => onToggleAll(Boolean(checked))}
          />
        ),
        width: 54,
        cell: ({ row }) => (
          <Checkbox
            checked={selectedIds.has(row.rowId)}
            aria-label={`选择通知 ${row.rowId}`}
            onChange={(checked) => onToggle(row.rowId, Boolean(checked))}
          />
        ),
      },
      {
        colKey: "notification",
        title: "通知",
        minWidth: 300,
        cell: ({ row }) => <NotificationIdentity item={row.item} />,
      },
      {
        colKey: "category",
        title: "来源",
        width: 112,
        cell: ({ row }) => (
          <Tag theme="default">{categoryLabel(row.item.notification.category)}</Tag>
        ),
      },
      {
        colKey: "severity",
        title: "严重性",
        width: 122,
        cell: ({ row }) => (
          <Tag theme={severityTheme(row.item.notification.severity)}>
            {severityLabel(row.item.notification.severity)}
          </Tag>
        ),
      },
      {
        colKey: "receipt",
        title: "回执",
        width: 92,
        cell: ({ row }) => (isUnread(row.item) ? "未读" : "已读"),
      },
      {
        colKey: "actions",
        title: "操作",
        minWidth: 270,
        cell: ({ row }) => (
          <NotificationActions
            item={row.item}
            readOnly={readOnly}
            onOpenDetail={onOpenDetail}
            onHandoff={onHandoff}
            onMarkRead={onMarkRead}
          />
        ),
      },
    ],
    [
      allSelected,
      onHandoff,
      onMarkRead,
      onOpenDetail,
      onToggle,
      onToggleAll,
      readOnly,
      selectedIds,
    ],
  );

  return (
    <div className="notification-center__desktop-table" data-testid="notification-desktop-table">
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

function MobileNotificationCards({
  items,
  selectedIds,
  onToggle,
  onToggleAll,
  readOnly,
  onOpenDetail,
  onHandoff,
  onMarkRead,
}: {
  items: NotificationInboxItem[];
  selectedIds: Set<string>;
  onToggle: (id: string, checked: boolean) => void;
  onToggleAll: (checked: boolean) => void;
  readOnly: boolean;
  onOpenDetail?: (item: NotificationInboxItem) => void;
  onHandoff?: NotificationHandoff;
  onMarkRead: (item: NotificationInboxItem) => void;
}) {
  const allSelected = items.length > 0 && items.every((item) => selectedIds.has(itemId(item)));
  return (
    <div className="notification-center__mobile-cards" data-testid="notification-mobile-cards">
      <div className="notification-center__mobile-select-all">
        <Checkbox
          checked={allSelected}
          indeterminate={selectedIds.size > 0 && !allSelected}
          aria-label={selectionLabel("all")}
          onChange={(checked) => onToggleAll(Boolean(checked))}
        >
          选择当前页
        </Checkbox>
      </div>
      {items.map((item) => (
        <article className="notification-center__mobile-card" key={itemId(item)}>
          <div className="notification-center__mobile-card-heading">
            <Checkbox
              checked={selectedIds.has(itemId(item))}
              aria-label={`选择通知 ${itemId(item)}`}
              onChange={(checked) => onToggle(itemId(item), Boolean(checked))}
            />
            <NotificationIdentity item={item} />
          </div>
          <div className="notification-center__mobile-card-facts">
            <Tag theme="default">{categoryLabel(item.notification.category)}</Tag>
            <Tag theme={severityTheme(item.notification.severity)}>
              {severityLabel(item.notification.severity)}
            </Tag>
            <span>{isUnread(item) ? "未读" : "已读"}</span>
          </div>
          <NotificationActions
            item={item}
            readOnly={readOnly}
            onOpenDetail={onOpenDetail}
            onHandoff={onHandoff}
            onMarkRead={onMarkRead}
          />
        </article>
      ))}
    </div>
  );
}

function NotificationList({
  controller,
  view,
  mobile,
  readOnly,
  selectedIds,
  onToggle,
  onToggleAll,
  onOpenDetail,
  onHandoff,
  onMarkRead,
  onRequestNextPage,
}: {
  controller: EnterpriseNotificationsHook;
  view: NotificationView;
  mobile: boolean;
  readOnly: boolean;
  selectedIds: Set<string>;
  onToggle: (id: string, checked: boolean) => void;
  onToggleAll: (checked: boolean) => void;
  onOpenDetail?: (item: NotificationInboxItem) => void;
  onHandoff?: NotificationHandoff;
  onMarkRead: (item: NotificationInboxItem) => void;
  onRequestNextPage?: (view: NotificationView) => void;
}) {
  const collection = collectionStatus(controller, view);
  return (
    <div className="notification-center__list-panel">
      <StateNotice
        status={collection.status}
        invalidItemCount={collection.invalidItemCount}
        hasItems={collection.items.length > 0}
      />
      {collection.items.length === 0 ? (
        collection.status !== "loading" ? (
          <EmptyCollection view={view} />
        ) : null
      ) : mobile ? (
        <MobileNotificationCards
          items={collection.items}
          selectedIds={selectedIds}
          onToggle={onToggle}
          onToggleAll={onToggleAll}
          readOnly={readOnly}
          onOpenDetail={onOpenDetail}
          onHandoff={onHandoff}
          onMarkRead={onMarkRead}
        />
      ) : (
        <DesktopNotificationTable
          items={collection.items}
          selectedIds={selectedIds}
          onToggle={onToggle}
          onToggleAll={onToggleAll}
          readOnly={readOnly}
          onOpenDetail={onOpenDetail}
          onHandoff={onHandoff}
          onMarkRead={onMarkRead}
        />
      )}
      {collection.nextCursor ? (
        <Pagination
          className="notification-center__pagination"
          current={1}
          pageSize={Math.max(1, collection.items.length)}
          total={collection.items.length + 1}
          showPageSize={false}
          showJumper={false}
          onCurrentChange={() => onRequestNextPage?.(view)}
          totalContent="还有更多记录"
        />
      ) : null}
    </div>
  );
}

function DrawerHeader({
  inline,
  readOnly,
  onClose,
}: {
  inline: boolean;
  readOnly: boolean;
  onClose: () => void;
}) {
  return (
    <div className="notification-center__drawer-header">
      <div>
        <span className="notification-center__eyebrow">ENTERPRISE SIGNALS</span>
        <h2>{inline ? "通知收件箱" : "消息中心"}</h2>
      </div>
      <div className="notification-center__drawer-header-actions">
        {readOnly ? <Tag theme="warning">只读模式</Tag> : <Tag theme="success">Governed</Tag>}
        {!inline ? (
          <Button
            variant="text"
            shape="square"
            aria-label="关闭"
            icon={<CloseIcon />}
            onClick={onClose}
          />
        ) : null}
      </div>
    </div>
  );
}

export default function NotificationDrawer({
  controller,
  visible,
  onClose,
  inline = false,
  mobile: mobileOverride,
  readOnly = false,
  onOpenDetail,
  onHandoff,
  onRequestNextPage,
  showRail = true,
  returnFocusRef,
}: NotificationDrawerProps) {
  const mobile = useNotificationMobile(mobileOverride);
  const [view, setView] = useState<NotificationView>("unread");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const closeSentRef = useRef(false);

  useEffect(() => {
    if (visible) closeSentRef.current = false;
  }, [visible]);

  useEffect(() => {
    if (!visible || view === "unread" || controller.history.status !== "idle") return;
    void controller.history.load();
  }, [controller.history, view, visible]);

  const collection = collectionStatus(controller, view);
  useEffect(() => {
    const allowed = new Set(collection.items.map(itemId));
    setSelectedIds((current) => {
      const next = new Set([...current].filter((id) => allowed.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [collection.items]);

  const handleClose = useCallback(() => {
    if (closeSentRef.current) return;
    closeSentRef.current = true;
    onClose();
    window.setTimeout(() => returnFocusRef?.current?.focus(), 0);
  }, [onClose, returnFocusRef]);

  const handleToggle = useCallback((id: string, checked: boolean) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const handleToggleAll = useCallback(
    (checked: boolean) => {
      setSelectedIds(checked ? new Set(collection.items.map(itemId)) : new Set());
    },
    [collection.items],
  );

  const handleMarkRead = useCallback(
    (item: NotificationInboxItem) => {
      if (readOnly || !isUnread(item)) return;
      void controller.mutation.markRead(item.notification.id, {
        expectedRevision: item.receipt.revision,
        reason: READ_REASON,
      });
    },
    [controller.mutation, readOnly],
  );

  const handleBulkRead = useCallback(() => {
    if (readOnly || selectedIds.size === 0) return;
    const items = collection.items
      .filter((item) => selectedIds.has(itemId(item)) && isUnread(item))
      .map((item) => ({
        notificationId: item.notification.id,
        expectedRevision: item.receipt.revision,
      }));
    if (items.length === 0) return;
    void controller.mutation.bulkRead({ items, reason: READ_REASON }).then((outcome) => {
      if (outcome?.state !== "unavailable") setSelectedIds(new Set());
      if (outcome?.state === "applied" || outcome?.state === "coalesced")
        void controller.load.reload();
    });
  }, [collection.items, controller.load, controller.mutation, readOnly, selectedIds]);

  const body = (
    <div role="dialog" aria-label="消息中心" className="notification-center__drawer-dialog-shell">
      <div className="notification-center__drawer-surface">
        {showRail ? <SignalRoutingRail /> : null}
        <div className="notification-center__drawer-toolbar">
          <div>
            <span className="notification-center__eyebrow">INBOX AUTHORITY</span>
            <p>
              {controller.summary.value?.as_of
                ? `权威时间 ${formatNotificationDate(controller.summary.value.as_of)}`
                : "等待权威时间"}
            </p>
          </div>
          <div className="notification-center__drawer-toolbar-actions">
            <span className="notification-center__status-chip">
              <span className="notification-center__status-dot" aria-hidden="true" />
              {sourceStatusLabel(collection.status)}
            </span>
            <Button
              variant="outline"
              size="small"
              icon={<RefreshIcon />}
              aria-label="刷新通知"
              disabled={!controller.active || controller.load.status === "loading"}
              onClick={() => void controller.load.reload()}
            >
              刷新
            </Button>
          </div>
        </div>
        <div className="notification-center__bulk-toolbar">
          <Checkbox
            checked={
              collection.items.length > 0 &&
              collection.items.every((item) => selectedIds.has(itemId(item)))
            }
            indeterminate={selectedIds.size > 0 && selectedIds.size < collection.items.length}
            aria-label={selectionLabel(view)}
            onChange={(checked) => handleToggleAll(Boolean(checked))}
          >
            选择当前页
          </Checkbox>
          <span className="notification-center__selection-count">已选择 {selectedIds.size} 条</span>
          <Button
            theme="primary"
            size="small"
            icon={<CheckCircleIcon />}
            disabled={readOnly || selectedIds.size === 0 || controller.mutation.status === "saving"}
            loading={controller.mutation.status === "saving"}
            aria-label="批量标记已读"
            onClick={handleBulkRead}
          >
            批量标记已读
          </Button>
          {readOnly ? (
            <span className="notification-center__read-only-copy">只读模式下不允许修改回执</span>
          ) : null}
        </div>
        <Tabs
          className="notification-center__inbox-tabs"
          value={view}
          onChange={(next) => {
            setView(String(next) as NotificationView);
            setSelectedIds(new Set());
            if (String(next) !== "unread" && controller.history.status === "idle") {
              void controller.history.load();
            }
          }}
          theme="normal"
        >
          {VIEW_LABELS.map((tab) => (
            <Tabs.TabPanel
              key={tab.value}
              value={tab.value}
              label={
                <AccessibleTabLabel
                  label={tab.label}
                  active={view === tab.value}
                  onActivate={() => {
                    setView(tab.value);
                    setSelectedIds(new Set());
                    if (tab.value !== "unread" && controller.history.status === "idle") {
                      void controller.history.load();
                    }
                  }}
                />
              }
              destroyOnHide
            >
              <NotificationList
                controller={controller}
                view={tab.value}
                mobile={mobile}
                readOnly={readOnly}
                selectedIds={selectedIds}
                onToggle={handleToggle}
                onToggleAll={handleToggleAll}
                onOpenDetail={onOpenDetail}
                onHandoff={onHandoff}
                onMarkRead={handleMarkRead}
                onRequestNextPage={onRequestNextPage}
              />
            </Tabs.TabPanel>
          ))}
        </Tabs>
      </div>
    </div>
  );

  const header = <DrawerHeader inline={inline} readOnly={readOnly} onClose={handleClose} />;
  if (inline) {
    return (
      <section
        className="notification-center__inline-drawer"
        aria-label="消息中心收件箱"
        data-testid="notification-inline-drawer"
      >
        {header}
        {body}
      </section>
    );
  }

  return (
    <Drawer
      className="notification-center__drawer"
      closeBtn={false}
      visible={visible}
      placement="right"
      size={mobile ? "100%" : "min(760px, 100vw)"}
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
