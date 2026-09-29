import { useCallback, type RefObject } from "react";
import { Button, Drawer, Tag } from "tdesign-react";
import {
  ArrowRightIcon,
  CheckCircleIcon,
  CloseIcon,
  DeleteTimeIcon,
  SecuredIcon,
} from "tdesign-icons-react";

import type {
  LegalHold,
  RecoveryEntryDetail,
  RecoveryResource,
  RecoveryApprovalHandoff,
} from "./contentRecoveryTypes";
import {
  formatRecoveryDate,
  recoveryStatusLabel,
  recoveryStatusTheme,
  safeSnapshotEntries,
} from "./recoveryUi";
import { Alert, Empty, Spin } from "../../ui";

export interface RecoveryDetailDrawerProps {
  visible: boolean;
  state: RecoveryResource<RecoveryEntryDetail>;
  readOnly?: boolean;
  onClose: () => void;
  onRestore?: (entry: RecoveryEntryDetail) => void;
  onAddHold?: (entry: RecoveryEntryDetail) => void;
  onReleaseHold?: (hold: LegalHold) => void;
  onRequestPurge?: (entry: RecoveryEntryDetail) => void;
  onApprovalHandoff?: RecoveryApprovalHandoff;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

function EventLabel({ eventType }: { eventType: string }) {
  const labels: Record<string, string> = {
    recycled: "移入回收站",
    restored: "恢复文档",
    hold_applied: "添加法律保留",
    hold_released: "释放法律保留",
    purge_requested: "提交清除审批",
    purge_approved: "清除审批通过",
    purge_cancelled: "取消清除审批",
  };
  return <span>{labels[eventType] ?? "生命周期事件"}</span>;
}

function DetailFacts({ entry }: { entry: RecoveryEntryDetail }) {
  const facts = safeSnapshotEntries(entry.safe_snapshot);
  return (
    <section
      className="content-recovery__detail-section"
      aria-labelledby="recovery-detail-facts-title"
    >
      <div className="content-recovery__section-heading">
        <div>
          <span className="content-recovery__eyebrow">SAFE RECOVERY FACTS</span>
          <h3 id="recovery-detail-facts-title">回收条目事实</h3>
        </div>
        <Tag theme="success" variant="light-outline" size="small">
          Body-free snapshot
        </Tag>
      </div>
      <dl className="content-recovery__detail-facts">
        <div>
          <dt>文档</dt>
          <dd>{entry.document_label}</dd>
        </div>
        <div>
          <dt>知识库</dt>
          <dd>{entry.dataset_label}</dd>
        </div>
        <div>
          <dt>条目 ID</dt>
          <dd>
            <code>{entry.id}</code>
          </dd>
        </div>
        <div>
          <dt>文档 ID</dt>
          <dd>
            <code>{entry.document_id}</code>
          </dd>
        </div>
        <div>
          <dt>状态</dt>
          <dd>
            <Tag theme={recoveryStatusTheme(entry.status)}>{recoveryStatusLabel(entry.status)}</Tag>
          </dd>
        </div>
        <div>
          <dt>版本栅栏</dt>
          <dd>
            <code>revision {entry.revision}</code>
          </dd>
        </div>
        <div>
          <dt>原检索状态</dt>
          <dd>{entry.original_retrieval_enabled ? "启用" : "关闭"}</dd>
        </div>
        <div>
          <dt>当前检索状态</dt>
          <dd>{entry.current_retrieval_enabled ? "启用" : "关闭"}</dd>
        </div>
        <div>
          <dt>保留截止</dt>
          <dd>
            <time dateTime={entry.purge_eligible_at ?? undefined}>
              {formatRecoveryDate(entry.purge_eligible_at)}
            </time>
          </dd>
        </div>
        <div>
          <dt>有效法律保留</dt>
          <dd>{entry.active_hold_count === null ? "未返回" : `${entry.active_hold_count} 个`}</dd>
        </div>
      </dl>
      {facts.length > 0 ? (
        <dl className="content-recovery__safe-snapshot">
          {facts.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <Empty
          type="empty"
          title="暂无安全快照字段"
          description="服务端没有返回可展示的 allow-list 字段。"
        />
      )}
    </section>
  );
}

function DetailHolds({
  entry,
  readOnly,
  onAddHold,
  onReleaseHold,
}: Pick<RecoveryDetailDrawerProps, "readOnly" | "onAddHold" | "onReleaseHold"> & {
  entry: RecoveryEntryDetail;
}) {
  const activeHolds = entry.holds.filter((hold) => hold.status === "active");
  return (
    <section
      className="content-recovery__detail-section"
      aria-labelledby="recovery-detail-holds-title"
    >
      <div className="content-recovery__section-heading">
        <div>
          <span className="content-recovery__eyebrow">LEGAL HOLD REGISTER</span>
          <h3 id="recovery-detail-holds-title">法律保留</h3>
        </div>
        {onAddHold && entry.status === "recycled" && activeHolds.length === 0 ? (
          <Button
            size="small"
            variant="outline"
            icon={<SecuredIcon />}
            disabled={readOnly}
            onClick={() => onAddHold(entry)}
          >
            添加保留
          </Button>
        ) : null}
      </div>
      {entry.holds.length === 0 ? (
        <Empty type="empty" title="暂无法律保留" description="未发现阻断清除资格的有效法律保全。" />
      ) : (
        <div className="content-recovery__hold-list">
          {entry.holds.map((hold) => (
            <article key={hold.id} className={`content-recovery__hold-item is-${hold.status}`}>
              <div>
                <strong>{hold.safe_reason}</strong>
                <span>
                  {hold.reason_code} · revision {hold.revision}
                </span>
                <time dateTime={hold.held_at}>创建于 {formatRecoveryDate(hold.held_at)}</time>
              </div>
              {hold.status === "active" && onReleaseHold ? (
                <Button
                  size="small"
                  variant="text"
                  theme="danger"
                  disabled={readOnly}
                  aria-label={`释放法律保留 ${hold.safe_reason}`}
                  onClick={() => onReleaseHold(hold)}
                >
                  释放
                </Button>
              ) : (
                <Tag theme="default" size="small">
                  已释放
                </Tag>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function DetailPurgeRequests({
  entry,
  readOnly,
  onRequestPurge,
  onApprovalHandoff,
}: Pick<RecoveryDetailDrawerProps, "readOnly" | "onRequestPurge" | "onApprovalHandoff"> & {
  entry: RecoveryEntryDetail;
}) {
  const request = entry.purge_requests[0];
  return (
    <section
      className="content-recovery__detail-section"
      aria-labelledby="recovery-detail-purge-title"
    >
      <div className="content-recovery__section-heading">
        <div>
          <span className="content-recovery__eyebrow">APPROVAL GATED PURGE</span>
          <h3 id="recovery-detail-purge-title">清除审批</h3>
        </div>
        {entry.status === "recycled" && onRequestPurge ? (
          <Button
            theme="danger"
            variant="outline"
            size="small"
            icon={<DeleteTimeIcon />}
            disabled={readOnly}
            onClick={() => onRequestPurge(entry)}
          >
            申请清除
          </Button>
        ) : null}
      </div>
      {request ? (
        <div className="content-recovery__purge-request">
          <Tag theme={request.status === "approved" ? "success" : "warning"}>
            {request.status === "pending_approval" ? "待审批" : request.status}
          </Tag>
          <code>{request.id}</code>
          <span>申请时间 {formatRecoveryDate(request.requested_at)}</span>
          {request.approval_request_id && onApprovalHandoff ? (
            <Button
              variant="text"
              size="small"
              icon={<ArrowRightIcon />}
              onClick={() => onApprovalHandoff(request.approval_request_id as string)}
            >
              前往审批中心
            </Button>
          ) : null}
        </div>
      ) : (
        <p className="content-recovery__empty-copy">
          尚未提交清除审批申请。本中心不会批准或执行物理清除。
        </p>
      )}
    </section>
  );
}

function DetailTimeline({ entry }: { entry: RecoveryEntryDetail }) {
  if (entry.events.length === 0) {
    return (
      <Empty
        type="empty"
        title="暂无恢复事件"
        description="当前条目尚无可展示的不可变生命周期事件。"
      />
    );
  }
  return (
    <section
      className="content-recovery__detail-section"
      aria-labelledby="recovery-detail-events-title"
    >
      <div className="content-recovery__section-heading">
        <div>
          <span className="content-recovery__eyebrow">IMMUTABLE RECOVERY EVENT CHAIN</span>
          <h3 id="recovery-detail-events-title">恢复事件链</h3>
        </div>
        <Tag theme="success" variant="light-outline" size="small">
          append-only
        </Tag>
      </div>
      <ol className="content-recovery__event-timeline">
        {entry.events.map((event) => (
          <li key={event.id}>
            <span className="content-recovery__event-marker" aria-hidden="true">
              <CheckCircleIcon />
            </span>
            <div className="content-recovery__event-copy">
              <div>
                <strong>
                  <EventLabel eventType={event.event_type} />
                </strong>
                <span>#{event.sequence}</span>
              </div>
              <time dateTime={event.occurred_at}>{formatRecoveryDate(event.occurred_at)}</time>
              <span>
                actor {event.actor_id} · request {event.request_id}
              </span>
              <code title={event.event_digest}>digest {event.event_digest}</code>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function RecoveryDetailDrawer({
  visible,
  state,
  readOnly = false,
  onClose,
  onRestore,
  onAddHold,
  onReleaseHold,
  onRequestPurge,
  onApprovalHandoff,
  returnFocusRef,
}: RecoveryDetailDrawerProps) {
  const handleClose = useCallback(() => {
    onClose();
    const target = returnFocusRef?.current;
    if (target && target.isConnected) window.setTimeout(() => target.focus(), 0);
  }, [onClose, returnFocusRef]);
  const entry = state.value;
  const header = (
    <div className="content-recovery__drawer-header">
      <div>
        <span className="content-recovery__eyebrow">RECOVERY ENTRY</span>
        <h2>{entry?.document_label ?? "回收条目详情"}</h2>
        {entry ? <code>{entry.id}</code> : null}
      </div>
      <div className="content-recovery__drawer-header-actions">
        <Tag theme={entry ? recoveryStatusTheme(entry.status) : "default"}>
          {entry ? recoveryStatusLabel(entry.status) : "Unavailable"}
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
    <div
      className="content-recovery__detail-dialog-shell"
      role="dialog"
      aria-label="回收条目详情"
      aria-modal="true"
    >
      {state.status === "loading" ? (
        <div className="content-recovery__state content-recovery__state--loading">
          <Spin tip="正在读取回收条目详情…" />
        </div>
      ) : null}
      {state.status === "unavailable" ? (
        <div>
          <Alert
            theme="warning"
            title="详情权威暂不可用"
            message="当前无法读取回收条目详情，未返回的事实不会被推断。"
          />
        </div>
      ) : null}
      {state.status === "error" ? (
        <div>
          <Alert theme="error" title="详情读取失败" message="当前无法读取回收条目详情，请重试。" />
        </div>
      ) : null}
      {entry && state.status !== "loading" && state.status !== "unavailable" ? (
        <div className="content-recovery__detail-surface">
          <div className="content-recovery__detail-toolbar">
            <div>
              <span className="content-recovery__detail-boundary">
                <SecuredIcon aria-hidden="true" />
                恢复与清除均受版本栅栏约束
              </span>
            </div>
            <div className="content-recovery__detail-toolbar-actions">
              {entry.status === "recycled" && onRestore ? (
                <Button
                  theme="primary"
                  icon={<ArrowRightIcon />}
                  disabled={readOnly}
                  onClick={() => onRestore(entry)}
                >
                  恢复文档
                </Button>
              ) : null}
              {readOnly ? (
                <Tag theme="warning">只读模式</Tag>
              ) : (
                <Tag theme="success">Safe mutation boundary</Tag>
              )}
            </div>
          </div>
          <DetailFacts entry={entry} />
          <DetailHolds
            entry={entry}
            readOnly={readOnly}
            onAddHold={onAddHold}
            onReleaseHold={onReleaseHold}
          />
          <DetailPurgeRequests
            entry={entry}
            readOnly={readOnly}
            onRequestPurge={onRequestPurge}
            onApprovalHandoff={onApprovalHandoff}
          />
          <DetailTimeline entry={entry} />
        </div>
      ) : null}
      {!entry &&
      state.status !== "loading" &&
      state.status !== "unavailable" &&
      state.status !== "error" ? (
        <Empty type="empty" title="详情为空" description="服务端没有返回可验证的回收条目详情。" />
      ) : null}
    </div>
  );

  return (
    <Drawer
      className="content-recovery__detail-drawer"
      visible={visible}
      placement="right"
      size="min(680px, 100vw)"
      header={header}
      footer={null}
      attach="body"
      destroyOnClose
      closeBtn={false}
      closeOnEscKeydown
      closeOnOverlayClick
      onClose={handleClose}
      onEscKeydown={handleClose}
    >
      {body}
    </Drawer>
  );
}
