import { type RefObject } from "react";
import { Button, Drawer, Empty, Loading, Tag } from "tdesign-react";
import {
  ArrowRightIcon,
  CheckCircleIcon,
  CloseIcon,
  ErrorCircleIcon,
  RefreshIcon,
  SecuredIcon,
  TimeIcon,
} from "tdesign-icons-react";

import type { TaskOperation, TaskOperationDetail, TaskResource } from "./taskOperationsTypes";
import {
  formatDuration,
  formatTaskDate,
  safeTaskSnapshotEntries,
  taskStatusLabel,
  taskStatusTheme,
  useTaskEscape,
} from "./taskOperationsUi";

export interface TaskOperationDetailDrawerProps {
  visible: boolean;
  state: TaskResource<TaskOperationDetail>;
  fallbackTask?: TaskOperation | null;
  readOnly?: boolean;
  onClose: () => void;
  onRetry?: (task: TaskOperation) => void;
  onCancel?: (task: TaskOperation) => void;
  onAcknowledge?: (task: TaskOperation) => void;
  onHandoff?: (task: TaskOperation) => void;
  returnFocusRef?: RefObject<HTMLElement | null>;
}

function eventLabel(eventType: string): string {
  const labels: Record<string, string> = {
    enqueued: "进入队列",
    attempt_started: "开始尝试",
    attempt_failed: "尝试失败",
    retry_scheduled: "安排重试",
    completed: "任务完成",
    cancelled: "任务取消",
    reconciled: "完成对账",
    materialized: "投影已物化",
    status_changed: "状态已更新",
    source_stale: "来源已失效",
    action_requested: "操作已请求",
    action_applied: "操作已应用",
    action_rejected: "操作已拒绝",
    attention_acknowledged: "已确认关注事项",
  };
  return labels[eventType] ?? eventType;
}

