import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Tabs } from "tdesign-react";
import { TimeIcon } from "tdesign-icons-react";

import type {
  TaskOperation,
  TaskOperationsController,
  TaskOperationsTab,
} from "./taskOperationsTypes";
import { TaskStateNotice, formatTaskDate, useTaskFocusReturn } from "./taskOperationsUi";
import TaskAttentionBoard from "./TaskAttentionBoard";
import TaskOperationDetailDrawer from "./TaskOperationDetailDrawer";
import TaskOperationsHeader from "./TaskOperationsHeader";
import TaskOperationsLifecycleRail from "./TaskOperationsLifecycleRail";
import TaskOperationsTable from "./TaskOperationsTable";
import { TaskCancelDialog, TaskRetryDialog } from "./TaskMutationDialog";
import SavedViewsPanel from "./SavedViewsPanel";
import ReconciliationPanel from "./ReconciliationPanel";
import "../task-operations.css";

export interface TaskOperationsCenterProps {
  controller: TaskOperationsController;
  capabilityReady?: boolean;
  readOnly?: boolean;
  mobile?: boolean;
  tenantLabel?: string;
  title?: string;
  onHandoff?: (task: TaskOperation) => void;
}

function fallbackDetail(task: TaskOperation) {
  return { ...task, events: [] };
}

function TaskTabLabel({
  label,
  active,
  onActivate,
}: {
  label: string;
  active: boolean;
  onActivate: () => void;
}) {
  return (
    <span
      role="tab"
      tabIndex={0}
      aria-selected={active}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onActivate();
        }
      }}
    >
      {label}
    </span>
  );
}

