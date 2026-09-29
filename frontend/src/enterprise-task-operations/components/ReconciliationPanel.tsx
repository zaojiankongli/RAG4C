import { ArrowRightIcon, ErrorCircleIcon, SecuredIcon } from "tdesign-icons-react";

import { Button, Empty, Tag } from "../../ui";
import type { TaskReconciliationController, TaskReconciliationItem } from "./taskOperationsTypes";
import { formatTaskDate } from "./taskOperationsUi";

export interface ReconciliationPanelProps {
  controller: TaskReconciliationController;
  readOnly?: boolean;
}

function severityTheme(
  severity: TaskReconciliationItem["severity"],
): "default" | "warning" | "danger" {
  return severity === "critical" || severity === "high"
    ? "danger"
    : severity === "medium"
      ? "warning"
      : "default";
}

export default function ReconciliationPanel({
  controller,
  readOnly = false,
}: ReconciliationPanelProps) {
  const openCount =
    controller.status === "ready" || controller.status === "empty"
      ? controller.items.filter((item) => item.status === "open").length
      : null;

  return (
    <section
      className="task-operations__side-panel task-operations__reconciliation-panel"
      aria-labelledby="task-reconciliation-title"
    >
      <div className="task-operations__side-heading">
        <div>
          <span className="task-operations__eyebrow">STATE INTEGRITY</span>
          <h2 id="task-reconciliation-title">Reconciliation</h2>
        </div>
        <Tag theme="warning" variant="light-outline">
          <SecuredIcon aria-hidden="true" />{" "}
          {openCount === null ? "open 未返回" : `${openCount} open`}
        </Tag>
      </div>
      {controller.status === "unavailable" ? (
        <div role="alert">
          <Tag theme="warning">Reconciliation 暂不可用</Tag>
        </div>
      ) : null}
      {controller.status === "partial" ? (
        <div role="alert">
          <Tag theme="warning">部分对账事项无法读取</Tag>
        </div>
      ) : null}
      {controller.status === "error" ? (
        <div role="alert">
          <Tag theme="danger">Reconciliation 读取失败</Tag>
        </div>
      ) : null}
      {controller.items.length ? (
        <ul className="task-operations__reconciliation-list">
          {controller.items.map((item) => (
            <li key={item.id}>
              <div className="task-operations__reconciliation-marker" aria-hidden="true">
                <ErrorCircleIcon />
              </div>
              <div className="task-operations__reconciliation-copy">
                <div>
                  <Tag theme={severityTheme(item.severity)} variant="light-outline" size="small">
                    {item.severity}
                  </Tag>
                  <code>{item.id}</code>
                </div>
                <strong>{item.summary}</strong>
                <small>
                  {formatTaskDate(item.detected_at)} · task {item.task_id}
                </small>
                {item.action_required && item.status === "open" && controller.onResolve ? (
                  <Button
                    type="text"
                    size="small"
                    icon={<ArrowRightIcon />}
                    disabled={readOnly}
                    aria-label={`处理 ${item.id}`}
                    onClick={() => controller.onResolve?.(item)}
                  >
                    处理
                  </Button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : controller.status === "ready" || controller.status === "empty" ? (
        <Empty type="empty" title="暂无对账事项" description="运行状态一致，暂不需要人工介入。" />
      ) : null}
      <span className="task-operations__side-note">对账只修复状态一致性，不重放任务副作用。</span>
    </section>
  );
}
