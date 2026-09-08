import { Button, Loading, PrimaryTable, Tag, type PrimaryTableCol } from "tdesign-react";
import { CloseIcon, FileIcon, RefreshIcon } from "tdesign-icons-react";

import type { TaskOperation, TaskOperationAction } from "./taskOperationsTypes";
import {
  formatDuration,
  formatTaskDate,
  taskStatusDescription,
  taskStatusLabel,
  taskStatusTheme,
} from "./taskOperationsUi";

export interface TaskOperationsTableProps {
  operations: TaskOperation[];
  mobile?: boolean;
  loading?: boolean;
  readOnly?: boolean;
  onOpenDetail?: TaskOperationAction;
  onRetry?: TaskOperationAction;
  onCancel?: TaskOperationAction;
}

function TaskIdentity({ task }: { task: TaskOperation }) {
  return (
    <div className="task-operations__task-identity">
      <strong>{task.task_label}</strong>
      <span>
        {task.source_label} · {task.task_type}
      </span>
      <code>{task.id}</code>
    </div>
  );
}

function TaskStatus({ task }: { task: TaskOperation }) {
  return (
    <div className="task-operations__status-cell">
      <Tag theme={taskStatusTheme(task.status)} variant="light-outline" size="small">
        {taskStatusLabel(task.status)}
      </Tag>
      <small>{taskStatusDescription(task)}</small>
    </div>
  );
}

function TaskActions({
  task,
  readOnly,
  onOpenDetail,
  onRetry,
  onCancel,
}: Omit<TaskOperationsTableProps, "operations" | "mobile" | "loading"> & {
  task: TaskOperation;
}) {
  return (
    <div className="task-operations__row-actions">
      {onOpenDetail ? (
        <Button
          variant="text"
          size="small"
          icon={<FileIcon />}
          aria-label={`查看 ${task.task_label} 详情`}
          onClick={() => onOpenDetail(task)}
        >
          详情
        </Button>
      ) : null}
      {task.retryable && onRetry ? (
        <Button
          variant="outline"
          size="small"
          icon={<RefreshIcon />}
          aria-disabled={readOnly}
          className={readOnly ? "task-operations__button-is-disabled" : undefined}
          aria-label={`重试 ${task.task_label}`}
          onClick={() => {
            if (!readOnly) onRetry(task);
          }}
        >
          重试
        </Button>
      ) : null}
      {task.cancellable && onCancel ? (
        <Button
          theme="danger"
          variant="text"
          size="small"
          icon={<CloseIcon />}
          aria-disabled={readOnly}
          className={readOnly ? "task-operations__button-is-disabled" : undefined}
          aria-label={`取消 ${task.task_label}`}
          onClick={() => {
            if (!readOnly) onCancel(task);
          }}
        >
          取消
        </Button>
      ) : null}
    </div>
  );
}

function TaskFacts({ task }: { task: TaskOperation }) {
  return (
    <dl className="task-operations__task-facts">
      <div>
        <dt>状态</dt>
        <dd>
          <TaskStatus task={task} />
        </dd>
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
        <dt>队列</dt>
        <dd>{task.queue_name}</dd>
      </div>
      <div>
        <dt>耗时</dt>
        <dd>{formatDuration(task.duration_ms)}</dd>
      </div>
      <div>
        <dt>创建时间</dt>
        <dd>
          <time dateTime={task.created_at ?? undefined}>{formatTaskDate(task.created_at)}</time>
        </dd>
      </div>
      <div>
        <dt>revision</dt>
        <dd>
          <code>{task.revision}</code>
        </dd>
      </div>
    </dl>
  );
}

export default function TaskOperationsTable({
  operations,
  mobile = false,
  loading = false,
  readOnly = false,
  onOpenDetail,
  onRetry,
  onCancel,
}: TaskOperationsTableProps) {
  if (loading && operations.length === 0) {
    return (
      <div className="task-operations__table-loading">
        <Loading text="正在读取任务状态…" />
      </div>
    );
  }

  if (mobile) {
    return (
      <section
        className="task-operations__mobile-cards"
        data-testid="task-operations-mobile-cards"
        aria-label="任务卡片列表"
      >
        {operations.map((task) => (
          <article className="task-operations__task-card" key={task.id} tabIndex={0}>
            <header>
              <TaskIdentity task={task} />
              <Tag theme={taskStatusTheme(task.status)} variant="light-outline" size="small">
                {taskStatusLabel(task.status)}
              </Tag>
            </header>
            <TaskFacts task={task} />
            <TaskActions
              task={task}
              readOnly={readOnly}
              onOpenDetail={onOpenDetail}
              onRetry={onRetry}
              onCancel={onCancel}
            />
          </article>
        ))}
      </section>
    );
  }

  const columns: Array<PrimaryTableCol<TaskOperation>> = [
    {
      title: "任务与来源",
      colKey: "task",
      width: 280,
      cell: ({ row }) => <TaskIdentity task={row} />,
    },
    {
      title: "状态",
      colKey: "status",
      width: 175,
      cell: ({ row }) => <TaskStatus task={row} />,
    },
    {
      title: "队列 / 尝试",
      colKey: "attempt",
      width: 150,
      cell: ({ row }) => (
        <div className="task-operations__queue-cell">
          <strong>{row.queue_name}</strong>
          <code>
            {row.attempt_number} / {row.max_attempts}
          </code>
        </div>
      ),
    },
    {
      title: "时间 / 耗时",
      colKey: "timing",
      width: 170,
      cell: ({ row }) => (
        <div className="task-operations__timing-cell">
          <time dateTime={row.created_at ?? undefined}>{formatTaskDate(row.created_at)}</time>
          <small>{formatDuration(row.duration_ms)}</small>
        </div>
      ),
    },
    {
      title: "操作",
      colKey: "actions",
      width: 230,
      cell: ({ row }) => (
        <TaskActions
          task={row}
          readOnly={readOnly}
          onOpenDetail={onOpenDetail}
          onRetry={onRetry}
          onCancel={onCancel}
        />
      ),
    },
  ];

  return (
    <div
      className="task-operations__desktop-table"
      data-testid="task-operations-desktop-table"
      tabIndex={0}
      aria-label="任务表格，可横向滚动"
    >
      <PrimaryTable
        rowKey="id"
        columns={columns}
        data={operations}
        size="small"
        bordered
        hover
        verticalAlign="top"
        empty="暂无任务记录"
      />
    </div>
  );
}