function ActivityPanel({ controller }: { controller: TaskOperationsController }) {
  const status = controller.activity.status;
  if (status === "loading") {
    return <div className="task-operations__page-state">正在读取任务活动…</div>;
  }
  if (status === "unavailable") {
    return (
      <div role="alert">
        <Alert theme="warning" title="活动权威暂不可用" message="当前无法读取任务活动链。" />
      </div>
    );
  }
  if (status === "error") {
    return (
      <div role="alert">
        <Alert theme="error" title="活动读取失败" message="任务活动读取失败，请重试。" />
      </div>
    );
  }
  if (!controller.activity.items.length) {
    return (
      <TaskStateNotice
        status="empty"
        resourceLabel="任务活动"
        emptyTitle="暂无任务活动"
        emptyDescription="队列和尝试事件将在安全写入后显示。"
      />
    );
  }
  return (
    <section className="task-operations__activity-panel" aria-labelledby="task-activity-title">
      <div className="task-operations__section-heading">
        <div>
          <span className="task-operations__eyebrow">ACTIVITY AUTHORITY</span>
          <h2 id="task-activity-title">任务活动链</h2>
          <p>这里只展示当前租户可见的安全事件摘要。</p>
        </div>
        <span className="task-operations__activity-count">
          <TimeIcon aria-hidden="true" /> {controller.activity.items.length} records
        </span>
      </div>
      {status === "partial" ? (
        <div role="alert">
          <Alert
            theme="warning"
            title="部分活动可用"
            message={`${controller.activity.invalidItemCount} 条记录未通过安全校验，已隐藏。`}
          />
        </div>
      ) : null}
      <ol className="task-operations__timeline" aria-label="任务活动事件列表">
        {controller.activity.items.map((event) => (
          <li key={event.id}>
            <time dateTime={event.occurred_at}>{formatTaskDate(event.occurred_at)}</time>
            <div className="task-operations__timeline-item">
              <strong>{event.event_type}</strong>
              <span>
                #{event.sequence} · actor {event.actor_id}
              </span>
              <code>{event.id}</code>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function TaskOperationsCenter({
  controller,
  capabilityReady = true,
  readOnly = false,
  mobile = false,
  tenantLabel = "当前租户",
  title = "任务运营中心",
  onHandoff,
}: TaskOperationsCenterProps) {
  const [tab, setTab] = useState<TaskOperationsTab>("all");
  const [selectedTask, setSelectedTask] = useState<TaskOperation | null>(null);
  const [detailVisible, setDetailVisible] = useState(false);
  const [retryTask, setRetryTask] = useState<TaskOperation | null>(null);
  const [cancelTask, setCancelTask] = useState<TaskOperation | null>(null);
  const focus = useTaskFocusReturn();
  const summary = controller.summary.value;
  const operations = controller.operations.items;

  useEffect(() => {
    if (tab === "activity" && controller.activity.status === "idle") {
      void controller.activity.reload?.();
    }
  }, [controller.activity, tab]);

  useEffect(() => {
    if (!controller.active) return;
    if (controller.savedViews.status === "idle") void controller.savedViews.reload?.();
    if (controller.reconciliation.status === "idle") void controller.reconciliation.reload?.();
  }, [controller.active, controller.reconciliation, controller.savedViews]);

  const visibleOperations = useMemo(() => {
    if (tab === "running") {
      return operations.filter((task) => task.status === "queued" || task.status === "running");
    }
    if (tab === "failed")
      return operations.filter(
        (task) =>
          task.status === "failed" || task.status === "blocked" || task.status === "unavailable",
      );
    if (tab === "completed") return operations.filter((task) => task.status === "completed");
    return operations;
  }, [operations, tab]);

  const openDetail = useCallback(
    (task: TaskOperation) => {
      focus.capture();
      setSelectedTask(task);
      setDetailVisible(true);
      void controller.detail.load?.(task.id);
    },
    [controller.detail, focus],
  );
  const openRetry = useCallback(
    (task: TaskOperation) => {
      focus.capture();
      setRetryTask(task);
    },
    [focus],
  );
  const openCancel = useCallback(
    (task: TaskOperation) => {
      focus.capture();
      setCancelTask(task);
    },
    [focus],
  );
  const closeDetail = useCallback(() => {
    setDetailVisible(false);
    focus.restore();
  }, [focus]);
  const closeRetry = useCallback(() => {
    setRetryTask(null);
    focus.restore();
  }, [focus]);
  const closeCancel = useCallback(() => {
    setCancelTask(null);
    focus.restore();
  }, [focus]);

  const runRetry = useCallback(
    async (task: TaskOperation) => {
      if (readOnly || !controller.mutation.retry) return;
      try {
        await controller.mutation.retry(task);
        setRetryTask(null);
        focus.restore();
      } catch {
        // The controller owns the safe mutation error projection; keep the dialog open.
      }
    },
    [controller.mutation, focus, readOnly],
  );
  const runCancel = useCallback(
    async (task: TaskOperation) => {
      if (readOnly || !controller.mutation.cancel) return;
      try {
        await controller.mutation.cancel(task);
        setCancelTask(null);
        focus.restore();
      } catch {
        // The controller owns the safe mutation error projection; keep the dialog open.
      }
    },
    [controller.mutation, focus, readOnly],
  );
  const runAcknowledge = useCallback(
    async (task: TaskOperation) => {
      if (readOnly || !controller.mutation.acknowledge) return;
      try {
        await controller.mutation.acknowledge(task);
      } catch {
        // The controller owns the safe mutation error projection; keep the detail surface open.
      }
    },
    [controller.mutation, readOnly],
  );

  const detailState = useMemo(() => {
    if (!selectedTask) return controller.detail;
    if (controller.detail.value?.id === selectedTask.id) return controller.detail;
    return {
      status: controller.detail.status === "idle" ? "ready" : controller.detail.status,
      value: fallbackDetail(selectedTask),
      error: controller.detail.error,
    } as typeof controller.detail;
  }, [controller.detail, selectedTask]);

  const unavailable = !capabilityReady || !controller.active;
  const tableStatus = unavailable ? "unavailable" : controller.operations.status;
  const tableStateHasItems = visibleOperations.length > 0;

  return (
    <section
      role="region"
      className="task-operations"
      aria-labelledby="task-operations-title"
      data-title={title}
    >
      <TaskOperationsHeader
        tenantLabel={tenantLabel}
        asOf={summary?.as_of}
        readOnly={readOnly}
        title={title}
        onRefresh={
          controller.operations.reload ? () => void controller.operations.reload?.() : undefined
        }
      />
      <TaskAttentionBoard summary={summary} />
      <TaskOperationsLifecycleRail summary={summary} />
      <div className="task-operations__content-grid">
        <div className="task-operations__main-column">
          <Tabs
            className="task-operations__tabs"
            value={tab}
            onChange={(next) => setTab(String(next) as TaskOperationsTab)}
            theme="card"
          >
            <Tabs.TabPanel
              value="all"
              label={
                <TaskTabLabel label="All" active={tab === "all"} onActivate={() => setTab("all")} />
              }
              destroyOnHide
            >
              <TaskStateNotice
                status={tableStatus}
                invalidItemCount={controller.operations.invalidItemCount}
                hasItems={tableStateHasItems}
              />
              {tableStateHasItems || controller.operations.status === "loading" ? (
                <TaskOperationsTable
                  operations={visibleOperations}
                  mobile={mobile}
                  loading={controller.operations.status === "loading"}
                  readOnly={readOnly}
                  onOpenDetail={openDetail}
                  onRetry={openRetry}
                  onCancel={openCancel}
                />
              ) : null}
            </Tabs.TabPanel>
            <Tabs.TabPanel
              value="running"
              label={
                <TaskTabLabel
                  label="Running"
                  active={tab === "running"}
                  onActivate={() => setTab("running")}
                />
              }
              destroyOnHide
            >
              <TaskStateNotice
                status={tableStatus}
                invalidItemCount={controller.operations.invalidItemCount}
                hasItems={tableStateHasItems}
                emptyTitle="暂无运行中任务"
                emptyDescription="队列中没有等待或正在执行的任务。"
              />
              {tableStateHasItems || controller.operations.status === "loading" ? (
                <TaskOperationsTable
                  operations={visibleOperations}
                  mobile={mobile}
                  loading={controller.operations.status === "loading"}
                  readOnly={readOnly}
                  onOpenDetail={openDetail}
                  onRetry={openRetry}
                  onCancel={openCancel}
                />
              ) : null}
            </Tabs.TabPanel>
            <Tabs.TabPanel
              value="failed"
              label={
                <TaskTabLabel
                  label="Failed"
                  active={tab === "failed"}
                  onActivate={() => setTab("failed")}
                />
              }
              destroyOnHide
            >
              <TaskStateNotice
                status={tableStatus}
                invalidItemCount={controller.operations.invalidItemCount}
                hasItems={tableStateHasItems}
                emptyTitle="暂无失败任务"
                emptyDescription="当前没有需要人工重试或取消的失败任务。"
              />
              {tableStateHasItems || controller.operations.status === "loading" ? (
                <TaskOperationsTable
                  operations={visibleOperations}
                  mobile={mobile}
                  loading={controller.operations.status === "loading"}
                  readOnly={readOnly}
                  onOpenDetail={openDetail}
                  onRetry={openRetry}
                  onCancel={openCancel}
                />
              ) : null}
            </Tabs.TabPanel>
            <Tabs.TabPanel
              value="completed"
              label={
                <TaskTabLabel
                  label="Completed"
                  active={tab === "completed"}
                  onActivate={() => setTab("completed")}
                />
              }
              destroyOnHide
            >
              <TaskStateNotice
                status={tableStatus}
                invalidItemCount={controller.operations.invalidItemCount}
                hasItems={tableStateHasItems}
                emptyTitle="暂无已完成任务"
                emptyDescription="完成的任务结果会在这里保留可追溯记录。"
              />
              {tableStateHasItems || controller.operations.status === "loading" ? (
                <TaskOperationsTable
                  operations={visibleOperations}
                  mobile={mobile}
                  loading={controller.operations.status === "loading"}
                  readOnly={readOnly}
                  onOpenDetail={openDetail}
                  onRetry={openRetry}
                  onCancel={openCancel}
                />
              ) : null}
            </Tabs.TabPanel>
            <Tabs.TabPanel
              value="activity"
              label={
                <TaskTabLabel
                  label="Activity"
                  active={tab === "activity"}
                  onActivate={() => setTab("activity")}
                />
              }
              destroyOnHide
            >
              <ActivityPanel controller={controller} />
            </Tabs.TabPanel>
          </Tabs>
        </div>
        <aside className="task-operations__sidebar">
          <SavedViewsPanel controller={controller.savedViews} readOnly={readOnly} />
          <ReconciliationPanel controller={controller.reconciliation} readOnly={readOnly} />
        </aside>
      </div>
      {controller.mutation.error ? (
        <div className="task-operations__mutation-error" role="alert">
          {controller.mutation.error}
        </div>
      ) : null}
      <TaskOperationDetailDrawer
        visible={detailVisible}
        state={detailState}
        fallbackTask={selectedTask}
        readOnly={readOnly}
        onClose={closeDetail}
        onRetry={openRetry}
        onCancel={openCancel}
        onAcknowledge={runAcknowledge}
        onHandoff={onHandoff}
        returnFocusRef={focus.returnFocusRef}
      />
      <TaskRetryDialog
        visible={retryTask !== null}
        task={retryTask}
        readOnly={readOnly}
        saving={controller.mutation.status === "saving"}
        onClose={closeRetry}
        onSubmit={runRetry}
        returnFocusRef={focus.returnFocusRef}
      />
      <TaskCancelDialog
        visible={cancelTask !== null}
        task={cancelTask}
        readOnly={readOnly}
        saving={controller.mutation.status === "saving"}
        onClose={closeCancel}
        onSubmit={runCancel}
        returnFocusRef={focus.returnFocusRef}
      />
    </section>
  );
}
