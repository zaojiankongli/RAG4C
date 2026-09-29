import { RefreshIcon } from "tdesign-icons-react";

import { Button } from "../../ui";
import { TaskBoundaryTag, formatTaskDate } from "./taskOperationsUi";

export interface TaskOperationsHeaderProps {
  tenantLabel?: string;
  asOf?: string | null;
  readOnly?: boolean;
  onRefresh?: () => void;
  title?: string;
}

export default function TaskOperationsHeader({
  tenantLabel = "当前租户",
  asOf,
  readOnly = false,
  onRefresh,
  title = "任务运营中心",
}: TaskOperationsHeaderProps) {
  return (
    <header className="task-operations__header">
      <div className="task-operations__title-block">
        <div className="task-operations__title-copy">
          <h1 id="task-operations-title">{title}</h1>
          <p>
            <span className="task-operations__tenant-label">{tenantLabel}</span>
            <span aria-hidden="true"> · 查看任务进度，处理执行异常</span>
          </p>
        </div>
      </div>
      <div className="task-operations__header-actions">
        <div className="task-operations__header-status">
          <TaskBoundaryTag readOnly={readOnly} />
          {asOf ? (
            <span className="task-operations__as-of">权威时间 {formatTaskDate(asOf)}</span>
          ) : null}
        </div>
        {onRefresh ? (
          <Button type="default" size="small" icon={<RefreshIcon />} onClick={onRefresh}>
            刷新状态
          </Button>
        ) : null}
      </div>
    </header>
  );
}