export default function TaskOperationDetailDrawer({
  visible,
  state,
  fallbackTask = null,
  readOnly = false,
  onClose,
  onRetry,
  onCancel,
  onAcknowledge,
  onHandoff,
  returnFocusRef,
}: TaskOperationDetailDrawerProps) {
  useTaskEscape(onClose, visible);

  const task = state.value ?? fallbackTask;
  const events = state.value?.events ?? [];
  const handleClose = () => {
    onClose();
    const target = returnFocusRef?.current;
    if (target && target.isConnected) window.setTimeout(() => target.focus(), 0);
  };

  const header = (
    <div className="task-operations__drawer-header">
      <div>
        <span className="task-operations__eyebrow">TASK OPERATION</span>
        <h2>{task?.task_label ?? "任务详情与事件链"}</h2>
        {task ? <code>{task.id}</code> : null}
      </div>
      <div className="task-operations__drawer-header-actions">
        {task ? (
          <Tag theme={taskStatusTheme(task.status)}>{taskStatusLabel(task.status)}</Tag>
        ) : null}
        <Button
          variant="text"
          shape="square"
          icon={<CloseIcon />}
          aria-label="关闭任务详情"
          onClick={handleClose}
        />
      </div>
    </div>
  );

  return (
    <Drawer
      className="task-operations__detail-drawer"
      visible={visible}
      placement="right"
      size="min(700px, 100vw)"
      header={header}
      footer={null}
      attach="body"
      destroyOnClose
      closeBtn={false}
      closeOnEscKeydown={false}
      closeOnOverlayClick
      onClose={handleClose}
    >
      <div
        className="task-operations__detail-shell"
        role="dialog"
        aria-label="任务详情与事件链"
        aria-modal="true"
      >
        {state.status === "loading" ? (
          <div className="task-operations__detail-state">
            <Loading text="正在读取任务事件链…" />
          </div>
        ) : null}
        {state.status === "unavailable" ? (
          <div role="alert">
            <Tag theme="warning">任务详情权威暂不可用，未返回的事实不会被推断。</Tag>
          </div>
        ) : null}
        {state.status === "error" ? (
          <div role="alert">
            <Tag theme="danger">任务详情读取失败，请稍后重试。</Tag>
          </div>
        ) : null}
        {task &&
        state.status !== "loading" &&
        state.status !== "unavailable" &&
        state.status !== "error" ? (
          <div className="task-operations__detail-surface">
            <div className="task-operations__detail-toolbar">
              <span className="task-operations__detail-boundary">
                <SecuredIcon aria-hidden="true" /> 任务状态受 revision 与事件链保护
              </span>
              <div className="task-operations__detail-toolbar-actions">
                {onHandoff ? (
                  <Button
                    icon={<ArrowRightIcon />}
                    variant="outline"
                    aria-label="查看任务来源"
                    onClick={() => onHandoff(task)}
                  >
                    查看来源
                  </Button>
                ) : null}
                {task.action_required && onAcknowledge ? (
                  <Button
                    icon={<CheckCircleIcon />}
                    variant="outline"
                    disabled={readOnly}
                    aria-label="确认已知悉"
                    onClick={() => onAcknowledge(task)}
                  >
                    确认已知悉
                  </Button>
                ) : null}
                {task.retryable && onRetry ? (
                  <Button
                    icon={<RefreshIcon />}
                    variant="outline"
                    disabled={readOnly}
                    onClick={() => onRetry(task)}
                  >
                    重试
                  </Button>
                ) : null}
                {task.cancellable && onCancel ? (
                  <Button
                    icon={<CloseIcon />}
                    theme="danger"
                    variant="text"
                    disabled={readOnly}
                    onClick={() => onCancel(task)}
                  >
                    取消任务
                  </Button>
                ) : null}
                {readOnly ? (
                  <Tag theme="warning">只读模式</Tag>
                ) : (
                  <Tag theme="success">Safe mutation boundary</Tag>
                )}
              </div>
            </div>
            <section
              className="task-operations__detail-section"
              aria-labelledby="task-detail-facts-title"
            >
              <div className="task-operations__section-heading">
                <div>
                  <span className="task-operations__eyebrow">SAFE EXECUTION FACTS</span>
                  <h3 id="task-detail-facts-title">任务事实</h3>
                </div>
                <Tag theme="success" variant="light-outline">
                  Body-free snapshot
                </Tag>
              </div>
              <dl className="task-operations__detail-facts">
                <div>
                  <dt>任务类型</dt>
                  <dd>{task.task_type}</dd>
                </div>
                <div>
                  <dt>来源</dt>
                  <dd>{task.source_label}</dd>
                </div>
                <div>
                  <dt>队列</dt>
                  <dd>{task.queue_name}</dd>
                </div>
                <div>
                  <dt>尝试</dt>
                  <dd>
                    <code>
                      {task.attempt_number} / {task.max_attempts}
                    </code>
                  </dd>
                </div>
                <div>
                  <dt>创建时间</dt>
                  <dd>{formatTaskDate(task.created_at)}</dd>
                </div>
                <div>
                  <dt>耗时</dt>
                  <dd>{formatDuration(task.duration_ms)}</dd>
                </div>
                <div>
                  <dt>状态</dt>
                  <dd>
                    <Tag theme={taskStatusTheme(task.status)}>{taskStatusLabel(task.status)}</Tag>
                  </dd>
                </div>
                <div>
                  <dt>revision</dt>
                  <dd>
                    <code>{task.revision}</code>
                  </dd>
                </div>
              </dl>
              {safeTaskSnapshotEntries(task.safe_snapshot).length ? (
                <dl className="task-operations__safe-snapshot">
                  {safeTaskSnapshotEntries(task.safe_snapshot).map(([label, value]) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
            </section>
            <section
              className="task-operations__detail-section"
              aria-labelledby="task-event-chain-title"
            >
              <div className="task-operations__section-heading">
                <div>
                  <span className="task-operations__eyebrow">Immutable task event chain</span>
                  <h3 id="task-event-chain-title">事件链</h3>
                </div>
                <Tag theme="primary" variant="light-outline">
                  {events.length} events
                </Tag>
              </div>
              {events.length ? (
                <ol className="task-operations__event-list">
                  {events.map((event) => (
                    <li key={event.id}>
                      <span className="task-operations__event-marker" aria-hidden="true">
                        {event.event_type === "attempt_failed" ? (
                          <ErrorCircleIcon />
                        ) : event.event_type === "completed" ? (
                          <CheckCircleIcon />
                        ) : (
                          <TimeIcon />
                        )}
                      </span>
                      <div className="task-operations__event-copy">
                        <div>
                          <strong>{eventLabel(event.event_type)}</strong>
                          <span>
                            #{event.sequence} · {event.event_type}
                          </span>
                        </div>
                        <time dateTime={event.occurred_at}>
                          {formatTaskDate(event.occurred_at)}
                        </time>
                        <small>
                          actor {event.actor_id} · request {event.request_id}
                        </small>
                        <code title={event.event_digest}>digest {event.event_digest}</code>
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <Empty
                  type="empty"
                  title="暂无事件"
                  description="服务端没有返回可验证的任务事件。"
                />
              )}
            </section>
          </div>
        ) : null}
        {!task &&
        state.status !== "loading" &&
        state.status !== "unavailable" &&
        state.status !== "error" ? (
          <Empty type="empty" title="任务详情为空" description="服务端没有返回可验证的任务详情。" />
        ) : null}
      </div>
    </Drawer>
  );
}
